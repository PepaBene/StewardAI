"""Authenticated routes for SEC XBRL fundamentals: revenue by region / segment / product,
and the on-demand run of the `sec_xbrl` job (shares outstanding and segment revenue)."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.routes import _enqueue
from steward.config.common import Ticker
from steward.fundamentals.read import HoldingSegments, PeriodBasis, holding_segments
from steward.portfolio.read import JobSummary

router = APIRouter()

TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


@router.get("/holdings/{ticker}/segments", tags=["holdings"])
async def get_holding_segments(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    basis: Annotated[PeriodBasis | None, Query(description="annual or quarter")] = None,
) -> HoldingSegments:
    """Revenue by region, business segment and product as the company reports them: the
    latest fiscal year (or quarter) from its SEC XBRL filing, with the earlier periods
    stored for the same basis. Holdings outside EDGAR show the rows entered in their
    holding profile, labelled as entered manually."""
    return await holding_segments(pool, ticker, basis)


class XbrlRunRequest(BaseModel):
    ticker: Ticker | None = Field(default=None, description="One holding, or all when null.")
    force: bool = Field(default=False, description="Parse the latest filings again.")


@router.post("/jobs/sec-xbrl", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_sec_xbrl(body: XbrlRunRequest, pool: Pool, owner: Owner) -> JobSummary:
    """Read shares outstanding and revenue breakdowns from the SEC XBRL filings now."""
    return await _enqueue(
        pool, "sec_xbrl", owner.user_id, body.model_dump(mode="json", exclude_none=True)
    )
