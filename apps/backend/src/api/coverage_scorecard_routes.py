"""Owner-only measured figures for the free-data plan's release targets.

Wiring: `app.include_router(router)` in api/main.py. All routes are read-only.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from api.auth import Owner
from api.db import Pool
from steward.reference.coverage_scorecard import (
    DEFAULT_MAX_STALE_SESSIONS,
    DataReach,
    IdentityReview,
    PriceCoverage,
    ProvenanceCompleteness,
    data_reach,
    identity_review,
    price_coverage,
    provenance_completeness,
)

router = APIRouter()


@router.get("/portfolio/price-coverage", tags=["portfolio"])
async def get_price_coverage(
    pool: Pool,
    _owner: Owner,
    max_stale_sessions: Annotated[
        int, Query(ge=0, le=30, description="A close older than this many sessions is stale.")
    ] = DEFAULT_MAX_STALE_SESSIONS,
) -> PriceCoverage:
    """Share of held market value (base currency, latest snapshot) with a usable latest close,
    the unknown bucket, and the per-holding state, against the plan's 95% target."""
    return await price_coverage(pool, max_stale_sessions=max_stale_sessions)


@router.get("/portfolio/identity-review", tags=["portfolio"])
async def get_identity_review(pool: Pool, _owner: Owner) -> IdentityReview:
    """Share of held positions with a confirmed issuer/security/listing path, and the
    unresolved exceptions."""
    return await identity_review(pool)


@router.get("/data/reach", tags=["data"])
async def get_data_reach(pool: Pool, _owner: Owner) -> DataReach:
    """Stored coverage by exchange, country, asset class and metric for the app's own
    universe. Never extrapolated to a global percentage."""
    return await data_reach(pool)


@router.get("/data/provenance-completeness", tags=["data"])
async def get_provenance_completeness(pool: Pool, _owner: Owner) -> ProvenanceCompleteness:
    """Share of rows in each displayed fact table carrying source, as-of, known-at, method or
    vintage, and access state (null column = missing)."""
    return await provenance_completeness(pool)
