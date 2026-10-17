"""The extended portfolio risk report (plan M6.1).

Extends the existing volatility, beta, drawdown, Sharpe and pair-correlation figures with
downside deviation, the Sortino ratio, historical value at risk and expected shortfall, rolling
correlation with the benchmark, a diversification benefit and tracking error. Every figure is an
`AnalyticsResult`: it carries its sample size, window and method version, and is withheld with a
reason below the minimum observations in `analytics.thresholds`. Value at risk and expected
shortfall are descriptive of the sample; they are not forecasts.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, Field

from steward.analytics import risk as stats
from steward.analytics.contract import (
    AnalyticsResult,
    Coverage,
    SourceRef,
    Status,
    unavailable,
)
from steward.analytics.thresholds import THRESHOLDS
from steward.analytics.versions import version
from steward.decision.series import SeriesBundle, aligned
from steward.portfolio.risk import RiskFree

MAX_MATRIX = 10
MAX_POINTS = 260
ZERO = Decimal(0)


class RiskWindow(BaseModel):
    start: dt.date | None
    end: dt.date | None
    observations: int = Field(description="Aligned daily returns the statistics are built on.")
    frequency: str = "daily"
    annualisation: int = 252


class RiskSeriesPoint(BaseModel):
    date: dt.date
    value: float


class CorrelationMatrix(BaseModel):
    tickers: list[str]
    values: list[list[float | None]]
    observations: list[list[int]]
    average: float | None = Field(description="Mean of the pairs that have enough observations.")
    pairs_measured: int
    pairs_total: int
    min_observations: int


class RiskReport(BaseModel):
    method_version: str
    as_of: dt.date | None
    base_currency: str | None
    window: RiskWindow
    benchmark_symbol: str | None
    benchmark_note: str | None
    risk_free: RiskFree | None
    stats: dict[str, AnalyticsResult]
    rolling_correlation: list[RiskSeriesPoint]
    rolling_window: int
    drawdown_path: list[RiskSeriesPoint]
    correlation: CorrelationMatrix
    diversification: AnalyticsResult
    holdings_covered: Coverage
    warnings: list[str]
    method: str


METHOD = (
    "Daily simple returns in the base currency on weekdays: the deposit- and "
    "withdrawal-adjusted portfolio index, the benchmark converted at each day's reference rate, "
    "and each holding's organic value change. Volatility, downside deviation and Sortino use the "
    "sample standard deviation or the root mean square shortfall, annualised by sqrt(252); "
    "Sharpe and Sortino subtract the previous day's risk-free rate. Historical VaR and expected "
    "shortfall are the k-th smallest return and the mean of the k smallest, k = ceil(n x "
    "(1 - confidence)), reported as positive losses for a single day. Tracking error is the "
    "annualised stdev of the daily active return. Diversification benefit is 1 - stdev(blend of "
    "the holdings at today's weights) / sum(weight x stdev). Each figure is withheld below the "
    "minimum observations shown with it."
)


def _thin(points: list[tuple[dt.date, Decimal]]) -> list[RiskSeriesPoint]:
    step = max(1, len(points) // MAX_POINTS)
    kept = points[::step]
    if points and kept[-1] != points[-1]:
        kept.append(points[-1])
    return [RiskSeriesPoint(date=d, value=float(v)) for d, v in kept]


def _with_sample(r: AnalyticsResult, n: int, window: str) -> AnalyticsResult:
    note = f"{n} daily observations, {window}"
    if note not in r.warnings and r.status != Status.UNAVAILABLE:
        r = r.model_copy(update={"warnings": [*r.warnings, note]})
    return r


def build_report(bundle: SeriesBundle, rolling_window: int = 90) -> RiskReport:
    method = version("risk")
    warnings: list[str] = []
    base_kwargs = {
        "method_version": method,
        "as_of": bundle.window_end,
        "base_currency": bundle.base_currency,
        "benchmark_symbol": bundle.benchmark_symbol,
        "benchmark_note": bundle.benchmark_note,
        "risk_free": bundle.risk_free,
        "method": METHOD,
    }
    empty_matrix = CorrelationMatrix(
        tickers=[], values=[], observations=[], average=None, pairs_measured=0, pairs_total=0,
        min_observations=THRESHOLDS.min_obs_correlation,
    )  # fmt: skip
    if bundle.gap or not bundle.portfolio:
        reason = bundle.gap or "no portfolio return history"
        return RiskReport(
            **base_kwargs,
            window=RiskWindow(start=None, end=None, observations=0),
            stats={},
            rolling_correlation=[],
            rolling_window=rolling_window,
            drawdown_path=[],
            correlation=empty_matrix,
            diversification=unavailable("fraction", method, reason),
            holdings_covered=Coverage(numerator=ZERO, denominator=Decimal(1), basis="weight"),
            warnings=[reason],
        )

    days = sorted(bundle.portfolio)
    returns = [bundle.portfolio[d] for d in days]
    n = len(returns)
    window_text = f"{days[0]} to {days[-1]}"
    out: dict[str, AnalyticsResult] = {}
    out["volatility"] = stats.volatility(returns)
    out["downside_deviation"] = stats.downside_deviation(returns)
    out["max_drawdown"] = stats.max_drawdown(returns)
    var95, es95 = stats.var_es(returns, Decimal("0.95"))
    var99, es99 = stats.var_es(returns, Decimal("0.99"))
    out.update({"var_95": var95, "es_95": es95, "var_99": var99, "es_99": es99})

    rf_days = [d for d in days if d in bundle.daily_risk_free]
    if bundle.risk_free is None or len(rf_days) != n:
        reason = (
            "no risk-free rate stored for the base currency"
            if bundle.risk_free is None
            else f"the risk-free rate covers {len(rf_days)} of {n} days"
        )
        out["sharpe"] = unavailable("ratio", method, reason)
        out["sortino"] = unavailable("ratio", method, reason)
    else:
        rf = [bundle.daily_risk_free[d] for d in days]
        out["sharpe"] = stats.sharpe(returns, rf)
        out["sortino"] = stats.sortino(returns, rf)

    rolling: list[RiskSeriesPoint] = []
    if bundle.benchmark:
        shared, p, b = aligned(bundle.portfolio, bundle.benchmark)
        out["beta"] = stats.beta(p, b)
        out["tracking_error"] = stats.tracking_error(p, b)
        out["correlation_benchmark"] = stats.correlation(p, b)
        if len(shared) >= rolling_window and rolling_window >= THRESHOLDS.min_obs_correlation:
            roll = stats.rolling_correlation(p, b, rolling_window)
            rolling = _thin([(d, v) for d, v in zip(shared, roll, strict=True) if v is not None])
    else:
        reason = bundle.benchmark_note or "no benchmark is configured"
        for key in ("beta", "tracking_error", "correlation_benchmark"):
            out[key] = unavailable(
                "ratio" if key != "tracking_error" else "fraction", method, reason
            )
        warnings.append(f"Benchmark statistics withheld: {reason}.")

    for key, value in list(out.items()):
        out[key] = _with_sample(value, n, window_text)

    wealth = ZERO + Decimal(1)
    peak = Decimal(1)
    path: list[tuple[dt.date, Decimal]] = []
    for d, r in zip(days, returns, strict=True):
        wealth *= Decimal(1) + r
        peak = max(peak, wealth)
        path.append((d, wealth / peak - Decimal(1)))

    correlation, diversification, covered = _holdings(bundle)
    if covered.fraction is not None and covered.fraction < Decimal("0.8"):
        warnings.append(
            f"Holding-level figures cover {covered.fraction:.0%} of the invested value; the rest "
            "has too little aligned history."
        )
    return RiskReport(
        **base_kwargs,
        window=RiskWindow(start=days[0], end=days[-1], observations=n),
        stats=out,
        rolling_correlation=rolling,
        rolling_window=rolling_window,
        drawdown_path=_thin(path),
        correlation=correlation,
        diversification=diversification,
        holdings_covered=covered,
        warnings=warnings,
    )


def _holdings(bundle: SeriesBundle) -> tuple[CorrelationMatrix, AnalyticsResult, Coverage]:
    method = version("risk")
    ranked = sorted(bundle.holdings, key=lambda t: -bundle.weights.get(t, ZERO))
    ranked = [t for t in ranked if t in bundle.weights]
    minimum = THRESHOLDS.min_obs_diversification

    # Grow the set in weight order while the shared days stay above the minimum.
    chosen: list[str] = []
    shared: set[dt.date] | None = None
    for ticker in ranked:
        own = set(bundle.holdings[ticker])
        nxt = own if shared is None else shared & own
        if len(nxt) >= minimum:
            chosen.append(ticker)
            shared = nxt
    total_weight = sum(bundle.weights.values(), ZERO)
    covered = Coverage(
        numerator=sum((bundle.weights[t] for t in chosen), ZERO),
        denominator=total_weight if total_weight > 0 else Decimal(1),
        basis="weight",
    )
    if len(chosen) < 2 or shared is None:
        div = unavailable(
            "fraction",
            method,
            "fewer than two holdings share enough aligned days "
            f"(minimum {minimum}) for a diversification benefit",
        )
    else:
        order = sorted(shared)
        series = [[bundle.holdings[t][d] for d in order] for t in chosen]
        weights = [bundle.weights[t] for t in chosen]
        div = stats.diversification_benefit(weights, series)
        div = div.model_copy(
            update={
                "coverage": covered,
                "sources": [SourceRef(kind="price_bar", ref=", ".join(chosen))],
            }
        )

    top = ranked[:MAX_MATRIX]
    values: list[list[float | None]] = []
    counts: list[list[int]] = []
    pair_values: list[float] = []
    total_pairs = len(top) * (len(top) - 1) // 2
    for a in top:
        row: list[float | None] = []
        crow: list[int] = []
        for b in top:
            if a == b:
                row.append(1.0)
                crow.append(len(bundle.holdings[a]))
                continue
            days, x, y = aligned(bundle.holdings[a], bundle.holdings[b])
            crow.append(len(days))
            got = stats.correlation(x, y) if len(days) >= THRESHOLDS.min_obs_correlation else None
            value = None if got is None or got.value is None else float(got.value)
            row.append(value)
        values.append(row)
        counts.append(crow)
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            if values[i][j] is not None:
                pair_values.append(values[i][j])  # type: ignore[arg-type]
    matrix = CorrelationMatrix(
        tickers=top,
        values=values,
        observations=counts,
        average=sum(pair_values) / len(pair_values) if pair_values else None,
        pairs_measured=len(pair_values),
        pairs_total=total_pairs,
        min_observations=THRESHOLDS.min_obs_correlation,
    )
    return matrix, div, covered
