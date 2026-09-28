"""Valuation read models: a holding's P/E, P/B and dividend yield, and the portfolio's
against its benchmark. Computed on read from stored data only: the latest Stooq close
(`price_bar`), the stored reference rates (`fx_rate`), the fundamentals the
`valuation_refresh` job stored, the holding profile's manual figures and the ETF providers'
fund figures. Nothing is estimated; a ratio that cannot be computed is null with a reason.
"""

import datetime as dt
import json
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import FundValuationSource, JobsConfig
from steward.config.store import load_active
from steward.fx.convert import FxRate, FxSourceName, FxTable, MissingRateError
from steward.reference.prices import symbols_for
from steward.valuation.collect import STATUS_KEY
from steward.valuation.common import (
    fund_for_ticker,
    fx_source,
    fx_table,
    instrument_info,
    price_currency,
)
from steward.valuation.fundamentals import EpsStep, FactRef, Figure, SourceFigures

# iShares' method, applied to the portfolio so it compares like for like with CSPX.
PE_CAP = 60.0
PB_CAP = 25.0
# A price older than this many days, or figures for a period ending longer ago, are stale.
PRICE_STALE_DAYS = 5
FIGURES_STALE_DAYS = 200
FUND_STALE_DAYS = 45
HISTORY_DAYS = 730

SourceName = Literal["sec", "esef", "manual"]

SOURCE_LABEL: dict[str, str] = {
    "sec": "SEC EDGAR XBRL (companyfacts)",
    "sec_instance": "SEC filing's XBRL instance",
    "esef": "ESEF report via filings.xbrl.org",
    "manual": "entered manually",
    "finnhub": "Finnhub basic financials (tier 2)",
}
PROVIDER_LABEL = {"ishares": "iShares", "vanguard": "Vanguard"}

PORTFOLIO_METHOD = (
    "Portfolio P/E is the value-weighted harmonic mean of the holdings' P/Es, "
    "sum(w) / sum(w / P/E), which equals total value over the earnings attributable to it. "
    "To compare like for like with the iShares benchmark figures, iShares' rules are "
    f"applied: holdings with negative or zero earnings are excluded and P/Es above "
    f"{PE_CAP:.0f} are set to {PE_CAP:.0f}; P/B likewise, with negative book values "
    f"excluded and P/Bs above {PB_CAP:.0f} set to {PB_CAP:.0f}. ETFs count with the "
    "fund-level P/E and P/B their provider publishes. Dividend yield is the value-weighted "
    "average of trailing yields (a fund counts with its provider's figure, which for "
    "Vanguard is a forecast). Coverage is the share of invested value (cash excluded) that "
    "has the figure; weights are renormalised over the covered holdings."
)


class RatioOut(BaseModel):
    value: float | None = Field(description="Null when it cannot be computed (see reason).")
    reason: str | None = Field(description="Why the value is null, e.g. 'n/m: negative earnings'.")


class PriceOut(BaseModel):
    symbol: str
    close: float
    currency: str | None
    date: dt.date
    source: str = "Stooq daily close"
    stale: bool


class FigureOut(BaseModel):
    value: float
    currency: str
    period_start: dt.date | None
    period_end: dt.date | None
    method: str
    annual_only: bool
    stale: bool = Field(description="The period ended more than 200 days ago.")
    inputs: list[FactRef] = Field(default_factory=list)


class FundamentalsOut(BaseModel):
    source: SourceName
    source_label: str
    basis: str | None
    eps_kind: str
    document_ref: str | None
    document_url: str | None
    fetched_at: dt.datetime | None
    as_of: dt.date | None = Field(description="Manual figures: the date they were entered for.")
    notes: list[str]
    last_error: str | None


class AlternativeOut(BaseModel):
    source: SourceName
    source_label: str
    basis: str | None
    eps_ttm: float | None
    currency: str | None
    period_end: dt.date | None
    method: str | None
    pe: float | None


class CrossCheckOut(BaseModel):
    source: str = "finnhub"
    source_label: str = SOURCE_LABEL["finnhub"]
    symbol: str | None
    pe: float | None
    pb: float | None
    eps_ttm: float | None
    dividend_yield: float | None
    fetched_at: dt.datetime


class FundValuationOut(BaseModel):
    key: str
    label: str
    provider: str
    provider_label: str
    as_of: dt.date
    pe: float | None
    pb: float | None
    index_label: str | None
    index_pe: float | None
    index_pb: float | None
    dividend_yield: float | None = Field(description="Fraction.")
    dividend_yield_kind: str | None
    methodology: str | None
    source_url: str
    fetched_at: dt.datetime
    stale: bool = Field(description="As of more than 45 days ago.")


class PePoint(BaseModel):
    date: dt.date
    pe: float
    eps: float


class HoldingValuation(BaseModel):
    ticker: str
    name: str | None
    kind: Literal["stock", "fund"]
    price: PriceOut | None
    pe: RatioOut
    pb: RatioOut
    dividend_yield: RatioOut = Field(description="Fraction (0.012 = 1.2 %).")
    eps_ttm: FigureOut | None
    book_value_per_share: FigureOut | None
    dividend_per_share: FigureOut | None
    shares_per_listing: float = Field(
        description="Ordinary shares per priced unit (depositary receipts)."
    )
    fundamentals: FundamentalsOut | None = Field(description="The figures the ratios use.")
    alternatives: list[AlternativeOut] = Field(
        description="Other sources or accounting bases, for comparison (not used)."
    )
    cross_check: CrossCheckOut | None
    fund: FundValuationOut | None = Field(description="ETFs: the provider's fund figures.")
    pe_history: list[PePoint] = Field(
        description="Weekly P/E over two years: each close over the EPS TTM in force then "
        "(from the first filing of each period); empty when it cannot be built."
    )
    errors: dict[str, str] = Field(description="The last refresh's error per source, if any.")
    checked_at: dt.datetime | None = Field(description="When the refresh job last ran for it.")
    method: str


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


async def _configs(pool: asyncpg.Pool) -> tuple[JobsConfig, HoldingProfilesConfig]:
    jobs = await load_active(pool, "jobs", JobsConfig)
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    return (
        jobs.body if jobs else JobsConfig(),
        profiles.body if profiles else HoldingProfilesConfig(),
    )


async def _status(pool: asyncpg.Pool) -> dict[str, Any]:
    raw = await pool.fetchval("select value from public.sync_state where key = $1", STATUS_KEY)
    return dict(json.loads(raw)) if raw else {}


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        loaded: dict[str, Any] = json.loads(value)
        return loaded
    return dict(value)


async def latest_fund(
    pool: asyncpg.Pool, fund: FundValuationSource, today: dt.date
) -> FundValuationOut | None:
    row = await pool.fetchrow(
        "select * from public.fund_valuation where fund_key = $1 order by as_of desc limit 1",
        fund.key,
    )
    if row is None:
        return None
    return FundValuationOut(
        key=fund.key,
        label=fund.label,
        provider=row["provider"],
        provider_label=PROVIDER_LABEL.get(row["provider"], row["provider"]),
        as_of=row["as_of"],
        pe=_f(row["pe"]),
        pb=_f(row["pb"]),
        index_label=fund.index_label or None,
        index_pe=_f(row["index_pe"]),
        index_pb=_f(row["index_pb"]),
        dividend_yield=_f(row["dividend_yield"]),
        dividend_yield_kind=row["dividend_yield_kind"],
        methodology=row["methodology"],
        source_url=row["source_url"],
        fetched_at=row["fetched_at"],
        stale=(today - row["as_of"]).days > FUND_STALE_DAYS,
    )


def _f(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


# ---------------------------------------------------------------------------
# Figures and ratios
# ---------------------------------------------------------------------------


def _stale(end: dt.date | None, today: dt.date) -> bool:
    return end is not None and (today - end).days > FIGURES_STALE_DAYS


def _figure_out(figure: Figure | None, today: dt.date) -> FigureOut | None:
    if figure is None:
        return None
    return FigureOut(
        value=figure.value,
        currency=figure.currency,
        period_start=figure.period_start,
        period_end=figure.period_end,
        method=figure.method,
        annual_only=figure.annual_only,
        stale=_stale(figure.period_end, today),
        inputs=figure.inputs,
    )


def _manual_figure(
    value: float | None, profile: HoldingProfile, what: str, today: dt.date
) -> FigureOut | None:
    manual = profile.manual_fundamentals
    if manual is None or value is None:
        return None
    return FigureOut(
        value=value,
        currency=manual.currency,
        period_start=None,
        period_end=manual.as_of,
        method=f"{what} entered manually"
        + (f": {manual.source_note}" if manual.source_note else ""),
        annual_only=False,
        stale=_stale(manual.as_of, today),
    )


class _Chosen(BaseModel):
    fundamentals: FundamentalsOut
    eps: FigureOut | None
    bvps: FigureOut | None
    dps: FigureOut | None
    steps: list[EpsStep]


def _from_row(row: asyncpg.Record, today: dt.date) -> tuple[_Chosen, SourceFigures]:
    figures = SourceFigures.model_validate(_json(row["figures"]))
    eps = figures.eps_ttm
    label_source = (
        "sec_instance"
        if row["source"] == "sec"
        and eps is not None
        and any(i.source == "sec_instance" for i in eps.inputs)
        else row["source"]
    )
    chosen = _Chosen(
        fundamentals=FundamentalsOut(
            source=row["source"],
            source_label=SOURCE_LABEL[label_source],
            basis=figures.basis,
            eps_kind=figures.eps_kind,
            document_ref=row["document_ref"],
            document_url=row["document_url"],
            fetched_at=row["fetched_at"],
            as_of=None,
            notes=figures.notes,
            last_error=row["last_error"],
        ),
        eps=_figure_out(figures.eps_ttm, today),
        bvps=_figure_out(figures.book_value_per_share, today),
        dps=_figure_out(figures.dividend_per_share, today),
        steps=figures.eps_steps,
    )
    return chosen, figures


def _from_manual(profile: HoldingProfile, today: dt.date) -> _Chosen | None:
    manual = profile.manual_fundamentals
    if manual is None:
        return None
    return _Chosen(
        fundamentals=FundamentalsOut(
            source="manual",
            source_label=SOURCE_LABEL["manual"],
            basis=None,
            eps_kind="as entered",
            document_ref=None,
            document_url=None,
            fetched_at=None,
            as_of=manual.as_of,
            notes=[manual.source_note] if manual.source_note else [],
            last_error=None,
        ),
        eps=_manual_figure(manual.eps_ttm, profile, "EPS", today),
        bvps=_manual_figure(manual.book_value_per_share, profile, "Book value per share", today),
        dps=_manual_figure(manual.dividend_per_share, profile, "Dividend per share", today),
        steps=[],
    )


def _pick(candidates: dict[str, _Chosen], preference: str) -> tuple[_Chosen | None, str | None]:
    if preference in ("sec", "esef", "manual"):
        found = candidates.get(preference)
        if found is None:
            return (
                None,
                f"the holding profile asks for {SOURCE_LABEL[preference]} figures; there are none",
            )
        return found, None
    automatic = [c for k, c in candidates.items() if k != "manual" and c.eps is not None]
    if automatic:
        best = max(
            automatic,
            key=lambda c: (
                c.eps.period_end if c.eps and c.eps.period_end else dt.date.min,
                c.fundamentals.source == "sec",
            ),
        )
        return best, None
    if "manual" in candidates:
        return candidates["manual"], None
    return None, None


def _convert(
    fx: FxTable | None, amount: float, from_ccy: str | None, to_ccy: str
) -> tuple[float | None, str | None]:
    if from_ccy is None:
        return None, "the price's currency is unknown"
    if fx is None:
        return (amount, None) if from_ccy == to_ccy else (None, "no reference rates stored")
    try:
        return float(fx.convert(Decimal(str(amount)), from_ccy, to_ccy)), None
    except MissingRateError as exc:
        return None, f"no {exc.source.upper()} reference rate for {exc.currency}"


def ratio(
    price: PriceOut | None,
    figure: FigureOut | None,
    per_listing: float,
    fx: FxTable | None,
    kind: Literal["pe", "pb", "yield"],
    missing: str,
) -> RatioOut:
    if price is None:
        return RatioOut(value=None, reason="no stored price")
    if figure is None:
        return RatioOut(value=None, reason=missing)
    per_unit = figure.value * per_listing
    if kind in ("pe", "pb") and per_unit <= 0:
        what = "negative earnings" if kind == "pe" else "negative book value"
        if per_unit == 0:
            what = "zero earnings" if kind == "pe" else "zero book value"
        return RatioOut(value=None, reason=f"n/m: {what}")
    converted, why = _convert(fx, price.close, price.currency, figure.currency)
    if converted is None:
        return RatioOut(value=None, reason=why)
    if kind == "yield":
        return RatioOut(value=per_unit / converted if converted > 0 else None, reason=None)
    return RatioOut(value=converted / per_unit, reason=None)


async def _price(
    pool: asyncpg.Pool, symbol: str | None, currency: str | None, today: dt.date
) -> PriceOut | None:
    if not symbol:
        return None
    row = await pool.fetchrow(
        "select date, close from public.price_bar where symbol = $1 and source = 'stooq'"
        " order by date desc limit 1",
        symbol,
    )
    if row is None:
        return None
    return PriceOut(
        symbol=symbol,
        close=float(row["close"]),
        currency=currency,
        date=row["date"],
        stale=(today - row["date"]).days > PRICE_STALE_DAYS,
    )


async def pe_history(
    pool: asyncpg.Pool,
    symbol: str,
    price_ccy: str | None,
    eps_ccy: str,
    steps: list[EpsStep],
    per_listing: float,
    source: FxSourceName,
    today: dt.date,
) -> list[PePoint]:
    """Weekly (last close of each ISO week) P/E over the EPS step in force on that day."""
    if not steps or price_ccy is None:
        return []
    since = today - dt.timedelta(days=HISTORY_DAYS)
    bars = await pool.fetch(
        "select date, close from public.price_bar where symbol = $1 and source = 'stooq'"
        " and date >= $2 order by date",
        symbol,
        since,
    )
    weekly: dict[tuple[int, int], asyncpg.Record] = {}
    for bar in bars:
        weekly[bar["date"].isocalendar()[:2]] = bar
    rates: list[FxRate] = []
    if price_ccy != eps_ccy:
        rows = await pool.fetch(
            "select source, rate_date, currency, rate, published_amount, published_rate,"
            " provider from public.fx_rate where source = $1 and rate_date >= $2",
            source,
            since - dt.timedelta(days=10),
        )
        rates = [FxRate(**dict(r)) for r in rows]
    out: list[PePoint] = []
    for bar in weekly.values():
        day = bar["date"]
        in_force = [s for s in steps if s.effective <= day]
        if not in_force:
            continue
        eps = in_force[-1].eps * per_listing
        if eps <= 0:
            continue
        close = float(bar["close"])
        if price_ccy != eps_ccy:
            try:
                close = float(
                    FxTable.as_of(source, rates, day).convert(
                        Decimal(str(close)), price_ccy, eps_ccy
                    )
                )
            except MissingRateError:
                continue
        out.append(PePoint(date=day, pe=round(close / eps, 4), eps=in_force[-1].eps))
    return sorted(out, key=lambda p: p.date)


HOLDING_METHOD = (
    "P/E = latest close / EPS over the trailing twelve months; P/B = close / book value per "
    "share; dividend yield = dividends per share over the trailing year / close. The close is "
    "converted into the figures' currency with the stored reference rates of its date, and "
    "per-share figures are multiplied by the shares one listed unit represents (depositary "
    "receipts). EPS is diluted, from the latest filings: four quarters (or the full year plus "
    "the year to date less the prior year's) where interim XBRL exists, else the latest full "
    "year (flagged as annual)."
)


async def holding_valuation(
    pool: asyncpg.Pool,
    ticker: str,
    *,
    today: dt.date | None = None,
    with_history: bool = True,
    configs: tuple[JobsConfig, HoldingProfilesConfig] | None = None,
    status: dict[str, Any] | None = None,
) -> HoldingValuation:
    today = today or dt.datetime.now(dt.UTC).date()
    jobs, profiles = configs or await _configs(pool)
    status = status if status is not None else await _status(pool)
    profile = profiles.for_ticker(ticker) or HoldingProfile(ticker=ticker)
    info = (await instrument_info(pool, [ticker])).get(ticker)
    symbol = (await symbols_for(pool, [ticker], profiles)).get(ticker)
    price = await _price(
        pool, symbol, price_currency(symbol, info.currency if info else None), today
    )
    source = await fx_source(pool)
    fx = await fx_table(pool, source, price.date if price else today)
    held = status.get("holdings", {}).get(ticker) or {}
    checked = held.get("checked_at")
    common: dict[str, Any] = {
        "ticker": ticker,
        "name": (info.name if info else None) or profile.company_name or None,
        "price": price,
        "errors": dict(held.get("errors") or {}),
        "checked_at": dt.datetime.fromisoformat(checked) if isinstance(checked, str) else None,
    }

    fund_source = fund_for_ticker(jobs, ticker)
    if fund_source is not None or (info is not None and info.is_fund):
        fund = await latest_fund(pool, fund_source, today) if fund_source else None
        reason = (
            "no fund figures stored yet (valuation refresh job)"
            if fund_source
            else "no fund valuation source configured for this ETF (Jobs config: valuation funds)"
        )
        return HoldingValuation(
            **common,
            kind="fund",
            pe=RatioOut(
                value=fund.pe if fund else None, reason=None if fund and fund.pe else reason
            ),
            pb=RatioOut(
                value=fund.pb if fund else None, reason=None if fund and fund.pb else reason
            ),
            dividend_yield=RatioOut(
                value=fund.dividend_yield if fund else None,
                reason=None
                if fund and fund.dividend_yield is not None
                else (
                    reason
                    if not fund
                    else f"{fund.provider_label} publishes no dividend yield for it"
                ),
            ),
            eps_ttm=None,
            book_value_per_share=None,
            dividend_per_share=None,
            shares_per_listing=1.0,
            fundamentals=None,
            alternatives=[],
            cross_check=None,
            fund=fund,
            pe_history=[],
            method="An ETF is valued at fund level: the P/E and P/B its provider publishes for "
            "the portfolio it holds, with the provider's methodology.",
        )

    rows = {
        r["source"]: r
        for r in await pool.fetch("select * from public.fundamentals where ticker = $1", ticker)
    }
    candidates: dict[str, _Chosen] = {}
    raw_figures: dict[str, SourceFigures] = {}
    for name in ("sec", "esef"):
        if name in rows:
            candidates[name], raw_figures[name] = _from_row(rows[name], today)
    manual = _from_manual(profile, today)
    if manual is not None:
        candidates["manual"] = manual
    chosen, pick_problem = _pick(candidates, profile.valuation_source)

    per_listing = profile.shares_per_listing or 1.0
    depositary_unknown = bool(info and info.is_depositary and profile.shares_per_listing is None)
    if chosen is None:
        missing = pick_problem or _missing_reason(profile, rows, held)
    else:
        missing = "not in the source's figures"
    eps = chosen.eps if chosen else None
    bvps = chosen.bvps if chosen else None
    dps = chosen.dps if chosen else None
    if depositary_unknown:
        dr = "depositary receipt: set 'shares per listing' in the holding profile"
        pe = pb = dy = RatioOut(value=None, reason=dr)
    else:
        pe = ratio(
            price,
            eps,
            per_listing,
            fx,
            "pe",
            missing if chosen is None else "no EPS in the figures",
        )
        pb = ratio(
            price,
            bvps,
            per_listing,
            fx,
            "pb",
            missing if chosen is None else "no book value per share in the figures",
        )
        dy = ratio(
            price,
            dps,
            per_listing,
            fx,
            "yield",
            missing if chosen is None else "no dividends per share in the figures",
        )

    alternatives: list[AlternativeOut] = []
    for key, candidate in candidates.items():
        if candidate is chosen:
            continue
        alternatives.append(
            _alternative(key, candidate.fundamentals.basis, candidate.eps, price, per_listing, fx)
        )
    for key, figures in raw_figures.items():
        for alt in figures.alternatives:
            alternatives.append(
                _alternative(
                    key, alt.basis, _figure_out(alt.eps_ttm, today), price, per_listing, fx
                )
            )

    cross = None
    if "finnhub" in rows:
        body = _json(rows["finnhub"]["figures"])
        cross = CrossCheckOut(
            symbol=body.get("symbol"),
            pe=body.get("pe"),
            pb=body.get("pb"),
            eps_ttm=body.get("eps_ttm"),
            dividend_yield=body.get("dividend_yield"),
            fetched_at=rows["finnhub"]["fetched_at"],
        )

    history: list[PePoint] = []
    if (
        with_history
        and chosen is not None
        and eps is not None
        and symbol
        and price
        and not depositary_unknown
    ):
        history = await pe_history(
            pool, symbol, price.currency, eps.currency, chosen.steps, per_listing, source, today
        )

    return HoldingValuation(
        **common,
        kind="stock",
        pe=pe,
        pb=pb,
        dividend_yield=dy,
        eps_ttm=eps,
        book_value_per_share=bvps,
        dividend_per_share=dps,
        shares_per_listing=per_listing,
        fundamentals=chosen.fundamentals if chosen else None,
        alternatives=alternatives,
        cross_check=cross,
        fund=None,
        pe_history=history,
        method=HOLDING_METHOD,
    )


def _missing_reason(
    profile: HoldingProfile, rows: dict[str, asyncpg.Record], held: dict[str, Any]
) -> str:
    errors = held.get("errors") or {}
    if errors:
        return "the last refresh failed: " + "; ".join(f"{k}: {v}" for k, v in errors.items())
    notes = [str(held[k]) for k in ("sec", "esef") if isinstance(held.get(k), str)]
    if notes:
        return (
            "; ".join(notes) + ". Enter the figures in the holding profile (manual fundamentals)."
        )
    if not held:
        return "not fetched yet (valuation refresh job)"
    return "no source has figures; enter them in the holding profile (manual fundamentals)"


def _alternative(
    key: str,
    basis: str | None,
    eps: FigureOut | None,
    price: PriceOut | None,
    per_listing: float,
    fx: FxTable | None,
) -> AlternativeOut:
    pe = ratio(price, eps, per_listing, fx, "pe", "")
    return AlternativeOut(
        source=key,
        source_label=SOURCE_LABEL[key],
        basis=basis,
        eps_ttm=eps.value if eps else None,
        currency=eps.currency if eps else None,
        period_end=eps.period_end if eps else None,
        method=eps.method if eps else None,
        pe=pe.value,
    )


# ---------------------------------------------------------------------------
# Portfolio vs benchmark
# ---------------------------------------------------------------------------


class Coverage(BaseModel):
    pe: float = Field(description="Share of invested value (0..1) with a P/E (incl. excluded n/m).")
    pb: float
    dividend_yield: float


class Aggregate(BaseModel):
    pe: float | None
    pb: float | None
    dividend_yield: float | None = Field(description="Fraction.")
    coverage: Coverage


class HoldingContribution(BaseModel):
    ticker: str
    name: str | None
    kind: Literal["stock", "fund"]
    weight: float = Field(description="Share of invested value (cash excluded), 0..1.")
    pe: float | None = Field(description="As used: capped at 60; null when excluded or missing.")
    pe_raw: float | None
    pe_note: str | None
    pb: float | None
    pb_raw: float | None
    pb_note: str | None
    dividend_yield: float | None
    source_label: str | None
    stale: bool


class BenchmarkValuation(BaseModel):
    label: str = Field(description="What is compared: 'S&P 500', 'FTSE All-World'.")
    symbol: str | None = Field(description="The Prices benchmark symbol it stands for.")
    via: str = Field(description="Whose figures: 'CSPX fund figures (iShares)'.")
    fund_key: str
    pe: float | None
    pb: float | None
    dividend_yield: float | None
    dividend_yield_kind: str | None
    as_of: dt.date
    source_url: str
    methodology: str | None
    stale: bool


class PortfolioValuation(BaseModel):
    as_of: dt.date | None = Field(description="The snapshot the weights come from.")
    base_currency: str | None
    portfolio: Aggregate
    benchmark: BenchmarkValuation | None = Field(
        description="The Prices config's first benchmark, via the fund configured for it."
    )
    comparisons: list[BenchmarkValuation] = Field(
        description="Every configured fund's (and, where published, its index's) figures."
    )
    holdings: list[HoldingContribution]
    method: str


def aggregate(rows: list[HoldingContribution]) -> Aggregate:
    total = sum(r.weight for r in rows)

    def harmonic(values: list[tuple[float, float]]) -> float | None:
        weight = sum(w for w, _ in values)
        inverse = sum(w / v for w, v in values if v > 0)
        return weight / inverse if weight > 0 and inverse > 0 else None

    pe_rows = [r for r in rows if r.pe is not None]
    # Holdings excluded for negative earnings count as covered (the figure exists).
    pe_covered = sum(
        r.weight for r in rows if r.pe is not None or (r.pe_note or "").startswith("n/m")
    )
    pb_rows = [r for r in rows if r.pb is not None]
    pb_covered = sum(
        r.weight for r in rows if r.pb is not None or (r.pb_note or "").startswith("n/m")
    )
    dy_rows = [r for r in rows if r.dividend_yield is not None]
    dy_weight = sum(r.weight for r in dy_rows)
    return Aggregate(
        pe=harmonic([(r.weight, r.pe) for r in pe_rows if r.pe is not None]),
        pb=harmonic([(r.weight, r.pb) for r in pb_rows if r.pb is not None]),
        dividend_yield=(
            sum(r.weight * (r.dividend_yield or 0) for r in dy_rows) / dy_weight
            if dy_weight > 0
            else None
        ),
        coverage=Coverage(
            pe=pe_covered / total if total else 0.0,
            pb=pb_covered / total if total else 0.0,
            dividend_yield=dy_weight / total if total else 0.0,
        ),
    )


def contribution(valuation: HoldingValuation, weight: float) -> HoldingContribution:
    pe_raw = valuation.pe.value
    pb_raw = valuation.pb.value
    pe_note = valuation.pe.reason
    pe = None
    if pe_raw is not None and pe_raw > 0:
        pe = min(pe_raw, PE_CAP)
        if pe_raw > PE_CAP:
            pe_note = f"capped at {PE_CAP:.0f}"
    pb = min(pb_raw, PB_CAP) if pb_raw is not None and pb_raw > 0 else None
    pb_note = valuation.pb.reason
    if pb_raw is not None and pb_raw > PB_CAP:
        pb_note = f"capped at {PB_CAP:.0f}"
    label = (
        f"{valuation.fund.provider_label} fund figures"
        if valuation.fund
        else valuation.fundamentals.source_label
        if valuation.fundamentals
        else None
    )
    stale = bool(
        (valuation.price and valuation.price.stale)
        or (valuation.eps_ttm and valuation.eps_ttm.stale)
        or (valuation.fund and valuation.fund.stale)
    )
    return HoldingContribution(
        ticker=valuation.ticker,
        name=valuation.name,
        kind=valuation.kind,
        weight=weight,
        pe=pe,
        pe_raw=pe_raw,
        pe_note=pe_note,
        pb=pb,
        pb_raw=pb_raw,
        pb_note=pb_note,
        dividend_yield=valuation.dividend_yield.value,
        source_label=label,
        stale=stale,
    )


def _benchmarks(
    fund: FundValuationSource, figures: FundValuationOut, symbol: str | None
) -> list[BenchmarkValuation]:
    out = [
        BenchmarkValuation(
            label=fund.index_label or fund.label,
            symbol=symbol,
            via=f"{fund.key} fund figures ({figures.provider_label})",
            fund_key=fund.key,
            pe=figures.pe,
            pb=figures.pb,
            dividend_yield=figures.dividend_yield,
            dividend_yield_kind=figures.dividend_yield_kind,
            as_of=figures.as_of,
            source_url=figures.source_url,
            methodology=figures.methodology,
            stale=figures.stale,
        )
    ]
    if figures.index_pe is not None or figures.index_pb is not None:
        out.append(
            BenchmarkValuation(
                label=f"{fund.index_label or fund.label} (index)",
                symbol=symbol,
                via=f"index figures published by {figures.provider_label} for {fund.key}",
                fund_key=fund.key,
                pe=figures.index_pe,
                pb=figures.index_pb,
                dividend_yield=None,
                dividend_yield_kind=None,
                as_of=figures.as_of,
                source_url=figures.source_url,
                methodology=figures.methodology,
                stale=figures.stale,
            )
        )
    return out


async def portfolio_valuation(
    pool: asyncpg.Pool, today: dt.date | None = None
) -> PortfolioValuation:
    today = today or dt.datetime.now(dt.UTC).date()
    configs = await _configs(pool)
    jobs, _ = configs
    status = await _status(pool)
    snapshot = await pool.fetchrow(
        "select snapshot_date, base_currency from public.account_snapshot"
        " order by snapshot_date desc limit 1"
    )
    positions = []
    if snapshot is not None:
        positions = await pool.fetch(
            "select ticker, value_base from public.position_snapshot where snapshot_date = $1"
            " and value_base > 0 order by value_base desc, ticker",
            snapshot["snapshot_date"],
        )
    invested = sum(float(p["value_base"]) for p in positions)
    holdings = []
    for p in positions:
        valuation = await holding_valuation(
            pool, p["ticker"], today=today, with_history=False, configs=configs, status=status
        )
        holdings.append(contribution(valuation, float(p["value_base"]) / invested))

    benchmark_symbol = jobs.prices.benchmark_symbols[0] if jobs.prices.benchmark_symbols else None
    benchmark = None
    comparisons: list[BenchmarkValuation] = []
    for fund in jobs.valuation.funds:
        figures = await latest_fund(pool, fund, today)
        if figures is None:
            continue
        symbol = next(iter(fund.benchmark_symbols), None)
        rows = _benchmarks(fund, figures, symbol)
        comparisons += rows
        if benchmark is None and benchmark_symbol and benchmark_symbol in fund.benchmark_symbols:
            benchmark = rows[0]
    return PortfolioValuation(
        as_of=snapshot["snapshot_date"] if snapshot else None,
        base_currency=snapshot["base_currency"] if snapshot else None,
        portfolio=aggregate(holdings),
        benchmark=benchmark,
        comparisons=comparisons,
        holdings=holdings,
        method=PORTFOLIO_METHOD,
    )
