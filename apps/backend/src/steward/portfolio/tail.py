"""Tail risk and drawdown odds of the current book (plan Q2.1 to Q2.4). No I/O.

The portfolio's weekly return series is today's weights applied to the weekly base-currency
returns of the ex-ante model (short histories backfilled from their proxy, as there). Everything
is therefore a statement about how today's book would have behaved, on weekly data: the one-month
horizon is four compounded weeks; there is no one-day figure here (the older daily value at risk
on the risk page stays). Positions left out of the model count as cash with no return, which
understates the tail by about their weight (the coverage line says how much).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
from pydantic import BaseModel, Field

from steward.config.strategy import StrategyConfig
from steward.portfolio import exante as ea
from steward.quant import bootstrap as bs
from steward.quant import downside as dn
from steward.quant import proxy as px
from steward.quant import tail as tl

METHOD_VERSION = "tail-report-1"
LEVELS = (0.95, 0.99)
HORIZONS = (1, 4)  # weeks: one week and one month
YEARS = (1, 2, 3)
CONTRIBUTION_ROWS = 50  # the page shows the top few; the thesis pack looks one up


class TailLine(BaseModel):
    horizon_weeks: int
    level: float
    method: str
    var: float = Field(description="Loss exceeded on (1 - level) of periods, share of the book.")
    es: float = Field(description="Average loss on those periods.")
    var_value: float | None = None
    es_value: float | None = None


class EsContribution(BaseModel):
    ticker: str
    name: str | None = None
    weight: float
    es_share: float = Field(description="Share of the book's one-month 95 % Expected Shortfall.")
    part_of_es: float = Field(description="That figure as a part of the total (adds to 1).")


class DrawdownOdds(BaseModel):
    years: int
    probability: float
    threshold: float
    drawdown_p50: float
    drawdown_p95: float
    conditional_drawdown_at_risk: float
    terminal_p05: float
    terminal_p50: float
    terminal_p95: float
    recovery_median_weeks: float | None = None
    recovered_share: float | None = None
    # Value of 1 invested today by percentile of the simulated paths, one point per fan step.
    fan: dict[str, list[float]] = Field(default_factory=dict)
    fan_step_weeks: int = bs.FAN_STEP


class DownsideOut(BaseModel):
    periods: int
    annual_return: float
    downside_deviation: float
    sortino: float | None
    max_drawdown: float
    calmar: float | None
    ulcer_index: float
    skew: float
    excess_kurtosis: float


class TailReport(BaseModel):
    method_version: str = METHOD_VERSION
    available: bool
    reason: str | None = None
    base_currency: str | None = None
    as_of: dt.date | None = None
    weeks: int = 0
    total_value: float | None = None
    covered_share: float | None = None
    lines: list[TailLine] = Field(default_factory=list)
    contributions: list[EsContribution] = Field(default_factory=list)
    drawdown: list[DrawdownOdds] = Field(default_factory=list)
    downside: DownsideOut | None = None
    es_95_month: float | None = Field(
        default=None, description="The larger of the historical and filtered-historical figures."
    )
    block_weeks: float = bs.DEFAULT_BLOCK
    seed: int = bs.DEFAULT_SEED
    paths: int = bs.DEFAULT_PATHS
    warnings: list[str] = Field(default_factory=list)
    not_modelled: list[str] = Field(default_factory=list)


def portfolio_series(
    book: ea.LoadedBook, strategy: StrategyConfig
) -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray, float, list[str]] | None:
    """(simple weekly portfolio returns, tickers used, weights, asset simple returns (weeks x
    positions), covered share of the book, warnings); None when nothing has a series."""
    if book.total_value <= 0 or not book.positions:
        return None
    candidates = [
        k for k in book.weekly.keys if k == ea.BENCHMARK_KEY or k.startswith(ea.PROXY_PREFIX)
    ]
    filled, status = px.backfill(
        book.weekly, candidates, preferred=ea.proxy_preferences(book, strategy)
    )
    usable = [
        p
        for p in book.positions
        if (st := status.get(p.ticker)) is not None and st.status != "excluded"
    ]
    if not usable:
        return None
    tickers = [p.ticker for p in usable]
    weights = np.array([p.value_base / book.total_value for p in usable])
    assets = np.expm1(np.column_stack([filled.column(t) for t in tickers]))
    ok = ~np.isnan(assets).any(axis=1)
    assets = assets[ok]
    covered = float(weights.sum() / (sum(p.value_base for p in book.positions) / book.total_value))
    warnings: list[str] = []
    left_out = [p.ticker for p in book.positions if p.ticker not in tickers]
    if left_out:
        warnings.append(
            f"{', '.join(left_out)} {'is' if len(left_out) == 1 else 'are'} left out of the "
            "model, so the tail is understated by about their weight"
        )
    proxied = [t for t in tickers if status[t].status == "proxied"]
    if proxied:
        warnings.append(
            f"{', '.join(proxied)}: history before the stock's own is estimated from a proxy"
        )
    return assets @ weights, tickers, weights, assets, covered, warnings


def tail_report(book: ea.LoadedBook, strategy: StrategyConfig, *, full: bool = True) -> TailReport:
    """The report. `full=False` skips the downside statistics and the longer horizons (the
    sync-time check only needs the one-month Expected Shortfall and the limit's horizon)."""
    built = portfolio_series(book, strategy)
    if built is None:
        return TailReport(available=False, reason="no position has a weekly return series")
    series, tickers, weights, assets, covered, warnings = built
    out = TailReport(
        available=True,
        base_currency=book.base_currency,
        as_of=book.as_of,
        weeks=len(series),
        total_value=book.total_value,
        covered_share=covered,
        warnings=warnings,
        not_modelled=[
            "a one-day horizon (weekly returns only)",
            "events the history does not contain; the simulation resamples what happened",
        ],
    )
    if len(series) < tl.MIN_OBSERVATIONS:
        out.available = False
        out.reason = f"only {len(series)} weeks of history; at least {tl.MIN_OBSERVATIONS} needed"
        return out
    for periods in HORIZONS if full else (4,):
        window = tl.horizon_returns(series, periods)
        for level in LEVELS if full else (0.95,):
            for name, got in (
                ("historical", tl.historical(window, level)),
                ("filtered historical", _filtered(series, periods, level)),
                ("Cornish-Fisher", tl.cornish_fisher(window, level)),
            ):
                if got is None:
                    continue
                out.lines.append(
                    TailLine(
                        horizon_weeks=periods,
                        level=level,
                        method=name,
                        var=got.var,
                        es=got.es,
                        var_value=got.var * book.total_value,
                        es_value=got.es * book.total_value,
                    )
                )
    month = [x.es for x in out.lines if x.horizon_weeks == 4 and x.level == 0.95]
    out.es_95_month = max(month) if month else None
    shares = tl.es_contributions(weights, assets, 0.95)
    if shares is not None and full:
        by_ticker = {p.ticker: p for p in book.positions}
        values, _ = shares
        total = float(values.sum()) or 1.0
        rows = [
            EsContribution(
                ticker=t,
                name=by_ticker[t].name,
                weight=float(w),
                es_share=float(v),
                part_of_es=float(v) / total,
            )
            for t, w, v in zip(tickers, weights, values, strict=True)
        ]
        out.contributions = sorted(rows, key=lambda r: -r.part_of_es)[:CONTRIBUTION_ROWS]
        out.not_modelled.append(
            "contributions are one-week (the worst 5 % of weeks), not one-month"
        )
    threshold = strategy.risk.max_drawdown or 0.20
    for years in YEARS if full else (strategy.risk.drawdown_probability_years,):
        sim = bs.simulate(series, 52 * years, threshold=threshold)
        if sim is None:
            continue
        out.drawdown.append(
            DrawdownOdds(
                years=years,
                probability=sim.probability_of_drawdown,
                threshold=threshold,
                drawdown_p50=sim.drawdown_quantiles["p50"],
                drawdown_p95=sim.drawdown_quantiles["p95"],
                conditional_drawdown_at_risk=sim.conditional_drawdown_at_risk,
                terminal_p05=sim.terminal_quantiles["p05"],
                terminal_p50=sim.terminal_quantiles["p50"],
                terminal_p95=sim.terminal_quantiles["p95"],
                recovery_median_weeks=sim.recovery_median_weeks,
                recovered_share=sim.recovered_share,
                fan=sim.fan,
            )
        )
    if full:
        stats = dn.downside_stats(series)
        if stats is not None:
            out.downside = DownsideOut(**stats.__dict__)
    return out


def _filtered(series: np.ndarray, periods: int, level: float) -> tl.TailRisk | None:
    """Filtered historical for a horizon: the weekly series is filtered, then compounded."""
    if periods == 1:
        return tl.filtered_historical(series, level)
    sigma = tl.ewma_volatility(series)
    today = float(np.sqrt(tl.EWMA_LAMBDA * sigma[-1] ** 2 + (1 - tl.EWMA_LAMBDA) * series[-1] ** 2))
    scaled = series / np.where(sigma > 0, sigma, np.nan) * today
    scaled = np.nan_to_num(scaled)
    return tl.historical(tl.horizon_returns(scaled, periods), level)
