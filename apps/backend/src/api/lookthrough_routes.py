"""Authenticated API routes for ETF look-through: the portfolio with its funds expanded
into their holdings (from stored issuer snapshots), and a manual run of the fetch job."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from api.auth import Owner
from api.db import Pool
from api.routes import _enqueue
from steward.lookthrough.read import LookThroughReport, portfolio_look_through
from steward.portfolio.read import JobSummary

router = APIRouter()


@router.get("/portfolio/look-through", tags=["portfolio"])
async def get_portfolio_look_through(
    pool: Pool,
    _owner: Owner,
    top: Annotated[int, Query(ge=1, le=100)] = 25,
) -> LookThroughReport:
    """Exposure by country, region, sector and currency with the funds expanded (direct
    stocks plus fund weight x holding weight; cash, the funds' own cash & derivatives and
    funds with no snapshot are separate buckets), the top underlying holdings merged across
    funds and direct positions, concentration (HHI, 1/HHI, top-10 share) of the portfolio and
    of the benchmark proxy, FX exposure against the benchmark, each fund's as-of date, source
    and tier, and coverage. Read from stored snapshots only; nothing is fetched here."""
    return await portfolio_look_through(pool, top=top)


@router.post("/jobs/etf-look-through", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_etf_look_through(pool: Pool, owner: Owner) -> JobSummary:
    """Fetch the funds' holdings from the issuers now (skipped per fund when the stored
    as-of date is already current)."""
    return await _enqueue(pool, "etf_look_through", owner.user_id)
