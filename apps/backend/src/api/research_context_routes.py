"""Read-only research context APIs (plan D7.4): country, industry and company claims that
deep research and chat cite. Numbers come from stored observations or deterministic
arithmetic over them; each claim carries `known_as_of` and honours the `as_of` cutoff.

Wiring: `app.include_router(router)` of `router` in api/main.py.
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query

from api.auth import Owner
from api.db import Pool
from steward.data_context.research import (
    ResearchContext,
    company_context,
    country_context,
    industry_context,
)

router = APIRouter()
AsOfTime = Annotated[
    dt.datetime | None, Query(description="Point-in-time cutoff; records known later are hidden.")
]


def _reject_future(as_of: dt.datetime | None) -> None:
    if as_of is not None:
        stamp = as_of if as_of.tzinfo else as_of.replace(tzinfo=dt.UTC)
        if stamp > dt.datetime.now(dt.UTC):
            raise HTTPException(422, "as_of cannot be in the future")


@router.get("/research-context/country/{geography}", tags=["research"])
async def get_country_context(
    geography: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{2,5}$")],
    pool: Pool,
    _owner: Owner,
    as_of: AsOfTime = None,
) -> ResearchContext:
    _reject_future(as_of)
    return await country_context(pool, geography.upper(), as_of)


@router.get("/research-context/industry/{hs_code}", tags=["research"])
async def get_industry_context(
    hs_code: Annotated[str, Path(pattern=r"^[0-9]{2,6}$")],
    pool: Pool,
    _owner: Owner,
    as_of: AsOfTime = None,
) -> ResearchContext:
    _reject_future(as_of)
    return await industry_context(pool, hs_code, as_of)


@router.get("/research-context/company/{ticker}", tags=["research"])
async def get_company_context(
    ticker: Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")],
    pool: Pool,
    _owner: Owner,
    as_of: AsOfTime = None,
) -> ResearchContext:
    _reject_future(as_of)
    return await company_context(pool, ticker, as_of)
