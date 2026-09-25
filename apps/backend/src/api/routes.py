"""Authenticated API routes: portfolio reads, on-demand jobs, versioned config."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from api.auth import Owner
from api.db import Pool
from steward.config import JobsConfig, StrategyConfig, ThesesConfig
from steward.config.store import StoredConfig, load_active, save_config
from steward.jobs import queue
from steward.jobs.runner import create_job
from steward.portfolio.read import (
    HoldingsResponse,
    JobSummary,
    PortfolioOverview,
    portfolio_holdings,
    portfolio_overview,
)

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


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@router.post("/jobs/portfolio-sync", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_portfolio_sync(pool: Pool, owner: Owner) -> JobSummary:
    """Queue a portfolio sync now. The worker runs it; a run already in progress finishes
    first (the job waits for it), so two syncs never write at the same time."""
    async with pool.acquire() as conn, conn.transaction():
        job_id = await create_job(conn, "portfolio_sync", "manual", requested_by=owner.user_id)
        await queue.enqueue(conn, job_id, "portfolio_sync")
        row = await conn.fetchrow(
            "select id, job_type, trigger, status, error, created_at, started_at, finished_at"
            " from public.job where id = $1",
            job_id,
        )
    return JobSummary(**dict(row))


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
