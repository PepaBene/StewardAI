"""Income metrics (plan I1.5). Pure: no I/O.

- trailing twelve months received: the Trading212 dividend ledger rows of the last 365 days, in
  the account currency, the same rows the Performance returns table sums;
- next twelve months: declared plus estimated schedule rows (unconfirmed rows are not counted);
- yield on price: next twelve months per share / today's price, in the dividend's currency;
- yield on cost: next twelve months income / the open lots' cost, both in the account currency;
- dividend growth: compound growth of calendar-year dividends per share (regular payments only)
  over 5 and 10 complete years, when every year in between has a payment in one currency;
- years without a cut: consecutive complete calendar years whose dividend per share did not fall;
- contributors: each holding's share of the last twelve months received;
- income by month: 36 months received and 12 months forecast.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from steward.dividends.model import ZERO, HoldingSchedule, PaymentPoint, ReceivedPayment

CUT_TOLERANCE = Decimal("0.01")  # a fall of 1 % or less is rounding, not a cut


def month_key(day: dt.date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def months_back(today: dt.date, n: int) -> list[str]:
    y, m = today.year, today.month
    out: list[str] = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def months_ahead(today: dt.date, n: int) -> list[str]:
    y, m = today.year, today.month
    out: list[str] = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def trailing_received(payments: Iterable[ReceivedPayment], today: dt.date) -> Decimal:
    start = today - dt.timedelta(days=365)
    return sum((p.amount for p in payments if start < p.paid_on <= today), ZERO)


def received_by_month(
    payments: Iterable[ReceivedPayment], today: dt.date, months: int = 36
) -> dict[str, Decimal]:
    keys = months_back(today, months)
    out = {k: ZERO for k in keys}
    for p in payments:
        k = month_key(p.paid_on)
        if k in out:
            out[k] += p.amount
    return out


def forecast_by_month(
    schedules: Iterable[HoldingSchedule], today: dt.date, months: int = 12
) -> dict[str, dict[str, Decimal]]:
    """Month -> {declared, estimated} gross in the account currency."""
    keys = months_ahead(today, months + 1)  # the current month and the next twelve
    out: dict[str, dict[str, Decimal]] = {k: {"declared": ZERO, "estimated": ZERO} for k in keys}
    for s in schedules:
        for r in s.rows:
            if r.kind == "unconfirmed" or r.amount is None:
                continue
            k = month_key(r.pay_date)
            if k in out:
                out[k][r.kind] += r.amount
    return out


def annual_dps(points: Sequence[PaymentPoint]) -> tuple[dict[int, Decimal], str | None]:
    """Calendar-year regular dividends per share (by pay date), and the problem when the
    currencies are mixed."""
    regular = [p for p in points if not p.special]
    currencies = {p.currency for p in regular if p.currency}
    if len(currencies) > 1:
        return {}, f"payments are in several currencies ({', '.join(sorted(currencies))})"
    out: dict[int, Decimal] = defaultdict(lambda: ZERO)
    counts: dict[int, int] = defaultdict(int)
    for p in regular:
        out[p.pay_date.year] += p.per_share
        counts[p.pay_date.year] += 1
    # The history usually starts mid-year: a first year with fewer payments than the years
    # after it is part of a year, not a smaller dividend (Apple's 2016 is one payment of four).
    years = sorted(counts)
    if len(years) >= 3:
        usual = sorted(counts[y] for y in years[1:-1])[len(years[1:-1]) // 2]
        if counts[years[0]] < usual:
            del out[years[0]]
    return dict(out), None


def dps_cagr(
    annual: dict[int, Decimal], years: int, today: dt.date
) -> tuple[Decimal | None, str | None]:
    last = today.year - 1
    first = last - years
    if first not in annual or last not in annual:
        return None, f"needs dividends in {first} and {last}"
    missing = [y for y in range(first, last + 1) if y not in annual]
    if missing:
        return None, f"no payment on record in {', '.join(map(str, missing[:3]))}"
    a, b = annual[first], annual[last]
    if a <= 0 or b <= 0:
        return None, "a year with no positive dividend"
    return Decimal(str(float(b / a) ** (1 / years) - 1)), None


def years_without_cut(annual: dict[int, Decimal], today: dt.date) -> int | None:
    """Consecutive complete years, ending last year, without a fall in dividends per share.
    None when last year has no payment on record."""
    year = today.year - 1
    if year not in annual:
        return None
    n = 0
    while year - 1 in annual and annual[year] >= annual[year - 1] * (Decimal(1) - CUT_TOLERANCE):
        n += 1
        year -= 1
    return n


@dataclass(frozen=True)
class Contributor:
    ticker: str
    amount: Decimal
    share: Decimal | None


def contributors(payments: Iterable[ReceivedPayment], today: dt.date) -> list[Contributor]:
    start = today - dt.timedelta(days=365)
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for p in payments:
        if start < p.paid_on <= today:
            totals[p.ticker] += p.amount
    whole = sum(totals.values(), ZERO)
    rows = [Contributor(t, a, (a / whole) if whole > 0 else None) for t, a in totals.items()]
    return sorted(rows, key=lambda c: (-c.amount, c.ticker))


def ratio(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    if num is None or den is None or den <= 0:
        return None
    return num / den
