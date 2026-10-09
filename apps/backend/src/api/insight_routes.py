"""Read-only routes for the sector map and the "what changed" feed (plan M7.2, M7.3)."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from api.auth import Owner
from api.db import Pool
from steward.changes import feed
from steward.sectors import map as sector_map

router = APIRouter(tags=["insights"])


@router.get("/insights/sectors")
async def get_sector_map(pool: Pool, _owner: Owner) -> sector_map.SectorMap:
    """Sectors and industries of the companies with a stored profile: company and market counts,
    growth, margin and valuation spread, each with the sample behind it."""
    return await sector_map.build_sector_map(pool)


@router.get("/insights/changes")
async def get_changes(
    pool: Pool,
    _owner: Owner,
    since: Annotated[
        dt.date | None,
        Query(description="Compare with the end of this day; default is the previous snapshot."),
    ] = None,
) -> feed.ChangeFeed:
    """What differs between the app's knowledge at the earlier day and now, largest portfolio
    weight first, each item pointing at the old and new records."""
    if since is not None and since > dt.date.today():
        raise HTTPException(422, "The date is in the future.")
    result = await feed.build_feed(pool, since)
    if result is None:
        raise HTTPException(404, "No portfolio snapshot is stored yet.")
    return result
