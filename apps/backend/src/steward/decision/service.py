"""Database-facing assembly for the Phase 6 decision tools. Reads only: nothing here writes to
Trading212 or to a stored holding; saved scenarios go through `steward.saved`."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable
from decimal import Decimal

import asyncpg
from pydantic import BaseModel, Field, field_validator

from steward.analytics import risk as stats
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.performance import PerformanceSettings
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig
from steward.decision import covariance
from steward.decision import pretrade as pt
from steward.decision import rebalance as rb
from steward.decision.lot_impact import OpenLot, SaleImpact, consume
from steward.decision.series import SeriesBundle, aligned, load_bundle
from steward.decision.shocks import (
    MeasuredInputs,
    Sensitivity,
    ShockAssumptions,
    ShockScenarioResult,
    ShockSet,
    apply_shocks,
    standard_set,
)
from steward.fx.convert import normalise_currency
from steward.fx.history import load_fx_history
from steward.lookthrough.read import PortfolioIn
from steward.performance.reports import LotsResponse
from steward.performance.service import lots_report
from steward.performance.settings import load_latest_settings
from steward.portfolio.drift import HoldingValue, compute_drift
from steward.portfolio.risk import _risk_free
from steward.reference import risk as ref_risk
from steward.reference.price_series import choose_series, holding_symbols
from steward.reference.read import load_series
from steward.xray import service as xray_service
from steward.xray.scenario import ProposalError, ProposedRequest, ProposedWeight, XrayProposal

log = logging.getLogger(__name__)

SERIES_DAYS = 365
CONTEXT_SERIES = (
    "fed_funds",
    "ecb_deposit_rate",
    "euro_area_10y_yield",
    "us_hy_spread",
    "czeonia",
    "estr",
    "us_unemployment",
)


class NoStrategy(Exception):
    pass


class NoHoldings(Exception):
    pass


# ------------------------------------------------------------------------------- scenario run


class MeasuredLine(BaseModel):
    ticker: str
    label: str | None
    kind: str = Field(description="equity beta or duration")
    value: float | None
    source: str
    detail: str


class ScenarioMacroContext(BaseModel):
    series_id: str
    label: str
    source: str
    unit: str | None
    period: dt.date
    value: float
    year_ago: float | None
    change: float | None
    url: str | None


class ShockRun(BaseModel):
    as_of: str
    benchmark_symbol: str | None
    result: ShockScenarioResult
    measured: list[MeasuredLine]
    context: list[ScenarioMacroContext]
    foreign_currencies: list[str]
    notes: list[str]


class StandardLine(BaseModel):
    name: str
    shocks: ShockSet
    pnl_pct: float
    pnl: float
    unshocked_weight: float


class StandardTable(BaseModel):
    as_of: str
    base_currency: str | None
    total_value: float
    lines: list[StandardLine]
    assumptions: ShockAssumptions
    notes: list[str]


async def macro_context(pool: asyncpg.Pool) -> list[ScenarioMacroContext]:
    rows = await pool.fetch(
        "select distinct on (series_id, source) series_id, source, label, unit, period, value, url"
        " from public.macro_series where series_id = any($1::text[]) and value is not null"
        " order by series_id, source, period desc",
        list(CONTEXT_SERIES),
    )
    out: list[ScenarioMacroContext] = []
    for r in rows:
        year_ago = await pool.fetchval(
            "select value from public.macro_series where series_id=$1 and source=$2"
            " and period <= $3 and value is not null order by period desc limit 1",
            r["series_id"],
            r["source"],
            r["period"] - dt.timedelta(days=365),
        )
        value = float(r["value"])
        ago = None if year_ago is None else float(year_ago)
        out.append(
            ScenarioMacroContext(
                series_id=r["series_id"],
                label=r["label"] or r["series_id"],
                source=r["source"],
                unit=r["unit"],
                period=r["period"],
                value=value,
                year_ago=ago,
                change=None if ago is None else value - ago,
                url=r["url"],
            )
        )
    return out


async def recorded_durations(
    pool: asyncpg.Pool, owner_id: str, loaded: xray_service.Loaded
) -> dict[str, Sensitivity]:
    """Effective duration the owner (or the issuer sync) recorded for a held fund, by ticker."""
    rows = await pool.fetch(
        "select distinct on (fund_isin) fund_isin, value, source_url, as_of"
        " from public.fund_owner_fact where owner_id=$1 and key='effective_duration_years'"
        " order by fund_isin, id desc",
        owner_id,
    )
    by_isin = {r["fund_isin"]: r for r in rows}
    out: dict[str, Sensitivity] = {}
    for key, info in loaded.leafset.channels.items():
        row = by_isin.get(key)
        if row is None or not info.ticker:
            continue
        try:
            years = float(row["value"])
        except ValueError:
            continue
        stated = f" as of {row['as_of']}" if row["as_of"] else ""
        out[info.ticker] = Sensitivity(
            value=years, source="recorded", detail=f"recorded for the fund{stated}"
        )
    return out


def measured_betas(
    bundle: SeriesBundle, held: dict[str, str | None]
) -> tuple[dict[str, Sensitivity], dict[str, str]]:
    """Regression beta of each held ticker's daily returns on the benchmark's, where the sample
    and the fit meet the thresholds; else the reason."""
    betas: dict[str, Sensitivity] = {}
    reasons: dict[str, str] = {}
    if not bundle.benchmark:
        why = bundle.benchmark_note or "no benchmark is configured"
        return betas, dict.fromkeys(held, why)
    for ticker in held:
        series = bundle.holdings.get(ticker)
        if not series:
            reasons[ticker] = "no snapshot history for the holding"
            continue
        days, a, b = aligned(series, bundle.benchmark)
        got, r2 = stats.regression_sensitivity(a, b)
        if got.value is None:
            reasons[ticker] = got.warnings[0] if got.warnings else "no valid fit"
            continue
        betas[ticker] = Sensitivity(
            value=float(got.value),
            source="measured",
            detail=f"{len(days)} days against {bundle.benchmark_symbol}, R squared "
            f"{float(r2 or 0):.2f}",
        )
    return betas, reasons


async def _scenario_inputs(
    pool: asyncpg.Pool, owner_id: str, loaded: xray_service.Loaded
) -> tuple[MeasuredInputs, SeriesBundle]:
    bundle = await load_bundle(pool, SERIES_DAYS)
    held: dict[str, str | None] = {
        info.ticker: info.label for info in loaded.leafset.channels.values() if info.ticker
    }
    for leaf in loaded.leafset.leaves:
        if leaf.channel_kind == "direct" and leaf.ticker:
            held.setdefault(leaf.ticker, leaf.name)
    betas, reasons = measured_betas(bundle, held)
    durations = await recorded_durations(pool, owner_id, loaded)
    return (
        MeasuredInputs(equity_beta=betas, duration_years=durations, equity_beta_reasons=reasons),
        bundle,
    )


def _foreign(loaded: xray_service.Loaded) -> list[str]:
    base = loaded.leafset.base_currency
    return sorted({x.currency for x in loaded.leafset.leaves if x.currency and x.currency != base})


async def run_shock(
    pool: asyncpg.Pool,
    owner_id: str,
    shocks: ShockSet,
    assumptions: ShockAssumptions,
    *,
    loaded: xray_service.Loaded | None = None,
) -> ShockRun:
    loaded = loaded or await xray_service.load_cached(pool, owner_id)
    if loaded.data.portfolio is None:
        raise NoHoldings
    measured, bundle = await _scenario_inputs(pool, owner_id, loaded)
    result = apply_shocks(
        loaded.leafset, shocks, assumptions, measured, cash_currency=loaded.leafset.base_currency
    )
    labels = {info.ticker: info.label for info in loaded.leafset.channels.values() if info.ticker}
    lines = [
        MeasuredLine(
            ticker=t, label=labels.get(t), kind="equity beta", value=s.value,
            source=s.source, detail=s.detail,
        )
        for t, s in measured.equity_beta.items()
    ] + [
        MeasuredLine(
            ticker=t, label=labels.get(t), kind="equity beta", value=None,
            source="none", detail=why,
        )
        for t, why in measured.equity_beta_reasons.items()
        if t not in measured.equity_beta
    ] + [
        MeasuredLine(
            ticker=t, label=labels.get(t), kind="duration", value=s.value,
            source=s.source, detail=s.detail,
        )
        for t, s in measured.duration_years.items()
    ]  # fmt: skip
    notes = list(loaded.notes)
    if bundle.gap:
        notes.append(f"No return history: {bundle.gap}. Every beta below is unmeasured.")
    return ShockRun(
        as_of=loaded.day.isoformat(),
        benchmark_symbol=bundle.benchmark_symbol,
        result=result,
        measured=lines,
        context=await macro_context(pool),
        foreign_currencies=_foreign(loaded),
        notes=notes,
    )


async def run_standard(pool: asyncpg.Pool, owner_id: str) -> StandardTable:
    loaded = await xray_service.load_cached(pool, owner_id)
    if loaded.data.portfolio is None:
        raise NoHoldings
    measured, _bundle = await _scenario_inputs(pool, owner_id, loaded)
    assumptions = ShockAssumptions()
    lines: list[StandardLine] = []
    for name, shocks in standard_set(loaded.leafset.base_currency, _foreign(loaded)):
        res = apply_shocks(loaded.leafset, shocks, assumptions, measured)
        lines.append(
            StandardLine(
                name=name, shocks=shocks, pnl_pct=res.pnl_pct, pnl=res.pnl,
                unshocked_weight=res.unshocked_weight,
            )
        )  # fmt: skip
    return StandardTable(
        as_of=loaded.day.isoformat(),
        base_currency=loaded.leafset.base_currency,
        total_value=loaded.leafset.total,
        lines=lines,
        assumptions=assumptions,
        notes=[
            "Starting points with stated sizes, not forecasts. Equity moves use a measured beta "
            "where the history is good enough, else 1.0 (a stated fallback). Rates and credit "
            "need a recorded or owner duration, so a portfolio without one shows no effect "
            "there, with the unshocked weight beside it."
        ],
    )


# -------------------------------------------------------------------------- strategy and facts


async def load_strategy(pool: asyncpg.Pool) -> StrategyConfig:
    stored = await load_active(pool, "strategy", StrategyConfig)
    if stored is None:
        raise NoStrategy
    return stored.body


async def _latest_positions(pool: asyncpg.Pool) -> dict[str, asyncpg.Record]:
    rows = await pool.fetch(
        "select p.ticker, i.name, p.quantity, p.current_price, p.instrument_currency,"
        " p.value_base, p.value_account, p.account_currency, p.fx_rate, p.snapshot_date"
        " from public.position_snapshot p left join public.instrument i on i.t212_ticker = p.ticker"
        " where p.snapshot_date = (select max(snapshot_date) from public.position_snapshot)"
    )
    return {r["ticker"]: r for r in rows}


async def resolve_tickers(pool: asyncpg.Pool, typed: Iterable[str]) -> dict[str, str]:
    """Typed ticker -> the broker's spelling. Trading212 tickers are case-sensitive (`CSPXl_EQ`),
    so a ticker typed in another case is matched to a held one, else to the one instrument it can
    only mean; an exact spelling always wins and a ticker matching nothing is kept as typed."""
    wanted = sorted({t.strip() for t in typed if t and t.strip()})
    if not wanted:
        return {}
    rows = await pool.fetch(
        "select ticker, 0 as rank from public.position_snapshot"
        " where snapshot_date = (select max(snapshot_date) from public.position_snapshot)"
        " and upper(ticker) = any($1::text[])"
        " union select t212_ticker, 1 from public.instrument"
        " where upper(t212_ticker) = any($1::text[])",
        [t.upper() for t in wanted],
    )
    return match_tickers(
        wanted,
        held=[r["ticker"] for r in rows if r["rank"] == 0],
        instruments=[r["ticker"] for r in rows if r["rank"] == 1],
    )


def match_tickers(
    wanted: Iterable[str], *, held: Iterable[str], instruments: Iterable[str]
) -> dict[str, str]:
    """The matching rule of `resolve_tickers`, without the database."""
    held = list(held)
    instruments = list(instruments)
    exact = {*held, *instruments}
    by_held: dict[str, str] = {}
    for t in held:
        by_held.setdefault(t.upper(), t)
    others: dict[str, set[str]] = {}
    for t in instruments:
        others.setdefault(t.upper(), set()).add(t)
    out: dict[str, str] = {}
    for t in wanted:
        if t in exact:
            out[t] = t
        elif t.upper() in by_held:
            out[t] = by_held[t.upper()]
        elif len(others.get(t.upper(), ())) == 1:
            out[t] = next(iter(others[t.upper()]))
        else:
            out[t] = t
    return out


async def canonical_assumptions(pool: asyncpg.Pool, a: ShockAssumptions) -> ShockAssumptions:
    names = await resolve_tickers(
        pool, [*a.equity_beta, *a.duration_years, *a.spread_duration_years]
    )
    return a.model_copy(
        update={
            "equity_beta": {names.get(k.strip(), k): v for k, v in a.equity_beta.items()},
            "duration_years": {names.get(k.strip(), k): v for k, v in a.duration_years.items()},
            "spread_duration_years": {
                names.get(k.strip(), k): v for k, v in a.spread_duration_years.items()
            },
        }
    )


async def canonical_trades(pool: asyncpg.Pool, req: PretradeRequest) -> PretradeRequest:
    names = await resolve_tickers(pool, [t.ticker for t in req.trades])
    trades = [
        t.model_copy(update={"ticker": names.get(t.ticker.strip(), t.ticker)}) for t in req.trades
    ]
    return req.model_copy(update={"trades": trades})


async def canonical_overrides(pool: asyncpg.Pool, req: rb.RebalanceRequest) -> rb.RebalanceRequest:
    names = await resolve_tickers(pool, req.target_overrides)
    overrides = {names.get(k.strip(), k): v for k, v in req.target_overrides.items()}
    return req.model_copy(update={"target_overrides": overrides})


async def _instruments(pool: asyncpg.Pool, tickers: list[str]) -> dict[str, asyncpg.Record]:
    if not tickers:
        return {}
    rows = await pool.fetch(
        "select t212_ticker, name, currency from public.instrument"
        " where t212_ticker = any($1::text[])",
        tickers,
    )
    return {r["t212_ticker"]: r for r in rows}


def _holdings(portfolio: PortfolioIn, positions: dict[str, asyncpg.Record]) -> list[rb.Holding]:
    out: list[rb.Holding] = []
    for p in portfolio.positions:
        rec = positions.get(p.ticker)
        out.append(
            rb.Holding(
                ticker=p.ticker,
                name=p.name,
                value_base=p.value_base,
                quantity=float(rec["quantity"]) if rec else None,
                currency=(rec["instrument_currency"] if rec else p.currency)
                or portfolio.base_currency,
            )
        )
    return out


# --------------------------------------------------------------------------------- rebalance


class RebalanceResponse(BaseModel):
    as_of: str
    plan: rb.RebalancePlan
    lot_impacts: dict[str, SaleImpact]
    xray: XrayProposal | None
    notes: list[str]


async def open_lots(
    pool: asyncpg.Pool,
) -> tuple[dict[str, list[OpenLot]], LotsResponse, PerformanceSettings]:
    settings = await load_latest_settings(pool)
    report = await lots_report(pool, settings)
    by_ticker: dict[str, list[OpenLot]] = {}
    for lot in report.lots:
        if lot.quantity <= 0:
            continue
        by_ticker.setdefault(lot.ticker, []).append(
            OpenLot(
                lot_id=lot.lot_id,
                bought_on=lot.bought_on,
                quantity=Decimal(str(lot.quantity)),
                unit_cost=Decimal(str(lot.unit_cost)),
            )
        )
    return by_ticker, report, settings


def proposal_from_values(
    values_after: dict[str, float],
    held: set[str],
    total: float,
    deposit: float = 0.0,
) -> ProposedRequest:
    """Express absolute post-trade values as a proposed-portfolio request (weights of the total
    after the deposit)."""
    denominator = total + deposit
    weights: list[ProposedWeight] = []
    additions: list[ProposedWeight] = []
    for ticker, value in values_after.items():
        w = max(0.0, min(1.0, value / denominator))
        (weights if ticker in held else additions).append(ProposedWeight(ticker=ticker, weight=w))
    return ProposedRequest(weights=weights, additions=additions, deposit=deposit)


async def run_rebalance(
    pool: asyncpg.Pool, owner_id: str, request: rb.RebalanceRequest
) -> RebalanceResponse:
    strategy = await load_strategy(pool)
    loaded = await xray_service.load_cached(pool, owner_id)
    portfolio = loaded.data.portfolio
    if portfolio is None:
        raise NoHoldings
    positions = await _latest_positions(pool)
    holdings = _holdings(portfolio, positions)
    unheld = [t.ticker for t in strategy.target_positions if t.ticker not in positions]
    instruments = await _instruments(pool, unheld)
    plan = rb.plan(
        holdings,
        portfolio.cash_base,
        strategy,
        request,
        unheld_currency={
            k: v["currency"] or strategy.base_currency for k, v in instruments.items()
        },
    )
    notes: list[str] = []
    impacts: dict[str, SaleImpact] = {}
    lots, _report, settings = await open_lots(pool)
    for t in plan.trades:
        rec = positions.get(t.ticker)
        if t.action != "sell" or rec is None or not rec["quantity"]:
            continue
        if not rec["value_base"] or not rec["value_account"]:
            notes.append(f"{t.ticker}: the position has no value; no tax-lot consequence shown.")
            continue
        mine = lots.get(t.ticker)
        if not mine:
            notes.append(f"{t.ticker}: no open lots in the ledger; no tax-lot consequence shown.")
            continue
        price_base = rec["value_base"] / rec["quantity"]
        units = Decimal(str(abs(t.trade_value))) / Decimal(str(price_base))
        price_account = rec["value_account"] / rec["quantity"]
        impacts[t.ticker] = consume(
            t.ticker,
            mine,
            min(units, rec["quantity"]),
            price_account,
            rec["snapshot_date"],
            years=settings.time_test_years,
            warn_months=Decimal(settings.time_test_warning_months),
            average_cost=settings.cost_method == "average",
        )
    xray: XrayProposal | None = None
    try:
        values_after = {t.ticker: t.value_after for t in plan.trades}
        held_set = {h.ticker for h in holdings}
        # Held positions without a target keep their value.
        request_x = proposal_from_values(
            {**{h.ticker: h.value_base for h in holdings}, **values_after},
            held_set,
            plan.total_now,
            request.deposit,
        )
        xray = await xray_service.propose(pool, owner_id, request_x, loaded=loaded)
        notes.append(
            "The X-Ray after the trades does not deduct the estimated costs from cash; they are "
            "shown above."
        )
    except ProposalError as err:
        notes.append(f"The X-Ray after the trades could not be built: {err}")
    return RebalanceResponse(
        as_of=loaded.day.isoformat(), plan=plan, lot_impacts=impacts, xray=xray, notes=notes
    )


# ----------------------------------------------------------------------------------- pre-trade


class PretradeRequest(BaseModel):
    trades: list[pt.TradeLeg] = Field(min_length=1, max_length=40)
    rules: pt.PretradeRules | None = Field(
        default=None,
        description="Thresholds for this check only; when omitted the strategy's are used.",
    )
    deposit: float = Field(
        default=0, ge=-1e12, le=1e12, description="Cash added (or taken out) alongside the trades."
    )

    @field_validator("trades")
    @classmethod
    def _one_leg_per_side_and_ticker(cls, trades: list[pt.TradeLeg]) -> list[pt.TradeLeg]:
        """Legs on the same side of one ticker are one trade: the lot consequence of a sale is
        computed once per ticker, so a second leg must not overwrite the first."""
        merged: dict[tuple[str, str], pt.TradeLeg] = {}
        for leg in trades:
            key = (leg.side, leg.ticker)
            first = merged.get(key)
            if first is None:
                merged[key] = leg
            else:
                merged[key] = first.model_copy(
                    update={"value_base": first.value_base + leg.value_base}
                )
        return list(merged.values())


async def volatility_and_price(
    pool: asyncpg.Pool, tickers: list[str]
) -> dict[str, tuple[float | None, float | None, dt.date | None]]:
    """(1-year volatility, last close, its date) from the holding's own stored series."""
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool, tickers, profiles.body if profiles else HoldingProfilesConfig()
    )
    chosen = await choose_series(pool, symbols)
    out: dict[str, tuple[float | None, float | None, dt.date | None]] = {}
    for ticker in tickers:
        ref = chosen.get(ticker)
        if ref is None:
            out[ticker] = (None, None, None)
            continue
        series = await load_series(pool, ref)
        vol = ref_risk.volatility(series) if series else None
        out[ticker] = (vol, series[-1][1] if series else None, series[-1][0] if series else None)
    return out


async def build_pretrade_context(
    pool: asyncpg.Pool,
    owner_id: str,
    trades: list[pt.TradeLeg],
    rules: pt.PretradeRules,
    *,
    deposit: float = 0.0,
    loaded: xray_service.Loaded | None = None,
) -> tuple[pt.PretradeContext, PortfolioIn, dict[str, asyncpg.Record]]:
    strategy = await load_strategy(pool)
    loaded = loaded or await xray_service.load_cached(pool, owner_id)
    portfolio = loaded.data.portfolio
    if portfolio is None:
        raise NoHoldings
    positions = await _latest_positions(pool)
    base = portfolio.base_currency
    by_ticker = {p.ticker: p for p in portfolio.positions}
    total = sum(p.value_base for p in portfolio.positions) + portfolio.cash_base
    tickers = sorted({t.ticker for t in trades})
    unheld = [t for t in tickers if t not in by_ticker]
    instruments = await _instruments(pool, unheld)
    theses = await load_active(pool, "theses", ThesesConfig)
    thesis_by = {t.ticker: t for t in (theses.body.theses if theses else [])}
    market = await volatility_and_price(pool, tickers)
    notes: list[str] = []

    facts: dict[str, pt.TickerFacts] = {}
    for ticker in tickers:
        pos = by_ticker.get(ticker)
        rec = positions.get(ticker)
        ins = instruments.get(ticker)
        vol, last_close, last_day = market[ticker]
        thesis = thesis_by.get(ticker)
        target = thesis.price_target if thesis else None
        quote = rec["instrument_currency"] if rec else (ins["currency"] if ins else None)
        price = float(rec["current_price"]) if rec else last_close
        if rec is None and last_close is not None and last_day is not None:
            notes.append(
                f"{ticker}: price {last_close:,.2f} is the last stored close ({last_day})."
            )
        facts[ticker] = pt.TickerFacts(
            ticker=ticker,
            name=(pos.name if pos else (ins["name"] if ins else None)),
            held=pos is not None,
            tier=strategy.classifications.get(ticker, {}).get("risk"),
            value_now=pos.value_base if pos else 0.0,
            price=price,
            quote_currency=quote,
            sell_target=target.sell_target if target else None,
            bear_case=(
                target.bear_case if target and (not quote or target.currency == quote) else None
            ),
            target_currency=target.currency if target else None,
            has_thesis=thesis is not None,
            thesis_gaps=thesis.missing_requirements() if thesis else [],
            volatility=vol,
            currency=quote,
        )

    holdings_before = {p.ticker: p.value_base for p in portfolio.positions}
    cash_before = portfolio.cash_base
    after = dict(holdings_before)
    cash_after = cash_before + deposit
    for t in trades:
        sign = 1.0 if t.side == "buy" else -1.0
        after[t.ticker] = after.get(t.ticker, 0.0) + sign * t.value_base
        cash_after -= sign * t.value_base
    currency_of = {
        k: (
            positions[k]["instrument_currency"]
            if k in positions
            else (instruments[k]["currency"] if k in instruments else base)
        )
        for k in set(after) | set(holdings_before)
    }

    def drift(values: dict[str, float], cash: float):  # type: ignore[no-untyped-def]
        return compute_drift(
            [
                HoldingValue(k, currency_of.get(k) or base, Decimal(str(max(v, 0.0))))
                for k, v in values.items()
                if v > 1e-9
            ],
            Decimal(str(cash)),
            strategy,
        )

    ctx = pt.PretradeContext(
        strategy=strategy,
        rules=rules,
        base_currency=base,
        total_value=total + deposit,
        cash_base=cash_before,
        facts=facts,
        drift_before=drift(holdings_before, cash_before),
        drift_after=drift(after, cash_after),
        horizon_years=float(strategy.horizon_years or 3),
        notes=notes,
    )
    if cash_after < -1e-6:
        notes.append(
            f"The buys exceed cash by {-cash_after:,.0f} {base}; the post-trade weights assume "
            "the sales or a deposit cover it."
        )
    return ctx, portfolio, positions


async def run_pretrade(
    pool: asyncpg.Pool, owner_id: str, request: PretradeRequest
) -> pt.PretradeResult:
    loaded = await xray_service.load_cached(pool, owner_id)
    rules = request.rules
    if rules is None:
        strategy = await load_strategy(pool)
        rules = pt.PretradeRules(**strategy.pretrade.model_dump())
    ctx, portfolio, positions = await build_pretrade_context(
        pool, owner_id, request.trades, rules, deposit=request.deposit, loaded=loaded
    )
    await _attach_sales(pool, ctx, request.trades, positions, portfolio)
    await _attach_rates(pool, ctx, portfolio)
    await _attach_covariance(pool, ctx, portfolio, request.trades)
    await _attach_lookthrough(pool, owner_id, ctx, request, loaded)
    return pt.run(request.trades, ctx)


async def _attach_covariance(
    pool: asyncpg.Pool, ctx: pt.PretradeContext, portfolio: PortfolioIn, trades: list[pt.TradeLeg]
) -> None:
    """The covariance model over the holdings and the traded stocks, and the weights before and
    after the trades. A stock without enough shared history is left out and named."""
    before = {p.ticker: p.value_base for p in portfolio.positions}
    after = dict(before)
    for t in trades:
        after[t.ticker] = after.get(t.ticker, 0.0) + (
            t.value_base if t.side == "buy" else -t.value_base
        )
    tickers = sorted(set(before) | set(after))
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool, tickers, profiles.body if profiles else HoldingProfilesConfig()
    )
    chosen = await choose_series(pool, symbols)
    since = dt.date.today() - dt.timedelta(days=covariance.WINDOW_DAYS)
    series: dict[str, list[tuple[dt.date, float]]] = {}
    for ticker in tickers:
        ref = chosen.get(ticker)
        if ref is not None:
            series[ticker] = [(d, float(p)) for d, p in await load_series(pool, ref) if d >= since]
    model = covariance.build(series)
    # A holding with no stored price series never reaches the model, so it is not in `left_out`.
    ctx.unmodelled = [t for t in tickers if t not in series and after.get(t, 0.0) > 0]
    total = ctx.total_value
    if model is None or total <= 0:
        ctx.notes.append(
            "The covariance model could not be built: too little shared price history."
        )
        return
    ctx.cov = model
    ctx.weights_before = {k: v / total for k, v in before.items() if v > 0}
    ctx.weights_after = {k: v / total for k, v in after.items() if v > 0}
    left_out = sorted(set(model.left_out) | set(ctx.unmodelled))
    if left_out:
        ctx.notes.append(
            "Left out of the covariance model for lack of shared history: "
            + ", ".join(left_out)
            + "."
        )


async def _attach_rates(
    pool: asyncpg.Pool, ctx: pt.PretradeContext, portfolio: PortfolioIn
) -> None:
    found = await _risk_free(pool, portfolio.base_currency, dt.date.today() - dt.timedelta(days=30))
    if found is not None and found[0].latest_rate is not None:
        ctx.risk_free = found[0].latest_rate
    else:
        ctx.notes.append("No risk-free rate is stored for the base currency.")


async def _attach_sales(
    pool: asyncpg.Pool,
    ctx: pt.PretradeContext,
    trades: list[pt.TradeLeg],
    positions: dict[str, asyncpg.Record],
    portfolio: PortfolioIn,
) -> None:
    sells = [t for t in trades if t.side == "sell"]
    if not sells:
        return
    lots, report, settings = await open_lots(pool)
    ctx.warn_months = float(settings.time_test_warning_months)
    ctx.exemption_limit = float(settings.exemption_limit)
    ctx.exemption_currency = settings.exemption_currency
    year = report.as_of.year if report.as_of else dt.date.today().year
    row = next((e for e in report.exemption if e.year == year), None)
    ctx.year_proceeds_so_far = row.gross_proceeds if row else 0.0
    for t in sells:
        rec = positions.get(t.ticker)
        mine = lots.get(t.ticker)
        if rec is None or not rec["quantity"] or not mine:
            continue
        if not rec["value_base"] or not rec["value_account"]:
            ctx.notes.append(
                f"{t.ticker}: the position has no value; no tax-lot consequence shown."
            )
            continue
        units = min(
            Decimal(str(t.value_base)) / Decimal(str(rec["value_base"] / rec["quantity"])),
            rec["quantity"],
        )
        price_account = rec["value_account"] / rec["quantity"]
        impact = consume(
            t.ticker,
            mine,
            units,
            price_account,
            rec["snapshot_date"],
            years=settings.time_test_years,
            warn_months=Decimal(settings.time_test_warning_months),
            average_cost=settings.cost_method == "average",
        )
        ctx.sale_impacts[t.ticker] = impact
        # Base currency per account currency, from the position itself, to carry the lot gains
        # (account currency) into the base currency the hurdle is stated in.
        ctx.sale_base_per_account[t.ticker] = float(rec["value_base"]) / float(rec["value_account"])
        account = rec["account_currency"]
        if normalise_currency(account)[0] == normalise_currency(settings.exemption_currency)[0]:
            rate: Decimal | None = Decimal(1)
        else:
            stored = await load_active(pool, "strategy", StrategyConfig)
            source = stored.body.fx_source if stored and stored.body.fx_source else "ecb"
            day = rec["snapshot_date"]
            fx = await load_fx_history(
                pool,
                source,
                [account, settings.exemption_currency],
                day - dt.timedelta(days=10),
                day,
            )
            rate = fx.conversion(account, settings.exemption_currency, day)
        ctx.sale_within_test_exemption_ccy[t.ticker] = (
            None if rate is None else float(Decimal(str(impact.gross_within_time_test)) * rate)
        )


async def _attach_lookthrough(
    pool: asyncpg.Pool,
    owner_id: str,
    ctx: pt.PretradeContext,
    request: PretradeRequest,
    loaded: xray_service.Loaded,
) -> None:
    """The overlap limit on the post-trade X-Ray, when the strategy sets one."""
    limit = ctx.strategy.risk.max_look_through_weight
    if limit is None:
        return
    portfolio = loaded.data.portfolio
    assert portfolio is not None
    values = {p.ticker: p.value_base for p in portfolio.positions}
    held = set(values)
    for t in request.trades:
        values[t.ticker] = values.get(t.ticker, 0.0) + (
            t.value_base if t.side == "buy" else -t.value_base
        )
    try:
        proposal = await xray_service.propose(
            pool,
            owner_id,
            proposal_from_values(values, held, ctx.total_value - request.deposit, request.deposit),
            loaded=loaded,
        )
    except ProposalError as err:
        ctx.extra_checks.append(
            pt.PretradeCheck(
                id="after.look_through",
                principle="4c",
                ticker=None,
                status="unavailable",
                title="Look-through limit not evaluated",
                detail=str(err),
            )
        )
        return
    top = proposal.after.largest_issuer_weight
    name = proposal.after.largest_issuer
    if top is None:
        return
    ctx.extra_checks.append(
        pt.PretradeCheck(
            id="after.look_through",
            principle="4c",
            ticker=None,
            status="pass" if top <= limit + 1e-9 else "flag",
            title=f"Largest company through funds: {name} at {top:.1%} (limit {limit:.0%})",
            detail="The largest issuer on the post-trade X-Ray, counting direct holdings and fund "
            "rows together.",
            values={"weight": top, "limit": limit},
        )
    )
