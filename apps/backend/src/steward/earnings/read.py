"""Read models for the holding page: position, thesis, KPI verdict history, facts."""

import datetime as dt
import json
from collections.abc import Mapping
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig, ThesisConfig, ThesisKPI
from steward.daily import DayCount, per_day, window_bounds, window_days
from steward.documents.fetch import Document
from steward.documents.text import contains
from steward.earnings.periods import sort_key
from steward.earnings.scores import (
    HISTORY_POINTS,
    THESIS_SCORE_METHOD,
    Judgement,
    ThesisState,
    VerdictPoint,
    day_end,
    portfolio_history,
    score_as_of,
    score_history,
    thesis_score,
    thesis_state,
    verdict_days,
    weighted_score,
)
from steward.earnings.source_tier import SourceTiers, load_source_tiers
from steward.portfolio.read import Freshness, Holding, JobSummary, portfolio_holdings

# The four labels of the Design and UI section.
Confidence = Literal["verified", "corroborated", "unconfirmed", "disputed"]
FactStatus = Literal["verified", "unverified", "rejected", "quarantined"]

FACT_CONFIDENCE: dict[str, Confidence] = {
    "verified": "verified",
    "unverified": "unconfirmed",
    "rejected": "disputed",
    "quarantined": "disputed",
}

# How a piece of evidence relates to the claim it's attached to — a different question
# from confidence (how sure we are). Derived from the same validator/reconciliation
# checks that already decide a fact's status, never a separately-modeled judgement.
EvidenceRelation = Literal["supports", "contradicts", "mentions"]


class FactCheck(BaseModel):
    check: str
    ok: bool
    detail: str = ""


def evidence_relation(status: FactStatus, checks: list[FactCheck]) -> EvidenceRelation:
    """Does this evidence support, contradict, or merely mention the claim it backs?

    A failed check (a bound the validator or reconciliation broke) means the source
    itself conflicts with the claim: contradicts. A verified fact with no failed check
    actively backs the value: supports. Anything still awaiting review (unverified) only
    mentions it, not yet corroborated enough to count as support.
    """
    if status == "rejected" or any(not c.ok for c in checks):
        return "contradicts"
    if status == "verified":
        return "supports"
    return "mentions"


class FactOut(BaseModel):
    id: int
    metric: str
    basis: Literal["reported", "adjusted"]
    period: str
    fiscal_year: int
    fiscal_period: str
    period_end: dt.date | None
    value: float = Field(description="As printed, in `scale`.")
    value_as_written: str
    value_units: float = Field(description="With the scale applied.")
    scale: Literal["units", "thousands", "millions", "billions"]
    unit: Literal["currency", "per_share", "percent", "count", "ratio"]
    currency: str | None
    source_url: str
    snippet: str
    unit_snippet: str | None
    evidence_sha256: str
    status: FactStatus
    status_reason: str | None
    checks: list[FactCheck]
    restates_fact_id: int | None
    extracted_at: dt.datetime
    reviewed_at: dt.datetime | None
    origin: Literal["extraction", "owner"] = Field(
        description="owner: a correction the owner entered; it wins over the extracted value."
    )
    confidence: Confidence
    check_score: float | None = Field(
        description="Passed checks / all checks stored with the figure (0..1): the share of "
        "the validator's checks it passed (`checks`). Null when no check ran."
    )
    source_tier: int | None = Field(
        description="Trust tier (1 best, 3 weakest) of the host the figure was read from: 1 "
        "for the holding's own IR or results domains, else the news_sources config's domain "
        "tier; one tier lower while the owner's feedback has the host's trust below the "
        "verification config's demote_below. Null when the host has no configured tier."
    )


def check_score(checks: list[FactCheck]) -> float | None:
    """Passed checks / all checks; None when there are none."""
    return sum(c.ok for c in checks) / len(checks) if checks else None


class VerdictOut(BaseModel):
    id: int
    kpi_id: str
    period: str | None
    fiscal_year: int | None
    fiscal_period: str | None
    status: Literal["pass", "breach", "unknown"]
    observed_value: float | None = Field(description="In the KPI's unit (e.g. 9.63 for '%').")
    operator: str
    threshold: float
    threshold_unit: str
    rationale: str
    fact_ids: list[int]
    config_version_id: int
    created_at: dt.datetime
    confidence: Confidence
    stale: bool = Field(
        description="Judged under an older theses config than the active one; re-run verdicts."
    )


class KpiStatus(BaseModel):
    kpi: ThesisKPI
    current: VerdictOut | None = Field(description="Latest period's verdict.")
    history: list[VerdictOut] = Field(description="Latest verdict per period, newest first.")


class InstrumentInfo(BaseModel):
    name: str | None
    isin: str | None
    currency: str | None
    cik: str | None
    next_report_date: dt.date | None
    report_date_source: str | None
    last_report_date: dt.date | None
    report_date_checked_at: dt.datetime | None


class HoldingDetail(BaseModel):
    ticker: str
    instrument: InstrumentInfo | None
    position: Holding | None = Field(description="Null when the latest sync has no position.")
    base_currency: str | None
    freshness: Freshness | None
    thesis: ThesisConfig | None
    theses_config_version_id: int | None
    profile: HoldingProfile | None
    kpis: list[KpiStatus]
    facts: list[FactOut] = Field(description="Every fact, newest period first.")
    last_earnings_check: JobSummary | None
    last_verdict_run: JobSummary | None


class EvidenceLink(BaseModel):
    fact_id: int
    ticker: str
    metric: str
    period: str
    value_as_written: str
    snippet: str
    unit_snippet: str | None
    confidence: Confidence
    relation: EvidenceRelation = Field(
        description="Whether this source supports, contradicts, or merely mentions the claim —"
        " distinct from confidence, which is how sure we are the value itself is right."
    )
    origin: Literal["extraction", "owner"] = Field(
        description="Provenance, a different question from confidence: read straight from the"
        " document, or a correction the owner entered."
    )
    source_url: str
    evidence_url: str = Field(description="Signed URL of the archived copy, valid 10 minutes.")
    content_type: str
    fetched_at: dt.datetime
    sha256: str
    context: str | None = Field(description="The archived text around the snippet.")


def fact_out(row: asyncpg.Record, ticker: str, tiers: SourceTiers | None) -> FactOut:
    """A fact row as served; `tiers` resolves `source_tier` (None: unknown)."""
    data = {k: row[k] for k in FACT_COLUMN_NAMES}
    checks = [FactCheck(**c) for c in json.loads(data.pop("checks") or "[]")]
    return FactOut(
        **data,
        checks=checks,
        confidence=FACT_CONFIDENCE[row["status"]],
        check_score=check_score(checks),
        source_tier=tiers.tier(ticker, row["source_url"]) if tiers else None,
    )


FACT_COLUMN_NAMES = (
    "id", "metric", "basis", "period", "fiscal_year", "fiscal_period", "period_end", "value",
    "value_as_written", "value_units", "scale", "unit", "currency", "source_url", "snippet",
    "unit_snippet", "evidence_sha256", "status", "status_reason", "checks", "restates_fact_id",
    "extracted_at", "reviewed_at", "origin",
)  # fmt: skip


def fact_columns(alias: str = "") -> str:
    """The columns `fact_out` reads, optionally qualified with a table alias."""
    prefix = f"{alias}." if alias else ""
    return ", ".join(prefix + c for c in FACT_COLUMN_NAMES)


FACT_COLUMNS = fact_columns()


async def _latest_job(pool: asyncpg.Pool, job_type: str, ticker: str | None) -> JobSummary | None:
    row = await pool.fetchrow(
        "select id, job_type, trigger, status, error, created_at, started_at, finished_at"
        " from public.job where job_type = $1 and ($2::text is null or params ->> 'ticker' = $2"
        " or params ->> 'ticker' is null) order by id desc limit 1",
        job_type,
        ticker,
    )
    return JobSummary(**dict(row)) if row else None


def verdict_confidence(
    status: str, fact_ids: list[int], fact_status: Mapping[int, str]
) -> Confidence:
    if status == "unknown" or not fact_ids:
        return "unconfirmed"
    labels = {FACT_CONFIDENCE.get(fact_status.get(i, "rejected"), "disputed") for i in fact_ids}
    order: tuple[Confidence, ...] = ("disputed", "unconfirmed", "corroborated")
    for label in order:
        if label in labels:
            return label
    return "verified"


async def holding_detail(pool: asyncpg.Pool, ticker: str) -> HoldingDetail | None:
    instrument = await pool.fetchrow(
        "select name, isin, currency, cik, next_report_date, report_date_source,"
        " last_report_date, report_date_checked_at from public.instrument"
        " where t212_ticker = $1",
        ticker,
    )
    theses = await load_active(pool, "theses", ThesesConfig)
    thesis = next((t for t in theses.body.theses if t.ticker == ticker), None) if theses else None
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profile = profiles.body.for_ticker(ticker) if profiles else None
    fact_rows = await pool.fetch(
        f"select {FACT_COLUMNS} from public.fact where ticker = $1 order by id desc",  # noqa: S608
        ticker,
    )
    if instrument is None and thesis is None and profile is None and not fact_rows:
        return None

    holdings = await portfolio_holdings(pool)
    position = next((h for h in holdings.holdings if h.ticker == ticker), None)
    tiers = await load_source_tiers(pool)
    facts = [fact_out(r, ticker, tiers) for r in fact_rows]
    facts.sort(key=lambda f: (sort_key(f.fiscal_year, f.fiscal_period), f.id), reverse=True)
    fact_status = {f.id: f.status for f in facts}

    verdict_rows = await pool.fetch(
        "select distinct on (kpi_id, fiscal_year, fiscal_period) id, kpi_id, period,"
        " fiscal_year, fiscal_period, status, observed_value, operator, threshold,"
        " threshold_unit, rationale, fact_ids, config_version_id, created_at"
        " from public.verdict where ticker = $1"
        " order by kpi_id, fiscal_year, fiscal_period, id desc",
        ticker,
    )
    active_version = theses.id if theses else None
    by_kpi: dict[str, list[VerdictOut]] = {}
    for r in verdict_rows:
        data: dict[str, Any] = dict(r)
        by_kpi.setdefault(r["kpi_id"], []).append(
            VerdictOut(
                **data,
                confidence=verdict_confidence(r["status"], r["fact_ids"], fact_status),
                stale=r["config_version_id"] != active_version,
            )
        )

    kpis = [kpi_status(kpi, by_kpi.get(kpi.id, [])) for kpi in (thesis.kpis if thesis else [])]

    return HoldingDetail(
        ticker=ticker,
        instrument=InstrumentInfo(**dict(instrument)) if instrument else None,
        position=position,
        base_currency=holdings.base_currency,
        freshness=holdings.freshness,
        thesis=thesis,
        theses_config_version_id=active_version,
        profile=profile,
        kpis=kpis,
        facts=facts,
        last_earnings_check=await _latest_job(pool, "earnings_check", ticker),
        last_verdict_run=await _latest_job(pool, "thesis_verdicts", ticker),
    )


def kpi_status(kpi: ThesisKPI, rows: list[VerdictOut]) -> KpiStatus:
    """The KPI's current verdict (latest judged period) and its history, newest first."""
    dated = [v for v in rows if v.fiscal_year is not None and v.fiscal_period is not None]
    dated.sort(key=lambda v: sort_key(v.fiscal_year or 0, v.fiscal_period or "FY"), reverse=True)
    undated = [v for v in rows if v.fiscal_year is None]
    # A newer "no data" verdict only counts when no period has been judged.
    history = dated or undated
    return KpiStatus(kpi=kpi, current=history[0] if history else None, history=history)


async def verdicts_by_kpi(
    pool: asyncpg.Pool, tickers: list[str], active_version: int | None
) -> dict[tuple[str, str], list[VerdictOut]]:
    """Latest verdict per (ticker, KPI, period), with its confidence from its facts."""
    rows = await pool.fetch(
        "select distinct on (ticker, kpi_id, fiscal_year, fiscal_period) id, ticker, kpi_id,"
        " period, fiscal_year, fiscal_period, status, observed_value, operator, threshold,"
        " threshold_unit, rationale, fact_ids, config_version_id, created_at"
        " from public.verdict where ticker = any($1::text[])"
        " order by ticker, kpi_id, fiscal_year, fiscal_period, id desc",
        tickers,
    )
    fact_ids = sorted({i for r in rows for i in r["fact_ids"]})
    fact_status = {
        r["id"]: r["status"]
        for r in await pool.fetch(
            "select id, status from public.fact where id = any($1::bigint[])", fact_ids
        )
    }
    out: dict[tuple[str, str], list[VerdictOut]] = {}
    for r in rows:
        data: dict[str, Any] = dict(r)
        data.pop("ticker")
        out.setdefault((r["ticker"], r["kpi_id"]), []).append(
            VerdictOut(
                **data,
                confidence=verdict_confidence(r["status"], r["fact_ids"], fact_status),
                stale=r["config_version_id"] != active_version,
            )
        )
    return out


class ScorePoint(BaseModel):
    date: dt.date
    score: int


class BoardKpi(BaseModel):
    kpi: ThesisKPI
    status: Literal["pass", "breach", "unknown"] = Field(
        description="The current verdict's status; unknown when there is none yet."
    )
    verdict: VerdictOut | None = Field(description="Null: never judged (shown as unknown).")


class BoardRow(BaseModel):
    ticker: str
    name: str | None
    why_owned: str
    review_date: dt.date | None
    review_overdue: bool
    held: bool = Field(description="In the latest portfolio snapshot.")
    weight: float | None
    overall: Literal["breach", "unknown", "pass", "no_kpis"] = Field(
        description="breach if any KPI breaches, else unknown if any is unknown, else pass."
    )
    counts: dict[str, int]
    kpis: list[BoardKpi]
    open_review_items: int = Field(description="Held or quarantined facts for this holding.")
    score: int | None = Field(
        description="0-100: 100 x mean s_k over KPIs with a verdict, rounded half up; s_k = 1 "
        "pass, 0.5 unknown or stale, 0 breach. Null when no KPI has been judged. See "
        "score_method."
    )
    score_history: list[ScorePoint] = Field(
        description="The score as of the end of each UTC day a verdict for one of this "
        "holding's KPIs was recorded (latest 12 such days, oldest first)."
    )
    state: ThesisState = Field(
        description="unjudged: no score. Else by breaching KPIs (current verdict breach): "
        "intact none, watch one, at_risk two or more."
    )


class VerdictActivity(BaseModel):
    count: int = Field(
        description="Verdict records created in the window (a verdict is "
        "recorded only when its outcome, figures or config changed)."
    )
    fact_count: int = Field(description="Distinct facts those verdicts cite.")
    per_day: list[DayCount] = Field(description="Every UTC day of the window, oldest first.")


class ThesisBoard(BaseModel):
    theses_config_version_id: int | None
    as_of: dt.datetime | None = Field(description="Newest verdict shown.")
    rows: list[BoardRow]
    score_method: str = Field(
        default=THESIS_SCORE_METHOD, description="How score and portfolio_score are computed."
    )
    portfolio_score: int | None = Field(
        default=None,
        description="Mean of held rows' scores weighted by current position weight, over held "
        "rows with a score and a weight. Null when there is none.",
    )
    portfolio_score_history: list[ScorePoint] = Field(
        default_factory=list,
        description="portfolio_score recomputed as of the end of each UTC day any held row had "
        "a verdict recorded (latest 12 such days, oldest first), with today's weights.",
    )
    verdicts_30d: VerdictActivity = Field(
        description="Verdict records over the last 30 UTC days, today included."
    )


async def verdict_activity(pool: asyncpg.Pool, now: dt.datetime, days: int = 30) -> VerdictActivity:
    window = window_days(now, days)
    start, end = window_bounds(window)
    rows = await pool.fetch(
        "select (created_at at time zone 'UTC')::date as day, count(*) as n"
        " from public.verdict where created_at >= $1 and created_at < $2 group by 1",
        start,
        end,
    )
    facts = await pool.fetchval(
        "select count(distinct f) from public.verdict v, unnest(v.fact_ids) f"
        " where v.created_at >= $1 and v.created_at < $2",
        start,
        end,
    )
    by_day = {r["day"]: r["n"] for r in rows}
    return VerdictActivity(
        count=sum(by_day.values()), fact_count=facts or 0, per_day=per_day(window, by_day)
    )


async def _verdict_points(pool: asyncpg.Pool, tickers: list[str]) -> dict[str, list[VerdictPoint]]:
    out: dict[str, list[VerdictPoint]] = {}
    for r in await pool.fetch(
        "select id, ticker, kpi_id, fiscal_year, fiscal_period, status, config_version_id,"
        " created_at from public.verdict where ticker = any($1::text[]) order by id",
        tickers,
    ):
        data = dict(r)
        out.setdefault(data.pop("ticker"), []).append(VerdictPoint(**data))
    return out


async def thesis_board(
    pool: asyncpg.Pool, today: dt.date, now: dt.datetime | None = None
) -> ThesisBoard:
    """The board, with each holding's thesis score (see steward.earnings.scores)."""
    now = now or dt.datetime.now(dt.UTC)
    theses = await load_active(pool, "theses", ThesesConfig)
    if theses is None:
        return ThesisBoard(
            theses_config_version_id=None,
            as_of=None,
            rows=[],
            verdicts_30d=await verdict_activity(pool, now),
        )
    tickers = [t.ticker for t in theses.body.theses]
    verdicts = await verdicts_by_kpi(pool, tickers, theses.id)
    points = await _verdict_points(pool, tickers)
    versions = [
        (r["id"], r["created_at"])
        for r in await pool.fetch(
            "select id, created_at from public.config_version where domain = 'theses'"
        )
    ]
    names = {
        r["t212_ticker"]: r["name"]
        for r in await pool.fetch(
            "select t212_ticker, name from public.instrument where t212_ticker = any($1::text[])",
            tickers,
        )
    }
    held = {h.ticker: h for h in (await portfolio_holdings(pool)).holdings}
    open_items = {
        r["ticker"]: r["n"]
        for r in await pool.fetch(
            "select ticker, count(*) as n from public.fact where status in ('unverified',"
            " 'quarantined') group by ticker"
        )
    }
    rows = []
    newest: dt.datetime | None = None
    for thesis in theses.body.theses:
        kpis = []
        counts = {"pass": 0, "breach": 0, "unknown": 0}
        judgements: list[Judgement | None] = []
        for kpi in thesis.kpis:
            current = kpi_status(kpi, verdicts.get((thesis.ticker, kpi.id), [])).current
            status: Literal["pass", "breach", "unknown"] = current.status if current else "unknown"
            counts[status] += 1
            kpis.append(BoardKpi(kpi=kpi, status=status, verdict=current))
            judgements.append(Judgement(current.status, current.stale) if current else None)
            if current is not None and (newest is None or current.created_at > newest):
                newest = current.created_at
        overall: Literal["breach", "unknown", "pass", "no_kpis"] = (
            "no_kpis"
            if not kpis
            else "breach"
            if counts["breach"]
            else "unknown"
            if counts["unknown"]
            else "pass"
        )
        position = held.get(thesis.ticker)
        score = thesis_score(judgements)
        kpi_ids = [k.id for k in thesis.kpis]
        rows_points = points.get(thesis.ticker, [])
        history = score_history(
            rows_points, kpi_ids, versions, verdict_days(rows_points, kpi_ids)[-HISTORY_POINTS:]
        )
        rows.append(
            BoardRow(
                ticker=thesis.ticker,
                name=names.get(thesis.ticker),
                why_owned=thesis.why_owned,
                review_date=thesis.review_date,
                review_overdue=thesis.review_date is not None and thesis.review_date < today,
                held=position is not None,
                weight=position.weight if position else None,
                overall=overall,
                counts=counts,
                kpis=kpis,
                open_review_items=open_items.get(thesis.ticker, 0),
                score=score,
                score_history=[ScorePoint(date=d, score=v) for d, v in history],
                state=thesis_state(score, counts["breach"]),
            )
        )
    order = {"breach": 0, "unknown": 1, "no_kpis": 2, "pass": 3}
    rows.sort(key=lambda r: (order[r.overall], -(r.weight or 0), r.ticker))

    held_rows = [r for r in rows if r.held]
    kpi_ids_of = {t.ticker: [k.id for k in t.kpis] for t in theses.body.theses}
    days = sorted(
        {d for r in held_rows for d in verdict_days(points.get(r.ticker, []), kpi_ids_of[r.ticker])}
    )[-HISTORY_POINTS:]
    scores_at = {
        r.ticker: {
            d: score_as_of(points.get(r.ticker, []), kpi_ids_of[r.ticker], versions, day_end(d))
            for d in days
        }
        for r in held_rows
    }
    weights = {r.ticker: r.weight for r in held_rows}
    return ThesisBoard(
        theses_config_version_id=theses.id,
        as_of=newest,
        rows=rows,
        portfolio_score=weighted_score((r.score, r.weight) for r in held_rows),
        portfolio_score_history=[
            ScorePoint(date=d, score=v) for d, v in portfolio_history(scores_at, weights, days)
        ],
        verdicts_30d=await verdict_activity(pool, now),
    )


def snippet_context(text: str, snippet: str, lines_around: int = 4) -> str | None:
    lines = text.splitlines()
    for i in range(len(lines)):
        window = " ".join(lines[i : i + 3])
        if contains(window, snippet):
            start, end = max(0, i - lines_around), min(len(lines), i + 3 + lines_around)
            return "\n".join(lines[start:end])
    return None


def document_text(data: bytes, content_type: str, url: str) -> str:
    return Document(url=url, content_type=content_type, data=data).text()


class FactIndexEntry(BaseModel):
    """One (ticker, metric): the newest fact for it, so a search can resolve a typed metric
    straight to its evidence page rather than only to the holding page."""

    ticker: str
    name: str | None
    metric: str
    fact_id: int
    period: str


async def fact_index(pool: asyncpg.Pool) -> list[FactIndexEntry]:
    """The latest fact per (ticker, metric) across every holding — deliberately small (no
    snippet, no checks, no evidence url) since this only feeds a search index, never a
    claim surface. Excludes rejected facts: a search shouldn't resolve to a figure the
    owner or the validator has already thrown out."""
    rows = await pool.fetch(
        "select distinct on (f.ticker, f.metric) f.ticker, i.name, f.metric, f.id, f.period"
        " from public.fact f left join public.instrument i on i.t212_ticker = f.ticker"
        " where f.status != 'rejected'"
        " order by f.ticker, f.metric, f.id desc"
    )
    return [
        FactIndexEntry(
            ticker=r["ticker"],
            name=r["name"],
            metric=r["metric"],
            fact_id=r["id"],
            period=r["period"],
        )
        for r in rows
    ]
