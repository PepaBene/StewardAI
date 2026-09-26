"""Authenticated API routes for Phase 2b: identity and structure, relationship decisions,
price history and risk, reference-data gaps, the exchange calendar, and on-demand runs."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.routes import _enqueue
from steward.config.common import Ticker
from steward.config.jobs import ExchangeClosure
from steward.ownership.read import HoldingOwnership, holding_ownership
from steward.portfolio.read import JobSummary
from steward.reference.read import (
    ExchangeCalendar,
    HoldingIdentity,
    PriceHistory,
    ReferenceGaps,
    RelationshipDecisionError,
    RelationshipOut,
    confirm_closures,
    decide_relationship,
    exchange_calendar,
    holding_identity,
    price_history,
    reference_gaps,
)

router = APIRouter()

TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


@router.get("/holdings/{ticker}/identity", tags=["holdings"])
async def get_holding_identity(ticker: TickerPath, pool: Pool, _owner: Owner) -> HoldingIdentity:
    """Identifiers, listings, home market, legal entity, group structure and the
    relationships waiting for confirmation."""
    identity = await holding_identity(pool, ticker)
    if identity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nothing is known about {ticker}")
    return identity


class RelationshipDecision(BaseModel):
    relationship: RelationshipOut
    holding_profiles_version_id: int | None = Field(
        description="The new holding-profiles version, when the confirmation changed it."
    )


async def _decide(
    relationship_id: int, confirm: bool, pool: Pool, owner_id: str
) -> RelationshipDecision:
    try:
        rel, version = await decide_relationship(pool, relationship_id, confirm, owner_id)
    except RelationshipDecisionError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.not_found else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(exc)) from exc
    return RelationshipDecision(relationship=rel, holding_profiles_version_id=version)


@router.post("/relationships/{relationship_id}/confirm", tags=["holdings"])
async def confirm_relationship(
    relationship_id: int, pool: Pool, owner: Owner
) -> RelationshipDecision:
    """Confirm a suggested relationship. A competitor, supplier or customer is added to the
    holding profile (a new config version), so the next news run matches on it."""
    return await _decide(relationship_id, True, pool, owner.user_id)


@router.post("/relationships/{relationship_id}/reject", tags=["holdings"])
async def reject_relationship(
    relationship_id: int, pool: Pool, owner: Owner
) -> RelationshipDecision:
    """Reject a suggested relationship; it is never suggested again."""
    return await _decide(relationship_id, False, pool, owner.user_id)


@router.get("/holdings/{ticker}/prices", tags=["holdings"])
async def get_price_history(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    days: Annotated[int, Query(ge=7, le=3700)] = 365,
) -> PriceHistory:
    """Daily closes with the benchmark, and the risk card computed from the stored bars."""
    return await price_history(pool, ticker, days)


@router.get("/reference/gaps", tags=["reference"])
async def get_reference_gaps(pool: Pool, _owner: Owner) -> ReferenceGaps:
    """Instruments whose identity could not be resolved, and symbols with no price history."""
    return await reference_gaps(pool)


@router.get("/calendar/exchanges", tags=["reference"])
async def get_exchange_calendar(pool: Pool, _owner: Owner) -> ExchangeCalendar:
    """Confirmed exchange closures and the public holidays suggested as closures."""
    return await exchange_calendar(pool, dt.datetime.now(dt.UTC).date())


class ClosureConfirmation(BaseModel):
    closures: list[ExchangeClosure] = Field(min_length=1)


class ClosureResult(BaseModel):
    jobs_version_id: int


@router.post("/calendar/exchanges/closures", tags=["reference"])
async def add_exchange_closures(
    body: ClosureConfirmation, pool: Pool, owner: Owner
) -> ClosureResult:
    """Confirm closures into the Jobs config (a new version); used from the next run."""
    return ClosureResult(jobs_version_id=await confirm_closures(pool, body.closures, owner.user_id))


class ReferenceRunRequest(BaseModel):
    ticker: Ticker | None = Field(default=None, description="One holding, or all when null.")
    force: bool = Field(default=False, description="Refresh OpenFIGI and Wikidata now.")


@router.post("/jobs/reference-data", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_reference_data(body: ReferenceRunRequest, pool: Pool, owner: Owner) -> JobSummary:
    """Resolve identity, reference data and structure now."""
    return await _enqueue(
        pool, "reference_data", owner.user_id, body.model_dump(mode="json", exclude_none=True)
    )


@router.post("/jobs/price-history", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_price_history(pool: Pool, owner: Owner) -> JobSummary:
    """Collect price and FX history now."""
    return await _enqueue(pool, "price_history", owner.user_id)


@router.post("/jobs/holiday-refresh", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_holiday_refresh(pool: Pool, owner: Owner) -> JobSummary:
    """Load public holidays for this and next year now."""
    return await _enqueue(pool, "holiday_refresh", owner.user_id)


@router.get("/holdings/{ticker}/ownership", tags=["reference"])
async def get_holding_ownership(
    ticker: Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")], pool: Pool, _owner: Owner
) -> HoldingOwnership:
    """Institutional ownership from SEC Form 13F data sets: holders, shares and value per
    report period (newest first) with changes, and the largest holders. Only US-listed
    holdings are covered; every figure carries its period and filing date."""
    return await holding_ownership(pool, ticker)
