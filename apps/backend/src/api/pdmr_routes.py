"""Stored UK PDMR notifications (MAR Article 19) per holding, with the channel's honest status."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from api.auth import Owner
from api.db import Pool
from api.routes import _enqueue
from steward.ownership.pdmr_read import HoldingPdmrOut, holding_pdmr
from steward.portfolio.read import JobSummary

router = APIRouter()
TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


@router.get(
    "/holdings/{ticker}/pdmr-notifications",
    tags=["holdings"],
    response_model=HoldingPdmrOut,
)
async def get_pdmr_notifications(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    limit: Annotated[int, Query(ge=1, le=300)] = 100,
) -> HoldingPdmrOut:
    """UK PDMR notifications parsed from the RNS stream, with the status of every attempt
    (an announcement that could not be read is listed with why, never dropped)."""
    return await holding_pdmr(pool, ticker.upper(), limit=limit)


@router.post("/jobs/pdmr-uk", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_pdmr_uk(pool: Pool, owner: Owner) -> JobSummary:
    """Parse UK PDMR announcements from the RNS stream now."""
    return await _enqueue(pool, "pdmr_uk", owner.user_id)
