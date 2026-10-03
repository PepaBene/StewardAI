"""Owner-only data-context read models (plan Phase 7: D7.2, D7.3, D7.7).

Wiring: `app.include_router(router)` of `router` in api/main.py. All routes are read-only and
point-in-time: pass `as_of` to hide anything learned later.
"""

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query

from api.auth import Owner
from api.db import Pool
from api.typed_responses import EvidenceDomainBody, typed
from steward.data_context import evidence
from steward.data_context.evidence import EvidenceSummary
from steward.data_context.holding import HoldingDataContext, holding_data_context
from steward.data_context.portfolio import (
    MixedCurrencyError,
    PortfolioDataContext,
    portfolio_data_context,
)

router = APIRouter()
TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]
AsOfTime = Annotated[
    dt.datetime | None, Query(description="Point-in-time cutoff; records known later are hidden.")
]
EvidenceDomain = Literal[
    "filed-notes", "positions", "insider-activity", "dependencies", "deals", "consensus"
]


def _reject_future(as_of: dt.datetime | None) -> None:
    if as_of is not None:
        stamp = as_of if as_of.tzinfo else as_of.replace(tzinfo=dt.UTC)
        if stamp > dt.datetime.now(dt.UTC):
            raise HTTPException(422, "as_of cannot be in the future")


@router.get("/holdings/{ticker}/data-context", tags=["holdings"])
async def get_holding_data_context(
    ticker: TickerPath, pool: Pool, _owner: Owner, as_of: AsOfTime = None
) -> HoldingDataContext:
    """Global financial history, country/trade exposure, business network, patents, source
    disagreements, coverage gaps and primary-versus-vendor provenance of one holding."""
    _reject_future(as_of)
    return await holding_data_context(pool, ticker, as_of)


@router.get("/portfolio/data-context", tags=["portfolio"])
async def get_portfolio_data_context(
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[dt.date | None, Query()] = None,
    series_limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PortfolioDataContext:
    """Macro sensitivities (reported versus inferred), country and product concentration,
    adjusted-return coverage and fund overlap. Every ratio carries its denominator; only
    mapped holdings are rolled up."""
    if as_of is not None and as_of > dt.datetime.now(dt.UTC).date():
        raise HTTPException(422, "as_of cannot be in the future")
    try:
        return await portfolio_data_context(pool, as_of, series_limit=series_limit)
    except MixedCurrencyError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/holdings/{ticker}/evidence-summary", tags=["holdings"])
async def get_evidence_summary(
    ticker: TickerPath, pool: Pool, _owner: Owner, as_of: AsOfTime = None
) -> EvidenceSummary:
    """Filed-note evidence, separate control / institutional / fund positions, classified
    insider activity, confirmed dependencies, deal states and consensus vintages, each with
    its source passage, known-at date and coverage reason."""
    _reject_future(as_of)
    return await evidence.evidence_summary(pool, ticker, as_of)


@router.get(
    "/holdings/{ticker}/evidence/{domain}",
    tags=["holdings"],
    response_model=None,
    responses=typed(EvidenceDomainBody),
)
async def get_evidence_domain(
    ticker: TickerPath, domain: EvidenceDomain, pool: Pool, _owner: Owner, as_of: AsOfTime = None
) -> object:
    """One evidence domain of `evidence-summary` on its own."""
    _reject_future(as_of)
    if domain == "filed-notes":
        return await evidence.filed_notes(pool, ticker, as_of)
    if domain == "positions":
        return await evidence.positions(pool, ticker, as_of)
    if domain == "insider-activity":
        return await evidence.insider(pool, ticker, as_of)
    if domain == "dependencies":
        return await evidence.dependencies(pool, ticker, as_of)
    if domain == "deals":
        return await evidence.deals(pool, ticker, as_of)
    return await evidence.consensus(pool, ticker, as_of)
