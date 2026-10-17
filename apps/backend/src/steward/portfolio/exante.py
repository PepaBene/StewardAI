"""Ex-ante portfolio risk: today's risk from current weights and a covariance model (quant plan
Q0 and Q1).

The realized volatility of `portfolio/risk.py` looks back at the flow-adjusted index, which
mixes positions sold months ago with today's book. This module asks instead: what is the
volatility of the book held now, and where does it come from?

Method (module docstrings of `steward.quant` hold the formulas):

1. Returns: weekly (Wednesday to Wednesday) log returns in the base currency of every held
   position, the first benchmark and the proxy ETFs, over the last 156 weeks, each close converted
   at that day's ECB reference rate (the ECB history is backfilled over `history_years`; ČNB
   fixings are stored only from the first sync).
2. Short histories: a position with 26 to 155 weeks of its own is regressed on the best-fitting
   proxy and the missing weeks are backfilled (labelled `proxied`); under 26 weeks, or with no
   usable proxy, it is left out and its weight is reported as uncovered.
3. Covariance: EWMA (half-life 26 weeks) for the current regime and Ledoit-Wolf
   constant-correlation shrinkage for the long run. The forecast tested against the volatility
   limit is the HIGHER of the two (conservative), on weights as shares of total portfolio value,
   so cash counts at zero volatility.
4. Decomposition: Euler risk contributions per position and per tier, sector, country and
   currency, the diversification ratio and Meucci's effective number of bets.

Positions left out of the model contribute nothing to the forecast, so it understates the
risk by roughly their weight; `coverage` says how much that is.
"""

import datetime as dt
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Literal

import asyncpg
import numpy as np
from pydantic import BaseModel, Field

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.fx.convert import FxSourceName, normalise_currency
from steward.fx.history import FxHistory, load_fx_history
from steward.portfolio.drift import UNCLASSIFIED
from steward.portfolio.risk_snapshots import save_snapshot
from steward.portfolio.risk_tier import apply_tiers, derive_for_book, with_saved_tiers
from steward.portfolio.snapshot import PositionRow
from steward.quant import covariance as cv
from steward.quant import decompose as dc
from steward.quant import proxy as px
from steward.quant import returns as rt
from steward.reference import risk as ref_risk
from steward.reference.price_series import PriceSymbols, choose_one, choose_series, holding_symbols
from steward.reference.read import index_series, load_series

log = logging.getLogger(__name__)

METHOD_VERSION = "exante-1"
WINDOW_WEEKS = 156  # three years of weekly returns, the Ledoit-Wolf window
EWMA_WEEKS = 104  # the EWMA looks at the last two years; older weeks weigh under 7 %
STALE_DAYS = 14  # a series whose last return is older than this is flagged
BOOK_CACHE_SECONDS = 60.0
_BOOK_CACHE: dict[tuple[int, object, float, tuple[str, ...]], tuple[float, "LoadedBook"]] = {}
DAILY_ROWS = 756  # three years of weekdays
DAILY_MIN_FIT = 130  # twenty-six weeks of weekdays, the daily counterpart of the weekly minimum
CORRELATION_WEEKS = 104  # the pair-correlation limit looks at the last two years
CORRELATION_MIN_WEEKS = 40
FX_SOURCE: FxSourceName = "ecb"
BENCHMARK_KEY = "benchmark"
PROXY_PREFIX = "proxy:"

METHOD = (
    "Weekly (Wednesday to Wednesday) log returns in the base currency over the last "
    f"{WINDOW_WEEKS} weeks; EWMA covariance (half-life {cv.EWMA_HALF_LIFE_WEEKS} weeks) and "
    "Ledoit-Wolf constant-correlation shrinkage, annualised by 52; the forecast is the higher "
    "of the two, on weights as shares of total portfolio value (cash at zero volatility). "
    "Holdings with 26 to 155 weeks of history are backfilled from the best-fitting proxy ETF "
    "(regression, residuals resampled with a fixed seed); shorter ones are left out."
)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BookPosition:
    ticker: str
    name: str | None
    currency: str  # instrument currency
    value_base: float


@dataclass
class LoadedBook:
    base_currency: str
    total_value: float
    positions: list[BookPosition]
    weekly: rt.ReturnMatrix  # as stored, gaps not filled
    daily_closes: dict[str, list[tuple[dt.date, float]]]  # local currency, held tickers
    proxy_labels: dict[str, str]
    unavailable: dict[str, str] = field(default_factory=dict)  # ticker -> why it has no series
    # proxy key -> lower-case words that make it the preferred stand-in for a sector or region
    proxy_matches: dict[str, list[str]] = field(default_factory=dict)
    # What the daily matrix is built from; empty on a synthetic book.
    inputs: list[rt.SeriesInput] = field(default_factory=list)
    fx: rt.FxLookup | None = None
    start: dt.date | None = None
    end: dt.date | None = None

    @property
    def as_of(self) -> dt.date | None:
        return self.weekly.dates[-1] if self.weekly.dates else None

    def daily(self, rows: int = DAILY_ROWS) -> rt.ReturnMatrix:
        """Daily base-currency log returns of the same series over the last `rows` weekdays
        (not backfilled; see `daily_filled`). Built on demand: it costs a rate lookup per series
        per day. Empty when the book carries no price inputs."""
        if self.fx is None or self.start is None or self.end is None or not self.inputs:
            return rt.build_return_matrix([], lambda c, d: None, "daily", dt.date.min, dt.date.min)
        return rt.build_return_matrix(self.inputs, self.fx, "daily", self.start, self.end).last(
            rows
        )

    def daily_filled(
        self, rows: int = DAILY_ROWS
    ) -> tuple[rt.ReturnMatrix, dict[str, px.ColumnStatus]]:
        """The daily matrix with short histories backfilled from the best proxy, as the weekly
        one is (at least `DAILY_MIN_FIT` days of own history to fit)."""
        matrix = self.daily(rows)
        candidates = [k for k in matrix.keys if k == BENCHMARK_KEY or k.startswith(PROXY_PREFIX)]
        return px.backfill(matrix, candidates, DAILY_MIN_FIT)


def fx_lookup(fx: FxHistory, base: str) -> rt.FxLookup:
    @cache
    def lookup(currency: str, day: dt.date) -> float | None:
        rate = fx.conversion(currency, base, day)
        return float(rate) if rate is not None else None

    return lookup


async def load_book(
    pool: asyncpg.Pool,
    positions: Sequence[BookPosition],
    total_value: float,
    base_currency: str,
    today: dt.date | None = None,
) -> LoadedBook:
    """Closes, FX rates and the weekly base-currency return matrix for the held positions, the
    first benchmark and the configured proxy ETFs. Nothing is fetched from a provider."""
    from steward.portfolio.risk import benchmark_currency  # imported here: portfolio.risk cycle

    today = today or dt.date.today()
    start = today - dt.timedelta(weeks=WINDOW_WEEKS + 2)
    jobs = await load_active(pool, "jobs", JobsConfig)
    settings = (jobs.body if jobs else JobsConfig()).prices
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)

    held = [p for p in positions if p.value_base > 0]
    tickers = sorted({p.ticker for p in held})
    symbols = await holding_symbols(
        pool, tickers, profiles.body if profiles else HoldingProfilesConfig()
    )
    chosen = await choose_series(pool, symbols)

    inputs: list[rt.SeriesInput] = []
    daily: dict[str, list[tuple[dt.date, float]]] = {}
    unavailable: dict[str, str] = {}
    currency_of = {p.ticker: p.currency for p in held}
    for ticker in tickers:
        ref = chosen.get(ticker)
        if ref is None:
            unavailable[ticker] = "no stored price series"
            continue
        closes = await load_series(pool, ref, start - dt.timedelta(days=400))
        if len(closes) < 2:
            unavailable[ticker] = "fewer than two stored closes"
            continue
        daily[ticker] = closes
        inputs.append(rt.SeriesInput(ticker, closes, currency_of[ticker]))

    labels: dict[str, str] = {}
    matches: dict[str, list[str]] = {}
    if settings.benchmark_symbols:
        symbol = settings.benchmark_symbols[0]
        quote = benchmark_currency(symbol)
        _, closes = await index_series(pool, settings, symbol, start)
        if quote and closes:
            inputs.append(rt.SeriesInput(BENCHMARK_KEY, closes, quote))
            labels[BENCHMARK_KEY] = f"Benchmark {symbol}"
    for proxy in settings.proxy_series:
        ref = await choose_one(pool, PriceSymbols(stooq=proxy.symbol, yahoo=proxy.yahoo()))
        if ref is None:
            continue
        closes = await load_series(pool, ref, start)
        if len(closes) >= 2:
            key = PROXY_PREFIX + proxy.symbol
            inputs.append(rt.SeriesInput(key, closes, proxy.currency))
            labels[key] = proxy.label
            matches[key] = list(proxy.match)

    currencies = {s.currency for s in inputs} | {base_currency}
    end = max((c[-1][0] for s in inputs if (c := list(s.closes))), default=today)
    fx = await load_fx_history(pool, FX_SOURCE, currencies, start, end)
    lookup = fx_lookup(fx, normalise_currency(base_currency)[0])
    weekly = rt.build_return_matrix(inputs, lookup, "weekly", start, end)
    return LoadedBook(
        base_currency,
        total_value,
        list(held),
        weekly.last(WINDOW_WEEKS),
        daily,
        labels,
        unavailable,
        matches,
        inputs,
        lookup,
        start,
        end,
    )


def proxy_preferences(book: LoadedBook, strategy: StrategyConfig) -> dict[str, list[str]]:
    """For each position, the proxies whose match words appear in its sector or country label."""
    out: dict[str, list[str]] = {}
    for p in book.positions:
        text = " ".join(
            _bucket(strategy, p, dim).lower() for dim in ("sector", "geography")
        ).replace("unclassified", "")
        hits = [k for k, words in book.proxy_matches.items() if any(w in text for w in words)]
        if hits:
            out[p.ticker] = hits
    return out


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

Status = Literal["real", "proxied", "excluded"]


class ProxyFitOut(BaseModel):
    proxy: str = Field(description="Stooq symbol of the ETF the history was backfilled from.")
    label: str
    beta: float
    r_squared: float
    fit_weeks: int
    fit_start: dt.date
    fit_end: dt.date
    backfilled_weeks: int


class PositionExAnte(BaseModel):
    ticker: str
    name: str | None
    tier: str
    status: Status = Field(
        description="real: own history over the whole window; proxied: part of it backfilled "
        "from a proxy ETF; excluded: left out of the model (see `reason`)."
    )
    reason: str | None
    weight: float = Field(description="Share of total portfolio value, cash included.")
    volatility: float | None = Field(description="Standalone annualised volatility.")
    risk_share: float | None = Field(
        description="Euler share of the forecast variance; all positions sum to 1."
    )
    marginal_risk: float | None = Field(
        description="Change in annualised portfolio volatility per unit of weight."
    )
    real_weeks: int
    proxy: ProxyFitOut | None
    weight_of_invested: float | None = Field(
        default=None, description="Share of the invested part (positions without cash)."
    )
    last_price: dt.date | None = Field(
        default=None, description="Date of the latest stored close of this position's series."
    )


class GroupRisk(BaseModel):
    label: str
    weight: float
    risk_share: float


class Coverage(BaseModel):
    invested_weight: float = Field(description="Share of total portfolio value held in positions.")
    real_weight: float
    proxied_weight: float
    uncovered_weight: float = Field(description="Positions left out of the model.")
    covered_share: float = Field(description="(real + proxied) / invested.")


class TierCheck(BaseModel):
    ticker: str
    tier: str
    volatility: float
    window_days: int
    observations: int
    threshold: float
    rule: Literal["speculative_above", "core_above"]
    breached: bool


class FigureBasis(BaseModel):
    """What one figure rests on (rule 2): its window, how many weekly observations it used and
    the model behind it."""

    window_start: dt.date | None
    window_end: dt.date | None
    observations: int
    method: str


class ExAnteLimits(BaseModel):
    max_volatility: float | None
    tier_risk_budgets: dict[str, float] | None
    min_effective_bets: float | None


class ExAnteRisk(BaseModel):
    as_of: dt.date | None
    base_currency: str | None
    method_version: str
    method: str
    window_start: dt.date | None
    window_end: dt.date | None
    weeks: int
    total_value: float | None
    volatility_ewma: float | None
    volatility_ledoit_wolf: float | None
    volatility_forecast: float | None = Field(
        description="The higher of the two, tested against the volatility limit."
    )
    forecast_model: Literal["ewma", "ledoit_wolf"] | None
    volatility_forecast_scaled: float | None = Field(
        default=None,
        description="The forecast divided by the covered share of the invested weight: what it "
        "would be if the positions left out for lack of history carried the average risk of "
        "the rest. Equal to the forecast when nothing is left out. Shown, not tested.",
    )
    weight_basis: Literal["total", "invested"] = Field(
        default="total",
        description="The strategy's weight basis, which the page uses to show sizes. Risk "
        "shares are always computed on total value.",
    )
    shrinkage_intensity: float | None = Field(
        description="Weight Ledoit-Wolf puts on the constant-correlation target."
    )
    realized_volatility: float | None = Field(
        description="Realized volatility of the flow-adjusted index over the last year."
    )
    diversification_ratio: float | None
    effective_bets: float | None
    positions: list[PositionExAnte]
    by_tier: list[GroupRisk]
    by_sector: list[GroupRisk]
    by_country: list[GroupRisk]
    by_currency: list[GroupRisk]
    coverage: Coverage | None
    tier_checks: list[TierCheck]
    figure_basis: dict[str, FigureBasis] = Field(
        default_factory=dict,
        description="The window and observation count behind each headline figure.",
    )
    limits: ExAnteLimits
    warnings: list[str]
    reason: str | None = Field(description="Why there is no forecast, when there is none.")


def _empty(
    book_currency: str | None, reason: str, limits: ExAnteLimits, warnings: list[str] | None = None
) -> ExAnteRisk:
    return ExAnteRisk(
        as_of=None,
        base_currency=book_currency,
        method_version=METHOD_VERSION,
        method=METHOD,
        window_start=None,
        window_end=None,
        weeks=0,
        total_value=None,
        volatility_ewma=None,
        volatility_ledoit_wolf=None,
        volatility_forecast=None,
        forecast_model=None,
        shrinkage_intensity=None,
        realized_volatility=None,
        diversification_ratio=None,
        effective_bets=None,
        positions=[],
        by_tier=[],
        by_sector=[],
        by_country=[],
        by_currency=[],
        coverage=None,
        tier_checks=[],
        limits=limits,
        warnings=warnings or [],
        reason=reason,
    )


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


def _bucket(strategy: StrategyConfig, p: BookPosition, dimension: str) -> str:
    assigned = strategy.classifications.get(p.ticker, {}).get(dimension)
    if assigned:
        return assigned
    if dimension == "currency":
        return normalise_currency(p.currency)[0]
    return UNCLASSIFIED


def tier_checks(
    book: LoadedBook, strategy: StrategyConfig, window: int = ref_risk.TRADING_DAYS
) -> list[TierCheck]:
    """The volatility criterion of the risk tiers (principle 3, decision D6): a holding above
    the speculative threshold that is not tiered speculative, or a core holding above the core
    ceiling. The volatility is the holding page's own: the sample stdev of the last `window`
    daily local-currency log returns x sqrt(252). Unclassified holdings are not flagged."""
    limits = strategy.risk
    out: list[TierCheck] = []
    for p in book.positions:
        tier = _bucket(strategy, p, "risk")
        closes = book.daily_closes.get(p.ticker)
        if tier == UNCLASSIFIED or not closes:
            continue
        vol = ref_risk.volatility(closes, window)
        if vol is None:
            continue
        n = min(len(closes) - 1, window)
        if limits.tier_speculative_volatility is not None and tier != "speculative":
            out.append(
                TierCheck(
                    ticker=p.ticker,
                    tier=tier,
                    volatility=vol,
                    window_days=window,
                    observations=n,
                    threshold=limits.tier_speculative_volatility,
                    rule="speculative_above",
                    breached=vol > limits.tier_speculative_volatility,
                )
            )
        if limits.tier_core_max_volatility is not None and tier == "core":
            out.append(
                TierCheck(
                    ticker=p.ticker,
                    tier=tier,
                    volatility=vol,
                    window_days=window,
                    observations=n,
                    threshold=limits.tier_core_max_volatility,
                    rule="core_above",
                    breached=vol > limits.tier_core_max_volatility,
                )
            )
    return out


def _group(positions: Sequence[dc.PositionRisk], labels: dict[str, str]) -> list[GroupRisk]:
    return [
        GroupRisk(label=g, weight=w, risk_share=s) for g, w, s in dc.group_shares(positions, labels)
    ]


def build_report(
    book: LoadedBook, strategy: StrategyConfig, realized_volatility: float | None = None
) -> ExAnteRisk:
    """The ex-ante risk report of a loaded book (pure: no I/O)."""
    limits = ExAnteLimits(
        max_volatility=strategy.risk.max_volatility,
        tier_risk_budgets=strategy.risk.tier_risk_budgets,
        min_effective_bets=strategy.risk.min_effective_bets,
    )
    if book.total_value <= 0 or not book.positions:
        return _empty(book.base_currency, "no open positions", limits)

    candidates = [k for k in book.weekly.keys if k == BENCHMARK_KEY or k.startswith(PROXY_PREFIX)]
    filled, status = px.backfill(
        book.weekly, candidates, preferred=proxy_preferences(book, strategy)
    )
    total = book.total_value
    warnings: list[str] = []
    rows: list[PositionExAnte] = []
    usable: list[BookPosition] = []
    for p in sorted(book.positions, key=lambda x: -x.value_base):
        st = status.get(p.ticker)
        tier = _bucket(strategy, p, "risk")
        weight = p.value_base / total
        if st is None:
            rows.append(
                PositionExAnte(
                    ticker=p.ticker,
                    name=p.name,
                    tier=tier,
                    status="excluded",
                    reason=book.unavailable.get(p.ticker, "no weekly returns in the window"),
                    weight=weight,
                    volatility=None,
                    risk_share=None,
                    marginal_risk=None,
                    real_weeks=0,
                    proxy=None,
                )
            )
            continue
        fit = st.fit
        rows.append(
            PositionExAnte(
                ticker=p.ticker, name=p.name, tier=tier, status=st.status, reason=st.reason,
                weight=weight, volatility=None, risk_share=None, marginal_risk=None,
                real_weeks=st.real_weeks,
                proxy=ProxyFitOut(
                    proxy=fit.proxy.removeprefix(PROXY_PREFIX),
                    label=book.proxy_labels.get(fit.proxy, fit.proxy),
                    beta=fit.beta, r_squared=fit.r_squared, fit_weeks=fit.fit_weeks,
                    fit_start=fit.fit_start, fit_end=fit.fit_end,
                    backfilled_weeks=fit.backfilled_weeks,
                ) if fit is not None else None,
            )
        )  # fmt: skip
        if st.status != "excluded":
            usable.append(p)

    invested = sum(p.value_base for p in book.positions) / total
    real = sum(r.weight for r in rows if r.status == "real")
    proxied = sum(r.weight for r in rows if r.status == "proxied")
    coverage = Coverage(
        invested_weight=invested,
        real_weight=real,
        proxied_weight=proxied,
        uncovered_weight=invested - real - proxied,
        covered_share=(real + proxied) / invested if invested > 0 else 0.0,
    )
    invested_value = sum(p.value_base for p in book.positions)
    last_return = {k: c.last_return for k, c in book.weekly.coverage.items()}
    for r in rows:
        r.weight_of_invested = r.weight * total / invested_value if invested_value > 0 else None
        r.last_price = last_return.get(r.ticker)
        if (
            r.last_price is not None
            and book.as_of is not None
            and (book.as_of - r.last_price).days > STALE_DAYS
        ):
            warnings.append(
                f"{r.ticker}: the latest stored price is from {r.last_price:%d %b %Y}, so the "
                "price feed may have stalled; its recent weeks are estimated, not observed"
            )
    for r in rows:
        if r.status == "excluded":
            warnings.append(f"{r.ticker} is left out of the model: {r.reason}")
        elif r.status == "proxied" and r.proxy is not None:
            warnings.append(
                f"{r.ticker} has {r.real_weeks} weeks of its own history; the rest is backfilled "
                f"from {r.proxy.label} (R² {r.proxy.r_squared:.2f})"
            )
    if coverage.uncovered_weight > 0.005:
        warnings.append(
            f"{coverage.uncovered_weight:.1%} of the portfolio is left out of the model, so the "
            "forecast understates the risk by about that much (scaled up for the missing "
            "weight it would be higher; see volatility_forecast_scaled)"
        )

    base = _build_base(book, strategy, filled, usable, rows, coverage, warnings, limits)
    base.realized_volatility = realized_volatility
    base.tier_checks = tier_checks(book, strategy)
    return base


def _build_base(
    book: LoadedBook,
    strategy: StrategyConfig,
    filled: rt.ReturnMatrix,
    usable: list[BookPosition],
    rows: list[PositionExAnte],
    coverage: Coverage,
    warnings: list[str],
    limits: ExAnteLimits,
) -> ExAnteRisk:
    if not usable:
        out = _empty(book.base_currency, "no position has enough history for the model", limits)
        out.positions, out.coverage, out.warnings = rows, coverage, warnings
        out.total_value = book.total_value
        return out
    keys = [p.ticker for p in usable]
    data = np.column_stack([filled.column(k) for k in keys])
    weights = np.array([p.value_base / book.total_value for p in usable])
    ewma = cv.ewma_covariance(data[-EWMA_WEEKS:])
    shrunk = cv.ledoit_wolf(data)
    vol_ewma = cv.portfolio_volatility(weights, ewma)
    vol_lw = cv.portfolio_volatility(weights, shrunk.covariance)
    use_ewma = vol_ewma >= vol_lw
    chosen = ewma if use_ewma else shrunk.covariance
    d = dc.decompose(keys, weights, chosen)
    by_key = {p.key: p for p in d.positions}
    for r in rows:
        if r.ticker in by_key:
            p = by_key[r.ticker]
            r.volatility, r.risk_share, r.marginal_risk = p.volatility, p.risk_share, p.marginal
    rows.sort(key=lambda r: -(r.risk_share if r.risk_share is not None else -1.0))
    labels = {dim: {p.ticker: _bucket(strategy, p, dim) for p in usable} for dim in _DIMENSIONS}
    weeks = len(filled.dates)
    ewma_weeks = min(weeks, EWMA_WEEKS)

    def basis(observations: int, method: str) -> FigureBasis:
        first = filled.dates[-observations] if observations <= weeks else filled.dates[0]
        return FigureBasis(
            window_start=first,
            window_end=filled.dates[-1],
            observations=observations,
            method=method,
        )

    figure_basis = {
        "volatility_ewma": basis(ewma_weeks, "ewma"),
        "volatility_ledoit_wolf": basis(weeks, "ledoit_wolf"),
        "volatility_forecast": basis(ewma_weeks if use_ewma else weeks, METHOD),
        "risk_share": basis(ewma_weeks if use_ewma else weeks, METHOD),
        "effective_bets": basis(ewma_weeks if use_ewma else weeks, METHOD),
        "diversification_ratio": basis(ewma_weeks if use_ewma else weeks, METHOD),
    }
    return ExAnteRisk(
        figure_basis=figure_basis,
        as_of=filled.dates[-1],
        base_currency=book.base_currency,
        method_version=METHOD_VERSION,
        method=METHOD,
        window_start=filled.dates[0],
        window_end=filled.dates[-1],
        weeks=len(filled.dates),
        total_value=book.total_value,
        volatility_ewma=vol_ewma,
        volatility_ledoit_wolf=vol_lw,
        volatility_forecast=max(vol_ewma, vol_lw),
        forecast_model="ewma" if use_ewma else "ledoit_wolf",
        volatility_forecast_scaled=(
            max(vol_ewma, vol_lw) / coverage.covered_share if coverage.covered_share > 0 else None
        ),
        weight_basis=strategy.weight_basis,
        shrinkage_intensity=shrunk.intensity,
        realized_volatility=None,
        diversification_ratio=d.diversification_ratio,
        effective_bets=d.effective_bets,
        positions=rows,
        by_tier=_group(d.positions, labels["risk"]),
        by_sector=_group(d.positions, labels["sector"]),
        by_country=_group(d.positions, labels["geography"]),
        by_currency=_group(d.positions, labels["currency"]),
        coverage=coverage,
        tier_checks=[],
        limits=limits,
        warnings=warnings,
        reason=None,
    )


_DIMENSIONS = ("risk", "sector", "geography", "currency")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def book_from_snapshot(
    pool: asyncpg.Pool,
) -> tuple[LoadedBook | None, StrategyConfig | None]:
    account = await pool.fetchrow(
        "select snapshot_date, base_currency, cash_base, invested_base, total_base"
        " from public.account_snapshot order by snapshot_date desc limit 1"
    )
    if account is None:
        return None, None
    stored = await load_active(pool, "strategy", StrategyConfig)
    strategy = stored.body if stored else StrategyConfig(base_currency=account["base_currency"])
    rows = await pool.fetch(
        "select p.ticker, p.instrument_currency, p.value_base, i.name"
        " from public.position_snapshot p"
        " left join public.instrument i on i.t212_ticker = p.ticker where p.snapshot_date = $1",
        account["snapshot_date"],
    )
    positions = [
        BookPosition(r["ticker"], r["name"], r["instrument_currency"], float(r["value_base"]))
        for r in rows
    ]
    total = float(account["total_base"])
    # Loading three years of closes and rates for every holding, the benchmark and the proxy
    # ETFs is the slow part, and the ex-ante, factor and replay pages each ask for it. One copy
    # serves them for a minute, for the same snapshot.
    key = (id(pool), account["snapshot_date"], total, tuple(sorted(p.ticker for p in positions)))
    now = time.monotonic()
    hit = _BOOK_CACHE.get(key)
    if hit is not None and now - hit[0] < BOOK_CACHE_SECONDS:
        return hit[1], await with_saved_tiers(pool, strategy)
    book = await load_book(pool, positions, total, account["base_currency"])
    _BOOK_CACHE.clear()
    _BOOK_CACHE[key] = (now, book)
    return book, await with_book_tiers(pool, book, strategy)


async def with_book_tiers(
    pool: asyncpg.Pool,
    book: LoadedBook,
    strategy: StrategyConfig,
    changes: list[str] | None = None,
) -> StrategyConfig:
    """The strategy with each holding's derived risk tier filled in (and stored), when the
    strategy asks for derived tiers, and the derived sectors and countries. Tier changes since
    the stored tiers go into `changes`."""
    from steward.portfolio.classify import with_derived_classes

    strategy = await with_derived_classes(pool, strategy)
    tiers = await derive_for_book(
        pool, book.daily_closes, [p.ticker for p in book.positions], strategy, changes
    )
    return apply_tiers(strategy, {t: d.tier for t, d in tiers.items()})


async def book_from_positions(
    pool: asyncpg.Pool, positions: Sequence[PositionRow], base_currency: str | None = None
) -> LoadedBook:
    """A loaded book for sync-time positions (`PositionRow`), using the latest stored account
    totals for cash."""
    account = await pool.fetchrow(
        "select base_currency, cash_base, invested_base from public.account_snapshot"
        " order by snapshot_date desc limit 1"
    )
    base = base_currency or (account["base_currency"] if account else "CZK")
    held = [
        BookPosition(p.ticker, p.name, p.instrument_currency, float(p.value_base))
        for p in positions
    ]
    cash = float(account["cash_base"]) if account else 0.0
    total = sum(p.value_base for p in held) + cash
    return await load_book(pool, held, total, base)


async def ex_ante_risk(pool: asyncpg.Pool) -> ExAnteRisk:
    """The report for the latest synced book."""
    from steward.portfolio.risk import portfolio_risk

    book, strategy = await book_from_snapshot(pool)
    if book is None or strategy is None:
        return _empty(
            None,
            "no portfolio snapshot yet; run the portfolio sync",
            ExAnteLimits(max_volatility=None, tier_risk_budgets=None, min_effective_bets=None),
        )
    realized: float | None = None
    try:
        realized = (
            await portfolio_risk(pool, ref_risk.TRADING_DAYS + 113)
        ).portfolio.annualised_volatility
    except Exception as exc:  # a missing history must not hide the forecast
        log.warning("realized volatility unavailable: %s", exc)
    report = build_report(book, strategy, realized)
    if report.volatility_forecast is not None:
        await save_snapshot(pool, "ex_ante", report, report.as_of, report.method_version)
    return report


def weekly_pair_correlations(
    book: LoadedBook, weeks: int = CORRELATION_WEEKS, min_weeks: int = CORRELATION_MIN_WEEKS
) -> list[tuple[str, str, float, int]]:
    """(a, b, correlation, shared weeks) for every pair of held positions with enough shared
    weekly base-currency returns of their own (never backfilled ones, which would correlate with
    their proxy by construction)."""
    recent = book.weekly.last(weeks)
    tickers = sorted(p.ticker for p in book.positions if p.ticker in recent.keys)
    out: list[tuple[str, str, float, int]] = []
    for i, a in enumerate(tickers):
        for b in tickers[i + 1 :]:
            got = rt.pairwise_correlation(recent.column(a), recent.column(b), min_weeks)
            if got is not None:
                out.append((a, b, got[0], got[1]))
    return out


__all__ = [
    "METHOD_VERSION",
    "BookPosition",
    "ExAnteRisk",
    "LoadedBook",
    "build_report",
    "ex_ante_risk",
    "load_book",
    "tier_checks",
    "weekly_pair_correlations",
]
