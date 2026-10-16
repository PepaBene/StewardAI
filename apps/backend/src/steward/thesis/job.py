"""The thesis job: the owner asks for the thesis of one stock and the app does the rest.

Started by the owner (never by a schedule or the autopilot), it runs without further input:

1. a deep research run on the stock with every record the app holds (portfolio, transactions,
   notes, documents, news wire, risk, fundamentals, filings) and web search;
2. the authoring step (`thesis/author.py`): stance, executive summary, the detailed memo, KPIs,
   exit conditions, scenarios and targets, fenced in code;
3. for a holding, the thesis is saved as an AI-authored `theses` config version (roll back to
   undo it), replacing any earlier thesis for the ticker; for a stock the owner might buy, it
   waits as an open memo until the owner accepts it. A trim or exit stance opens a
   "Needs you" notice. Nothing is ever traded.
"""

import datetime as dt
import json
import logging
from typing import Any, Literal, cast

import asyncpg

from steward.ai import AINotConfigured, Usage
from steward.autopilot import ledger
from steward.config.jobs import JobsConfig, ThesisSettings, effort_kwargs
from steward.config.store import ConfigConflictError, load_active, save_config
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig, ThesisConfig
from steward.engine import overrides
from steward.jobs.runner import JobContext, JobFailed
from steward.llm.client import create_llm
from steward.llm.routing import routing_client
from steward.llm.types import LLMError
from steward.ops import tasks
from steward.research.run import run_research_job
from steward.settings import get_settings
from steward.thesis import author
from steward.thesis.proposal import replace_thesis

logger = logging.getLogger("steward.thesis")

Mode = Literal["held", "buy"]
STANCE_TASK = "thesis_stance"
MAX_OUTPUT_TOKENS = 12000
AUTHOR_ATTEMPTS = 2


async def _thesis_settings(ctx: JobContext) -> ThesisSettings:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    return (stored.body if stored else JobsConfig()).thesis_settings


async def _position(pool: asyncpg.Pool, ticker: str, mode: Mode) -> dict[str, Any]:
    """Name, currency, price and weight; a stock not held has the instrument's currency only."""
    row = await pool.fetchrow(
        "select p.current_price, p.instrument_currency, p.weight, i.name from"
        " public.position_snapshot p join public.instrument i on i.t212_ticker = p.ticker"
        " where p.ticker = $1 order by p.snapshot_date desc limit 1",
        ticker,
    )
    if row is not None:
        return {
            "name": str(row["name"] or ticker),
            "currency": str(row["instrument_currency"]),
            "price": float(row["current_price"]),
            "weight": float(row["weight"]) if row["weight"] is not None else None,
        }
    inst = await pool.fetchrow(
        "select name, currency from public.instrument where t212_ticker = $1", ticker
    )
    if mode == "held":
        raise JobFailed(f"{ticker} is not in the latest portfolio snapshot")
    if inst is None or not inst["currency"]:
        raise JobFailed(f"{ticker} is not a known instrument with a currency")
    return {
        "name": str(inst["name"] or ticker),
        "currency": str(inst["currency"]).upper(),
        "price": None,
        "weight": None,
    }


def _body(raw: Any) -> dict[str, Any]:
    data = json.loads(raw) if isinstance(raw, str) else raw
    return cast("dict[str, Any]", data)


async def author_from_report(
    ctx: JobContext, report_id: int, mode: Mode, cfg: ThesisSettings
) -> dict[str, Any]:
    """Write, save and record the thesis of `ctx.params['ticker']` from research report
    `report_id`. Returns the memo id and stance."""
    pool = ctx.pool
    ticker = str(ctx.params["ticker"])
    existing = await pool.fetchval(
        "select id from public.thesis_memo where job_id = $1", ctx.job_id
    )
    if existing is not None:
        return {"memo_id": int(existing), "resumed": True}
    row = await pool.fetchrow("select body from public.report where id = $1", report_id)
    strategy = await load_active(pool, "strategy", StrategyConfig)
    if row is None or strategy is None:
        raise JobFailed("the research report or the strategy is missing")
    pos = await _position(pool, ticker, mode)
    report = _body(row["body"])
    text, figures = author.report_text(report)
    prompt = author.author_prompt(
        ticker=ticker,
        name=pos["name"],
        currency=pos["currency"],
        price=pos["price"],
        weight=pos["weight"],
        report_text=text,
        valuation=figures,
        strategy_text=author.strategy_text(strategy.body, ticker),
    )
    try:
        client = routing_client(create_llm(get_settings()))
    except AINotConfigured as exc:
        raise JobFailed(str(exc)) from exc
    usage = Usage(model=cfg.model)
    corrections = await overrides.lessons(pool, "theses", "theses the engine wrote")
    async with ctx.step("author_thesis") as step:
        today = dt.datetime.now(dt.UTC).date()
        ask = prompt
        # A draft the rules refuse (scenario values out of order, say) is asked for once more
        # with the reason: the research behind it is already paid for.
        for attempt in range(AUTHOR_ATTEMPTS):
            try:
                response = await client.messages.parse(
                    model=cfg.model,
                    system=author.system_for(mode)
                    + ("\n\n" + corrections if corrections else ""),
                    messages=[{"role": "user", "content": ask}],
                    output_format=author.ThesisDraft,
                    **effort_kwargs(cfg.model, cfg.effort, MAX_OUTPUT_TOKENS),
                )
            except LLMError as exc:
                raise JobFailed(f"the model call failed: {str(exc)[:200]}") from exc
            usage.add(response.usage)
            step.usage = usage.step_usage()
            draft = response.parsed_output
            if draft is None:
                raise JobFailed("the model gave no parseable thesis")
            try:
                thesis = author.build_thesis(
                    draft, ticker, pos["currency"], today, today_price=pos["price"], mode=mode
                )
                break
            except author.AuthorError as exc:
                if attempt + 1 == AUTHOR_ATTEMPTS:
                    raise JobFailed(f"the thesis was refused: {exc}") from exc
                step.detail["retried_because"] = str(exc)[:300]
                ask = (
                    f"{prompt}\n\nYour previous draft was refused: {exc}. "
                    "Write the whole thesis again with that fixed."
                )
        valid = {str(s.get("id")) for s in report.get("sources", [])}
        memo = author.clean_memo(draft, valid)
        version_id = await _save_held(pool, thesis, draft, report_id) if mode == "held" else None
        price = pos["price"] or draft.current_price
        memo_id = await pool.fetchval(
            "insert into public.thesis_memo (ticker, mode, stance, executive_summary, body,"
            " research_report_id, job_id, review_id, config_version_id, status)"
            " values ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10) returning id",
            ticker,
            mode,
            draft.stance,
            draft.executive_summary.strip(),
            json.dumps(
                {
                    "thesis": thesis.model_dump(mode="json"),
                    "memo": memo,
                    "stance_reason": draft.stance_reason,
                    "current_price": price,
                    "currency": pos["currency"],
                    "cited_sources": draft.cited_sources,
                    "model": cfg.model,
                }
            ),
            report_id,
            ctx.job_id,
            ctx.params.get("review_id"),
            version_id,
            "accepted" if version_id is not None else "open",
        )
        step.detail.update(
            memo_id=memo_id,
            stance=draft.stance,
            kpis=len(thesis.kpis),
            saved_version_id=version_id,
        )
    if mode == "held" and draft.stance in author.STANCES_NEEDING_OWNER:
        weights = await ledger.holding_weights(pool)
        await tasks.see(
            pool,
            STANCE_TASK,
            ticker,
            title=f"The thesis for {ticker} says {draft.stance}",
            detail=draft.stance_reason,
            href=f"/research/{report_id}",
            weight=weights.get(ticker),
            status="needs_owner",
        )
    return {"memo_id": int(memo_id), "stance": draft.stance}


async def _save_held(
    pool: asyncpg.Pool, thesis: ThesisConfig, draft: author.ThesisDraft, report_id: int
) -> int | None:
    """Save the drafted thesis as the stock's thesis, unless the owner has changed what the
    engine wrote for this stock: then the memo stays open for the owner to accept."""
    if await overrides.was_overruled(pool, "config", f"theses:{thesis.ticker}"):
        return None
    stored = await load_active(pool, "theses", ThesesConfig)
    config = stored.body if stored else ThesesConfig()
    try:
        async with pool.acquire() as conn:
            saved = await save_config(
                conn,
                "theses",
                replace_thesis(config, thesis),
                created_by=None,
                note=f"{author.MARKER} thesis for {thesis.ticker} from research report "
                f"#{report_id}; stance {draft.stance}: {draft.stance_reason}"[:480],
                expected_active_id=stored.id if stored else None,
            )
    except ConfigConflictError as exc:
        raise JobFailed(
            "the theses were saved by someone else while this ran; run it again"
        ) from exc
    return int(saved.id)


async def accept_memo(
    pool: asyncpg.Pool, memo_id: int, *, created_by: str | None = None
) -> dict[str, Any]:
    """The owner decided to buy: save the thesis of an open buy memo as the stock's thesis. The
    version carries the owner's id, so it is the owner's decision and not an engine write."""
    row = await pool.fetchrow(
        "select ticker, mode, status, body, stance from public.thesis_memo where id = $1", memo_id
    )
    if row is None:
        raise LookupError("no such thesis memo")
    if row["status"] != "open":
        raise ValueError(f"the memo is already {row['status']}")
    thesis = ThesisConfig.model_validate(_body(row["body"])["thesis"])
    stored = await load_active(pool, "theses", ThesesConfig)
    config = stored.body if stored else ThesesConfig()
    try:
        async with pool.acquire() as conn:
            saved = await save_config(
                conn,
                "theses",
                replace_thesis(config, thesis),
                created_by=created_by,
                note=f"{author.MARKER} buy thesis for {thesis.ticker} (memo #{memo_id})",
                expected_active_id=stored.id if stored else None,
            )
    except ConfigConflictError as exc:
        raise ValueError("the theses changed since; open the memo again") from exc
    await pool.execute(
        "update public.thesis_memo set status = 'accepted', config_version_id = $2 where id = $1",
        memo_id,
        saved.id,
    )
    return {"memo_id": memo_id, "ticker": row["ticker"], "saved_version_id": int(saved.id)}


async def thesis_handler(ctx: JobContext) -> None:
    ticker = str(ctx.params.get("ticker") or "").strip()
    if not ticker:
        raise JobFailed("this job needs a 'ticker' parameter")
    mode: Mode = "buy" if ctx.params.get("mode") == "buy" else "held"
    cfg = await _thesis_settings(ctx)
    ctx.params["ticker"] = ticker
    ctx.params.setdefault("profile", cfg.research_profile)
    ctx.params["full_data"] = True
    report_id = await run_research_job(ctx)
    await author_from_report(ctx, report_id, mode, cfg)
    review_id = ctx.params.get("review_id")
    if review_id is not None:
        from steward.thesis.review import finish_if_ready

        await finish_if_ready(ctx.pool, int(review_id))
