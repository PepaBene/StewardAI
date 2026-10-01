"""Classified insider activity and net discretionary buying / selling per holding.

Additive to `insider_routes.py` (raw as-filed rows): this route returns the economic
classification, footnotes, derivative terms, Rule 10b5-1 status and amendment supersession,
with the exclusions and limitations that qualify the net figure.
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Path, Query

from api.auth import Owner
from api.db import Pool
from steward.ownership.insider_read import InsiderActivityOut, holding_insider_activity

router = APIRouter()
TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


@router.get(
    "/holdings/{ticker}/insider-activity",
    tags=["holdings"],
    response_model=InsiderActivityOut,
)
async def get_insider_activity(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    since: Annotated[dt.date | None, Query(description="Default: 365 days before until.")] = None,
    until: Annotated[dt.date | None, Query(description="Default: today.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> InsiderActivityOut:
    """Insider transactions by economic class and net discretionary (open-market) activity."""
    return await holding_insider_activity(
        pool, ticker.upper(), since=since, until=until, limit=limit
    )
