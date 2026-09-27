"""Turning daily account snapshots into a flow-adjusted performance index.

Pure: no I/O. `account_snapshot.total_base` includes new deposits and withdrawals, so
indexing it directly would overstate or understate performance every time money moves in
or out — a deposit would show up as a fake gain, a withdrawal as a fake loss. This module
removes that effect: each day's return is computed on the value net of that day's external
cash flow, then the returns are compounded from 100. A pure fee or dividend is not an
external flow (it is part of investment return) and is never excluded here; only the
DEPOSIT/WITHDRAW/TRANSFER Trading212 transaction types are.
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
    100 on the first day. A day whose prior total_base is zero or negative (nothing to
    measure a return against yet, e.g. the day money first arrived) carries the index
    forward unchanged rather than dividing by zero."""
    if not days:
        return []
    index = Decimal(100)
    points = [(days[0].date, float(index))]
    for prev, cur in pairwise(days):
        if prev.total_base > 0:
            daily_return = (cur.total_base - cur.flow_base) / prev.total_base - 1
            index *= 1 + daily_return
        points.append((cur.date, float(index)))
    return points
