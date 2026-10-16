"""Configured Form 13F manager tracker."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path

from api.auth import Owner
from api.db import Pool
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.ownership.investors import (
    InvestorsByHolding,
    ManagerDetail,
    ManagerSummary,
    by_holding,
    manager_detail,
    managers,
)

router = APIRouter()
CIKPath = Annotated[str, Path(pattern=r"^[0-9]{1,10}$")]


async def _tracked(pool: Pool) -> list[tuple[str, str]]:
    config = await load_active(pool, "jobs", JobsConfig)
    return [
        (item.cik, item.name)
        for item in (config.body if config else JobsConfig()).ownership.tracked_managers
    ]


@router.get("/investors", tags=["investors"], response_model=list[ManagerSummary])
async def get_investors(pool: Pool, _owner: Owner) -> list[ManagerSummary]:
    """Configured managers and their latest stored 13F filing summary."""
    return await managers(pool, await _tracked(pool))


@router.get("/investors/by-holding", tags=["investors"], response_model=InvestorsByHolding)
async def get_investors_by_holding(pool: Pool, _owner: Owner) -> InvestorsByHolding:
    """Each current holding's 13F holders at the latest quarter end, the move against the
    earlier comparable quarter, and what the tracked managers did in it."""
    return await by_holding(pool, await _tracked(pool))


@router.get("/investors/{cik}", tags=["investors"], response_model=ManagerDetail)
async def get_investor(cik: CIKPath, pool: Pool, _owner: Owner) -> ManagerDetail:
    """Largest common-stock positions from the manager's latest stored filing."""
    configured = {key.zfill(10): name for key, name in await _tracked(pool)}
    normalized = cik.zfill(10)
    if normalized not in configured:
        raise HTTPException(404, "This manager is not configured for tracking")
    detail = await manager_detail(pool, normalized, configured[normalized])
    if detail is None:
        raise HTTPException(404, "No 13F filings are stored yet; load ownership data first")
    return detail
