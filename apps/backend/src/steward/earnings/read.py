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
from steward.documents.fetch import Document
from steward.documents.text import contains
from steward.earnings.periods import sort_key
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


def fact_out(row: asyncpg.Record) -> FactOut:
    data = {k: row[k] for k in FACT_COLUMN_NAMES}
    checks = json.loads(data.pop("checks") or "[]")
    return FactOut(
        **data,
        checks=[FactCheck(**c) for c in checks],
        confidence=FACT_CONFIDENCE[row["status"]],
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
    facts = [fact_out(r) for r in fact_rows]
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


class ThesisBoard(BaseModel):
    theses_config_version_id: int | None
    as_of: dt.datetime | None = Field(description="Newest verdict shown.")
    rows: list[BoardRow]


async def thesis_board(pool: asyncpg.Pool, today: dt.date) -> ThesisBoard:
    theses = await load_active(pool, "theses", ThesesConfig)
    if theses is None:
        return ThesisBoard(theses_config_version_id=None, as_of=None, rows=[])
    tickers = [t.ticker for t in theses.body.theses]
    verdicts = await verdicts_by_kpi(pool, tickers, theses.id)
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
        for kpi in thesis.kpis:
            current = kpi_status(kpi, verdicts.get((thesis.ticker, kpi.id), [])).current
            status: Literal["pass", "breach", "unknown"] = current.status if current else "unknown"
            counts[status] += 1
            kpis.append(BoardKpi(kpi=kpi, status=status, verdict=current))
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
            )
        )
    order = {"breach": 0, "unknown": 1, "no_kpis": 2, "pass": 3}
    rows.sort(key=lambda r: (order[r.overall], -(r.weight or 0), r.ticker))
    return ThesisBoard(theses_config_version_id=theses.id, as_of=newest, rows=rows)


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
