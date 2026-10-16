"""Last price and day change of one company from the stored daily bars (the same source rules as the
screens), in major currency units. Missing data is a null with a reason, never an error."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import asyncpg
from pydantic import BaseModel, Field

from steward.company.ratios import market_from
from steward.discovery import read as discovery_read
from steward.discovery.read import UniverseMember, _member

Q4 = Decimal("0.0001")


class Quote(BaseModel):
    last: Decimal | None
    currency: str | None = Field(description="Major unit, e.g. GBP for a pence-quoted listing.")
    as_of: dt.date | None
    previous: Decimal | None
    day_change: Decimal | None = Field(description="Last minus previous close.")
    day_change_pct: Decimal | None = Field(description="Last / previous close - 1, a fraction.")
    reason: str | None = None


def quote_from(closes: list[tuple[dt.date, Decimal]], currency: str | None) -> Quote:
    if not currency:
        return Quote(last=None, currency=None, as_of=None, previous=None, day_change=None,
                     day_change_pct=None, reason="the listing has no currency")  # fmt: skip
    major = market_from(closes, currency, None)
    if not major.closes:
        return Quote(last=None, currency=currency, as_of=None, previous=None, day_change=None,
                     day_change_pct=None, reason="no stored daily prices")  # fmt: skip
    day, last = major.closes[-1]
    if len(major.closes) < 2:
        return Quote(last=last.quantize(Q4), currency=major.currency, as_of=day, previous=None,
                     day_change=None, day_change_pct=None,
                     reason="only one stored close, so no day change")  # fmt: skip
    prev = major.closes[-2][1]
    return Quote(
        last=last.quantize(Q4), currency=major.currency, as_of=day, previous=prev.quantize(Q4),
        day_change=(last - prev).quantize(Q4),
        day_change_pct=((last / prev) - 1).quantize(Q4) if prev > 0 else None,
    )  # fmt: skip


async def load_member(pool: asyncpg.Pool, ticker: str) -> UniverseMember | None:
    row = await pool.fetchrow("select * from public.investment_universe where ticker=$1", ticker)
    return None if row is None else _member(row)


async def load_quote(
    pool: asyncpg.Pool, member: UniverseMember, today: dt.date | None = None
) -> Quote:
    today = today or dt.date.today()
    got = await discovery_read.closes_between(
        pool, [member], today - dt.timedelta(days=21), today
    )
    return quote_from(got.get(member.ticker, []), member.currency)
