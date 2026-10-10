"""Historical screen test over frozen rebalance universes, with explicit transaction costs
(plan M1.7).

Pure `Decimal` code. The caller supplies, per rebalance date, the names a screen picked as of
that date (built only from the universe and facts known then) and the stored price series. This
module holds the portfolio arithmetic:

* equal weight across the names that have an entry price on the rebalance date;
* a name with no price at the rebalance is dropped and counted, never valued at zero;
* a name whose series ends before the period does is held at its last price and flagged: the
  delisting outcome is unknown, so the result says so rather than assuming a loss or a gain;
* each rebalance pays `cost_bps` on the traded fraction of the portfolio (`sum |w_new - w_drift|`,
  the first buy-in counting as 1.0), so a result is always shown net of an explicit assumption;
* `FrozenBacktestPlan.assert_point_in_time` rejects a plan whose universe or facts postdate the
  rebalance, and `survivorship_warning` flags a plan that never held a name that later left.

Nothing here is a forecast; it describes what the rule would have done with the stored data.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from steward.analytics.contract import input_hash, rounded
from steward.discovery.universe import (
    FrozenBacktestPlan,
    UniverseMember,
    survivorship_warning,
)

ONE = Decimal(1)
ZERO = Decimal(0)
BPS = Decimal(10000)
DAYS_PER_YEAR = Decimal("365.25")
MAX_ENTRY_GAP_DAYS = 10  # an entry or exit price older than this is not "the price on that date"

Closes = Sequence[tuple[dt.date, Decimal]]


@dataclass
class NameResult:
    ticker: str
    entry: Decimal
    exit: Decimal
    return_: Decimal
    weight: Decimal
    ended_early: bool  # series stopped before the period did: held at the last price


@dataclass
class PeriodResult:
    start: dt.date
    end: dt.date
    picked: list[str]
    held: list[NameResult]
    dropped_no_price: list[str]
    gross_return: Decimal | None
    turnover: Decimal
    cost: Decimal
    net_return: Decimal | None
    benchmark_return: Decimal | None = None
    note: str | None = None


@dataclass
class BacktestResult:
    periods: list[PeriodResult]
    cumulative_net: Decimal | None
    cumulative_gross: Decimal | None
    cost_drag: Decimal | None  # gross cumulative minus net cumulative
    benchmark_cumulative: Decimal | None
    annualised_net: Decimal | None
    cost_bps: int
    warnings: list[str] = field(default_factory=list)
    fingerprint: str = ""
    method_version: str = "1.0.0"


def _price_on(closes: Closes, day: dt.date) -> tuple[dt.date, Decimal] | None:
    """Last close on or before `day`, only when it is recent enough to stand for that date."""
    best: tuple[dt.date, Decimal] | None = None
    for d, p in closes:
        if d <= day and (best is None or d > best[0]):
            best = (d, p)
    if best is None or (day - best[0]).days > MAX_ENTRY_GAP_DAYS or best[1] <= 0:
        return None
    return best


def _fill_price(closes: Closes, day: dt.date) -> tuple[dt.date, Decimal] | None:
    """First close after `day`: the signal is read from `day`'s close, so the trade fills at the
    next session's, never at the price the signal was computed from."""
    best: tuple[dt.date, Decimal] | None = None
    for d, p in closes:
        if d > day and (best is None or d < best[0]):
            best = (d, p)
    if best is None or (best[0] - day).days > MAX_ENTRY_GAP_DAYS or best[1] <= 0:
        return None
    return best


def _last_price(closes: Closes, day: dt.date) -> tuple[dt.date, Decimal] | None:
    """Last close on or before `day` with no recency limit (a delisted name's final price)."""
    best: tuple[dt.date, Decimal] | None = None
    for d, p in closes:
        if d <= day and p > 0 and (best is None or d > best[0]):
            best = (d, p)
    return best


def _equal_weights(names: Sequence[str]) -> dict[str, Decimal]:
    return {n: ONE / Decimal(len(names)) for n in names} if names else {}


def run_backtest(
    plan: FrozenBacktestPlan,
    picks: Mapping[dt.date, Sequence[str]],
    closes: Mapping[str, Closes],
    end: dt.date,
    *,
    benchmark: Closes | None = None,
    current_members: Sequence[UniverseMember] = (),
    basis: str = "stored adjusted closes",
) -> BacktestResult:
    plan.assert_point_in_time()
    slices = sorted(plan.slices, key=lambda s: s.rebalance_date)
    warnings: list[str] = []
    if not slices:
        return BacktestResult(
            [],
            None,
            None,
            None,
            None,
            None,
            plan.transaction_cost_bps,
            ["no rebalance dates"],
            plan.fingerprint,
        )
    for s in slices:
        outside = sorted(set(picks.get(s.rebalance_date, ())) - set(s.universe.tickers()))
        if outside:
            raise ValueError(
                f"{s.rebalance_date}: picked names outside the frozen universe: "
                f"{', '.join(outside)}"
            )
    periods: list[PeriodResult] = []
    drifted: dict[str, Decimal] = {}
    net_chain = gross_chain = bench_chain = ONE
    any_net = False
    bench_known = benchmark is not None
    for i, s in enumerate(slices):
        p_start = s.rebalance_date
        p_end = slices[i + 1].rebalance_date if i + 1 < len(slices) else end
        if p_end <= p_start:
            warnings.append(f"{p_start}: the period ends on or before it starts and was skipped")
            continue
        # A period that ends at the next rebalance exits at the same next-session close the
        # following period enters at; the last one ends on the end date's own close.
        exit_price = _price_on if p_end == end else _fill_price
        picked = sorted(set(picks.get(p_start, ())))
        entries: dict[str, Decimal] = {}
        dropped: list[str] = []
        for t in picked:
            quote = _fill_price(closes.get(t, ()), p_start)
            if quote is None:
                dropped.append(t)
            else:
                entries[t] = quote[1]
        names = sorted(entries)
        weights = _equal_weights(names)
        held: list[NameResult] = []
        gross: Decimal | None = None
        note = None
        if names:
            total = ZERO
            for t in names:
                exit_quote = exit_price(closes.get(t, ()), p_end)
                ended = exit_quote is None
                if ended:
                    exit_quote = _last_price(closes.get(t, ()), p_end)
                assert exit_quote is not None  # an entry price exists, so a last price does
                r = exit_quote[1] / entries[t] - ONE
                held.append(NameResult(t, entries[t], exit_quote[1], r, weights[t], ended))
                total += weights[t] * r
            gross = total
        else:
            note = "no name had an entry price: the portfolio stayed in cash (zero return)"
        # Traded fraction against the previous portfolio after price drift.
        union = set(weights) | set(drifted)
        turnover = sum((abs(weights.get(t, ZERO) - drifted.get(t, ZERO)) for t in union), ZERO)
        cost = turnover * Decimal(s.cost_bps) / BPS
        period_gross = gross if gross is not None else ZERO
        net = (ONE - cost) * (ONE + period_gross) - ONE
        net_chain *= ONE + net
        gross_chain *= ONE + period_gross
        any_net = any_net or bool(names)
        # Drifted weights for the next rebalance.
        denom = ONE + period_gross
        drifted = {h.ticker: h.weight * (ONE + h.return_) / denom for h in held} if denom else {}
        bench_r: Decimal | None = None
        if benchmark is not None:
            a, b = _fill_price(benchmark, p_start), exit_price(benchmark, p_end)
            if a is not None and b is not None:
                bench_r = b[1] / a[1] - ONE
                bench_chain *= ONE + bench_r
            else:
                bench_known = False
        periods.append(
            PeriodResult(
                p_start, p_end, picked, held, dropped, gross, turnover, cost, net, bench_r, note
            )
        )
        if dropped:
            warnings.append(
                f"{p_start}: {len(dropped)} picked name(s) had no price near the date and were "
                f"left out: {', '.join(dropped)}"
            )
        if any(h.ended_early for h in held):
            gone = ", ".join(h.ticker for h in held if h.ended_early)
            warnings.append(
                f"{p_start}: {gone} had no price by the period end (delisted or stopped); held at "
                "the last price, so the true delisting outcome is unknown"
            )
    if plan.transaction_cost_bps == 0:
        warnings.append("no transaction cost was assumed: the net result equals the gross one")
    warnings.extend(survivorship_warning(plan, current_members) if current_members else [])
    warnings.append(
        f"Prices: {basis}; dividends are included only where the provider adjusts for them."
    )
    if benchmark is not None and not bench_known:
        warnings.append(
            "the benchmark has no price near some rebalance dates: its total is withheld"
        )
    first, last = slices[0].rebalance_date, end
    years = Decimal((last - first).days) / DAYS_PER_YEAR
    cum_net = net_chain - ONE if periods and any_net else None
    cum_gross = gross_chain - ONE if periods and any_net else None
    annual = None
    if cum_net is not None and years >= ONE:
        annual = (net_chain ** (ONE / years)) - ONE if net_chain > 0 else None
    elif cum_net is not None:
        warnings.append("under one year: no annualised figure is published")
    result = BacktestResult(
        periods=periods,
        cumulative_net=rounded(cum_net, "fraction"),
        cumulative_gross=rounded(cum_gross, "fraction"),
        cost_drag=rounded(cum_gross - cum_net, "fraction")
        if cum_net is not None and cum_gross is not None
        else None,
        benchmark_cumulative=rounded(bench_chain - ONE, "fraction")
        if benchmark is not None and bench_known and periods
        else None,
        annualised_net=rounded(annual, "fraction"),
        cost_bps=plan.transaction_cost_bps,
        warnings=warnings,
        fingerprint=input_hash(
            {
                "plan": plan.fingerprint,
                "picks": {str(k): list(v) for k, v in picks.items()},
                "end": end,
            }
        ),
    )
    return result
