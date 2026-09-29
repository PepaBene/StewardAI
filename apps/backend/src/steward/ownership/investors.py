"""Portfolio views of configured institutional Form 13F filers."""

from datetime import date
from decimal import Decimal

import asyncpg
from pydantic import BaseModel

from steward.ownership.read import EFFECTIVE


async def _period_summary(pool: asyncpg.Pool, cik: str, period: date) -> tuple[Decimal, int]:
    row = await pool.fetchrow(
        EFFECTIVE  # noqa: S608 - fixed SQL template; user values are bound below.
        + " select coalesce(sum(h.value_usd),0) as value, count(*) as positions"
        " from effective e join public.institutional_holding h using (accession) where e.cik=$2",
        period,
        cik.zfill(10),
    )
    return row["value"], row["positions"]


class ManagerSummary(BaseModel):
    cik: str
    name: str
    period: date | None = None
    filed: date | None = None
    reported_value_usd: Decimal | None = None
    positions: int = 0
    previous_value_usd: Decimal | None = None
    earlier_period: date | None = None


class ManagerPosition(BaseModel):
    cusip: str
    issuer_name: str
    title_of_class: str
    value_usd: Decimal | None
    shares: Decimal | None
    shares_type: str


class ManagerDetail(ManagerSummary):
    positions_detail: list[ManagerPosition]
    earlier_period: date | None = None
    performance_available: bool = False
    performance_note: str = (
        "13F quarter-end value changes are not investment returns. Historical prices and "
        "complete security mapping are not available to calculate comparable performance."
    )


async def managers(pool: asyncpg.Pool, tracked: list[tuple[str, str]]) -> list[ManagerSummary]:
    if not tracked:
        return []
    rows = await pool.fetch(
        """select distinct on (cik) cik, period, filed from public.form13f_filing
           where cik = any($1::text[]) order by cik, period desc, filed desc""",
        [cik.zfill(10) for cik, _ in tracked],
    )
    by_cik = {row["cik"]: row for row in rows}
    result: list[ManagerSummary] = []
    for cik, name in tracked:
        latest = by_cik.get(cik.zfill(10))
        if latest is None:
            result.append(ManagerSummary(cik=cik.zfill(10), name=name))
            continue
        value, count = await _period_summary(pool, cik, latest["period"])
        previous = await pool.fetchval(
            "select max(period) from public.form13f_filing where cik=$1 and period < $2",
            cik.zfill(10),
            latest["period"],
        )
        previous_value = (await _period_summary(pool, cik, previous))[0] if previous else None
        result.append(
            ManagerSummary(
                cik=cik.zfill(10),
                name=name,
                period=latest["period"],
                filed=latest["filed"],
                reported_value_usd=value,
                positions=count,
                previous_value_usd=previous_value,
                earlier_period=previous,
            )
        )
    return result


async def manager_detail(pool: asyncpg.Pool, cik: str, name: str) -> ManagerDetail | None:
    latest = await pool.fetchrow(
        "select accession, period, filed from public.form13f_filing where cik=$1"
        " order by period desc, filed desc limit 1",
        cik.zfill(10),
    )
    if latest is None:
        return None
    value, count = await _period_summary(pool, cik, latest["period"])
    periods = await pool.fetch(
        "select distinct period from public.form13f_filing where cik=$1 and period < $2"
        " order by period desc limit 1",
        cik.zfill(10),
        latest["period"],
    )
    earlier = periods[0]["period"] if periods else None
    earlier_value = None
    if earlier:
        earlier_value, _ = await _period_summary(pool, cik, earlier)
    positions = await pool.fetch(
        EFFECTIVE  # noqa: S608 - fixed SQL template; user values are bound below.
        + " select h.cusip, max(h.issuer_name) issuer_name, max(h.title_of_class) title_of_class,"
        " sum(h.value_usd) value_usd, sum(h.shares) shares, max(h.shares_type) shares_type"
        " from effective e join public.institutional_holding h using (accession)"
        " where e.cik=$2 and h.shares_type='SH' and h.put_call is null group by h.cusip"
        " order by sum(h.value_usd) desc nulls last limit 50",
        latest["period"],
        cik.zfill(10),
    )
    return ManagerDetail(
        cik=cik.zfill(10),
        name=name,
        period=latest["period"],
        filed=latest["filed"],
        reported_value_usd=value,
        positions=count,
        previous_value_usd=earlier_value,
        earlier_period=earlier,
        positions_detail=[ManagerPosition(**dict(row)) for row in positions],
    )
