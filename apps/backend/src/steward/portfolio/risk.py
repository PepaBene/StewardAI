"""Risk-adjusted return: the Sharpe ratio of the portfolio and of its benchmark.

Method (Sharpe 1994, practitioner convention for daily data):

1. Returns: the portfolio's TIME-WEIGHTED daily return, i.e. the flow-adjusted index of
   GET /portfolio/history (deposits and withdrawals netted out, so money moving in or out
   never reads as return), in the base currency. Only weekday points are used, so a
   Monday's return spans the weekend and every observation is one trading day; exchange
   holidays still count as (near-zero) trading days.
2. Risk-free rate: CZEONIA (the ČNB's CZK overnight rate, stored daily by the macro job)
   for a CZK base, the same currency as the returns. For each return from day t-1 to day
   t, the rate as of t-1 (forward-filled over weekends and holidays) / 100 / 252.
3. Excess return per day = return - daily risk-free rate. Sharpe = mean(excess) /
   stdev(excess) x sqrt(252), with the sample standard deviation. Annualising by sqrt(252)
   assumes daily excess returns are serially uncorrelated.
4. The benchmark (the first configured benchmark symbol, Stooq closes in its own currency)
   is converted to the base currency at each day's stored reference rate first, so both
   ratios measure a CZK investor's return over the same CZK risk-free rate.

Fewer than MIN_OBSERVATIONS returns (about three months) gives no ratio, with a reason:
the estimate is too noisy to show.
"""

import bisect
import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

import asyncpg
from pydantic import BaseModel, Field

from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.fx.convert import FxSourceName
from steward.fx.history import load_fx_history
from steward.portfolio.read import portfolio_history
from steward.sources.market import stooq_currency

TRADING_DAYS = 252
MIN_OBSERVATIONS = 60
# A daily rate older than this (no fixing stored) is not carried forward.
RATE_MAX_AGE = dt.timedelta(days=10)

# Risk-free series per base currency, first available wins: (series_id, source, is_step).
# A step series (a policy rate listed by change date) carries forward without age limit.
RISK_FREE_SERIES: dict[str, list[tuple[str, str, bool]]] = {
    "CZK": [("czeonia", "cnb", False), ("cnb_repo_2w", "cnb", True)],
    "EUR": [("estr", "ecb", False), ("ecb_deposit_rate", "ecb", False)],
    "USD": [("fed_funds", "fred", False)],
}

# Quote currency of Stooq index symbols (share symbols carry a market suffix instead).
INDEX_CURRENCY: dict[str, str] = {
    "^spx": "USD",
    "^ndx": "USD",
    "^ndq": "USD",
    "^dji": "USD",
    "^dax": "EUR",
    "^stoxx": "EUR",
    "^sx5e": "EUR",
    "^cac": "EUR",
    "^aex": "EUR",
    "^ukx": "GBP",
    "^px": "CZK",
    "^nkx": "JPY",
}

METHOD = (
    "Sharpe ratio = mean daily excess return / standard deviation of daily excess returns "
    "x sqrt(252). Returns: the time-weighted (deposit- and withdrawal-adjusted) daily "
    "portfolio index in the base currency, weekdays only. Excess over the risk-free rate "
    "of the previous day / 100 / 252, forward-filled over weekends and holidays. The "
    "benchmark is converted to the base currency at each day's reference rate first."
)


# ---------------------------------------------------------------------------
# Pure math
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyReturn:
    start: dt.date  # the previous trading day
    end: dt.date
    value: float  # simple return, 0.01 = 1%


def trading_day_returns(points: Sequence[tuple[dt.date, float]]) -> list[DailyReturn]:
    """Simple returns between consecutive weekday points (oldest first). A non-positive
    level has no return to measure from and is skipped."""
    weekdays = [(d, v) for d, v in points if d.weekday() < 5]
    return [
        DailyReturn(a_date, b_date, b / a - 1)
        for (a_date, a), (b_date, b) in pairwise(weekdays)
        if a > 0 and b > 0
    ]


class RateSeries:
    """Annual rates in percent by date, looked up as of a day (forward-filled)."""

    def __init__(
        self, points: Sequence[tuple[dt.date, float]], max_age: dt.timedelta | None
    ) -> None:
        ordered = sorted(points)
        self._dates = [d for d, _ in ordered]
        self._values = [v for _, v in ordered]
        self.max_age = max_age

    def __len__(self) -> int:
        return len(self._dates)

    def on(self, day: dt.date) -> float | None:
        i = bisect.bisect_right(self._dates, day) - 1
        if i < 0:
            return None
        if self.max_age is not None and day - self._dates[i] > self.max_age:
            return None
        return self._values[i]


@dataclass(frozen=True)
class SharpeResult:
    sharpe: float | None
    annualised_volatility: float | None
    annualised_return: float | None
    mean_excess_return: float | None
    risk_free_average: float | None
    observations: int
    window_start: dt.date | None
    window_end: dt.date | None
    reason: str | None


def _stdev(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def sharpe_ratio(
    returns: Sequence[DailyReturn],
    rates: RateSeries,
    min_observations: int = MIN_OBSERVATIONS,
) -> SharpeResult:
    """The annualised Sharpe ratio of daily returns over the forward-filled daily rate.
    Returns with no rate as of their start day are left out (and not counted)."""
    paired = [(r, rate) for r in returns if (rate := rates.on(r.start)) is not None]
    n = len(paired)
    start = paired[0][0].start if paired else (returns[0].start if returns else None)
    end = paired[-1][0].end if paired else (returns[-1].end if returns else None)

    def empty(reason: str) -> SharpeResult:
        return SharpeResult(None, None, None, None, None, n, start, end, reason)

    if not returns:
        return empty("no daily returns in the window")
    if not paired:
        return empty("no risk-free rate stored for the window")
    if n < min_observations:
        return empty(
            f"{n} daily returns in the window; at least {min_observations} are needed "
            "for a meaningful estimate"
        )
    raw = [r.value for r, _ in paired]
    daily_rf = [rate / 100 / TRADING_DAYS for _, rate in paired]
    excess = [r - f for r, f in zip(raw, daily_rf, strict=True)]
    sd_excess = _stdev(excess)
    mean_excess = sum(excess) / n
    return SharpeResult(
        sharpe=mean_excess / sd_excess * math.sqrt(TRADING_DAYS) if sd_excess > 0 else None,
        annualised_volatility=_stdev(raw) * math.sqrt(TRADING_DAYS),
        annualised_return=sum(raw) / n * TRADING_DAYS,
        mean_excess_return=mean_excess * TRADING_DAYS,
        risk_free_average=sum(daily_rf) / n * TRADING_DAYS,
        observations=n,
        window_start=start,
        window_end=end,
        reason=None if sd_excess > 0 else "returns do not vary; the ratio is undefined",
    )


# ---------------------------------------------------------------------------
# API model and loader
# ---------------------------------------------------------------------------


class RiskFree(BaseModel):
    series_id: str = Field(description="macro_series id, e.g. 'czeonia'.")
    source: str = Field(description="macro_series source, e.g. 'cnb'.")
    label: str
    latest_rate: float | None = Field(description="Latest stored value, as a fraction.")
    latest_date: dt.date | None = Field(description="The day the latest value is for.")
    url: str | None
    fallback: bool = Field(
        description="True when the preferred series (CZEONIA for CZK) has nothing stored and "
        "a policy rate stands in."
    )


class SharpeStats(BaseModel):
    sharpe: float | None
    annualised_volatility: float | None = Field(
        description="Sample stdev of the daily returns x sqrt(252), fraction."
    )
    annualised_return: float | None = Field(description="Mean daily return x 252, fraction.")
    mean_excess_return: float | None = Field(
        description="Mean daily excess return x 252, fraction."
    )
    risk_free_average: float | None = Field(
        description="Mean daily risk-free rate x 252 over the observations, fraction."
    )
    observations: int
    window_start: dt.date | None
    window_end: dt.date | None
    currency: str | None = Field(description="Currency the returns are measured in.")
    reason: str | None = Field(description="Why `sharpe` is null, when it is.")


class PortfolioRisk(BaseModel):
    as_of: dt.date | None
    days: int
    base_currency: str | None
    method: str
    risk_free: RiskFree | None
    portfolio: SharpeStats
    benchmark_symbol: str | None
    benchmark: SharpeStats | None = Field(
        description="The same figures for the benchmark, converted to the base currency; null "
        "without a configured benchmark."
    )


def _stats(result: SharpeResult, currency: str | None) -> SharpeStats:
    return SharpeStats(
        sharpe=result.sharpe,
        annualised_volatility=result.annualised_volatility,
        annualised_return=result.annualised_return,
        mean_excess_return=result.mean_excess_return,
        risk_free_average=result.risk_free_average,
        observations=result.observations,
        window_start=result.window_start,
        window_end=result.window_end,
        currency=currency,
        reason=result.reason,
    )


def _no_stats(reason: str, currency: str | None, n: int = 0) -> SharpeStats:
    return SharpeStats(
        sharpe=None,
        annualised_volatility=None,
        annualised_return=None,
        mean_excess_return=None,
        risk_free_average=None,
        observations=n,
        window_start=None,
        window_end=None,
        currency=currency,
        reason=reason,
    )


async def _risk_free(
    pool: asyncpg.Pool, currency: str, since: dt.date
) -> tuple[RiskFree, RateSeries] | None:
    candidates = RISK_FREE_SERIES.get(currency, [])
    for i, (series_id, source, step) in enumerate(candidates):
        latest = await pool.fetchrow(
            "select period, value, label, url from public.macro_series where series_id = $1"
            " and source = $2 order by period desc limit 1",
            series_id,
            source,
        )
        if latest is None:
            continue
        # A step series needs its last change before the window, however old.
        rows = await pool.fetch(
            "select period, value from public.macro_series where series_id = $1 and source = $2"
            " and period >= coalesce((select max(period) from public.macro_series"
            " where series_id = $1 and source = $2 and period <= $3), $3)"
            " order by period",
            series_id,
            source,
            since - RATE_MAX_AGE,
        )
        value: Decimal = latest["value"]
        info = RiskFree(
            series_id=series_id,
            source=source,
            label=latest["label"] or series_id,
            latest_rate=float(value) / 100,
            latest_date=latest["period"],
            url=latest["url"],
            fallback=i > 0,
        )
        rates = RateSeries(
            [(r["period"], float(r["value"])) for r in rows], None if step else RATE_MAX_AGE
        )
        return info, rates
    return None


def benchmark_currency(symbol: str) -> str | None:
    return INDEX_CURRENCY.get(symbol.lower()) or stooq_currency(symbol)


async def _fx_source(pool: asyncpg.Pool, base: str) -> FxSourceName:
    stored = await load_active(pool, "strategy", StrategyConfig)
    if stored and stored.body.fx_source:
        return stored.body.fx_source
    latest = await pool.fetchval(
        "select fx_source from public.account_snapshot order by snapshot_date desc limit 1"
    )
    return latest or ("cnb" if base == "CZK" else "ecb")


async def portfolio_risk(pool: asyncpg.Pool, days: int) -> PortfolioRisk:
    history = await portfolio_history(pool, days)
    base = history.values_currency or history.base_currency
    symbol = history.benchmark_symbol

    def report(
        portfolio: SharpeStats, benchmark: SharpeStats | None, rf: RiskFree | None
    ) -> PortfolioRisk:
        return PortfolioRisk(
            as_of=history.as_of,
            days=days,
            base_currency=base,
            method=METHOD,
            risk_free=rf,
            portfolio=portfolio,
            benchmark_symbol=symbol,
            benchmark=benchmark,
        )

    if history.gap or not history.points or base is None:
        reason = history.gap or "no portfolio history"
        return report(_no_stats(reason, base), None, None)

    points = [(p.date, p.close) for p in history.points]
    since = points[0][0]
    found = await _risk_free(pool, base, since)
    returns = trading_day_returns(points)
    if found is None:
        names = ", ".join(s for s, _, _ in RISK_FREE_SERIES.get(base, [])) or "none configured"
        reason = f"no risk-free rate stored for {base} ({names}); run the macro job"
        bench = _no_stats(reason, base) if symbol else None
        return report(_no_stats(reason, base, len(returns)), bench, None)
    rf, rates = found
    portfolio = _stats(sharpe_ratio(returns, rates), base)

    bench_stats: SharpeStats | None = None
    if symbol:
        quote = benchmark_currency(symbol)
        closes = [(p.date, p.close) for p in history.benchmark if p.date >= since]
        if quote is None:
            bench_stats = _no_stats(f"quote currency of {symbol} is unknown", base)
        elif not closes:
            bench_stats = _no_stats(f"no stored closes of {symbol} in the window", base)
        else:
            source = await _fx_source(pool, base)
            fx = await load_fx_history(pool, source, {quote}, closes[0][0], closes[-1][0])
            converted = []
            for day, close in closes:
                rate = fx.conversion(quote, base, day)
                if rate is not None:
                    converted.append((day, close * float(rate)))
            if len(converted) < 2:
                bench_stats = _no_stats(f"no {quote}/{base} reference rates for the window", base)
            else:
                bench_stats = _stats(sharpe_ratio(trading_day_returns(converted), rates), base)
    return report(portfolio, bench_stats, rf)
