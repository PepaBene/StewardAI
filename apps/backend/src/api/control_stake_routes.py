"""Schedule 13D/G control stakes: a channel separate from Form 13F ownership."""

from typing import Annotated

from fastapi import APIRouter, Path

from api.auth import Owner
from api.db import Pool
from api.routes import _enqueue
from steward.ownership.control_stakes_read import ControlStakesOut, holding_control_stakes
from steward.portfolio.read import JobSummary

router = APIRouter()
TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


@router.post("/jobs/control-stakes", tags=["jobs"], status_code=202)
async def run_control_stakes(pool: Pool, owner: Owner) -> JobSummary:
    """Load Schedule 13D/G filings for held issuers and tracked managers now."""
    return await _enqueue(pool, "control_stakes", owner.user_id)


@router.get(
    "/holdings/{ticker}/control-stakes",
    tags=["holdings"],
    response_model=ControlStakesOut,
)
async def get_control_stakes(ticker: TickerPath, pool: Pool, _owner: Owner) -> ControlStakesOut:
    """Beneficial-ownership / control stakes (13D/G), never combined with 13F or fund data."""
    return await holding_control_stakes(pool, ticker)
