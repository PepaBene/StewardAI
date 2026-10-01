"""SEC N-PORT fund positions in a holding (fund positions, not beneficial ownership)."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Path, Query

from api.auth import Owner
from api.db import Pool
from api.routes import _enqueue
from steward.ownership.nport_read import HoldingFundPositions, holding_fund_positions
from steward.portfolio.read import JobSummary

router = APIRouter()


@router.post("/jobs/nport-holdings", tags=["jobs"], status_code=202)
async def run_nport_holdings(pool: Pool, owner: Owner) -> JobSummary:
    """Load SEC N-PORT holdings for the portfolio's US funds now."""
    return await _enqueue(pool, "nport_holdings", owner.user_id)


@router.get("/holdings/{ticker}/fund-positions", tags=["holdings"])
async def get_fund_positions(
    ticker: Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")],
    pool: Pool,
    _owner: Owner,
    as_of: dt.date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> HoldingFundPositions:
    """Latest N-PORT positions funds report in this holding, each with its report date,
    filing lag and public-availability date, plus coverage. Fund positions only: not beneficial
    ownership and never additive with Form 13F or 13D/G figures."""
    return await holding_fund_positions(pool, ticker, as_of=as_of, limit=limit)
