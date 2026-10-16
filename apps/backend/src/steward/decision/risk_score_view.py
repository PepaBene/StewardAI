# ruff: noqa: E501
"""The risk page's headline (insight I12.3): the risk score with its inputs and benchmark marker,
metric tiles with a verdict line, per-holding volatility, drawdown and beta with portfolio and
market reference bars, and the correlation pairs that move most and least together.

Every figure comes from the risk report's statistics (same functions, same thresholds); the
benchmark's figures are those statistics applied to the benchmark series of the same window.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from steward.analytics import risk as stats
from steward.analytics.contract import AnalyticsResult, Status
from steward.decision.risk_report import CorrelationMatrix, RiskReport
from steward.decision.risk_score import Inputs, RiskScore, Verdict, phrase, score
from steward.decision.series import SeriesBundle, aligned

ZERO = Decimal(0)
TILE_KEYS = (
    "volatility",
    "max_drawdown",
    "es_95",
    "beta",
    "sharpe",
    "sortino",
    "tracking_error",
    "correlation_benchmark",
)
TILE_LABELS = {
    "volatility": "Volatility",
    "max_drawdown": "Maximum drawdown",
    "es_95": "Expected shortfall, 95 %",
    "beta": "Beta",
    "sharpe": "Sharpe ratio",
    "sortino": "Sortino ratio",
    "tracking_error": "Tracking error",
    "correlation_benchmark": "Correlation with the benchmark",
    "concentration": "Three largest issuers",
    "diversification": "Diversification benefit",
}


class Tile(BaseModel):
    key: str
    label: str
    value: float | None
    unit: str
    benchmark_value: float | None
    status: str
    reason: str | None
    verdict: Verdict | None


class HoldingRisk(BaseModel):
    ticker: str
    weight: float = Field(description="Share of invested value, latest day.")
    volatility: float | None
    max_drawdown: float | None = Field(description="Positive size of the worst fall.")
    beta: float | None
    observations: int


class Pair(BaseModel):
    a: str
    b: str
    correlation: float


class RiskHeadline(BaseModel):
    as_of: dt.date | None
    window_start: dt.date | None
    window_end: dt.date | None
    observations: int
    benchmark_symbol: str | None
    score: RiskScore
    tiles: list[Tile]
    holdings: list[HoldingRisk]
    portfolio_reference: dict[str, float | None] = Field(
        description="The portfolio's own volatility, drawdown and beta for the reference bar."
    )
    market_reference: dict[str, float | None] = Field(
        description="The benchmark's volatility and drawdown over the window; beta 1."
    )
    correlation: CorrelationMatrix
    together: list[Pair] = Field(description="Pairs that moved most together.")
    least_together: list[Pair]
    concentration_basis: str
    warnings: list[str]


def _val(r: AnalyticsResult | None) -> Decimal | None:
    if r is None or r.value is None or r.status == Status.UNAVAILABLE:
        return None
    return Decimal(str(r.value))


def _f(x: Decimal | None) -> float | None:
    return None if x is None else float(x)


def _reason(r: AnalyticsResult | None) -> str | None:
    if r is None:
        return "not computed"
    if r.status == Status.UNAVAILABLE:
        return "; ".join(r.warnings) or "not available"
    return None


def benchmark_figures(bundle: SeriesBundle) -> dict[str, Decimal | None]:
    """The benchmark's own volatility, drawdown (positive) and ES over the window the portfolio
    figures use (days the portfolio has)."""
    if not bundle.benchmark or not bundle.portfolio:
        return {"volatility": None, "max_drawdown": None, "expected_shortfall": None}
    _days, _p, b = aligned(bundle.portfolio, bundle.benchmark)
    vol = _val(stats.volatility(b))
    dd = _val(stats.max_drawdown(b))
    _var, es = stats.var_es(b)
    return {
        "volatility": vol,
        "max_drawdown": None if dd is None else abs(dd),
        "expected_shortfall": _val(es),
    }


def pairs(matrix: CorrelationMatrix, n: int = 3) -> tuple[list[Pair], list[Pair]]:
    found: list[Pair] = []
    t = matrix.tickers
    for i in range(len(t)):
        for j in range(i + 1, len(t)):
            v = matrix.values[i][j]
            if v is not None:
                found.append(Pair(a=t[i], b=t[j], correlation=v))
    ranked = sorted(found, key=lambda p: -p.correlation)
    least = sorted(found, key=lambda p: p.correlation)[:n]
    return ranked[:n], [p for p in least if p not in ranked[:n]] or least


def holding_risk(bundle: SeriesBundle) -> list[HoldingRisk]:
    out: list[HoldingRisk] = []
    for ticker, series in bundle.holdings.items():
        weight = bundle.weights.get(ticker)
        if weight is None:
            continue
        days = sorted(series)
        returns = [series[d] for d in days]
        dd = _val(stats.max_drawdown(returns))
        b = None
        if bundle.benchmark:
            _shared, x, y = aligned(series, bundle.benchmark)
            b = _val(stats.beta(x, y))
        out.append(
            HoldingRisk(ticker=ticker, weight=float(weight), volatility=_f(_val(stats.volatility(returns))),
                        max_drawdown=None if dd is None else float(abs(dd)), beta=_f(b),
                        observations=len(returns))
        )  # fmt: skip
    return sorted(out, key=lambda h: -h.weight)


def build_headline(
    bundle: SeriesBundle,
    report: RiskReport,
    top3: Decimal | None,
    concentration_basis: str,
    concentration_reason: str | None = None,
) -> RiskHeadline:
    s = report.stats
    bench = benchmark_figures(bundle)
    dd = _val(s.get("max_drawdown"))
    values: dict[str, Decimal | None] = {
        "volatility": _val(s.get("volatility")),
        "max_drawdown": None if dd is None else abs(dd),
        "expected_shortfall": _val(s.get("es_95")),
        "concentration": top3,
        "beta": _val(s.get("beta")),
    }
    reasons = {
        k: r
        for k, r in {
            "volatility": _reason(s.get("volatility")),
            "max_drawdown": _reason(s.get("max_drawdown")),
            "expected_shortfall": _reason(s.get("es_95")),
            "beta": _reason(s.get("beta")),
            "concentration": concentration_reason if top3 is None else None,
        }.items()
        if r
    }
    headline = score(Inputs(values=values, benchmark=bench, reasons=reasons))
    bench_by_tile = {
        "volatility": bench["volatility"],
        "max_drawdown": None if bench["max_drawdown"] is None else -bench["max_drawdown"],
        "es_95": bench["expected_shortfall"],
        "beta": Decimal(1) if bundle.benchmark else None,
        "correlation_benchmark": Decimal(1) if bundle.benchmark else None,
    }
    tiles: list[Tile] = []
    for key in TILE_KEYS:
        r = s.get(key)
        v = _val(r)
        tiles.append(
            Tile(key=key, label=TILE_LABELS[key], value=_f(v), unit=r.unit if r else "fraction",
                 benchmark_value=_f(bench_by_tile.get(key)), status=str(r.status) if r else "unavailable",
                 reason=_reason(r), verdict=phrase(key, v))
        )  # fmt: skip
    tiles.append(
        Tile(key="concentration", label=TILE_LABELS["concentration"], value=_f(top3), unit="fraction",
             benchmark_value=0.12, status="complete" if top3 is not None else "unavailable",
             reason=concentration_reason if top3 is None else None, verdict=phrase("concentration", top3))
    )  # fmt: skip
    div = report.diversification
    tiles.append(
        Tile(key="diversification", label=TILE_LABELS["diversification"], value=_f(_val(div)),
             unit=div.unit, benchmark_value=None, status=str(div.status), reason=_reason(div),
             verdict=phrase("diversification", _val(div)))
    )  # fmt: skip
    together, least = pairs(report.correlation)
    return RiskHeadline(
        as_of=report.as_of, window_start=report.window.start, window_end=report.window.end,
        observations=report.window.observations, benchmark_symbol=report.benchmark_symbol,
        score=headline, tiles=tiles, holdings=holding_risk(bundle),
        portfolio_reference={"volatility": _f(values["volatility"]), "max_drawdown": _f(values["max_drawdown"]),
                             "beta": _f(values["beta"])},
        market_reference={"volatility": _f(bench["volatility"]), "max_drawdown": _f(bench["max_drawdown"]),
                          "beta": 1.0 if bundle.benchmark else None},
        correlation=report.correlation, together=together, least_together=least,
        concentration_basis=concentration_basis, warnings=report.warnings,
    )  # fmt: skip


def top3_from_weights(weights: dict[str, Any]) -> Decimal | None:
    """Fallback concentration when the X-Ray cannot be loaded: direct positions' weights (a fund
    counts as one holding, so this understates look-through concentration)."""
    total = sum((Decimal(str(w)) for w in weights.values()), ZERO)
    if total <= 0:
        return None
    ranked = sorted((Decimal(str(w)) / total for w in weights.values()), reverse=True)
    return sum(ranked[:3], ZERO)
