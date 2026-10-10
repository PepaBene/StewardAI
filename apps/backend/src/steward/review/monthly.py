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
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

import anthropic
import asyncpg
import pydantic
from anthropic import transform_schema
from anthropic.types import MessageParam
from pydantic import BaseModel, Field

from steward.ai import AINotConfigured, Usage, create_client
from steward.config.jobs import JobsConfig, ReviewSettings, effort_kwargs, reasoning_kwargs
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
from steward.journal.service import scorecard as journal_scorecard
from steward.llm.client import LLM, create_llm
from steward.llm.routing import routing_client
from steward.llm.types import Message, Request, provider_of
from steward.ownership.read import holding_ownership
from steward.performance.alerts import PerformanceBreach, alert_performance
from steward.performance.review import performance_review
from steward.performance.settings import PerformanceSettings, load_latest_settings
from steward.portfolio.breaches import _holds, evaluate
from steward.portfolio.drift import DriftReport
from steward.portfolio.read import portfolio_overview
from steward.research.sources import CitedSentence, Registry, Source
from steward.settings import get_settings

if TYPE_CHECKING:
    from steward.portfolio.exante import ExAnteRisk
    from steward.portfolio.validation import ModelValidation

logger = logging.getLogger(__name__)

MAX_PACKS = 5  # thesis risk lines in one review
Status = Literal["pass", "breach", "unknown"]


class ComplianceRow(BaseModel):
    id: str = Field(description="The strategy rule id, or hard_limit:/band: keys.")
    kind: Literal["rule", "hard_limit", "band", "performance"]
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


class HoldingPeriodRow(BaseModel):
    """An open purchase lot with both dates of the holding-period test (plan M4.8)."""

    ticker: str
    bought_on: dt.date
    passes_on: dt.date
    months_remaining: float
    passed: bool
    quantity: float
    forfeits_exemption_warning: bool


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
    holding_periods: list[HoldingPeriodRow] = Field(
        default_factory=list,
        description="Open lots with the date each passes the holding-period test; facts, not "
        "tax advice.",
    )
    decision_scorecard: list[str] = Field(
        default_factory=list,
        description="How past journal decisions turned out (price change since the decision, "
        "each with its sample size; sections with too few decisions are not summarised).",
    )
    risk_notes: list[str] = Field(
        default_factory=list,
        description="The forecast volatility from today's holdings, the factor tilts worth a "
        "look and the hardest past episode, each as a plain sentence.",
    )
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
Every action must cite the strategy rule ids, open risk limits (risk:..., listed under \
citable.risk_limits, put in rule_ids) or thesis KPIs (TICKER:kpi_id, exactly as listed) it \
rests on; an action you cannot tie to one is not an action to propose. Do not \
state figures that are not in the material. If nothing needs doing, return no actions.

How to choose and write actions:
- Rank by consequence: confirmed rule breaches and KPI breaches first, then theses due \
for review, then unknown or stale items, and only then optional improvements. Do not \
propose an action for a rule that passes.
- Respect the strategy's own logic: a breach of a risk limit, or a price fall on its \
own, forces a review of the thesis, not a sale; propose a sale only when the material \
shows a broken thesis (an exit condition or KPI breach) or a rule that calls for one. \
Prefer the least drastic action that resolves the item (review, hold, trim before \
sell), and name the holding or rule the action concerns.
- One action per underlying cause: several breaches that share a cause (one oversized \
position breaching both the position and the sector limit) are one action that cites \
all the rule ids.
- Each action says what to do, why in one sentence tied to the cited rule or KPI, and \
what would resolve it. Treat "unknown" as missing data to fix (refresh, review), not as \
a breach or a pass.
- Do not propose purchases of specific new securities: the material does not support \
choosing them.

Apply the owner's strategy as the material states it (its notes outrank this list): a \
core-satellite portfolio whose individual stocks must beat the core ETFs after costs \
and taxes, rotated on fundamentals and events.
- Risk-limit and tier breaches (volatility, drawdown, pair correlation, look-through \
weight, a position above its cap, tier caps) force a thesis review, never an automatic \
sale. A position above the cap is judged on its outlook: hold and let deposits dilute \
it while its thesis still projects growth, trim when that growth is exhausted or the \
thesis weakens.
- A loss of 25% or more from cost needs a review; it becomes a sale only with a \
fundamental miss (a KPI breach, a guidance cut or a profit warning) and no written \
re-underwriting. A price fall alone is never a reason to sell.
- Rotation from a holding to another idea needs the expected-return gap to clear the \
switching hurdle (tax on the lots sold, trading and FX costs, plus the safety margin); \
if the material has no such comparison, propose gathering it instead of a switch.
- Mind tax and the time test when the material shows lot ages or realized gains: lots \
near the three-year mark, the annual proceeds exemption, and losses that can offset \
gains in the same year. Prefer actions that wait for the time test when a thesis is \
intact.
- Check the satellites against the core over the rolling window when the material \
shows it, cash that has stayed above its target, and theses past their review date or \
reviewed less often than their tier requires (speculative monthly, others quarterly).
- Money is in CZK; note when a return is mostly currency effect.
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
        llm: LLM | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._system = system
        self._poll = poll_seconds
        self._llm = llm

    def _messages(self, material: str, today: dt.date) -> list[MessageParam]:
        return [cast(MessageParam, {"role": "user", "content": f"Today is {today}.\n\n{material}"})]

    async def propose(
        self, material: str, today: dt.date
    ) -> tuple[ReviewDraft | None, Usage, str | None]:
        if self._settings.use_batch:
            if provider_of(self._settings.model) == "anthropic":
                batched = await self._propose_batch(material, today)
            else:
                batched = await self._propose_batch_other(material, today)
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
            "system": self._system,
            "messages": self._messages(material, today),
            **reasoning_kwargs(
                self._settings.model,
                self._settings.effort,
                16000,
                {
                    "format": {
                        "type": "json_schema",
                        "schema": transform_schema(ReviewDraft.model_json_schema()),
                    }
                },
            ),
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

    async def _propose_batch_other(
        self, material: str, today: dt.date
    ) -> tuple[ReviewDraft | None, Usage, str | None] | None:
        """The draft through OpenAI's or Google's batch API, or None to fall back to direct
        calls. An answer that came back is final, parseable or not."""
        if self._llm is None:
            return None
        request = Request(
            model=self._settings.model,
            system=self._system,
            messages=(Message("user", f"Today is {today}.\n\n{material}"),),
            schema=ReviewDraft,
            effort=self._settings.effort,
            max_tokens=16000,
        )
        (done,) = await self._llm.complete_batch(
            [request],
            poll_seconds=self._poll,
            wait_minutes=self._settings.batch_wait_minutes,
        )
        if done is None:
            return None
        usage = Usage(model=self._settings.model, batch=True)
        usage.add(done.usage)
        if done.stop_reason == "refusal":
            return None, usage, "the model declined"
        if isinstance(done.parsed, ReviewDraft):
            return done.parsed, usage, None
        return None, usage, f"no parseable answer ({done.stop_reason})"

    async def _propose_direct(
        self, material: str, today: dt.date
    ) -> tuple[ReviewDraft | None, Usage, str | None]:
        usage = Usage(model=self._settings.model)
        note = None
        for _ in range(self._settings.max_steps):
            try:
                response = await self._client.messages.parse(
                    model=self._settings.model,
                    system=self._system,
                    messages=self._messages(material, today),
                    **effort_kwargs(self._settings.model, self._settings.effort, 16000),
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
        snippet=f"Rules: {'; '.join(f'{r.id}: {r.description}' for r in strategy.enabled_rules)}",
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
    for rule in strategy.enabled_rules:
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


def review_material(
    body: ReviewBody, strategy: StrategyConfig, risk_limits: list[str] | None = None
) -> str:
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
            "risk": body.risk_notes,
            "citable": {
                "rule_ids": [r.id for r in strategy.enabled_rules],
                "risk_limits": risk_limits or [],
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


async def decision_scorecard_lines(pool: asyncpg.Pool, notes: list[str]) -> list[str]:
    """The journal scorecard as plain lines. A failure adds a note and never stops the review."""
    try:
        # The worker has no signed-in user; the app has one owner, whose journal this reads.
        owner_id = await pool.fetchval(
            "select owner_id::text from public.decision_journal order by id limit 1"
        )
        if owner_id is None:
            return []
        card = await journal_scorecard(pool, owner_id)
    except Exception:
        logger.exception("decision scorecard failed")
        notes.append("The decision scorecard could not be computed.")
        return []
    lines: list[str] = []
    for section in card.sections:
        if section.status != "ok":
            lines.append(f"{section.title}: {section.note}")
            continue
        for row in section.rows:
            change = (
                f", median change {row.median_change:+.1%}" if row.median_change is not None else ""
            )
            share = f", {row.share:.0%}" if row.share is not None else ""
            lines.append(f"{section.title} / {row.label}: n={row.n}{change}{share}")
    return lines


def risk_section_lines(risk: "ExAnteRisk", validation: "ModelValidation | None") -> list[str]:
    """The model-based part of the review's risk section (pure): forecast against realised
    volatility, the largest risk contributors, risk by tier against its budgets, the measures
    that are not modelled yet and whether the model passed its own back-test."""
    lines: list[str] = []
    if risk.volatility_forecast is not None:
        limit = risk.limits.max_volatility
        tail = "" if limit is None else f", against a limit of {limit:.0%}"
        lines.append(
            f"Forecast volatility from today's holdings: {risk.volatility_forecast:.1%}{tail}."
        )
        if risk.realized_volatility is not None:
            lines.append(
                f"Realised volatility over the last year was {risk.realized_volatility:.1%}, "
                f"against the {risk.volatility_forecast:.1%} forecast."
            )
        top = [p for p in risk.positions if p.risk_share is not None][:3]
        if top:
            lines.append(
                "Largest risk contributors: "
                + "; ".join(
                    f"{p.ticker} {p.risk_share:.0%} of risk ({p.weight:.0%} of value)" for p in top
                )
                + "."
            )
        budgets = risk.limits.tier_risk_budgets or {}
        tiers = [
            f"{g.label} {g.risk_share:.0%}"
            + (f" (budget {budgets[g.label]:.0%})" if g.label in budgets else "")
            for g in risk.by_tier
            if g.label in budgets or g.risk_share >= 0.05
        ]
        if tiers:
            lines.append("Share of risk by tier: " + ", ".join(tiers) + ".")
        if risk.effective_bets is not None:
            lines.append(f"About {risk.effective_bets:.1f} independent bets.")
        lines += [w for w in risk.warnings if "understates" in w]
    lines.append(
        "Expected Shortfall and the probability of a drawdown are not modelled yet, so this "
        "section has no tail-loss figure."
    )
    if validation is not None and validation.available:
        lines.append(f"Model check: {validation.verdict}")
    elif validation is not None:
        lines.append(f"Model check not run: {validation.reason}.")
    return lines


async def open_risk_limit_keys(pool: asyncpg.Pool) -> list[str]:
    """Keys of the risk-limit findings open now; an action may cite them like a rule id."""
    rows = await pool.fetch(
        "select key from public.portfolio_breach where cleared_at is null and key like 'risk:%'"
        " order by key"
    )
    return [r["key"] for r in rows]


async def risk_note_lines(
    pool: asyncpg.Pool, notes: list[str], due: list[str] | None = None
) -> list[str]:
    """Forecast risk, flagged factor tilts, the hardest past episode and a risk line for each
    thesis due for review. A part that cannot be computed is left out and noted; it never stops
    the review."""
    from steward.portfolio.exante import ex_ante_risk
    from steward.portfolio.factors import factor_exposures
    from steward.portfolio.position_pack import position_risk_pack
    from steward.portfolio.replay import stress_replay
    from steward.portfolio.validation import model_validation

    lines: list[str] = []
    try:
        risk = await ex_ante_risk(pool)
        lines += risk_section_lines(risk, await model_validation(pool))
        for ticker in (due or [])[:MAX_PACKS]:
            pack = await position_risk_pack(pool, ticker)
            if pack.available and pack.summary:
                lines.append(f"Thesis review, {pack.summary}")
        factors = await factor_exposures(pool)
        lines += [t.note for t in factors.tilts if t.flagged and t.note]
        replay = await stress_replay(pool)
        worst = min(
            (e for e in replay.episodes if e.loss is not None),
            key=lambda e: e.loss or 0,
            default=None,
        )
        if worst is not None and worst.loss is not None:
            lines.append(
                f"Hardest past episode on today's holdings: {worst.label}, {worst.loss:+.1%}."
            )
    except Exception:
        logger.exception("risk notes failed")
        notes.append("Forecast risk, factor tilts and past episodes could not be computed.")
    return lines


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
    holding_periods: list[HoldingPeriodRow] = []
    try:
        # Never lets a performance error stop the review: the rows are added or the gap is noted.
        findings, lots = await performance_review(pool)
        rows += [
            ComplianceRow(
                id=f.id,
                kind="performance",
                description=f.description,
                status=f.status,
                detail=f.detail,
            )
            for f in findings
        ]
        holding_periods = [HoldingPeriodRow(**asdict(lot)) for lot in lots]
    except Exception:
        logger.exception("performance review rows failed")
        notes.append(
            "Performance checks (core versus stocks, holding periods) could not be computed."
        )
    kpis = await kpi_rows(pool, reg, theses)
    scorecard_lines = await decision_scorecard_lines(pool, notes)
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
        holding_periods=holding_periods,
        decision_scorecard=scorecard_lines,
        risk_notes=await risk_note_lines(pool, notes, due),
        sources=[],
    )
    usage: Usage | None = None
    if writer is None:
        notes.append("Actions not proposed: ANTHROPIC_API_KEY is not set.")
    else:
        limit_keys = await open_risk_limit_keys(pool)
        draft, usage, note = await writer.propose(
            review_material(body, strategy, limit_keys), today
        )
        if draft is None:
            notes.append(f"Actions not proposed: {note}.")
        else:
            body.actions, body.dropped_actions = keep_cited(
                draft,
                {r.id for r in strategy.enabled_rules} | set(limit_keys),
                {k.ref for k in kpis},
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
    if body.holding_periods:
        lines += ["", "## Holding periods", ""]
        lines += [
            f"- {h.ticker}: bought {h.bought_on}, passes the test on {h.passes_on} "
            f"({'passed' if h.passed else f'{h.months_remaining:.1f} months left'})"
            + (
                " - sell target reached: a full sale would give up the exemption"
                if h.forfeits_exemption_warning
                else ""
            )
            for h in body.holding_periods
        ]
    if body.decision_scorecard:
        lines += ["", "## Decision scorecard", ""]
        lines += [f"- {line}" for line in body.decision_scorecard]
    if body.risk_notes:
        lines += ["", "## Risk", ""] + [f"- {n}" for n in body.risk_notes]
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
        performance = await load_active(pool, "performance", PerformanceSettings)
        for v in (jobs, strategy, theses, performance):
            await ctx.record_config_version(v.id if v else None)
        # The sleeve gap, time test and alert severity come from these settings.
        step.detail["performance_config_version"] = performance.id if performance else "defaults"
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
    async with ctx.step("performance_alerts") as step:
        # Alerts follow the stored review so a failure here never costs the report.
        try:
            breaches = [
                PerformanceBreach(r.id, r.description, r.detail)
                for r in body.compliance
                if r.kind == "performance" and r.status == "breach"
            ]
            alerts = await alert_performance(
                pool,
                breaches,
                severity=(await load_latest_settings(pool)).alert_severity,
                now=deps.now,
                job_id=ctx.job_id,
                app_url=get_settings().app_url,
            )
            step.detail.update(breaches=len(breaches), alerts=alerts)
        except Exception:
            logger.exception("performance alerts failed")
            step.detail["error"] = "performance alerts could not be raised"
    return report_id


async def monthly_review_handler(ctx: JobContext) -> None:
    settings = get_settings()
    jobs = await load_active(ctx.pool, "jobs", JobsConfig)
    review = (jobs.body if jobs else JobsConfig()).review
    system = (await load_prompts(ctx.pool)).text("monthly_review", SYSTEM, review.model)
    try:
        anthropic_client = create_client(settings)
        llm = create_llm(settings, anthropic_client)
        writer: ReviewWriter | None = ClaudeReviewWriter(
            routing_client(anthropic_client, llm), review, system, llm=llm
        )
    except AINotConfigured:
        writer = None
    await run_review(ctx, ReviewDeps(writer=writer, now=dt.datetime.now(dt.UTC)))
