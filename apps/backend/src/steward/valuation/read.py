"""Valuation read models: a holding's P/E, P/B and dividend yield, and the portfolio's
against its benchmark. Computed on read from stored data only: the latest Stooq close
(`price_bar`, from the one series the price layer picks per holding: Stooq, else the
Yahoo fallback), the stored reference rates (`fx_rate`), the fundamentals the
`valuation_refresh` job stored, the holding profile's manual figures and the ETF providers'
fund figures. Nothing is estimated; a ratio that cannot be computed is null with a reason.
"""

import datetime as dt
import json
import math
from collections.abc import Iterable
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import FundValuationSource, JobsConfig
from steward.config.store import load_active
from steward.fundamentals.shares import DEFAULT_BASIS_BY_CIK, basis_for
from steward.fx.convert import FxRate, FxSourceName, FxTable, MissingRateError
from steward.reference.price_series import SeriesRef, choose_one, holding_symbols
from steward.valuation.collect import STATUS_KEY
from steward.valuation.common import (
    fund_for_ticker,
    fx_source,
    fx_table,
    instrument_info,
    price_currency,
)
from steward.valuation.fundamentals import EpsStep, Figure, FundamentalFactRef, SourceFigures

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


class ValuationRatio(BaseModel):
    value: float | None = Field(description="Null when it cannot be computed (see reason).")
    reason: str | None = Field(description="Why the value is null, e.g. 'n/m: negative earnings'.")
    source_label: str | None = Field(
        default=None, description="Provider used when this individual ratio is a fallback."
    )


class ValuationPrice(BaseModel):
    symbol: str
    close: float
    currency: str | None
    date: dt.date
    source: str = "Stooq daily close"
    stale: bool


class PerShareFigure(BaseModel):
    value: float
    currency: str
    period_start: dt.date | None
    period_end: dt.date | None
    method: str
    annual_only: bool
    stale: bool = Field(description="The period ended more than 200 days ago.")
    inputs: list[FundamentalFactRef] = Field(default_factory=list)


class FundamentalsSource(BaseModel):
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


class ValuationAlternative(BaseModel):
    source: SourceName
    source_label: str
    basis: str | None
    eps_ttm: float | None
    currency: str | None
    period_end: dt.date | None
    method: str | None
    pe: float | None


class ValuationCrossCheck(BaseModel):
    source: str = "finnhub"
    source_label: str = SOURCE_LABEL["finnhub"]
    symbol: str | None
    pe: float | None
    pb: float | None
    eps_ttm: float | None
    dividend_yield: float | None
    fetched_at: dt.datetime


class FundValuation(BaseModel):
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
    price: ValuationPrice | None
    pe: ValuationRatio
    pb: ValuationRatio
    dividend_yield: ValuationRatio = Field(description="Fraction (0.012 = 1.2 %).")
    ps: ValuationRatio = Field(
        default_factory=lambda: ValuationRatio(value=None, reason="not available for this security")
    )
    peg: ValuationRatio = Field(
        default_factory=lambda: ValuationRatio(value=None, reason="not available for this security")
    )
    gross_margin: ValuationRatio = Field(
        default_factory=lambda: ValuationRatio(value=None, reason="not available for this security")
    )
    operating_margin: ValuationRatio = Field(
        default_factory=lambda: ValuationRatio(value=None, reason="not available for this security")
    )
    net_margin: ValuationRatio = Field(
        default_factory=lambda: ValuationRatio(value=None, reason="not available for this security")
    )
    roe: ValuationRatio = Field(
        default_factory=lambda: ValuationRatio(value=None, reason="not available for this security")
    )
    roic: ValuationRatio = Field(
        default_factory=lambda: ValuationRatio(value=None, reason="not available for this security")
    )
    eps_ttm: PerShareFigure | None
    book_value_per_share: PerShareFigure | None
    dividend_per_share: PerShareFigure | None
    ads_ratio: float = Field(
        description="Home-market shares one priced share represents (TSMC's ADS = 5); "
        "per-share figures are multiplied by it."
    )
    ads_ratio_source: str = Field(
        description="Where the ratio comes from: the holding profile's share basis, the "
        "ratio the 20-F states (SEC XBRL job), the documented default, or 1."
    )
    fundamentals: FundamentalsSource | None = Field(description="The figures the ratios use.")
    alternatives: list[ValuationAlternative] = Field(
        description="Other sources or accounting bases, for comparison (not used)."
    )
    cross_check: ValuationCrossCheck | None
    fund: FundValuation | None = Field(description="ETFs: the provider's fund figures.")
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
) -> FundValuation | None:
    row = await pool.fetchrow(
        "select * from public.fund_valuation where fund_key = $1 order by as_of desc limit 1",
        fund.key,
    )
    if row is None:
        return None
    return FundValuation(
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


def _figure_out(figure: Figure | None, today: dt.date) -> PerShareFigure | None:
    if figure is None:
        return None
    return PerShareFigure(
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
) -> PerShareFigure | None:
    manual = profile.manual_fundamentals
    if manual is None or value is None:
        return None
    return PerShareFigure(
        value=value,
        currency=manual.currency,
        period_start=None,
        period_end=manual.as_of,
        method=f"{what} entered manually",
        annual_only=False,
        stale=_stale(manual.as_of, today),
    )


class _Chosen(BaseModel):
    fundamentals: FundamentalsSource
    eps: PerShareFigure | None
    bvps: PerShareFigure | None
    dps: PerShareFigure | None
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
        fundamentals=FundamentalsSource(
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
        fundamentals=FundamentalsSource(
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
    price: ValuationPrice | None,
    figure: PerShareFigure | None,
    per_listing: float,
    fx: FxTable | None,
    kind: Literal["pe", "pb", "yield"],
    missing: str,
) -> ValuationRatio:
    if price is None:
        return ValuationRatio(value=None, reason="no stored price")
    if figure is None:
        return ValuationRatio(value=None, reason=missing)
    per_unit = figure.value * per_listing
    if kind in ("pe", "pb") and per_unit <= 0:
        what = "negative earnings" if kind == "pe" else "negative book value"
        if per_unit == 0:
            what = "zero earnings" if kind == "pe" else "zero book value"
        return ValuationRatio(value=None, reason=f"n/m: {what}")
    converted, why = _convert(fx, price.close, price.currency, figure.currency)
    if converted is None:
        return ValuationRatio(value=None, reason=why)
    if kind == "yield":
        return ValuationRatio(value=per_unit / converted if converted > 0 else None, reason=None)
    return ValuationRatio(value=converted / per_unit, reason=None)


async def _price(
    pool: asyncpg.Pool, ref: SeriesRef | None, currency: str | None, today: dt.date
) -> ValuationPrice | None:
    """The latest close of the holding's chosen daily series (Stooq, else Yahoo). A Yahoo
    close carries its own quote currency; a Stooq close is in its market's currency."""
    if ref is None:
        return None
    row = await pool.fetchrow(
        "select date, close, currency from public.price_bar where symbol = $1 and source = $2"
        " order by date desc limit 1",
        ref.symbol,
        ref.source,
    )
    if row is None:
        return None
    return ValuationPrice(
        symbol=ref.symbol,
        close=float(row["close"]),
        currency=row["currency"] or currency,
        date=row["date"],
        source=f"{ref.name} daily close" + (" (unofficial)" if ref.source == "yahoo" else ""),
        stale=(today - row["date"]).days > PRICE_STALE_DAYS,
    )


async def pe_history(
    pool: asyncpg.Pool,
    ref: SeriesRef,
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
        "select date, close from public.price_bar where symbol = $1 and source = $2"
        " and date >= $3 order by date",
        ref.symbol,
        ref.source,
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
    symbols = (await holding_symbols(pool, [ticker], profiles)).get(ticker)
    ref = await choose_one(pool, symbols) if symbols else None
    stooq_symbol = symbols.stooq if symbols else None
    price = await _price(
        pool, ref, price_currency(stooq_symbol, info.currency if info else None), today
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
            pe=ValuationRatio(
                value=fund.pe if fund else None, reason=None if fund and fund.pe else reason
            ),
            pb=ValuationRatio(
                value=fund.pb if fund else None, reason=None if fund and fund.pb else reason
            ),
            dividend_yield=ValuationRatio(
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
            ads_ratio=1.0,
            ads_ratio_source="fund units",
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

    per_listing, per_listing_source = await listing_ratio(pool, ticker, profile)
    if chosen is None:
        missing = pick_problem or _missing_reason(profile, rows, held)
    else:
        missing = "not in the source's figures"
    eps = chosen.eps if chosen else None
    bvps = chosen.bvps if chosen else None
    dps = chosen.dps if chosen else None
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

    source_figures = raw_figures.get(chosen.fundamentals.source) if chosen else None
    if source_figures is not None and chosen is not None and chosen.fundamentals.source == "manual":
        source_figures = None
    revenue = source_figures.revenue_ttm if source_figures else None
    ps = ValuationRatio(value=None, reason="no revenue figure in the chosen source")
    if price is None:
        ps.reason = "no stored price"
    elif revenue is not None and source_figures is not None:
        if revenue.value <= 0:
            ps.reason = "n/m: zero or negative revenue"
        elif source_figures.shares_outstanding is None:
            ps.reason = "no share count for market capitalization"
        elif revenue.currency != (
            chosen.eps.currency if chosen and chosen.eps else revenue.currency
        ):
            ps.reason = "revenue and per-share figures use different currencies"
        else:
            market_price, why = _convert(fx, price.close, price.currency, revenue.currency)
            if market_price is None:
                ps.reason = why or "cannot convert price to revenue currency"
            else:
                market_cap = market_price * source_figures.shares_outstanding * per_listing
                ps = ValuationRatio(value=market_cap / revenue.value, reason=None)

    def margin(numerator: Figure | None, label: str) -> ValuationRatio:
        if numerator is None or revenue is None:
            return ValuationRatio(value=None, reason=f"no {label} or revenue figure")
        if numerator.currency != revenue.currency:
            return ValuationRatio(value=None, reason="figures use different currencies")
        if revenue.value <= 0:
            return ValuationRatio(value=None, reason="n/m: zero or negative revenue")
        return ValuationRatio(value=numerator.value / revenue.value, reason=None)

    gross_margin = margin(
        source_figures.gross_profit_ttm if source_figures else None, "gross profit"
    )
    operating_margin = margin(
        source_figures.operating_income_ttm if source_figures else None, "operating income"
    )
    net_margin = margin(source_figures.net_income_ttm if source_figures else None, "net income")

    roe = ValuationRatio(value=None, reason="no net income or balance-sheet equity figures")
    roic = ValuationRatio(value=None, reason="no operating income or invested-capital figures")
    if source_figures is not None:
        net_income = source_figures.net_income_ttm
        equity_now, equity_then = source_figures.equity_value, source_figures.equity_previous_value
        if (
            net_income
            and equity_now
            and equity_then
            and len({net_income.currency, equity_now.currency, equity_then.currency}) == 1
        ):
            average_equity = (equity_now.value + equity_then.value) / 2
            roe = ValuationRatio(
                value=net_income.value / average_equity if average_equity > 0 else None,
                reason=None if average_equity > 0 else "n/m: non-positive average equity",
            )
        elif net_income is not None:
            roe.reason = "no comparable prior and current equity figures"

        operating = source_figures.operating_income_ttm
        debt, cash = source_figures.debt_value, source_figures.cash_value
        equity = source_figures.equity_value
        tax, pretax = source_figures.income_tax_ttm, source_figures.pretax_income_ttm
        if (
            operating
            and equity
            and debt
            and cash
            and len({operating.currency, equity.currency, debt.currency, cash.currency}) == 1
        ):
            invested = equity.value + debt.value - cash.value
            if (
                tax
                and pretax
                and pretax.value > 0
                and tax.currency == operating.currency
                and pretax.currency == operating.currency
                and invested > 0
            ):
                tax_rate = min(1.0, max(0.0, tax.value / pretax.value))
                roic = ValuationRatio(
                    value=operating.value * (1.0 - tax_rate) / invested, reason=None
                )
            else:
                roic.reason = (
                    "no comparable tax, pre-tax income, or positive invested-capital figure"
                )
        elif operating is not None:
            roic.reason = (
                "equity, debt, cash and operating income must all be reported in one currency"
            )

    peg = ValuationRatio(value=None, reason="not enough positive EPS history to estimate growth")
    eps_steps = merged_steps(chosen, candidates.values()) if chosen is not None else []
    if pe.value is not None and len(eps_steps) >= 2:
        latest_step = eps_steps[-1]
        previous_steps = [
            step
            for step in eps_steps[:-1]
            if 300 <= (latest_step.period_end - step.period_end).days <= 430
        ]
        if previous_steps:
            previous_step = previous_steps[-1]
            if previous_step.eps > 0 and latest_step.eps > 0:
                span = (latest_step.period_end - previous_step.period_end).days
                annual_growth = (latest_step.eps / previous_step.eps) ** (365 / span) - 1
                if annual_growth > 0:
                    peg = ValuationRatio(value=pe.value / (annual_growth * 100), reason=None)
                else:
                    peg.reason = "n/m: EPS did not grow over the available year"
            else:
                peg.reason = "n/m: non-positive EPS in the growth period"

    # Alpha Vantage fills only gaps; computable SEC/ESEF ratios keep precedence.
    alpha = (
        _json(rows["alphavantage"]["figures"])
        if profile.valuation_source == "auto" and "alphavantage" in rows
        else {}
    )

    def alpha_number(key: str, *, positive: bool = False) -> float | None:
        value = alpha.get(key)
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed) or (positive and parsed <= 0):
            return None
        return parsed

    def fill(ratio_value: ValuationRatio, key: str, *, positive: bool = False) -> ValuationRatio:
        if ratio_value.value is not None or (ratio_value.reason or "").startswith("n/m:"):
            return ratio_value
        value = alpha_number(key, positive=positive)
        return (
            ValuationRatio(value=value, reason=None, source_label="Alpha Vantage Company Overview")
            if value is not None
            else ratio_value
        )

    pe = fill(pe, "PERatio", positive=True)
    pb = fill(pb, "PriceToBookRatio", positive=True)
    dy = fill(dy, "DividendYield")
    ps = fill(ps, "PriceToSalesRatioTTM", positive=True)
    peg = fill(peg, "PEGRatio", positive=True)
    if gross_margin.value is None:
        gross_profit, revenue_total = alpha_number("GrossProfitTTM"), alpha_number("RevenueTTM")
        if gross_profit is not None and revenue_total is not None and revenue_total > 0:
            gross_margin = ValuationRatio(
                value=gross_profit / revenue_total,
                reason=None,
                source_label="Alpha Vantage Company Overview",
            )
    operating_margin = fill(operating_margin, "OperatingMarginTTM")
    net_margin = fill(net_margin, "ProfitMargin")
    roe = fill(roe, "ReturnOnEquityTTM")

    alternatives: list[ValuationAlternative] = []
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
        cross = ValuationCrossCheck(
            symbol=body.get("symbol"),
            pe=body.get("pe"),
            pb=body.get("pb"),
            eps_ttm=body.get("eps_ttm"),
            dividend_yield=body.get("dividend_yield"),
            fetched_at=rows["finnhub"]["fetched_at"],
        )

    history: list[PePoint] = []
    if with_history and chosen is not None and eps is not None and ref and price:
        history = await pe_history(
            pool,
            ref,
            price.currency,
            eps.currency,
            merged_steps(chosen, candidates.values()),
            per_listing,
            source,
            today,
        )

    return HoldingValuation(
        **common,
        kind="stock",
        pe=pe,
        pb=pb,
        dividend_yield=dy,
        ps=ps,
        peg=peg,
        gross_margin=gross_margin,
        operating_margin=operating_margin,
        net_margin=net_margin,
        roe=roe,
        roic=roic,
        eps_ttm=eps,
        book_value_per_share=bvps,
        dividend_per_share=dps,
        ads_ratio=per_listing,
        ads_ratio_source=per_listing_source,
        fundamentals=chosen.fundamentals if chosen else None,
        alternatives=alternatives,
        cross_check=cross,
        fund=None,
        pe_history=history,
        method=HOLDING_METHOD,
    )


async def listing_ratio(
    pool: asyncpg.Pool, ticker: str, profile: HoldingProfile
) -> tuple[float, str]:
    """Home-market shares per priced share, from the one setting the SEC XBRL package owns
    (`share_basis.ads_ratio`): the holding profile's value, else the ratio the latest 20-F
    states (read by the SEC XBRL job into `shares_outstanding`), else the documented
    default by CIK (TSMC 5), else 1."""
    if profile.share_basis.ads_ratio is not None:
        return float(profile.share_basis.ads_ratio), "holding profile (share basis)"
    row = await pool.fetchrow(
        "select s.ads_ratio_reported, s.form, s.accession, i.cik from public.instrument i"
        " left join lateral (select ads_ratio_reported, form, accession"
        "   from public.shares_outstanding where ticker = i.t212_ticker"
        "   and ads_ratio_reported is not null order by as_of desc, fetched_at desc limit 1) s"
        "   on true where i.t212_ticker = $1",
        ticker,
    )
    if row is not None and row["ads_ratio_reported"] is not None:
        where = " ".join(x for x in (row["form"], row["accession"]) if x) or "annual report"
        return float(row["ads_ratio_reported"]), f"stated in the {where}"
    cik = profile.sec_cik.zfill(10) if profile.sec_cik else (row["cik"] if row else None)
    default = DEFAULT_BASIS_BY_CIK.get(cik or "")
    if default is not None and default.ads_ratio is not None:
        return float(basis_for(profile, cik).ads_ratio), f"default: {default.note}"
    return 1.0, "one home-market share per listed share (default)"


def merged_steps(chosen: _Chosen, candidates: Iterable[_Chosen]) -> list[EpsStep]:
    """The chosen source's EPS steps, preceded by another automatic source's steps (same
    currency and basis) from before the chosen source's first one: ESEF interim reports go
    back only so far, the SEC's annual figures further."""
    steps = list(chosen.steps)
    if not steps or chosen.eps is None:
        return steps
    first = steps[0].effective
    for other in candidates:
        if (
            other is chosen
            or other.eps is None
            or other.fundamentals.source == "manual"
            or other.eps.currency != chosen.eps.currency
            or other.fundamentals.basis != chosen.fundamentals.basis
        ):
            continue
        steps = [s for s in other.steps if s.effective < first] + steps
        break
    return steps


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
    eps: PerShareFigure | None,
    price: ValuationPrice | None,
    per_listing: float,
    fx: FxTable | None,
) -> ValuationAlternative:
    pe = ratio(price, eps, per_listing, fx, "pe", "")
    return ValuationAlternative(
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


class ValuationCoverage(BaseModel):
    pe: float = Field(description="Share of invested value (0..1) with a P/E (incl. excluded n/m).")
    pb: float
    dividend_yield: float


class ValuationAggregate(BaseModel):
    pe: float | None
    pb: float | None
    dividend_yield: float | None = Field(description="Fraction.")
    coverage: ValuationCoverage


class ValuationContribution(BaseModel):
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
    portfolio: ValuationAggregate
    benchmark: BenchmarkValuation | None = Field(
        description="The Prices config's first benchmark, via the fund configured for it."
    )
    comparisons: list[BenchmarkValuation] = Field(
        description="Every configured fund's (and, where published, its index's) figures."
    )
    holdings: list[ValuationContribution]
    method: str


def aggregate(rows: list[ValuationContribution]) -> ValuationAggregate:
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
    return ValuationAggregate(
        pe=harmonic([(r.weight, r.pe) for r in pe_rows if r.pe is not None]),
        pb=harmonic([(r.weight, r.pb) for r in pb_rows if r.pb is not None]),
        dividend_yield=(
            sum(r.weight * (r.dividend_yield or 0) for r in dy_rows) / dy_weight
            if dy_weight > 0
            else None
        ),
        coverage=ValuationCoverage(
            pe=pe_covered / total if total else 0.0,
            pb=pb_covered / total if total else 0.0,
            dividend_yield=dy_weight / total if total else 0.0,
        ),
    )


def contribution(valuation: HoldingValuation, weight: float) -> ValuationContribution:
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
    return ValuationContribution(
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
    fund: FundValuationSource, figures: FundValuation, symbol: str | None
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
