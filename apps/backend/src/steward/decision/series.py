"""Aligned return series for the risk statistics and the measured sensitivities (plan M6.1, M6.2).

All series are simple daily returns in the base currency, weekdays only, from stored data:

- the portfolio: the deposit- and withdrawal-adjusted daily index (`/portfolio/history`);
- the benchmark: the stored closes converted to the base currency at each day's reference rate;
- each holding: its security's stored adjusted closes in the base currency, where they reach
  further back than the owner's snapshots; otherwise the change in its stored base-currency value
  with the owner's own trading netted out (the change in cost basis between two snapshot days),
  the same principle as the per-holding contribution report. A holding that was traded heavily or
  that has gaps in its snapshots has a noisier series, and is only used where it has enough
  aligned days.

Nothing is fetched from a provider on a request.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise
from typing import Any

import asyncpg

from steward.fx.history import load_fx_history
from steward.portfolio.read import portfolio_history
from steward.portfolio.risk import (
    RateSeries,
    RiskFree,
    _fx_source,
    _risk_free,
    benchmark_currency,
    trading_day_returns,
)

# A gap longer than this between two snapshots of a holding breaks its return series.
MAX_GAP_DAYS = 5


def _d(x: float) -> Decimal:
    return Decimal(repr(x))


@dataclass
class SeriesBundle:
    base_currency: str | None
    window_start: dt.date | None
    window_end: dt.date | None
    portfolio: dict[dt.date, Decimal] = field(default_factory=dict)
    benchmark: dict[dt.date, Decimal] = field(default_factory=dict)
    benchmark_symbol: str | None = None
    benchmark_note: str | None = None
    holdings: dict[str, dict[dt.date, Decimal]] = field(default_factory=dict)
    weights: dict[str, Decimal] = field(default_factory=dict)  # of invested value, latest day
    daily_risk_free: dict[dt.date, Decimal] = field(default_factory=dict)
    risk_free: RiskFree | None = None
    gap: str | None = None


def aligned(
    a: dict[dt.date, Decimal], b: dict[dt.date, Decimal]
) -> tuple[list[dt.date], list[Decimal], list[Decimal]]:
    days = sorted(set(a) & set(b))
    return days, [a[d] for d in days], [b[d] for d in days]


async def _benchmark_returns(
    pool: asyncpg.Pool,
    closes: list[tuple[dt.date, float]],
    symbol: str,
    base: str,
) -> tuple[dict[dt.date, Decimal], str | None]:
    quote = benchmark_currency(symbol)
    if quote is None:
        return {}, f"the quote currency of {symbol} is unknown"
    if not closes:
        return {}, f"no stored closes of {symbol} in the window"
    source = await _fx_source(pool, base)
    fx = await load_fx_history(pool, source, {quote}, closes[0][0], closes[-1][0])
    converted: list[tuple[dt.date, float]] = []
    for day, close in closes:
        rate = fx.conversion(quote, base, day)
        if rate is not None:
            converted.append((day, close * float(rate)))
    if len(converted) < 2:
        return {}, f"no {quote}/{base} reference rates for the window"
    return {r.end: _d(r.value) for r in trading_day_returns(converted)}, None


def organic_returns(items: Sequence[Mapping[str, Any]]) -> dict[dt.date, Decimal]:
    """Daily returns of one holding from its snapshots (`snapshot_date`, `quantity`,
    `avg_price`, `fx_rate`, `value_base`), net of the money traded between them."""
    out: dict[dt.date, Decimal] = {}
    for prev, cur in pairwise(items):
        gap = (cur["snapshot_date"] - prev["snapshot_date"]).days
        if gap > MAX_GAP_DAYS or cur["snapshot_date"].weekday() >= 5:
            continue
        if prev["value_base"] <= 0 or cur["value_base"] <= 0:
            continue
        # The change in cost basis is the money traded, valued at one (today's) rate: a day's
        # move in the rate is part of the return, not a trade.
        traded = (cur["quantity"] * cur["avg_price"] - prev["quantity"] * prev["avg_price"]) * cur[
            "fx_rate"
        ]
        out[cur["snapshot_date"]] = (cur["value_base"] - traded) / prev["value_base"] - 1
    return out


async def market_returns(
    pool: asyncpg.Pool, tickers: Sequence[str], base: str, since: dt.date
) -> dict[str, dict[dt.date, Decimal]]:
    """Daily returns of each security from the stored adjusted closes its price readers use,
    converted to the base currency at each day's reference rate. These do not depend on when
    the owner bought, so a holding bought last month still has a year of behaviour to measure."""
    from steward.config.holding_profiles import HoldingProfilesConfig
    from steward.config.store import load_active
    from steward.reference.price_series import choose_series, holding_symbols

    if not tickers:
        return {}
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool, list(tickers), profiles.body if profiles else HoldingProfilesConfig()
    )
    refs = {t: ref for t, ref in (await choose_series(pool, symbols)).items() if ref}
    if not refs:
        return {}
    rows = await pool.fetch(
        "select symbol, source, date, close, currency from public.price_bar"
        " where (symbol, source) in (select * from unnest($1::text[], $2::text[]))"
        " and bar_type = 'adjusted' and access_state = 'available' and close > 0"
        " and date >= $3 order by date",
        [r.symbol for r in refs.values()],
        [r.source for r in refs.values()],
        since,
    )
    bars: dict[tuple[str, str], list[asyncpg.Record]] = {}
    for row in rows:
        bars.setdefault((row["symbol"], row["source"]), []).append(row)
    currencies = {row["currency"] for row in rows if row["currency"]}
    source = await _fx_source(pool, base)
    end = max((row["date"] for row in rows), default=since)
    fx = await load_fx_history(pool, source, currencies, since, end)
    out: dict[str, dict[dt.date, Decimal]] = {}
    for ticker, ref in refs.items():
        if returns := converted_returns(bars.get((ref.symbol, ref.source), []), fx, base):
            out[ticker] = returns
    return out


def converted_returns(
    bars: Sequence[Mapping[str, Any]], fx: Any, base: str
) -> dict[dt.date, Decimal]:
    """Weekday returns of closes (`date`, `close`, `currency`) in the base currency; a day
    without a reference rate is left out rather than valued at a stale or missing one."""
    converted: list[tuple[dt.date, float]] = []
    for row in bars:
        rate = fx.conversion(row["currency"], base, row["date"]) if row["currency"] else None
        if rate is not None:
            converted.append((row["date"], float(row["close"]) * float(rate)))
    return {r.end: _d(r.value) for r in trading_day_returns(converted)}


async def holding_returns(
    pool: asyncpg.Pool, since: dt.date, base: str | None = None
) -> tuple[dict[str, dict[dt.date, Decimal]], dict[str, Decimal]]:
    """Per-holding daily returns and the latest weights (of invested value). A holding's
    security returns from stored closes are used where they reach further back than its own
    snapshots; otherwise the organic returns of the snapshots."""
    rows = await pool.fetch(
        "select snapshot_date, ticker, quantity, avg_price, fx_rate, value_base"
        " from public.position_history where snapshot_date >= $1 order by ticker, snapshot_date",
        since,
    )
    by_ticker: dict[str, list[asyncpg.Record]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)
    series: dict[str, dict[dt.date, Decimal]] = {}
    for ticker, items in by_ticker.items():
        if out := organic_returns(items):
            series[ticker] = out
    latest = max((r["snapshot_date"] for r in rows), default=None)
    values = {r["ticker"]: r["value_base"] for r in rows if r["snapshot_date"] == latest}
    total = sum(values.values(), Decimal(0))
    weights = {t: v / total for t, v in values.items() if total > 0 and v > 0}
    if base is not None:
        market = await market_returns(pool, sorted(weights), base, since)
        for ticker, returns in market.items():
            if len(returns) > len(series.get(ticker, {})):
                series[ticker] = returns
    return series, weights


async def load_bundle(
    pool: asyncpg.Pool, days: int, benchmark_symbol: str | None = None
) -> SeriesBundle:
    history = await portfolio_history(pool, days)
    base = history.values_currency or history.base_currency
    bundle = SeriesBundle(base_currency=base, window_start=None, window_end=history.as_of)
    if history.gap or not history.points or base is None:
        bundle.gap = history.gap or "no portfolio history"
        return bundle
    points = [(p.date, p.close) for p in history.points]
    bundle.window_start = points[0][0]
    bundle.portfolio = {r.end: _d(r.value) for r in trading_day_returns(points)}

    symbol = benchmark_symbol or history.benchmark_symbol
    bundle.benchmark_symbol = symbol
    if symbol:
        if benchmark_symbol and benchmark_symbol != history.benchmark_symbol:
            from steward.config.jobs import JobsConfig
            from steward.config.store import load_active
            from steward.reference.read import index_series

            jobs = await load_active(pool, "jobs", JobsConfig)
            _ref, bench = await index_series(
                pool, (jobs.body if jobs else JobsConfig()).prices, symbol, bundle.window_start
            )
            closes = [(d, c) for d, c in bench if d >= bundle.window_start]
        else:
            closes = [(p.date, p.close) for p in history.benchmark if p.date >= points[0][0]]
        bundle.benchmark, bundle.benchmark_note = await _benchmark_returns(
            pool, closes, symbol, base
        )

    found = await _risk_free(pool, base, points[0][0])
    if found is not None:
        bundle.risk_free, rates = found
        bundle.daily_risk_free = _daily_rf(rates, sorted(bundle.portfolio))

    bundle.holdings, bundle.weights = await holding_returns(pool, points[0][0], base)
    return bundle


def _daily_rf(rates: RateSeries, days: list[dt.date]) -> dict[dt.date, Decimal]:
    """Daily risk-free return for each return day: the rate of the previous weekday, /100/252."""
    out: dict[dt.date, Decimal] = {}
    for day in days:
        prior = day - dt.timedelta(days=3 if day.weekday() == 0 else 1)
        rate = rates.on(prior)
        if rate is not None:
            out[day] = _d(rate) / Decimal(100) / Decimal(252)
    return out
