"""Portfolio views of configured institutional Form 13F filers."""

from datetime import date
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.ownership.read import EFFECTIVE

# Only quarters whose rows were read: a filing on record with none stored (the dataset load
# timed out or has not run) would read as an empty book and a fall to zero.
_READ = " and exists (select 1 from public.institutional_holding h where h.accession = f.accession)"


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
    reported_value_usd: Decimal | None = Field(
        default=None, description="Value of the manager's positions in the portfolio's holdings"
    )
    positions: int = Field(default=0, description="How many of the portfolio's holdings it owns")
    book_positions: int | None = Field(
        default=None, description="Lines in the whole 13F filing, when the filing states it"
    )
    book_value_usd: Decimal | None = Field(
        default=None, description="Total value of the whole 13F filing, when it states it"
    )
    previous_value_usd: Decimal | None = None
    earlier_period: date | None = None


PositionChange = Literal["new", "added", "trimmed", "unchanged", "sold", "unknown"]


class ManagerPosition(BaseModel):
    cusip: str
    issuer_name: str
    title_of_class: str
    value_usd: Decimal | None
    shares: Decimal | None
    shares_type: str
    previous_shares: Decimal | None = Field(
        default=None, description="Shares at the earlier quarter end; null when not held then."
    )
    previous_value_usd: Decimal | None = None
    change: PositionChange = "unknown"
    share_change_pct: float | None = Field(
        default=None, description="Shares now / shares then - 1; null for new or sold positions."
    )


def classify_change(
    shares: Decimal | None, previous: Decimal | None, *, has_earlier: bool
) -> tuple[PositionChange, float | None]:
    """How a position moved between two quarter ends, by share count (not value)."""
    if not has_earlier:
        return "unknown", None
    now = shares or Decimal(0)
    then = previous or Decimal(0)
    if then <= 0 and now > 0:
        return "new", None
    if now <= 0 and then > 0:
        return "sold", None
    if then <= 0:
        return "unknown", None
    pct = float(now / then - 1)
    if abs(pct) < 0.005:
        return "unchanged", 0.0
    return ("added" if pct > 0 else "trimmed"), pct


class ManagerDetail(ManagerSummary):
    positions_detail: list[ManagerPosition]
    sold_out: list[ManagerPosition] = Field(
        default_factory=list,
        description="Largest positions held at the earlier quarter end and gone at the latest.",
    )
    earlier_period: date | None = None
    performance_available: bool = False
    performance_note: str = (
        "13F quarter-end value changes are not investment returns. Historical prices and "
        "complete security mapping are not available to calculate comparable performance."
    )


async def _earlier_period(pool: asyncpg.Pool, cik: str, period: date) -> date | None:
    """The quarter before `period` whose rows were read, to compare with."""
    found: date | None = await pool.fetchval(
        "select max(period) from public.form13f_filing f where cik=$1 and period < $2" + _READ,  # noqa: S608 - fixed SQL
        cik.zfill(10),
        period,
    )
    return found


async def managers(pool: asyncpg.Pool, tracked: list[tuple[str, str]]) -> list[ManagerSummary]:
    if not tracked:
        return []
    rows = await pool.fetch(
        "select distinct on (cik) cik, period, filed, reported_line_count,"  # noqa: S608 - fixed SQL
        " reported_total_value_usd from public.form13f_filing f where cik = any($1::text[])"
        + _READ
        + " order by cik, period desc, filed desc",
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
        previous = await _earlier_period(pool, cik, latest["period"])
        previous_value = (await _period_summary(pool, cik, previous))[0] if previous else None
        result.append(
            ManagerSummary(
                cik=cik.zfill(10),
                name=name,
                period=latest["period"],
                filed=latest["filed"],
                reported_value_usd=value,
                positions=count,
                book_positions=latest["reported_line_count"],
                book_value_usd=latest["reported_total_value_usd"],
                previous_value_usd=previous_value,
                earlier_period=previous,
            )
        )
    return result


async def manager_detail(pool: asyncpg.Pool, cik: str, name: str) -> ManagerDetail | None:
    latest = await pool.fetchrow(
        "select accession, period, filed, reported_line_count, reported_total_value_usd"  # noqa: S608 - fixed SQL
        " from public.form13f_filing f where cik=$1" + _READ + " order by period desc, filed desc"
        " limit 1",
        cik.zfill(10),
    )
    if latest is None:
        return None
    value, count = await _period_summary(pool, cik, latest["period"])
    earlier = await _earlier_period(pool, cik, latest["period"])
    earlier_value = None
    if earlier:
        earlier_value, _ = await _period_summary(pool, cik, earlier)
    positions = await pool.fetch(_POSITIONS + " limit 50", latest["period"], cik.zfill(10))
    earlier_rows = await pool.fetch(_POSITIONS, earlier, cik.zfill(10)) if earlier else []
    then = {row["cusip"]: row for row in earlier_rows}
    detail: list[ManagerPosition] = []
    for row in positions:
        prev = then.get(row["cusip"])
        change, pct = classify_change(
            row["shares"], prev["shares"] if prev else None, has_earlier=earlier is not None
        )
        detail.append(
            ManagerPosition(
                **dict(row),
                previous_shares=prev["shares"] if prev else None,
                previous_value_usd=prev["value_usd"] if prev else None,
                change=change,
                share_change_pct=pct,
            )
        )
    current = {row["cusip"] for row in await pool.fetch(_CUSIPS, latest["period"], cik.zfill(10))}
    sold_out = [
        ManagerPosition(
            **{**dict(row), "value_usd": None, "shares": None},
            previous_shares=row["shares"],
            previous_value_usd=row["value_usd"],
            change="sold",
        )
        for row in earlier_rows
        if row["cusip"] not in current
    ][:10]
    return ManagerDetail(
        cik=cik.zfill(10),
        name=name,
        period=latest["period"],
        filed=latest["filed"],
        reported_value_usd=value,
        positions=count,
        book_positions=latest["reported_line_count"],
        book_value_usd=latest["reported_total_value_usd"],
        previous_value_usd=earlier_value,
        earlier_period=earlier,
        positions_detail=detail,
        sold_out=sold_out,
    )


class HolderMove(BaseModel):
    cik: str
    name: str
    shares: Decimal | None = Field(description="Shares at the latest quarter end; null if sold.")
    value_usd: Decimal | None = None
    previous_shares: Decimal | None = None
    change: PositionChange
    share_change_pct: float | None = None


class HoldingHolders(BaseModel):
    ticker: str
    name: str | None = None
    weight: float | None = Field(default=None, description="Share of the portfolio, 0-1.")
    holders: int = Field(description="13F filers with a share position at the latest quarter end")
    previous_holders: int | None = None
    new_holders: int | None = None
    exited_holders: int | None = None
    value_usd: Decimal = Field(description="Value of all 13F filers' positions")
    shares_change_pct: float | None = Field(
        default=None, description="All filers' shares now / then - 1."
    )
    tracked: list[HolderMove] = Field(
        default_factory=list,
        description="Tracked managers holding it now, or holding it at the earlier quarter end.",
    )


class InvestorsByHolding(BaseModel):
    period: date | None = None
    previous_period: date | None = None
    holdings: list[HoldingHolders] = Field(default_factory=list)
    tracked: list[str] = Field(default_factory=list, description="Names of tracked managers.")
    uncovered: list[str] = Field(
        default_factory=list,
        description="Held tickers with no 13F position on record (non-US listings, UCITS funds).",
    )


# A quarter compares with an earlier one only when that one was read about as fully: a dataset
# that stored a sliver of its rows would turn most holders into "new".
_COMPARABLE_SHARE = 0.5


async def _comparison_periods(pool: asyncpg.Pool) -> tuple[date | None, date | None]:
    rows = await pool.fetch(
        "select f.period, count(*) n from public.form13f_filing f"
        " join public.institutional_holding h using (accession)"
        " group by f.period order by f.period desc limit 12"
    )
    if not rows:
        return None, None
    latest = rows[0]
    earlier = next((r for r in rows[1:] if r["n"] >= latest["n"] * _COMPARABLE_SHARE), None)
    return latest["period"], earlier["period"] if earlier else None


# Each filer's effective share positions per portfolio ticker at two quarter ends ($1 now,
# $2 earlier), by the same amendment rules as `EFFECTIVE`, joined so moves can be counted.
_BY_HOLDING = """
with f as (
  select * from public.form13f_filing where period = any(array[$1::date, $2::date])
), base as (
  select distinct on (cik, period) accession, cik, period, filed from f
  where submission_type = '13F-HR' or coalesce(amendment_type, '') <> 'NEW HOLDINGS'
  order by cik, period, filed desc, accession desc
), adds as (
  select a.accession, a.cik, a.period from f a
  left join base b on b.cik = a.cik and b.period = a.period
  where a.submission_type = '13F-HR/A' and a.amendment_type = 'NEW HOLDINGS'
    and (b.accession is null or (a.filed, a.accession) > (b.filed, b.accession))
), effective as (
  select accession, cik, period from base union select accession, cik, period from adds
), pos as (
  select e.period, h.ticker, e.cik, sum(h.shares) shares, sum(h.value_usd) value
  from effective e join public.institutional_holding h using (accession)
  where h.shares_type = 'SH' and h.put_call is null and h.ticker = any($3::text[])
  group by 1, 2, 3
), j as (
  select coalesce(n.ticker, p.ticker) ticker, coalesce(n.cik, p.cik) cik,
    n.shares, n.value, p.shares pshares
  from (select * from pos where period = $1) n
  full join (select * from pos where period = $2) p on p.ticker = n.ticker and p.cik = n.cik
)
"""


async def by_holding(pool: asyncpg.Pool, tracked: list[tuple[str, str]]) -> InvestorsByHolding:
    """Who owns each current holding at the latest 13F quarter end, how that moved against the
    earlier comparable quarter, and which tracked managers hold, added, trimmed or sold it."""
    period, earlier = await _comparison_periods(pool)
    names = {cik.zfill(10): name for cik, name in tracked}
    out = InvestorsByHolding(period=period, previous_period=earlier, tracked=list(names.values()))
    if period is None:
        return out
    held = await pool.fetch(
        "select p.ticker, p.weight, i.name from public.position_snapshot p"
        " left join public.instrument i on i.t212_ticker = p.ticker"
        " where p.snapshot_date = (select max(snapshot_date) from public.position_snapshot)"
    )
    tickers = [r["ticker"] for r in held]
    # Without a comparable earlier quarter the same date stands in, so every "previous" is
    # the current figure; the counts below are then dropped rather than shown as zero moves.
    then = earlier or period
    totals = await pool.fetch(
        _BY_HOLDING  # noqa: S608 - fixed SQL template; user values are bound below.
        + " select ticker, count(shares) holders, count(pshares) previous_holders,"
        " count(*) filter (where shares is not null and pshares is null) new_holders,"
        " count(*) filter (where shares is null and pshares is not null) exited_holders,"
        " coalesce(sum(value), 0) value, sum(shares) shares,"
        " sum(pshares) filter (where shares is not null or pshares is not null) pshares"
        " from j group by ticker",
        period,
        then,
        tickers,
    )
    moves = await pool.fetch(
        _BY_HOLDING  # noqa: S608 - fixed SQL template; user values are bound below.
        + " select ticker, cik, shares, value, pshares from j where cik = any($4::text[])",
        period,
        then,
        tickers,
        list(names),
    )
    by_ticker: dict[str, list[HolderMove]] = {}
    for m in moves:
        change, pct = classify_change(m["shares"], m["pshares"], has_earlier=earlier is not None)
        by_ticker.setdefault(m["ticker"], []).append(
            HolderMove(
                cik=m["cik"],
                name=names[m["cik"]],
                shares=m["shares"],
                value_usd=m["value"],
                previous_shares=m["pshares"] if earlier else None,
                change=change,
                share_change_pct=pct,
            )
        )
    info = {r["ticker"]: r for r in held}
    for t in totals:
        compared = earlier is not None
        then_shares = t["pshares"]
        out.holdings.append(
            HoldingHolders(
                ticker=t["ticker"],
                name=info[t["ticker"]]["name"],
                weight=float(info[t["ticker"]]["weight"])
                if info[t["ticker"]]["weight"] is not None
                else None,
                holders=t["holders"],
                previous_holders=t["previous_holders"] if compared else None,
                new_holders=t["new_holders"] if compared else None,
                exited_holders=t["exited_holders"] if compared else None,
                value_usd=t["value"],
                shares_change_pct=float(t["shares"] / then_shares - 1)
                if compared and t["shares"] and then_shares
                else None,
                tracked=sorted(
                    by_ticker.get(t["ticker"], []),
                    key=lambda m: -(m.value_usd or 0),
                ),
            )
        )
    out.holdings.sort(key=lambda h: -(h.weight or 0))
    covered = {h.ticker for h in out.holdings}
    out.uncovered = [
        r["ticker"]
        for r in sorted(held, key=lambda r: -(r["weight"] or 0))
        if r["ticker"] not in covered
    ]
    return out


# Common-stock positions of one manager at one quarter end, one row per CUSIP, largest first.
_POSITIONS = (
    EFFECTIVE  # noqa: S608 - fixed SQL template; user values are bound by the caller.
    + " select h.cusip, max(h.issuer_name) issuer_name, max(h.title_of_class) title_of_class,"
    " sum(h.value_usd) value_usd, sum(h.shares) shares, max(h.shares_type) shares_type"
    " from effective e join public.institutional_holding h using (accession)"
    " where e.cik=$2 and h.shares_type='SH' and h.put_call is null group by h.cusip"
    " order by sum(h.value_usd) desc nulls last"
)
# Every CUSIP held at a quarter end, so "sold out" never means "fell off the top 50".
_CUSIPS = (
    EFFECTIVE  # noqa: S608 - fixed SQL template; user values are bound by the caller.
    + " select distinct h.cusip from effective e join public.institutional_holding h"
    " using (accession) where e.cik=$2 and h.shares_type='SH' and h.put_call is null"
)
