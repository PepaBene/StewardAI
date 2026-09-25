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


class FactCheck(BaseModel):
    check: str
    ok: bool
    detail: str = ""


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
    source_url: str
    evidence_url: str = Field(description="Signed URL of the archived copy, valid 10 minutes.")
    content_type: str
    fetched_at: dt.datetime
    sha256: str
    context: str | None = Field(description="The archived text around the snippet.")


def _fact(row: asyncpg.Record) -> FactOut:
    data = dict(row)
    checks = json.loads(data.pop("checks") or "[]")
    return FactOut(
        **data,
        checks=[FactCheck(**c) for c in checks],
        confidence=FACT_CONFIDENCE[row["status"]],
    )


_FACT_COLUMNS = (
    "id, metric, basis, period, fiscal_year, fiscal_period, period_end, value,"
    " value_as_written, value_units, scale, unit, currency, source_url, snippet, unit_snippet,"
    " evidence_sha256, status, status_reason, checks, restates_fact_id, extracted_at,"
    " reviewed_at"
)


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
        f"select {_FACT_COLUMNS} from public.fact where ticker = $1 order by id desc",  # noqa: S608
        ticker,
    )
    if instrument is None and thesis is None and profile is None and not fact_rows:
        return None

    holdings = await portfolio_holdings(pool)
    position = next((h for h in holdings.holdings if h.ticker == ticker), None)
    facts = [_fact(r) for r in fact_rows]
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

    kpis = []
    for kpi in thesis.kpis if thesis else []:
        rows = by_kpi.get(kpi.id, [])
        dated = [v for v in rows if v.fiscal_year is not None and v.fiscal_period is not None]
        dated.sort(
            key=lambda v: sort_key(v.fiscal_year or 0, v.fiscal_period or "FY"), reverse=True
        )
        undated = [v for v in rows if v.fiscal_year is None]
        # A newer "no data" verdict only counts when no period has been judged.
        history = dated or undated
        kpis.append(KpiStatus(kpi=kpi, current=history[0] if history else None, history=history))

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
