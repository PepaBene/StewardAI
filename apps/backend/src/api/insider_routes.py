"""Structured SEC Forms 3, 4 and 5 for portfolio issuers."""

import datetime as dt
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from api.auth import Owner
from api.db import Pool
from api.routes import _enqueue
from steward.portfolio.read import JobSummary

router = APIRouter()


@router.post("/jobs/insider-trades", tags=["jobs"], status_code=202)
async def run_insider_trades(pool: Pool, owner: Owner) -> JobSummary:
    """Load structured insider filings for portfolio issuers now."""
    return await _enqueue(pool, "insider_trades", owner.user_id)


class InsiderTransactionOut(BaseModel):
    accession: str
    form: str
    filed: dt.date
    ticker: str
    issuer_name: str | None
    owner_name: str | None
    reporting_owner_cik: str
    officer_title: str | None
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    security_title: str | None
    transaction_date: dt.date | None
    transaction_code: str
    shares: Decimal | None
    price_per_share: Decimal | None
    acquired_disposed: str | None
    shares_owned_after: Decimal | None
    direct_indirect: str | None
    derivative: bool
    source_url: str


@router.get(
    "/holdings/{ticker}/insider-trades",
    tags=["holdings"],
    response_model=list[InsiderTransactionOut],
)
async def get_insider_trades(
    ticker: Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")],
    pool: Pool,
    _owner: Owner,
    forms: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[InsiderTransactionOut]:
    """Reported insider transactions, newest first, from structured SEC ownership filings."""
    allowed = {"3", "3/A", "4", "4/A", "5", "5/A"}
    selected = [f for f in (forms or sorted(allowed)) if f in allowed]
    if not selected:
        return []
    rows = await pool.fetch(
        """select f.accession, f.form, f.filed, f.ticker, f.issuer_name, f.source_url,
                  t.owner_name, t.reporting_owner_cik, t.officer_title, t.is_director,
                  t.is_officer, t.is_ten_percent_owner, t.security_title, t.transaction_date,
                  t.transaction_code, t.shares, t.price_per_share, t.acquired_disposed,
                  t.shares_owned_after, t.direct_indirect, t.derivative
           from public.insider_filing f join public.insider_transaction t using (accession)
           where f.ticker = $1 and f.form = any($2::text[])
           order by coalesce(t.transaction_date, f.filed) desc, f.filed desc, f.accession desc,
                    t.row_index limit $3""",
        ticker.upper(),
        selected,
        limit,
    )
    return [InsiderTransactionOut(**dict(row)) for row in rows]
