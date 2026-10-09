"""Factor exposures of the book and of the benchmark (quant plan Q4.2, Q4.3).

`quant/factors.py` holds the regression. This module loads the stored Ken French factor
returns, lines them up with the book's weekly base-currency returns (the last two years),
and reports for the portfolio, the first benchmark and each holding: the loading on market,
size, value, profitability, investment and momentum, plus the US dollar and euro (against the
base currency), each with a Newey-West t-statistic and the regression's R-squared.

Choices that matter when reading the numbers:

- The factors are US dollar returns for one region (`developed` by default). A European
  holding is better matched by `europe`, a US one by `us`; the page lets the owner switch.
- A holding with a short history is regressed on its real weeks only. The portfolio is
  regressed on the weighted sum of the filled returns (proxy-backfilled weeks included), as
  the risk model does, and its weights are scaled to the invested part so the loadings can be
  compared with the benchmark, which is fully invested.
- A tilt is the portfolio's loading minus the benchmark's. A tilt beyond `TILT_FLAG` (0.3) on
  a style factor is a concentration note, not a limit breach.
- The library's files run a few weeks behind; weeks after the last factor date are left out
  of the regressions and the report says how many.
"""

import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from typing import Literal

import asyncpg
import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field

from steward.fx.convert import normalise_currency
from steward.fx.history import load_fx_history
from steward.portfolio import exante as ea
from steward.portfolio.risk_snapshots import save_snapshot
from steward.quant import factors as fc
from steward.quant import french, french_store
from steward.quant import proxy as px
from steward.quant import returns as rt

log = logging.getLogger(__name__)

METHOD_VERSION = "factors-1"
FACTOR_WEEKS = 104  # two years of weekly returns
TILT_FLAG = 0.3
STYLE_FACTORS = ("MKT_RF", "SMB", "HML", "RMW", "CMA", "MOM")
CURRENCY_FACTORS = ("USD", "EUR")
MAX_EXTRA_CURRENCIES = 4
RATES_FACTOR = "RATE10"
YIELD_SERIES = "us_10y"  # macro_series key of the US 10-year Treasury yield (FRED DGS10)
MACRO_FACTORS = (*CURRENCY_FACTORS, RATES_FACTOR)

METHOD = (
    "Weekly base-currency log returns over the last two years, minus the US risk-free rate, "
    "regressed on the Ken French factors for the chosen region (market, size, value, "
    "profitability, investment, momentum), the weekly change of the US dollar and the euro "
    "against the base currency and the weekly change of the US 10-year yield in percentage "
    "points (its loading is the return for a one-point rise). Standard errors are Newey-West "
    "with four lags."
)

# factor -> (name, what a high loading means, what a low one means)
FACTOR_TEXT: dict[str, tuple[str, str, str]] = {
    "MKT_RF": ("Market", "moves more than the stock market", "moves less than the stock market"),
    "SMB": ("Size", "leans to small companies", "leans to large companies"),
    "HML": (
        "Value",
        "leans to cheap, value-style shares",
        "leans to expensive, growth-style shares",
    ),
    "RMW": (
        "Profitability",
        "leans to highly profitable companies",
        "leans to weakly profitable ones",
    ),
    "CMA": (
        "Investment",
        "leans to companies that invest cautiously",
        "leans to companies that invest heavily",
    ),
    "MOM": ("Momentum", "leans to recent winners", "leans to recent losers"),
    "USD": ("US dollar", "gains when the dollar rises", "gains when the dollar falls"),
    "EUR": ("Euro", "gains when the euro rises", "gains when the euro falls"),
    "RATE10": (
        "US 10-year yield",
        "gains when yields rise by one point",
        "gains when yields fall by one point",
    ),
}


class FactorBeta(BaseModel):
    factor: str
    label: str
    beta: float
    t_stat: float


class FitOut(BaseModel):
    r_squared: float
    observations: int
    alpha_annual: float = Field(description="Intercept, annualised (x52), log return.")
    betas: list[FactorBeta]


class PositionFactors(BaseModel):
    ticker: str
    name: str | None
    weight: float
    basis: Literal["real", "proxied", "excluded"]
    fit: FitOut | None
    reason: str | None


class Tilt(BaseModel):
    factor: str
    label: str
    portfolio: float
    benchmark: float | None
    tilt: float | None = Field(description="Portfolio loading minus benchmark loading.")
    flagged: bool = Field(description="A style factor with a tilt beyond 0.3 either way.")
    note: str | None = Field(description="Plain sentence for a flagged tilt.")


class FactorCoverage(BaseModel):
    first_date: dt.date | None
    last_date: dt.date | None
    window_weeks: int
    weeks_without_factors: int


class FactorExposures(BaseModel):
    region: str
    regions: list[str]
    as_of: dt.date | None
    base_currency: str | None = None
    total_value: float | None = None
    method_version: str
    method: str
    window_start: dt.date | None
    window_end: dt.date | None
    tilt_flag: float
    portfolio: FitOut | None
    benchmark: FitOut | None
    benchmark_label: str | None
    tilts: list[Tilt]
    positions: list[PositionFactors]
    coverage: FactorCoverage | None
    warnings: list[str]
    reason: str | None


def _label(factor: str) -> str:
    return FACTOR_TEXT[factor][0] if factor in FACTOR_TEXT else factor


def _fit_out(fit: fc.FactorFit) -> FitOut:
    return FitOut(
        r_squared=fit.r_squared,
        observations=fit.observations,
        alpha_annual=fit.alpha * fc.WEEKS_PER_YEAR,
        betas=[
            FactorBeta(factor=k, label=_label(k), beta=b, t_stat=fit.t_stats[k])
            for k, b in fit.betas.items()
        ],
    )


def _empty(region: str, reason: str) -> FactorExposures:
    return FactorExposures(
        region=region,
        regions=list(french.REGIONS),
        as_of=None,
        method_version=METHOD_VERSION,
        method=METHOD,
        window_start=None,
        window_end=None,
        tilt_flag=TILT_FLAG,
        portfolio=None,
        benchmark=None,
        benchmark_label=None,
        tilts=[],
        positions=[],
        coverage=None,
        warnings=[],
        reason=reason,
    )


def currency_series(currency: str, start: dt.date, end: dt.date) -> rt.SeriesInput:
    """A unit of `currency` priced every day, so its level in the base currency is the rate."""
    days = (start + dt.timedelta(days=i) for i in range((end - start).days + 1))
    return rt.SeriesInput(f"fx:{currency}", [(d, 1.0) for d in days], currency)


def tilt_note(factor: str, tilt: float) -> str:
    name, high, low = FACTOR_TEXT[factor]
    return (
        f"{name}: the portfolio {high if tilt > 0 else low} compared with the benchmark "
        f"(tilt {tilt:+.2f})."
    )


def build_exposures(
    book: ea.LoadedBook,
    daily: Mapping[str, Sequence[tuple[dt.date, float]]],
    fx: rt.FxLookup,
    region: str,
    preferences: Mapping[str, Sequence[str]] | None = None,
    yields: Sequence[tuple[dt.date, float]] | None = None,
) -> FactorExposures:
    """Factor exposures of a loaded book (pure given the loaded data)."""
    if not book.positions or book.total_value <= 0:
        return _empty(region, "no open positions")
    if "MKT_RF" not in daily or "RF" not in daily:
        return _empty(
            region,
            f"no {region} factor returns are stored; run the factor returns job to download them",
        )
    weekly = book.weekly.last(FACTOR_WEEKS)
    if len(weekly.dates) < fc.MIN_OBSERVATIONS:
        return _empty(region, "less than a year of weekly returns is stored")
    dates = weekly.dates
    start, end = dates[0] - dt.timedelta(days=7), dates[-1]

    fw = fc.weekly_factor_returns(daily, dates)
    rf = fw.pop("RF")
    regressors: dict[str, npt.NDArray[np.float64]] = {
        k: fw[k] for k in STYLE_FACTORS if k in fw and not np.isnan(fw[k]).all()
    }
    base = normalise_currency(book.base_currency)[0]
    # USD and EUR always; any other currency a position is quoted in gets a factor of its own
    # (a handful at most), so a GBP or CHF holding's currency move is not left in its residual.
    held_currencies = {normalise_currency(p.currency)[0] for p in book.positions}
    extra = sorted(held_currencies - set(CURRENCY_FACTORS))[:MAX_EXTRA_CURRENCIES]
    for ccy in [*CURRENCY_FACTORS, *extra]:
        if ccy == base:
            continue
        m = rt.build_return_matrix([currency_series(ccy, start, end)], fx, "weekly", start, end)
        if m.dates == dates:
            regressors[ccy] = m.values[:, 0]

    if yields:
        changes = fc.weekly_level_changes(yields, dates)
        if not np.isnan(changes).all():
            regressors[RATES_FACTOR] = changes

    candidates = [k for k in weekly.keys if k == ea.BENCHMARK_KEY or k.startswith(ea.PROXY_PREFIX)]
    filled, status = px.backfill(weekly, candidates, preferred=preferences)
    total = book.total_value

    positions: list[PositionFactors] = []
    included: list[ea.BookPosition] = []
    for p in sorted(book.positions, key=lambda x: -x.value_base):
        st_ = status.get(p.ticker)
        weight = p.value_base / total
        if st_ is None or st_.status == "excluded":
            positions.append(
                PositionFactors(
                    ticker=p.ticker, name=p.name, weight=weight, basis="excluded", fit=None,
                    reason=(st_.reason if st_ else None)
                    or book.unavailable.get(p.ticker, "no weekly returns in the window"),
                )
            )  # fmt: skip
            continue
        included.append(p)
        fit = fc.regress(weekly.column(p.ticker) - rf, regressors, px.MIN_FIT_WEEKS)
        positions.append(
            PositionFactors(
                ticker=p.ticker, name=p.name, weight=weight,
                basis="real" if st_.status == "real" else "proxied",
                fit=_fit_out(fit) if fit else None,
                reason=None if fit else "too few weeks with both prices and factor data",
            )
        )  # fmt: skip

    portfolio: fc.FactorFit | None = None
    if included:
        w = np.array([p.value_base for p in included])
        w = w / w.sum()
        series = np.column_stack([filled.column(p.ticker) for p in included]) @ w - rf
        portfolio = fc.regress(series, regressors)

    benchmark: fc.FactorFit | None = None
    if ea.BENCHMARK_KEY in weekly.keys:
        benchmark = fc.regress(weekly.column(ea.BENCHMARK_KEY) - rf, regressors)

    tilts: list[Tilt] = []
    if portfolio is not None:
        for factor, beta in portfolio.betas.items():
            bench = benchmark.betas.get(factor) if benchmark else None
            tilt = beta - bench if bench is not None else None
            flagged = tilt is not None and factor in STYLE_FACTORS and abs(tilt) > TILT_FLAG
            tilts.append(
                Tilt(
                    factor=factor, label=_label(factor), portfolio=beta, benchmark=bench,
                    tilt=tilt, flagged=flagged,
                    note=tilt_note(factor, tilt) if flagged and tilt is not None else None,
                )
            )  # fmt: skip

    last = max(d[-1][0] for d in daily.values() if d)
    first = min(d[0][0] for d in daily.values() if d)
    no_factors = int(np.isnan(fw["MKT_RF"]).sum())
    warnings: list[str] = []
    if no_factors:
        warnings.append(
            f"The factor files end on {last:%d %b %Y}, so the latest {no_factors} weeks are left "
            "out of the regressions."
        )
    if any(p.basis == "proxied" for p in positions):
        warnings.append(
            "Holdings with a short history are regressed on their own weeks only; the portfolio "
            "line also uses their proxy-backfilled weeks."
        )
    if RATES_FACTOR not in regressors:
        warnings.append(
            "No rates factor: the US 10-year yield is not stored yet. Run the macro collection "
            "(the series us_10y) to add it."
        )
    if benchmark is None:
        warnings.append("The benchmark has no usable history, so there is nothing to compare with.")
    if portfolio is None:
        return _empty(region, "too few weeks have both prices and factor data")
    return FactorExposures(
        region=region,
        regions=list(french.REGIONS),
        as_of=dates[-1],
        base_currency=book.base_currency,
        total_value=book.total_value,
        method_version=METHOD_VERSION,
        method=METHOD,
        window_start=dates[0],
        window_end=dates[-1],
        tilt_flag=TILT_FLAG,
        portfolio=_fit_out(portfolio),
        benchmark=_fit_out(benchmark) if benchmark else None,
        benchmark_label=book.proxy_labels.get(ea.BENCHMARK_KEY),
        tilts=tilts,
        positions=positions,
        coverage=FactorCoverage(
            first_date=first,
            last_date=last,
            window_weeks=len(dates),
            weeks_without_factors=no_factors,
        ),
        warnings=warnings,
        reason=None,
    )


async def load_yields(pool: asyncpg.Pool, since: dt.date) -> list[tuple[dt.date, float]]:
    """The stored US 10-year yield, percentage points, oldest first (empty until the macro
    collection has run with the `us_10y` series)."""
    rows = await pool.fetch(
        "select period, value from public.macro_series where series_id = $1 and source = 'fred'"
        " and period >= $2 order by period",
        YIELD_SERIES,
        since - dt.timedelta(days=14),
    )
    return [(r["period"], float(r["value"])) for r in rows]


async def factor_exposures(pool: asyncpg.Pool, region: str = "developed") -> FactorExposures:
    """Factor exposures of the latest synced book for one region's factors."""
    if region not in french.REGIONS:
        return _empty("developed", f"unknown region {region!r}")
    book, strategy = await ea.book_from_snapshot(pool)
    if book is None or strategy is None:
        return _empty(region, "no portfolio snapshot yet; run the portfolio sync")
    since = dt.date.today() - dt.timedelta(weeks=ea.WINDOW_WEEKS + 4)
    daily = await french_store.load_factors(pool, region, since)
    currencies = {"USD", "EUR", book.base_currency} | {p.currency for p in book.positions}
    fx = await load_fx_history(pool, ea.FX_SOURCE, currencies, since, dt.date.today())
    lookup = ea.fx_lookup(fx, normalise_currency(book.base_currency)[0])
    yields = await load_yields(pool, since)
    report = build_exposures(
        book, daily, lookup, region, ea.proxy_preferences(book, strategy), yields
    )
    if report.portfolio is not None:
        await save_snapshot(
            pool, "factors", report, report.as_of, report.method_version, scope=region
        )
    return report


__all__ = ["METHOD_VERSION", "FactorExposures", "build_exposures", "factor_exposures"]
