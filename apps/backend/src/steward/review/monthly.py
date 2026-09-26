"""`monthly_review` (first weekday of the month): rule compliance and proposed actions.

Code writes everything factual:
- the compliance table: every strategy rule, every position hard limit and every target
  band, each pass / breach / unknown with its observed value, from the latest snapshot;
- thesis KPI statuses (latest verdict per KPI; unknown when there is none) and theses past
  their review date;
- flags: positions large against their market's daily turnover (FITRS), holdings sharing an
  ultimate parent (GLEIF) and their combined weight, and falling 13F holder counts.

The model (Opus 5.5 by default) reads that and proposes actions. Each action must cite a
strategy rule id or a thesis KPI ("TICKER:kpi_id") that exists; code drops any action that
cites neither, so no action is published without a citation.
"""

import asyncio
import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

import anthropic
import asyncpg
import pydantic
from anthropic import transform_schema
from anthropic.types import MessageParam, OutputConfigParam
from pydantic import BaseModel, Field

from steward.ai import AINotConfigured, Usage, create_client
from steward.config.jobs import JobsConfig, ReviewSettings
from steward.config.prompts import load_prompts
from steward.config.store import load_active
from steward.config.strategy import (
    CashWeightRule,
    CategoryWeightRule,
    PositionWeightRule,
    StrategyConfig,
    StrategyRule,
)
from steward.config.thesis import ThesesConfig
from steward.jobs.runner import JobContext
from steward.ownership.read import holding_ownership
from steward.portfolio.breaches import _holds, evaluate
from steward.portfolio.drift import DriftReport
from steward.portfolio.read import portfolio_overview
from steward.research.sources import CitedSentence, Registry, Source
from steward.settings import get_settings

logger = logging.getLogger(__name__)

Status = Literal["pass", "breach", "unknown"]


class ComplianceRow(BaseModel):
    id: str = Field(description="The strategy rule id, or hard_limit:/band: keys.")
    kind: Literal["rule", "hard_limit", "band"]
    description: str
    status: Status
    detail: str
    source_ids: list[str] = Field(default_factory=list)


class KpiRow(BaseModel):
    ticker: str
    kpi_id: str
    ref: str = Field(description="TICKER:kpi_id, as actions cite it.")
    description: str
    status: Status
    period: str | None
    verdict_id: int | None
    source_ids: list[str] = Field(default_factory=list)


class ProposedAction(BaseModel):
    action: str
    ticker: str | None
    rationale: str
    rule_ids: list[str] = Field(description="Strategy rule ids it rests on.")
    kpis: list[str] = Field(description="Thesis KPIs it rests on, as TICKER:kpi_id.")


class ReviewBody(BaseModel):
    month: str
    headline: str
    as_of: dt.date | None = Field(description="Snapshot date the compliance is judged on.")
    total_base: float | None
    base_currency: str | None
    compliance: list[ComplianceRow]
    kpis: list[KpiRow]
    reviews_due: list[str]
    flags: list[CitedSentence]
    actions: list[ProposedAction]
    dropped_actions: int = Field(default=0, description="Proposed actions citing nothing valid.")
    sources: list[Source]
    model: str | None = None
    notes: list[str] = Field(default_factory=list)


class ActionDraft(BaseModel):
    action: str = Field(description="One concrete step, e.g. 'Trim X towards its target'.")
    ticker: str | None = None
    rationale: str
    rule_ids: list[str] = Field(default_factory=list)
    kpis: list[str] = Field(default_factory=list, description="TICKER:kpi_id")


class ReviewDraft(BaseModel):
    actions: list[ActionDraft]


class ReviewWriter(Protocol):
    async def propose(
        self, material: str, today: dt.date
    ) -> tuple[ReviewDraft | None, Usage, str | None]: ...


SYSTEM = """\
You review a personal investment portfolio against its owner's written strategy. You get a \
compliance table (every strategy rule with pass, breach or unknown), each holding's thesis \
KPI statuses, theses due for review, and flags. Propose at most eight concrete actions. \
Every action must cite the strategy rule ids or thesis KPIs (TICKER:kpi_id, exactly as \
listed) it rests on; an action you cannot tie to one is not an action to propose. Do not \
state figures that are not in the material. If nothing needs doing, return no actions.
"""


class ClaudeReviewWriter:
    """Proposes the actions: through the Batch API when `use_batch` is set (the review is not
    time-sensitive; half price), falling back to direct calls when the batch does not finish
    within `batch_wait_minutes` or fails. No cache breakpoint: one call a month, and a retry
    only after a failure, so a cache write would almost never be read back."""

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        settings: ReviewSettings,
        system: str = SYSTEM,
        poll_seconds: float = 30.0,
    ) -> None:
        self._client = client
        self._settings = settings
        self._system = system
        self._poll = poll_seconds

    def _messages(self, material: str, today: dt.date) -> list[MessageParam]:
        return [cast(MessageParam, {"role": "user", "content": f"Today is {today}.\n\n{material}"})]

    async def propose(
        self, material: str, today: dt.date
    ) -> tuple[ReviewDraft | None, Usage, str | None]:
        if self._settings.use_batch:
            batched = await self._propose_batch(material, today)
            if batched is not None:
                return batched
        return await self._propose_direct(material, today)

    async def _propose_batch(
        self, material: str, today: dt.date
    ) -> tuple[ReviewDraft | None, Usage, str | None] | None:
        """The draft through the Batch API, or None to fall back to direct calls (the batch
        did not finish in time or failed before a message came back). A message that came
        back is final, parseable or not, so its batch-priced tokens are recorded exactly."""
        usage = Usage(model=self._settings.model, batch=True)
        params = {
            "model": self._settings.model,
            "max_tokens": 16000,
            "system": self._system,
            "messages": self._messages(material, today),
            "output_config": {
                "effort": self._settings.effort,
                "format": {
                    "type": "json_schema",
                    "schema": transform_schema(ReviewDraft.model_json_schema()),
                },
            },
        }
        try:
            batch = await self._client.messages.batches.create(
                requests=[{"custom_id": "review", "params": cast(Any, params)}]
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._settings.batch_wait_minutes * 60
            while batch.processing_status != "ended":
                if loop.time() > deadline:
                    await self._client.messages.batches.cancel(batch.id)
                    logger.warning("review batch %s not done in time; calling directly", batch.id)
                    return None
                await asyncio.sleep(self._poll)
                batch = await self._client.messages.batches.retrieve(batch.id)
            async for result in await self._client.messages.batches.results(batch.id):
                if result.custom_id != "review":
                    continue
                if result.result.type != "succeeded":
                    logger.warning("review batch request %s", result.result.type)
                    return None
                message = result.result.message
                usage.add(message.usage)
                if message.stop_reason == "refusal":
                    return None, usage, "the model declined"
                text = "".join(getattr(b, "text", "") for b in message.content if b.type == "text")
                try:
                    return ReviewDraft.model_validate_json(text), usage, None
                except pydantic.ValidationError:
                    return None, usage, f"no parseable answer ({message.stop_reason})"
        except anthropic.APIError as exc:
            logger.warning("review batch failed (%s); calling directly", exc)
            return None
        return None

    async def _propose_direct(
        self, material: str, today: dt.date
    ) -> tuple[ReviewDraft | None, Usage, str | None]:
        usage = Usage(model=self._settings.model)
        note = None
        for _ in range(self._settings.max_steps):
            try:
                response = await self._client.messages.parse(
                    model=self._settings.model,
                    max_tokens=16000,
                    system=self._system,
                    messages=self._messages(material, today),
                    output_config=cast(OutputConfigParam, {"effort": self._settings.effort}),
                    output_format=ReviewDraft,
                )
            except anthropic.APIError as exc:
                note = f"the call failed: {exc}"[:300]
                continue
            except (pydantic.ValidationError, ValueError):
                # `parse` validates the output before returning: malformed or truncated JSON
                # surfaces here (and that response's usage with it).
                note = "no parseable answer"
                continue
            usage.add(response.usage)
            if response.stop_reason == "refusal":
                return None, usage, "the model declined"
            if response.parsed_output is not None:
                return response.parsed_output, usage, None
            note = "no parseable answer"
        return None, usage, note


def first_weekday(day: dt.date) -> dt.date:
    first = day.replace(day=1)
    while first.weekday() >= 5:
        first += dt.timedelta(days=1)
    return first


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.2f}%"


def _not_checked(rule: StrategyRule, drift: DriftReport) -> tuple[Status, str] | None:
    """The status of a rule `evaluate` ran no check for, or None when it ran one.

    `evaluate` checks held positions, existing buckets and a known cash weight only. A rule
    on a position or bucket the portfolio does not hold is judged at a weight of 0 (so a
    minimum is a breach); a cash rule with no cash weight, or a rule over every position of
    an empty portfolio, is unknown.
    """
    limit = float(getattr(rule, "value", 0))
    target: str | None = None
    if isinstance(rule, PositionWeightRule):
        held = [p for p in drift.positions if p.held]
        if rule.ticker is None:
            return None if held else ("unknown", "no positions held to check")
        if any(p.ticker == rule.ticker for p in held):
            return None
        target = f"{rule.ticker} is not held"
    elif isinstance(rule, CategoryWeightRule):
        found = next((d for d in drift.categories if d.dimension == rule.dimension), None)
        buckets = found.buckets if found else []
        if rule.bucket is None:
            return None if buckets else ("unknown", f"no {rule.dimension} buckets to check")
        if any(b.bucket == rule.bucket for b in buckets):
            return None
        target = f"no holding is in {rule.dimension} {rule.bucket}"
    elif isinstance(rule, CashWeightRule):
        if drift.cash.weight is None:
            return "unknown", "cash weight unknown in the latest snapshot"
        return None
    else:
        return None
    if _holds(0.0, rule.operator, limit):
        return "pass", f"{target} (0.00%); holds ({rule.operator} {_pct(limit)})"
    requires = f"the rule requires {rule.operator} {_pct(limit)}"
    return "breach", f"Rule {rule.id}: {target} (0.00%); {requires}"


async def compliance(
    pool: asyncpg.Pool, reg: Registry, strategy: StrategyConfig, strategy_version: int | None
) -> tuple[list[ComplianceRow], dt.date | None, float | None, str | None]:
    overview = await portfolio_overview(pool)
    drift = overview.drift
    as_of = overview.freshness.snapshot_date if overview.freshness else None
    strategy_sid = reg.add(
        "strategy",
        strategy_version or "active",
        "Investment strategy",
        app_path="/config/strategy",
        tier=1,
        snippet=f"Rules: {'; '.join(f'{r.id}: {r.description}' for r in strategy.rules)}",
    )
    snapshot_sid = (
        reg.add(
            "position",
            f"snapshot:{as_of}",
            f"Portfolio snapshot {as_of}",
            app_path="/",
            tier=1,
            snippet=f"total {overview.base.total if overview.base else 'n/a'}",
            as_of=str(as_of),
        )
        if as_of
        else None
    )
    ids = [i for i in (strategy_sid, snapshot_sid) if i]
    findings = evaluate(drift, strategy) if drift is not None else []
    rows: list[ComplianceRow] = []
    for rule in strategy.rules:
        hits = [f for f in findings if f.key.startswith(f"rule:{rule.id}:")]
        limit = f"{rule.operator} {getattr(rule, 'value', '')}"
        description = rule.description or f"{rule.type} {limit}"
        unchecked = _not_checked(rule, drift) if drift is not None else None
        if drift is None:
            status: Status = "unknown"
            detail = "no portfolio snapshot yet"
        elif hits:
            status, detail = "breach", "; ".join(f.detail for f in hits)
        elif unchecked is not None:
            status, detail = unchecked
        else:
            status, detail = "pass", f"holds ({rule.type} {limit})"
        rows.append(
            ComplianceRow(
                id=rule.id,
                kind="rule",
                description=description,
                status=status,
                detail=detail,
                source_ids=ids,
            )
        )
    for target in strategy.target_positions:
        position = (
            next((p for p in drift.positions if p.ticker == target.ticker), None) if drift else None
        )
        if target.hard_limit is not None:
            key = f"hard_limit:{target.ticker}"
            hit = next((f for f in findings if f.key == key), None)
            rows.append(
                ComplianceRow(
                    id=key,
                    kind="hard_limit",
                    description=f"{target.ticker} at most {_pct(target.hard_limit)}",
                    status="unknown" if position is None else ("breach" if hit else "pass"),
                    detail=hit.detail
                    if hit
                    else (
                        f"weighs {_pct(position.weight)}" if position else "not held or no snapshot"
                    ),
                    source_ids=ids,
                )
            )
        key = f"band:position:{target.ticker}"
        hit = next((f for f in findings if f.key == key), None)
        rows.append(
            ComplianceRow(
                id=key,
                kind="band",
                description=f"{target.ticker} target {_pct(target.target_weight)} "
                f"+/- {_pct(target.band)}",
                status="unknown" if position is None else ("breach" if hit else "pass"),
                detail=hit.detail
                if hit
                else (f"weighs {_pct(position.weight)}" if position else "not held or no snapshot"),
                source_ids=ids,
            )
        )
    total = float(overview.base.total) if overview.base else None
    currency = overview.base.base_currency if overview.base else None
    return rows, as_of, total, currency


async def kpi_rows(pool: asyncpg.Pool, reg: Registry, theses: ThesesConfig) -> list[KpiRow]:
    rows: list[KpiRow] = []
    for thesis in theses.theses:
        latest = {
            r["kpi_id"]: r
            for r in await pool.fetch(
                "select distinct on (kpi_id) id, kpi_id, status, period, rationale"
                " from public.verdict where ticker = $1 order by kpi_id, id desc",
                thesis.ticker,
            )
        }
        for kpi in thesis.kpis:
            v = latest.get(kpi.id)
            sid = (
                reg.add(
                    "verdict",
                    v["id"],
                    f"{thesis.ticker} KPI {kpi.id}: {v['status']}",
                    app_path=f"/holdings/{thesis.ticker}",
                    tier=1,
                    snippet=v["rationale"],
                )
                if v
                else None
            )
            rows.append(
                KpiRow(
                    ticker=thesis.ticker,
                    kpi_id=kpi.id,
                    ref=f"{thesis.ticker}:{kpi.id}",
                    description=kpi.description
                    or f"{kpi.metric} {kpi.operator} {kpi.threshold} {kpi.unit}",
                    status=v["status"] if v else "unknown",
                    period=v["period"] if v else None,
                    verdict_id=v["id"] if v else None,
                    source_ids=[sid] if sid else [],
                )
            )
    return rows


async def flags(pool: asyncpg.Pool, reg: Registry, settings: ReviewSettings) -> list[CitedSentence]:
    out: list[CitedSentence] = []
    positions = await pool.fetch(
        "select p.ticker, p.value, p.instrument_currency, p.weight, p.snapshot_date, i.isin,"
        " l.avg_daily_turnover, l.turnover_currency, l.liquid from public.position_snapshot p"
        " join public.instrument i on i.t212_ticker = p.ticker"
        " left join public.listing_liquidity l on l.isin = i.isin"
        " where p.snapshot_date = coalesce("
        " (select max(snapshot_date) from public.account_snapshot),"
        " (select max(snapshot_date) from public.position_snapshot))"
    )
    for p in positions:
        if p["avg_daily_turnover"] and p["turnover_currency"] == p["instrument_currency"]:
            days = float(p["value"] / p["avg_daily_turnover"])
            if days > settings.liquidity_days or p["liquid"] is False:
                sid = reg.add(
                    "listing",
                    f"fitrs:{p['isin']}",
                    f"ESMA FITRS liquidity, {p['isin']}",
                    tier=1,
                    snippet=f"average daily turnover {p['avg_daily_turnover']} "
                    f"{p['turnover_currency']}, liquid {p['liquid']}",
                )
                illiquid = " and not classed as liquid" if p["liquid"] is False else ""
                out.append(
                    CitedSentence(
                        text=f"{p['ticker']} is {days:.2f} days of its market's average daily "
                        f"turnover{illiquid}.",
                        source_ids=[sid],
                    )
                )
    parents = await pool.fetch(
        "select r.object_key, max(r.object_name) as name, array_agg(distinct r.ticker) as tickers,"
        " max(r.id) as rel_id, max(r.source_ref) as ref from public.entity_relationship r"
        " where r.type = 'ultimate_parent' and r.status = 'confirmed'"
        " and r.ticker = any($1::text[]) group by r.object_key having count(distinct r.ticker) > 1",
        [p["ticker"] for p in positions],
    )
    weights = {p["ticker"]: float(p["weight"] or 0) for p in positions}
    for g in parents:
        sid = reg.add(
            "relationship",
            g["rel_id"],
            f"GLEIF ultimate parent {g['name']}",
            url=g["ref"],
            tier=1,
            snippet=f"ultimate parent of {', '.join(g['tickers'])}",
        )
        combined = sum(weights.get(t, 0) for t in g["tickers"])
        out.append(
            CitedSentence(
                text=f"{', '.join(sorted(g['tickers']))} share the ultimate parent {g['name']}: "
                f"{_pct(combined)} of the portfolio combined.",
                source_ids=[sid],
            )
        )
    for p in positions:
        own = await holding_ownership(pool, p["ticker"], periods=2, top=1)
        if len(own.periods) == 2 and own.periods[1].holders:
            now, before = own.periods
            change = now.holders / before.holders - 1
            if change <= -settings.holder_drop_flag_pct / 100:
                sid = reg.add(
                    "filing13f",
                    f"13F:{now.period}:{','.join(own.cusips)}",
                    f"SEC Form 13F data sets, period {now.period}",
                    tier=1,
                    snippet=f"{now.holders} holders vs {before.holders} for {before.period}",
                )
                out.append(
                    CitedSentence(
                        text=f"13F holders of {p['ticker']} fell {change * 100:.0f}% to "
                        f"{now.holders} for the period ending {now.period}.",
                        source_ids=[sid],
                    )
                )
    return out


def review_material(body: ReviewBody, strategy: StrategyConfig) -> str:
    return json.dumps(
        {
            "strategy": {
                "goals": strategy.goals,
                "notes": strategy.notes,
                "horizon_years": strategy.horizon_years,
            },
            "compliance": [
                r.model_dump(include={"id", "kind", "description", "status", "detail"})
                for r in body.compliance
            ],
            "thesis_kpis": [
                k.model_dump(include={"ref", "description", "status", "period"}) for k in body.kpis
            ],
            "theses_due_for_review": body.reviews_due,
            "flags": [f.text for f in body.flags],
            "citable": {
                "rule_ids": [r.id for r in strategy.rules],
                "kpis": [k.ref for k in body.kpis],
            },
        },
        ensure_ascii=False,
        indent=1,
    )


def keep_cited(
    draft: ReviewDraft, rule_ids: set[str], kpi_refs: set[str]
) -> tuple[list[ProposedAction], int]:
    kept: list[ProposedAction] = []
    dropped = 0
    for a in draft.actions:
        rules = [r for r in dict.fromkeys(a.rule_ids) if r in rule_ids]
        kpis = [k for k in dict.fromkeys(a.kpis) if k in kpi_refs]
        if (
            not a.action.strip()
            or not (rules or kpis)
            or len(rules) + len(kpis) != len(set(a.rule_ids) | set(a.kpis))
        ):
            dropped += 1
            continue
        kept.append(
            ProposedAction(
                action=a.action.strip(),
                ticker=a.ticker,
                rationale=a.rationale.strip(),
                rule_ids=rules,
                kpis=kpis,
            )
        )
    return kept, dropped


async def build_review(
    pool: asyncpg.Pool,
    now: dt.datetime,
    settings: ReviewSettings,
    writer: ReviewWriter | None,
    strategy: StrategyConfig | None,
    strategy_version: int | None,
    theses: ThesesConfig,
) -> tuple[ReviewBody, Usage | None]:
    reg = Registry()
    notes: list[str] = []
    strategy = strategy or StrategyConfig(base_currency="EUR")
    rows, as_of, total, currency = await compliance(pool, reg, strategy, strategy_version)
    kpis = await kpi_rows(pool, reg, theses)
    today = now.date()
    due = [t.ticker for t in theses.theses if t.review_date is not None and t.review_date <= today]
    body = ReviewBody(
        month=today.strftime("%Y-%m"),
        headline="",
        as_of=as_of,
        total_base=total,
        base_currency=currency,
        compliance=rows,
        kpis=kpis,
        reviews_due=due,
        flags=await flags(pool, reg, settings),
        actions=[],
        sources=[],
    )
    usage: Usage | None = None
    if writer is None:
        notes.append("Actions not proposed: ANTHROPIC_API_KEY is not set.")
    else:
        draft, usage, note = await writer.propose(review_material(body, strategy), today)
        if draft is None:
            notes.append(f"Actions not proposed: {note}.")
        else:
            body.actions, body.dropped_actions = keep_cited(
                draft, {r.id for r in strategy.rules}, {k.ref for k in kpis}
            )
            body.model = usage.model
    breaches = sum(1 for r in rows if r.status == "breach")
    unknown_kpis = sum(1 for k in kpis if k.status == "unknown")
    body.headline = (
        f"{len(rows)} checks, {breaches} breached; {len(kpis)} thesis KPIs "
        f"({unknown_kpis} unknown); {len(body.actions)} proposed actions."
    )
    body.sources = reg.sources
    body.notes = notes
    return body, usage


def render_md(body: ReviewBody) -> str:
    lines = [f"# Monthly review {body.month}", "", body.headline, "", "## Compliance", ""]
    lines += [f"- [{r.status}] {r.id}: {r.description} ({r.detail})" for r in body.compliance]
    lines += ["", "## Thesis KPIs", ""]
    lines += [f"- [{k.status}] {k.ref}: {k.description}" for k in body.kpis]
    if body.reviews_due:
        lines += ["", f"Theses due for review: {', '.join(body.reviews_due)}"]
    if body.flags:
        lines += ["", "## Flags", ""] + [f"- {f.text}" for f in body.flags]
    lines += ["", "## Proposed actions", ""]
    for a in body.actions:
        cites = ", ".join([*a.rule_ids, *a.kpis])
        lines.append(f"- {a.action}: {a.rationale} (cites {cites})")
    if not body.actions:
        lines.append("- None.")
    return "\n".join(lines)


@dataclass
class ReviewDeps:
    writer: ReviewWriter | None
    now: dt.datetime


async def run_review(ctx: JobContext, deps: ReviewDeps) -> int | None:
    pool = ctx.pool
    async with ctx.step("load_config") as step:
        jobs = await load_active(pool, "jobs", JobsConfig)
        strategy = await load_active(pool, "strategy", StrategyConfig)
        theses = await load_active(pool, "theses", ThesesConfig)
        for v in (jobs, strategy, theses):
            await ctx.record_config_version(v.id if v else None)
        settings = (jobs.body if jobs else JobsConfig()).review
        today = deps.now.date()
        if ctx.trigger == "schedule" and today != first_weekday(today):
            step.detail["skipped"] = f"{today} is not the first weekday of the month"
            return None
    async with ctx.step("review") as step:
        # A redelivered job keeps the report its first attempt stored, without a second call.
        existing = await pool.fetchval(
            "select id from public.report where job_id = $1 and type = 'review'", ctx.job_id
        )
        if existing is not None:
            step.detail["report_id"] = existing
            return int(existing)
        body, usage = await build_review(
            pool,
            deps.now,
            settings,
            deps.writer,
            strategy.body if strategy else None,
            strategy.id if strategy else None,
            theses.body if theses else ThesesConfig(),
        )
        if usage is not None:
            step.usage = usage.step_usage()
        step.detail.update(
            checks=len(body.compliance), actions=len(body.actions), dropped=body.dropped_actions
        )
        report_id: int = await pool.fetchval(
            "insert into public.report (type, period, title, body, body_md, period_end,"
            " config_version_ids, job_id) values ('review', 'monthly', $1, $2::jsonb, $3, $4,"
            " $5, $6) returning id",
            f"Monthly review, {body.month}",
            body.model_dump_json(),
            render_md(body),
            deps.now,
            ctx.config_version_ids,
            ctx.job_id,
        )
        step.detail["report_id"] = report_id
    return report_id


async def monthly_review_handler(ctx: JobContext) -> None:
    settings = get_settings()
    jobs = await load_active(ctx.pool, "jobs", JobsConfig)
    review = (jobs.body if jobs else JobsConfig()).review
    system = (await load_prompts(ctx.pool)).text("monthly_review", SYSTEM)
    try:
        writer: ReviewWriter | None = ClaudeReviewWriter(create_client(settings), review, system)
    except AINotConfigured:
        writer = None
    await run_review(ctx, ReviewDeps(writer=writer, now=dt.datetime.now(dt.UTC)))
