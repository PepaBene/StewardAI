"""Return attribution and contribution (plan M4.6): where the portfolio's money gain came from,
reconciled to the total with a disclosed residual.

Pure: no I/O. Over each pair of consecutive valuation days, per holding, in the base currency:

    price effect = (q1*p1 - q0*p0 - sum(q_f*p_f)) * fx1     units and prices in the listing's
                                                             own currency, fills q_f at p_f
    FX effect    = q0 * p0 * (fx1 - fx0)                     opening local value x rate change

so `price + FX` is exactly the holding's change in base value net of trading. Dividends are
income, fill fees and cash fees are costs. The window total is the sum of the daily intervals
and the bridge is `gain = end - start - external flows`, with `residual = gain - sum(components)`
shown, never folded in: the residual holds what the stored data cannot assign (interest on
cash, the translation of cash and trade cash between the account and base currency,
Trading212's own rounding).

A holding whose quantity change in an interval is not explained by stored fills (a split, a
missing fill) cannot be attributed. It is left out of price and FX, its end value is reported
as unattributed, and the bridge is marked partial with that market-value share.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise

from steward.analytics.returns import Bridge, attribution_bridge

ZERO = Decimal(0)
QUANTITY_TOLERANCE = Decimal("0.000001")
COMPONENTS = ("price", "income", "fx", "fees")


@dataclass(frozen=True)
class PositionDay:
    day: dt.date
    ticker: str
    quantity: Decimal
    price: Decimal  # listing currency, the unit fills use
    fx: Decimal  # base-currency units per one unit of the listing currency
    value_base: Decimal


FxLookup = Callable[[str, dt.date], Decimal | None]


def ticker_fx_lookup(positions: Iterable[PositionDay]) -> FxLookup:
    """The base rate of a holding's own listing currency on the nearest snapshot day it appears,
    earlier days first on a tie; None for a ticker that never appears."""
    by_ticker: dict[str, list[tuple[dt.date, Decimal]]] = defaultdict(list)
    for p in positions:
        by_ticker[p.ticker].append((p.day, p.fx))

    def lookup(ticker: str, day: dt.date) -> Decimal | None:
        rows = by_ticker.get(ticker)
        if not rows:
            return None
        return min(rows, key=lambda r: (abs((r[0] - day).days), r[0] > day))[1]

    return lookup


@dataclass(frozen=True)
class Trade:
    day: dt.date
    ticker: str
    quantity: Decimal  # signed: bought positive
    price: Decimal


@dataclass(frozen=True)
class Amount:
    """Income or a cost in the base currency, dated, optionally tied to one holding."""

    day: dt.date
    value: Decimal  # positive
    ticker: str | None = None


@dataclass(frozen=True)
class AccountDay:
    day: dt.date
    total_base: Decimal
    flow_base: Decimal = ZERO


@dataclass
class HoldingContribution:
    ticker: str
    price: Decimal = ZERO
    fx: Decimal = ZERO
    income: Decimal = ZERO
    fees: Decimal = ZERO  # negative
    attributed: bool = True

    @property
    def total(self) -> Decimal:
        return self.price + self.fx + self.income + self.fees


@dataclass(frozen=True)
class RelativeContribution:
    ticker: str
    benchmark_equivalent: Decimal | None  # its opening value's gain, had it been in the benchmark
    active: Decimal | None  # its contribution minus that
    reason: str | None = None


def benchmark_relative(
    holdings: Iterable[HoldingContribution],
    opening_values: Mapping[str, Decimal],
    benchmark_return: Decimal,
) -> list[RelativeContribution]:
    """Each holding's contribution against the same opening money placed in the benchmark:
    `active = total - opening value x benchmark return`. Both are money in the base currency, so
    the actives add up to the portfolio's gain on its opening holdings less what the benchmark
    would have made on them. A holding not held at the start has no opening money to compare
    (its buys are a decision, not a position), and a holding the stored data cannot attribute is
    left out; both say why."""
    out: list[RelativeContribution] = []
    for h in holdings:
        if not h.attributed:
            out.append(RelativeContribution(h.ticker, None, None, "not attributed"))
            continue
        opening = opening_values.get(h.ticker, ZERO)
        if opening <= 0:
            out.append(RelativeContribution(h.ticker, None, None, "not held at the start"))
            continue
        equivalent = opening * benchmark_return
        out.append(RelativeContribution(h.ticker, equivalent, h.total - equivalent))
    return out


@dataclass
class AttributionReport:
    start: dt.date
    end: dt.date
    bridge: Bridge
    holdings: list[HoldingContribution]
    start_value: Decimal
    end_value: Decimal
    net_external_flows: Decimal
    net_trading: Decimal  # buys minus sales at fill value: money moved into holdings, not a gain
    unattributed_tickers: list[str] = field(default_factory=list)
    intervals: int = 0


def attribute(
    account_days: Sequence[AccountDay],
    positions: Iterable[PositionDay],
    trades: Iterable[Trade],
    income: Iterable[Amount],
    costs: Iterable[Amount],
    fx_fallback: FxLookup | None = None,
) -> AttributionReport | None:
    """None with fewer than two valuation days.

    `fx_fallback` gives a holding's own base rate when it has no snapshot row at either end of an
    interval (bought and sold in between). Without a rate the holding is left unattributed: its
    fills cannot be valued, and a zero rate would silently lose its gain to the residual."""
    days = sorted(account_days, key=lambda d: d.day)
    if len(days) < 2:
        return None
    by_day: dict[dt.date, dict[str, PositionDay]] = defaultdict(dict)
    for p in positions:
        by_day[p.day][p.ticker] = p
    trade_list = sorted(trades, key=lambda t: t.day)
    income_list, cost_list = list(income), list(costs)
    contributions: dict[str, HoldingContribution] = {}
    unattributed: set[str] = set()
    price_total = fx_total = income_total = fee_total = net_trading = ZERO

    def holding(ticker: str) -> HoldingContribution:
        return contributions.setdefault(ticker, HoldingContribution(ticker))

    for before, after in pairwise(days):
        opened = by_day.get(before.day, {})
        closed = by_day.get(after.day, {})
        interval_trades: dict[str, list[Trade]] = defaultdict(list)
        for t in trade_list:
            if before.day < t.day <= after.day:
                interval_trades[t.ticker].append(t)
        for ticker in set(opened) | set(closed) | set(interval_trades):
            p0, p1 = opened.get(ticker), closed.get(ticker)
            q0 = p0.quantity if p0 else ZERO
            q1 = p1.quantity if p1 else ZERO
            fills = interval_trades.get(ticker, [])
            traded_q = sum((t.quantity for t in fills), ZERO)
            if abs(q1 - q0 - traded_q) > QUANTITY_TOLERANCE:
                unattributed.add(ticker)
                holding(ticker).attributed = False
                continue
            if p0 is None and p1 is None:
                rate = fx_fallback(ticker, after.day) if fx_fallback else None
                if rate is None:
                    unattributed.add(ticker)
                    holding(ticker).attributed = False
                    continue
                fx0 = fx1 = rate
            else:
                fx0 = p0.fx if p0 else (p1.fx if p1 else ZERO)
                fx1 = p1.fx if p1 else fx0
            local_open = q0 * p0.price if p0 else ZERO
            local_close = q1 * p1.price if p1 else ZERO
            fill_local = sum((t.quantity * t.price for t in fills), ZERO)
            price_effect = (local_close - local_open - fill_local) * fx1
            fx_effect = local_open * (fx1 - fx0)
            c = holding(ticker)
            c.price += price_effect
            c.fx += fx_effect
            price_total += price_effect
            fx_total += fx_effect
            net_trading += fill_local * fx1
        for amount in income_list:
            if before.day < amount.day <= after.day:
                income_total += amount.value
                if amount.ticker:
                    holding(amount.ticker).income += amount.value
        for amount in cost_list:
            if before.day < amount.day <= after.day:
                fee_total += amount.value
                if amount.ticker:
                    holding(amount.ticker).fees -= amount.value
    start, end = days[0], days[-1]
    flows = sum((d.flow_base for d in days[1:]), ZERO)
    end_positions = by_day.get(end.day, {})
    unattributed_value = sum(
        (end_positions[t].value_base for t in unattributed if t in end_positions), ZERO
    )
    bridge = attribution_bridge(
        start.total_base,
        end.total_base,
        flows,
        {"price": price_total, "income": income_total, "fx": fx_total, "fees": -fee_total},
        unattributed_value=unattributed_value,
        unattributed_count=len(unattributed),
    )
    return AttributionReport(
        start=start.day,
        end=end.day,
        bridge=bridge,
        holdings=sorted(contributions.values(), key=lambda c: c.total, reverse=True),
        start_value=start.total_base,
        end_value=end.total_base,
        net_external_flows=flows,
        net_trading=net_trading,
        unattributed_tickers=sorted(unattributed),
        intervals=len(days) - 1,
    )


def group_contributions(
    holdings: Iterable[HoldingContribution], buckets: Mapping[str, str], unknown: str = "Unknown"
) -> dict[str, Decimal]:
    """Contribution per bucket (sector, country, currency, ...). A holding with no bucket goes to
    `unknown`, shown as its own line so the groups still add up to the holdings' total."""
    out: dict[str, Decimal] = defaultdict(Decimal)
    for h in holdings:
        out[buckets.get(h.ticker) or unknown] += h.total
    return dict(out)
