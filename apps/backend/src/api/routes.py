"""Authenticated API routes: portfolio reads, holdings and evidence, on-demand jobs,
versioned config."""

import datetime as dt
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from steward.config import (
    BudgetConfig,
    HoldingProfilesConfig,
    JobsConfig,
    StrategyConfig,
    ThesesConfig,
    VerificationConfig,
)
from steward.config.common import Ticker
from steward.config.store import StoredConfig, load_active, save_config
from steward.earnings.read import (
    FACT_CONFIDENCE,
    EvidenceLink,
    HoldingDetail,
    document_text,
    holding_detail,
    snippet_context,
)
from steward.jobs import queue
from steward.jobs.runner import create_job
from steward.portfolio.read import (
    HoldingsResponse,
    JobSummary,
    PortfolioOverview,
    portfolio_holdings,
    portfolio_overview,
)
from steward.portfolio.transactions import (
    TransactionFilters,
    TransactionsPage,
    TransactionType,
    decode_cursor,
    list_transactions,
)
from steward.settings import get_settings
from steward.storage import EvidenceStore, StorageError, SupabaseStorage

router = APIRouter()


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


@router.get("/portfolio/overview", tags=["portfolio"])
async def get_portfolio_overview(pool: Pool, _owner: Owner) -> PortfolioOverview:
    """Totals, allocation, drift against the active strategy, and freshness, from the
    latest snapshot. Returns empty sections (not an error) before the first sync."""
    return await portfolio_overview(pool)


@router.get("/portfolio/holdings", tags=["portfolio"])
async def get_portfolio_holdings(pool: Pool, _owner: Owner) -> HoldingsResponse:
    """Every open position of the latest snapshot, largest first."""
    return await portfolio_holdings(pool)


@router.get("/portfolio/transactions", tags=["portfolio"])
async def get_portfolio_transactions(
    pool: Pool,
    _owner: Owner,
    type: Annotated[list[TransactionType] | None, Query(description="Any of these.")] = None,
    ticker: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = None,
    start: Annotated[dt.date | None, Query(alias="from", description="On or after.")] = None,
    end: Annotated[dt.date | None, Query(alias="to", description="On or before.")] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TransactionsPage:
    """Orders, dividends and cash movements, newest first, with totals per type and
    currency over every row matching the filters (dates are UTC days, both inclusive)."""
    try:
        decoded = decode_cursor(cursor) if cursor else None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Malformed cursor") from exc
    return await list_transactions(
        pool,
        TransactionFilters(
            types=list(type or []),
            ticker=ticker,
            start=dt.datetime.combine(start, dt.time(0), tzinfo=dt.UTC) if start else None,
            end=(
                dt.datetime.combine(end + dt.timedelta(days=1), dt.time(0), tzinfo=dt.UTC)
                if end
                else None
            ),
            cursor=decoded,
            limit=limit,
        ),
    )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


async def _enqueue(
    pool: Pool, job_type: str, owner_id: str, params: dict[str, Any] | None = None
) -> JobSummary:
    async with pool.acquire() as conn, conn.transaction():
        job_id = await create_job(
            conn, job_type, "manual", requested_by=owner_id, params=params or {}
        )
        await queue.enqueue(conn, job_id, job_type)
        row = await conn.fetchrow(
            "select id, job_type, trigger, status, error, created_at, started_at, finished_at"
            " from public.job where id = $1",
            job_id,
        )
    return JobSummary(**dict(row))


@router.post("/jobs/portfolio-sync", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_portfolio_sync(pool: Pool, owner: Owner) -> JobSummary:
    """Queue a portfolio sync now. The worker runs it; a run already in progress finishes
    first (the job waits for it), so two syncs never write at the same time."""
    return await _enqueue(pool, "portfolio_sync", owner.user_id)


class EarningsCheckRequest(BaseModel):
    ticker: Ticker
    report_date: dt.date | None = Field(
        default=None,
        description="Report date to look for. Default: the instrument's report date if it "
        "has passed, else today.",
    )
    url: str | None = Field(
        default=None,
        pattern=r"^https?://",
        max_length=2000,
        description="Use this document instead of searching (a press release or report).",
    )


@router.post("/jobs/earnings-check", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_earnings_check(body: EarningsCheckRequest, pool: Pool, owner: Owner) -> JobSummary:
    """Queue an earnings thesis check for one holding: find the release, extract and
    validate its figures, then judge the thesis KPIs."""
    return await _enqueue(
        pool, "earnings_check", owner.user_id, body.model_dump(mode="json", exclude_none=True)
    )


class ThesisVerdictsRequest(BaseModel):
    ticker: Ticker | None = Field(default=None, description="One holding, or all when null.")


@router.post("/jobs/thesis-verdicts", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_thesis_verdicts(body: ThesisVerdictsRequest, pool: Pool, owner: Owner) -> JobSummary:
    """Re-judge thesis KPIs from the stored facts with the active theses config. No
    fetching, no model calls: use it after editing a threshold."""
    return await _enqueue(
        pool, "thesis_verdicts", owner.user_id, body.model_dump(mode="json", exclude_none=True)
    )


@router.post("/jobs/earnings-calendar", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_earnings_calendar(pool: Pool, owner: Owner) -> JobSummary:
    """Refresh every holding's next report date now."""
    return await _enqueue(pool, "earnings_calendar", owner.user_id)


# ---------------------------------------------------------------------------
# Holdings, facts and evidence
# ---------------------------------------------------------------------------

TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


async def get_storage() -> AsyncIterator[EvidenceStore]:
    settings = get_settings()
    if settings.supabase_url is None or settings.supabase_secret_key is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Evidence storage is not configured (SUPABASE_URL, SUPABASE_SECRET_KEY)",
        )
    async with httpx.AsyncClient(timeout=30.0) as http:
        yield SupabaseStorage(
            http, settings.supabase_url, settings.supabase_secret_key.get_secret_value()
        )


Storage = Annotated[EvidenceStore, Depends(get_storage)]


@router.get("/holdings/{ticker}", tags=["holdings"])
async def get_holding(ticker: TickerPath, pool: Pool, _owner: Owner) -> HoldingDetail:
    """One holding: position, thesis, KPI verdicts with their history, and every extracted
    fact with its source."""
    detail = await holding_detail(pool, ticker)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nothing is known about {ticker}")
    return detail


@router.get("/facts/{fact_id}/evidence", tags=["holdings"])
async def get_fact_evidence(
    fact_id: int, pool: Pool, storage: Storage, _owner: Owner
) -> EvidenceLink:
    """The archived copy of the page a fact was read from: a short-lived signed URL and
    the text around the fact's snippet."""
    row = await pool.fetchrow(
        "select f.id, f.ticker, f.metric, f.period, f.value_as_written, f.snippet,"
        " f.unit_snippet, f.status, e.source_url, e.storage_path, e.content_type,"
        " e.fetched_at, e.sha256 from public.fact f"
        " join public.evidence e on e.sha256 = f.evidence_sha256 where f.id = $1",
        fact_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No fact {fact_id}")
    try:
        url = await storage.signed_url(row["storage_path"], 600)
        data = await storage.get(row["storage_path"])
    except StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    context = snippet_context(
        document_text(data, row["content_type"], row["source_url"]), row["snippet"]
    )
    return EvidenceLink(
        fact_id=row["id"],
        ticker=row["ticker"],
        metric=row["metric"],
        period=row["period"],
        value_as_written=row["value_as_written"],
        snippet=row["snippet"],
        unit_snippet=row["unit_snippet"],
        confidence=FACT_CONFIDENCE[row["status"]],
        source_url=row["source_url"],
        evidence_url=url,
        content_type=row["content_type"],
        fetched_at=row["fetched_at"],
        sha256=row["sha256"],
        context=context,
    )


class FactReview(BaseModel):
    action: Literal["verify", "reject"]
    note: str | None = Field(default=None, max_length=500)


class FactReviewResult(BaseModel):
    fact_id: int
    status: Literal["verified", "rejected"]
    verdict_job: JobSummary


@router.post("/facts/{fact_id}/review", tags=["holdings"])
async def review_fact(fact_id: int, body: FactReview, pool: Pool, owner: Owner) -> FactReviewResult:
    """Release or reject a fact the validator held for review (sanity bound, restatement,
    currency). Verdicts for the holding are re-run afterwards."""
    new_status: Literal["verified", "rejected"] = (
        "verified" if body.action == "verify" else "rejected"
    )
    reason = f"owner {body.action}" + (f": {body.note}" if body.note else "")
    ticker = await pool.fetchval(
        "update public.fact set status = $2, status_reason = $3, reviewed_at = now(),"
        " reviewed_by = $4::uuid where id = $1 and status = 'unverified' returning ticker",
        fact_id,
        new_status,
        reason,
        owner.user_id,
    )
    if ticker is None:
        exists = await pool.fetchval("select status from public.fact where id = $1", fact_id)
        if exists is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No fact {fact_id}")
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Fact {fact_id} is {exists}; only held facts are reviewed"
        )
    job = await _enqueue(pool, "thesis_verdicts", owner.user_id, {"ticker": ticker})
    return FactReviewResult(fact_id=fact_id, status=new_status, verdict_job=job)


# ---------------------------------------------------------------------------
# Config: GET the active version, PUT a new one. Every save is a new immutable version.
# ---------------------------------------------------------------------------


class StrategyConfigVersion(BaseModel):
    id: int
    created_at: dt.datetime
    note: str | None
    body: StrategyConfig


class ThesesConfigVersion(BaseModel):
    id: int
    created_at: dt.datetime
    note: str | None
    body: ThesesConfig


class JobsConfigVersion(BaseModel):
    id: int
    created_at: dt.datetime
    note: str | None
    body: JobsConfig


class HoldingProfilesConfigVersion(BaseModel):
    id: int
    created_at: dt.datetime
    note: str | None
    body: HoldingProfilesConfig


class BudgetConfigVersion(BaseModel):
    id: int
    created_at: dt.datetime
    note: str | None
    body: BudgetConfig


class VerificationConfigVersion(BaseModel):
    id: int
    created_at: dt.datetime
    note: str | None
    body: VerificationConfig


NoteParam = Annotated[str | None, Query(max_length=500, description="Why this change was made.")]


def _not_found(domain: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"No {domain} config saved yet")


def _as_dict[M: BaseModel](stored: StoredConfig[M]) -> dict[str, object]:
    return {
        "id": stored.id,
        "created_at": stored.created_at,
        "note": stored.note,
        "body": stored.body,
    }


@router.get("/config/strategy", tags=["config"])
async def get_strategy_config(pool: Pool, _owner: Owner) -> StrategyConfigVersion:
    stored = await load_active(pool, "strategy", StrategyConfig)
    if stored is None:
        raise _not_found("strategy")
    return StrategyConfigVersion(**_as_dict(stored))


@router.put("/config/strategy", tags=["config"])
async def save_strategy_config(
    body: StrategyConfig, pool: Pool, owner: Owner, note: NoteParam = None
) -> StrategyConfigVersion:
    """Validate and save as the new active strategy. Drift uses it immediately."""
    async with pool.acquire() as conn:
        stored = await save_config(conn, "strategy", body, created_by=owner.user_id, note=note)
    return StrategyConfigVersion(**_as_dict(stored))


@router.get("/config/theses", tags=["config"])
async def get_theses_config(pool: Pool, _owner: Owner) -> ThesesConfigVersion:
    stored = await load_active(pool, "theses", ThesesConfig)
    if stored is None:
        raise _not_found("theses")
    return ThesesConfigVersion(**_as_dict(stored))


@router.put("/config/theses", tags=["config"])
async def save_theses_config(
    body: ThesesConfig, pool: Pool, owner: Owner, note: NoteParam = None
) -> ThesesConfigVersion:
    async with pool.acquire() as conn:
        stored = await save_config(conn, "theses", body, created_by=owner.user_id, note=note)
    return ThesesConfigVersion(**_as_dict(stored))


@router.get("/config/jobs", tags=["config"])
async def get_jobs_config(pool: Pool, _owner: Owner) -> JobsConfigVersion:
    """The saved job schedules. 404 means the worker runs on the defaults."""
    stored = await load_active(pool, "jobs", JobsConfig)
    if stored is None:
        raise _not_found("jobs")
    return JobsConfigVersion(**_as_dict(stored))


@router.put("/config/jobs", tags=["config"])
async def save_jobs_config(
    body: JobsConfig, pool: Pool, owner: Owner, note: NoteParam = None
) -> JobsConfigVersion:
    """Save job schedules. The worker picks them up within a few minutes, no restart."""
    async with pool.acquire() as conn:
        stored = await save_config(conn, "jobs", body, created_by=owner.user_id, note=note)
    return JobsConfigVersion(**_as_dict(stored))


@router.post("/config/jobs/validate", tags=["config"])
async def validate_jobs(body: JobsConfig, _owner: Owner) -> JobsConfig:
    """Validate job schedules without saving. 422 lists every error."""
    return body


@router.get("/config/holding-profiles", tags=["config"])
async def get_holding_profiles_config(pool: Pool, _owner: Owner) -> HoldingProfilesConfigVersion:
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    if stored is None:
        raise _not_found("holding profiles")
    return HoldingProfilesConfigVersion(**_as_dict(stored))


@router.put("/config/holding-profiles", tags=["config"])
async def save_holding_profiles_config(
    body: HoldingProfilesConfig, pool: Pool, owner: Owner, note: NoteParam = None
) -> HoldingProfilesConfigVersion:
    """Save holding profiles. The next earnings check and calendar refresh use them."""
    async with pool.acquire() as conn:
        stored = await save_config(
            conn, "holding_profiles", body, created_by=owner.user_id, note=note
        )
    return HoldingProfilesConfigVersion(**_as_dict(stored))


@router.post("/config/holding-profiles/validate", tags=["config"])
async def validate_holding_profiles(
    body: HoldingProfilesConfig, _owner: Owner
) -> HoldingProfilesConfig:
    """Validate holding profiles without saving. 422 lists every error."""
    return body


@router.get("/config/verification", tags=["config"])
async def get_verification_config(pool: Pool, _owner: Owner) -> VerificationConfigVersion:
    """The saved validator thresholds. 404 means the defaults apply."""
    stored = await load_active(pool, "verification", VerificationConfig)
    if stored is None:
        raise _not_found("verification")
    return VerificationConfigVersion(**_as_dict(stored))


@router.put("/config/verification", tags=["config"])
async def save_verification_config(
    body: VerificationConfig, pool: Pool, owner: Owner, note: NoteParam = None
) -> VerificationConfigVersion:
    async with pool.acquire() as conn:
        stored = await save_config(conn, "verification", body, created_by=owner.user_id, note=note)
    return VerificationConfigVersion(**_as_dict(stored))


@router.post("/config/verification/validate", tags=["config"])
async def validate_verification(body: VerificationConfig, _owner: Owner) -> VerificationConfig:
    return body


@router.get("/config/budget", tags=["config"])
async def get_budget_config(pool: Pool, _owner: Owner) -> BudgetConfigVersion:
    """The saved price table. 404 means the default prices apply."""
    stored = await load_active(pool, "budget", BudgetConfig)
    if stored is None:
        raise _not_found("budget")
    return BudgetConfigVersion(**_as_dict(stored))


@router.put("/config/budget", tags=["config"])
async def save_budget_config(
    body: BudgetConfig, pool: Pool, owner: Owner, note: NoteParam = None
) -> BudgetConfigVersion:
    """Save the price table. Every AI step from the next job on is costed with it."""
    async with pool.acquire() as conn:
        stored = await save_config(conn, "budget", body, created_by=owner.user_id, note=note)
    return BudgetConfigVersion(**_as_dict(stored))


@router.post("/config/budget/validate", tags=["config"])
async def validate_budget(body: BudgetConfig, _owner: Owner) -> BudgetConfig:
    return body
