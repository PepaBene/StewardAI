"""Authenticated API routes: portfolio reads, holdings and evidence, on-demand jobs,
versioned config."""

import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Annotated, Any, Literal

import asyncpg
import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import DB_ID_MAX, IdPath, IdQuery, Pool
from steward.config import (
    BudgetConfig,
    HoldingProfilesConfig,
    JobsConfig,
    StrategyConfig,
    ThesesConfig,
    VerificationConfig,
)
from steward.config.common import Ticker
from steward.config.store import ConfigConflictError, StoredConfig, load_active, save_config
from steward.earnings.check import local_today
from steward.earnings.read import (
    FACT_CONFIDENCE,
    EvidenceLink,
    FactCheck,
    FactIndexEntry,
    HoldingDetail,
    ThesisBoard,
    document_text,
    evidence_relation,
    fact_index,
    holding_detail,
    snippet_context,
    thesis_board,
)
from steward.earnings.review import ReviewError, ReviewItem, review_queue
from steward.earnings.review import review_fact as review_fact_in_db
from steward.jobs import queue
from steward.jobs.runner import create_job
from steward.portfolio.read import (
    ContributionReport,
    HoldingsResponse,
    JobStatus,
    JobSummary,
    PortfolioHistory,
    PortfolioOverview,
    contribution_to_return,
    portfolio_history,
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


@router.get("/portfolio/history", tags=["portfolio"])
async def get_portfolio_history(
    pool: Pool,
    _owner: Owner,
    days: Annotated[int, Query(ge=7, le=3700)] = 365,
) -> PortfolioHistory:
    """Daily flow-adjusted portfolio value, indexed to 100 at the start of the window,
    against the first configured benchmark symbol. Deposits and withdrawals are excluded
    from the index so day-over-day change is investment return only, never a false gain
    or loss from money moving in or out. Empty (with `gap` set) before the first sync or
    with fewer than two snapshots in the window."""
    return await portfolio_history(pool, days)


@router.get("/portfolio/contribution", tags=["portfolio"])
async def get_portfolio_contribution(
    pool: Pool,
    _owner: Owner,
    days: Annotated[int, Query(ge=7, le=3700)] = 365,
) -> ContributionReport:
    """Every holding's contribution to the portfolio's return over the window, in base
    currency: its own trading (buying or selling more of it) netted out, so a holding is
    ranked by what the market actually did to it, not by how much was bought or sold.
    Empty (with `gap` set) before the first sync, or with no position snapshot in the
    window."""
    return await contribution_to_return(pool, days)


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


class JobStepOut(BaseModel):
    step_index: int
    name: str
    status: Literal["running", "succeeded", "failed"]
    started_at: dt.datetime
    finished_at: dt.datetime | None
    error: str | None
    detail: dict[str, Any] | None
    model: str | None
    cost_usd: float | None


_ATTEMPTS_DESCRIPTION = (
    "Times the runner started this job, a resumed run included; above 1 means it was retried."
    " 0 = never started."
)


class JobDetail(JobSummary):
    params: dict[str, Any]
    attempts: int = Field(description=_ATTEMPTS_DESCRIPTION)
    steps: list[JobStepOut]


class JobListItem(JobSummary):
    params: dict[str, Any]
    attempts: int = Field(description=_ATTEMPTS_DESCRIPTION)
    cost_usd: float | None = Field(description="Sum of the run's step costs; null if none.")
    failed_step: str | None = Field(description="The first failed step's name, if any.")


class JobList(BaseModel):
    jobs: list[JobListItem]
    next_before_id: int | None = Field(description="Pass as before_id for the next page.")


@router.get("/jobs", tags=["jobs"])
async def list_jobs(
    pool: Pool,
    _owner: Owner,
    job_type: Annotated[list[str] | None, Query(description="Any of these job types.")] = None,
    status_: Annotated[list[JobStatus] | None, Query(alias="status")] = None,
    ticker: Annotated[
        str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$", description="Runs for this holding.")
    ] = None,
    before_id: IdQuery = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobList:
    """The job log, newest first: every scheduled, manual and event-triggered run with its
    outcome and cost. Filters combine (AND)."""
    rows = await pool.fetch(
        "select j.id, j.job_type, j.trigger, j.status, j.error, j.created_at, j.started_at,"
        " j.finished_at, j.params, j.attempts, s.cost_usd, s.failed_step from public.job j"
        " left join lateral (select sum(cost_usd) as cost_usd, (array_agg(name order by"
        " step_index) filter (where status = 'failed'))[1] as failed_step"
        " from public.job_step where job_id = j.id) s on true"
        " where ($1::text[] is null or j.job_type = any($1))"
        " and ($2::text[] is null or j.status = any($2))"
        " and ($3::text is null or j.params ->> 'ticker' = $3)"
        " and ($4::bigint is null or j.id < $4)"
        " order by j.id desc limit $5",
        job_type or None,
        status_ or None,
        ticker,
        before_id,
        limit + 1,
    )
    more = len(rows) > limit
    items = [
        JobListItem(
            **{
                **dict(r),
                "params": json.loads(r["params"]) if r["params"] else {},
                "cost_usd": float(r["cost_usd"]) if r["cost_usd"] is not None else None,
            }
        )
        for r in rows[:limit]
    ]
    return JobList(jobs=items, next_before_id=items[-1].id if more and items else None)


@router.get("/jobs/{job_id}", tags=["jobs"])
async def get_job(job_id: IdPath, pool: Pool, _owner: Owner) -> JobDetail:
    """One job run with its steps, to follow an on-demand run to completion."""
    row = await pool.fetchrow(
        "select id, job_type, trigger, status, error, created_at, started_at, finished_at,"
        " params, attempts from public.job where id = $1",
        job_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No job {job_id}")
    steps = await pool.fetch(
        "select step_index, name, status, started_at, finished_at, error, detail, model,"
        " cost_usd from public.job_step where job_id = $1 order by step_index",
        job_id,
    )
    return JobDetail(
        **{**dict(row), "params": json.loads(row["params"])},
        steps=[
            JobStepOut(
                **{
                    **dict(s),
                    "detail": json.loads(s["detail"]) if s["detail"] else None,
                    "cost_usd": float(s["cost_usd"]) if s["cost_usd"] is not None else None,
                }
            )
            for s in steps
        ],
    )


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


class ReconcileRequest(BaseModel):
    ticker: Ticker | None = Field(default=None, description="One holding, or all when null.")


@router.post("/jobs/fact-reconcile", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_fact_reconcile(body: ReconcileRequest, pool: Pool, owner: Owner) -> JobSummary:
    """Check stored results figures against the issuers' 10-Q/10-K XBRL now."""
    return await _enqueue(
        pool, "fact_reconcile", owner.user_id, body.model_dump(mode="json", exclude_none=True)
    )


@router.post("/jobs/earnings-calendar", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_earnings_calendar(pool: Pool, owner: Owner) -> JobSummary:
    """Refresh every holding's next report date now."""
    return await _enqueue(pool, "earnings_calendar", owner.user_id, {"all": True})


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


@router.get("/theses/board", tags=["holdings"])
async def get_thesis_board(pool: Pool, _owner: Owner) -> ThesisBoard:
    """Every holding with a thesis, once: each KPI's current pass / breach / unknown with the
    verdict behind it. A KPI never judged is listed as unknown, never left out."""
    return await thesis_board(pool, local_today())


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
    fact_id: IdPath, pool: Pool, storage: Storage, _owner: Owner
) -> EvidenceLink:
    """The archived copy of the page a fact was read from: a short-lived signed URL and
    the text around the fact's snippet."""
    row = await pool.fetchrow(
        "select f.id, f.ticker, f.metric, f.period, f.value_as_written, f.snippet,"
        " f.unit_snippet, f.status, f.checks, f.origin, e.source_url, e.storage_path,"
        " e.content_type, e.fetched_at, e.sha256 from public.fact f"
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
    checks = [FactCheck(**c) for c in json.loads(row["checks"] or "[]")]
    return EvidenceLink(
        fact_id=row["id"],
        ticker=row["ticker"],
        metric=row["metric"],
        period=row["period"],
        value_as_written=row["value_as_written"],
        snippet=row["snippet"],
        unit_snippet=row["unit_snippet"],
        confidence=FACT_CONFIDENCE[row["status"]],
        relation=evidence_relation(row["status"], checks),
        origin=row["origin"],
        source_url=row["source_url"],
        evidence_url=url,
        content_type=row["content_type"],
        fetched_at=row["fetched_at"],
        sha256=row["sha256"],
        context=context,
    )


class FactReview(BaseModel):
    action: Literal["accept", "correct", "reject"]
    reason: str = Field(min_length=1, max_length=500, description="Logged with the decision.")
    value: Decimal | None = Field(
        default=None,
        description="For `correct`: the right value, in the fact's printed scale (the same "
        "scale as `value`, e.g. millions).",
    )


class FactReviewResult(BaseModel):
    fact_id: int
    status: Literal["verified", "rejected"]
    corrected_fact_id: int | None
    verdict_job: JobSummary


@router.post("/facts/{fact_id}/review", tags=["holdings"])
async def review_fact(
    fact_id: IdPath, body: FactReview, pool: Pool, owner: Owner
) -> FactReviewResult:
    """Decide on a fact: accept a held or quarantined one, correct any fact (the owner's
    value is stored as a new fact that wins), or reject it. The decision and its reason are
    logged, and the holding's verdicts are re-judged by the worker."""
    try:
        outcome = await review_fact_in_db(
            pool,
            fact_id,
            body.action,
            body.reason,
            owner_id=owner.user_id,
            corrected_value=body.value,
        )
    except ReviewError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.not_found else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(exc)) from exc
    job = await _enqueue(pool, "thesis_verdicts", owner.user_id, {"ticker": outcome.ticker})
    return FactReviewResult(
        fact_id=fact_id,
        status="verified" if outcome.status == "verified" else "rejected",
        corrected_fact_id=outcome.corrected_fact_id,
        verdict_job=job,
    )


@router.get("/facts/review-queue", tags=["holdings"])
async def get_review_queue(
    pool: Pool,
    _owner: Owner,
    ticker: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = None,
) -> list[ReviewItem]:
    """Facts waiting for the owner: held by the validator or quarantined by reconciliation,
    each with its snippet, source, the checks it broke and the reconciliation behind it."""
    return await review_queue(pool, ticker)


@router.get("/facts/index", tags=["holdings"])
async def get_fact_index(pool: Pool, _owner: Owner) -> list[FactIndexEntry]:
    """The newest fact per (ticker, metric), across every holding — small and cheap on
    purpose: it exists to let a search (the command palette) resolve a typed metric and
    ticker straight to that figure's evidence page, not to carry a claim's own detail."""
    return await fact_index(pool)


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
BaseVersionParam = Annotated[
    int | None,
    Query(
        ge=1,
        le=DB_ID_MAX,
        description="The version the edit started from. If another save has happened since, "
        "nothing is saved and the answer is 409.",
    ),
]


def config_conflict(exc: ConfigConflictError | asyncpg.UniqueViolationError) -> HTTPException:
    """409 for a config save that lost a race with another save."""
    if isinstance(exc, ConfigConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(
        status.HTTP_409_CONFLICT,
        "the config changed since you loaded it; reload it and apply the change again",
    )


async def save_versioned[M: BaseModel](
    pool: asyncpg.Pool,
    domain: str,
    body: M,
    *,
    created_by: str,
    note: str | None,
    base_version_id: int | None,
) -> StoredConfig[M]:
    """save_config for a PUT route: 409 when the active version is no longer
    `base_version_id` (or a concurrent save collided)."""
    try:
        async with pool.acquire() as conn:
            return await save_config(
                conn,
                domain,
                body,
                created_by=created_by,
                note=note,
                expected_active_id=base_version_id,
            )
    except (ConfigConflictError, asyncpg.UniqueViolationError) as exc:
        raise config_conflict(exc) from exc


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
    body: StrategyConfig,
    pool: Pool,
    owner: Owner,
    note: NoteParam = None,
    base_version_id: BaseVersionParam = None,
) -> StrategyConfigVersion:
    """Validate and save as the new active strategy. Drift uses it immediately."""
    stored = await save_versioned(
        pool,
        "strategy",
        body,
        created_by=owner.user_id,
        note=note,
        base_version_id=base_version_id,
    )
    return StrategyConfigVersion(**_as_dict(stored))


@router.get("/config/theses", tags=["config"])
async def get_theses_config(pool: Pool, _owner: Owner) -> ThesesConfigVersion:
    stored = await load_active(pool, "theses", ThesesConfig)
    if stored is None:
        raise _not_found("theses")
    return ThesesConfigVersion(**_as_dict(stored))


@router.put("/config/theses", tags=["config"])
async def save_theses_config(
    body: ThesesConfig,
    pool: Pool,
    owner: Owner,
    note: NoteParam = None,
    base_version_id: BaseVersionParam = None,
) -> ThesesConfigVersion:
    stored = await save_versioned(
        pool,
        "theses",
        body,
        created_by=owner.user_id,
        note=note,
        base_version_id=base_version_id,
    )
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
    body: JobsConfig,
    pool: Pool,
    owner: Owner,
    note: NoteParam = None,
    base_version_id: BaseVersionParam = None,
) -> JobsConfigVersion:
    """Save job schedules. The worker picks them up within a few minutes, no restart."""
    stored = await save_versioned(
        pool,
        "jobs",
        body,
        created_by=owner.user_id,
        note=note,
        base_version_id=base_version_id,
    )
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
    body: HoldingProfilesConfig,
    pool: Pool,
    owner: Owner,
    note: NoteParam = None,
    base_version_id: BaseVersionParam = None,
) -> HoldingProfilesConfigVersion:
    """Save holding profiles. The next earnings check and calendar refresh use them."""
    stored = await save_versioned(
        pool,
        "holding_profiles",
        body,
        created_by=owner.user_id,
        note=note,
        base_version_id=base_version_id,
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
    body: VerificationConfig,
    pool: Pool,
    owner: Owner,
    note: NoteParam = None,
    base_version_id: BaseVersionParam = None,
) -> VerificationConfigVersion:
    stored = await save_versioned(
        pool,
        "verification",
        body,
        created_by=owner.user_id,
        note=note,
        base_version_id=base_version_id,
    )
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
    body: BudgetConfig,
    pool: Pool,
    owner: Owner,
    note: NoteParam = None,
    base_version_id: BaseVersionParam = None,
) -> BudgetConfigVersion:
    """Save the price table. Every AI step from the next job on is costed with it."""
    stored = await save_versioned(
        pool,
        "budget",
        body,
        created_by=owner.user_id,
        note=note,
        base_version_id=base_version_id,
    )
    return BudgetConfigVersion(**_as_dict(stored))


@router.post("/config/budget/validate", tags=["config"])
async def validate_budget(body: BudgetConfig, _owner: Owner) -> BudgetConfig:
    return body
