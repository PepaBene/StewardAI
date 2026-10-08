"""Authenticated routes for the release gate, the source audit ledger and the owner pilot
(plan Phase 8). The audit ledger is append-only: there is no edit or delete route."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from steward.release import audit, pilot, readiness

router = APIRouter(tags=["release"])


class AuditOverview(BaseModel):
    summary: audit.AuditSummary
    rows: list[audit.AuditRow]


class PilotStart(BaseModel):
    length_days: int = Field(default=pilot.DEFAULT_DAYS, ge=7, le=120)


class UsageIn(BaseModel):
    path: str = Field(pattern=r"^/[a-z0-9/_-]{0,120}$")


class PilotState(BaseModel):
    window: pilot.PilotWindow | None
    report: pilot.PilotReport | None


@router.get("/release/readiness")
async def get_readiness(pool: Pool, owner: Owner) -> readiness.Readiness:
    """Each marketed feature as ready, partial, unavailable or untested, with the free-access
    state of its inputs, its source audit and whether it may be switched on."""
    return await readiness.build_readiness(pool, owner.user_id)


@router.get("/release/audit")
async def get_audit(
    pool: Pool, owner: Owner, limit: Annotated[int, Query(ge=1, le=2000)] = 300
) -> AuditOverview:
    """The sampled source-to-screen checks, with progress against the plan's sample."""
    every = await audit.list_rows(pool, owner.user_id)
    return AuditOverview(summary=audit.summarize(every), rows=every[:limit])


@router.post("/release/audit", status_code=201)
async def add_audit(body: audit.AuditIn, pool: Pool, owner: Owner) -> audit.AuditRow:
    """Record one displayed fact checked against its source. Append-only: to correct a result,
    record the fact again and the newest row stands."""
    return await audit.record(pool, owner.user_id, body)


@router.get("/release/pilot")
async def get_pilot(pool: Pool, owner: Owner) -> PilotState:
    window = await pilot.current(pool, owner.user_id)
    report = await pilot.build_report(pool, owner.user_id) if window else None
    return PilotState(window=window, report=report)


@router.post("/release/pilot", status_code=201)
async def start_pilot(body: PilotStart, pool: Pool, owner: Owner) -> pilot.PilotWindow:
    """Start a new pilot window today. An earlier window is kept but no longer reported."""
    return await pilot.start(pool, owner.user_id, body.length_days)


@router.post("/release/usage", status_code=204)
async def log_usage(body: UsageIn, pool: Pool, owner: Owner) -> None:
    """Note that a page was opened. Only the path is stored, and only while a pilot window is
    open; with none running this does nothing."""
    window = await pilot.current(pool, owner.user_id)
    if window is not None and not window.over:
        await pilot.log_use(pool, owner.user_id, body.path)
