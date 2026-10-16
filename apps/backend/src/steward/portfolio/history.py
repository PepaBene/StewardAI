"""Turning daily account and position snapshots into flow-adjusted figures.

Pure: no I/O. `account_snapshot.total_base` includes new deposits and withdrawals, so
indexing it directly would overstate or understate performance every time money moves in
or out — a deposit would show up as a fake gain, a withdrawal as a fake loss. This module
removes that effect: each day's return is computed on the value net of that day's external
cash flow, then the returns are compounded from 100. A pure fee or dividend is not an
external flow (it is part of investment return) and is never excluded here; only the
DEPOSIT/WITHDRAW/TRANSFER Trading212 transaction types are.

The same problem, one level down: `position_snapshot.value_base` moves when the owner
trades the position, not just when the market does. `position_organic_gain` nets that out
per holding, so holdings can be ranked by their actual contribution to the portfolio's
return (visualization §5.4) rather than by how much was bought or sold.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise


@dataclass(frozen=True)
class DailyValue:
    date: dt.date
    total_base: Decimal
    # Net external cash flow that landed on this day, already converted to base currency.
    # Positive: net deposit. Negative: net withdrawal. Zero on a day with none.
    flow_base: Decimal


def flow_adjusted_index(days: list[DailyValue]) -> list[tuple[dt.date, float]]:
    """One point per day, in the order given (must already be sorted by date), indexed to
    100 on the first day. A day's flow counts as arriving at the start of its period, so
    it is part of the base the period's gain is measured against: a deposit is usually
    invested before the market moves (a weekend deposit always is), and measuring that
    move against the old, smaller base turns a CZK 50 gain on CZK 10,109 into +46% on
    CZK 109. A day whose base (prior total_base plus the flow) is zero or negative
    (nothing to measure a return against yet, e.g. the day money first arrived) carries
    the index forward unchanged rather than dividing by zero."""
    if not days:
        return []
    index = Decimal(100)
    points = [(days[0].date, float(index))]
    for prev, cur in pairwise(days):
        base = prev.total_base + cur.flow_base
        if prev.total_base > 0 and base > 0:
            daily_return = (cur.total_base - cur.flow_base - prev.total_base) / base
            index *= 1 + daily_return
        points.append((cur.date, float(index)))
    return points


@dataclass(frozen=True)
class PositionDailyValue:
    date: dt.date
    value_base: Decimal
    # quantity * avg_price, converted to base currency that day (position_snapshot's own
    # fx_rate) — the position's own cost basis, which a trade moves and organic price
    # movement never does.
    cost_base: Decimal


def position_organic_gain(days: list[PositionDailyValue]) -> Decimal:
    """A holding's value change over the period with its own trading netted out, in base
    currency — the same principle as flow_adjusted_index (a deposit must not read as a
    gain), applied per position instead of per account, and returning the absolute organic
    gain rather than an index so holdings can be ranked by their contribution to the
    portfolio's return. A change in cost_base between two days is money the owner put into
    or took out of the position by trading it, not the market moving it, and is netted out;
    day 0's own cost_base never enters the total, the same way flow_adjusted_index never
    reads day 0's own flow.

    This only sees the days a position actually has a snapshot row for; a position closed
    out entirely during the period stops contributing at its last held day, not at the sale
    (realized proceeds from a full exit are not visible here) — a known limit of reading
    daily position snapshots rather than the trade log itself.
    """
    if len(days) < 2:
        return Decimal(0)
    traded = sum((cur.cost_base - prev.cost_base for prev, cur in pairwise(days)), Decimal(0))
    return days[-1].value_base - days[0].value_base - traded
