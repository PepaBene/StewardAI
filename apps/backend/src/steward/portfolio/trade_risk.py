"""What a trade or a deposit does to the ex-ante risk of the book (plan Q5.1).

The proposed trades (and deposit) are applied to a copy of today's loaded book and the ex-ante
report of `portfolio/exante.py` is built again, so the before and after use the same covariance
model, proxies and tiers. The risk-limit findings of the strategy (volatility forecast, tier risk
budgets, effective bets, pair correlation, tier criteria) are evaluated on both sides with the
code that raises them after a sync, and the difference is reported as new, deeper, still open or
cleared.

Expected Shortfall and the drawdown probability are not in the model yet (plan Q2); the result
says so instead of showing a figure.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import asyncpg
from pydantic import BaseModel, Field

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.decision.pretrade import PretradeCheck, TradeLeg
from steward.portfolio import exante as ea
from steward.portfolio.breaches import Finding
from steward.portfolio.risk_limits import RiskInputs, _book_inputs, evaluate_risk
from steward.portfolio.risk_tier import (
    apply_tiers,
    derive_tiers,
    load_fundamentals,
    volatilities_of,
)
from steward.quant import returns as rt
from steward.reference.price_series import choose_series, holding_symbols
from steward.reference.read import load_series

METHOD_VERSION = "trade-risk-1"
NOT_MODELLED = [
    "Expected Shortfall and the probability of a 20 % drawdown are not in the risk model yet "
    "(plan phase Q2), so the before and after cannot show them."
]
TOP_LINES = 12


class NewSeries(BaseModel):
    ticker: str
    name: str | None = None
    currency: str


class RiskLine(BaseModel):
    ticker: str
    name: str | None
    tier: str
    weight_before: float
    weight_after: float
    risk_share_before: float | None
    risk_share_after: float | None
    volatility: float | None = Field(description="Standalone annualised volatility (after).")
    status_after: str | None = Field(description="real, proxied or excluded after the trade.")


class TierLine(BaseModel):
    tier: str
    risk_share_before: float
    risk_share_after: float
    budget: float | None


class LimitLine(BaseModel):
    key: str
    ticker: str | None
    detail: str
    observed: float | None
    limit: float | None
    change: str = Field(description="new, deeper, still_open, eased or cleared.")


class TradeRisk(BaseModel):
    method_version: str = METHOD_VERSION
    available: bool
    reason: str | None = None
    as_of: dt.date | None = None
    volatility_before: float | None = None
    volatility_after: float | None = None
    volatility_limit: float | None = None
    effective_bets_before: float | None = None
    effective_bets_after: float | None = None
    diversification_before: float | None = None
    diversification_after: float | None = None
    covered_share_after: float | None = None
    lines: list[RiskLine] = Field(default_factory=list)
    tiers: list[TierLine] = Field(default_factory=list)
    limits: list[LimitLine] = Field(default_factory=list)
    not_modelled: list[str] = Field(default_factory=lambda: list(NOT_MODELLED))
    warnings: list[str] = Field(default_factory=list)


def book_after(
    book: ea.LoadedBook,
    deltas: dict[str, float],
    deposit: float = 0.0,
    new: dict[str, tuple[NewSeries, list[tuple[dt.date, float]]]] | None = None,
    known: dict[str, NewSeries] | None = None,
) -> ea.LoadedBook:
    """A copy of the book with each ticker's value moved by its delta (never below zero), the
    deposit added to the total, and the series of tickers not yet held added (pure). `known`
    names tickers whose series the book already carries from an earlier call."""
    new = new or {}
    values = {p.ticker: p.value_base for p in book.positions}
    info = {p.ticker: p for p in book.positions}
    for ticker, delta in deltas.items():
        values[ticker] = max(0.0, values.get(ticker, 0.0) + delta)
    positions: list[ea.BookPosition] = []
    for ticker, value in values.items():
        if value <= 1e-9:
            continue
        if ticker in info:
            positions.append(replace(info[ticker], value_base=value))
        elif ticker in new or (known and ticker in known):
            meta = new[ticker][0] if ticker in new else (known or {})[ticker]
            positions.append(ea.BookPosition(ticker, meta.name, meta.currency, value))
    out = replace(book, positions=positions, total_value=book.total_value + deposit)
    if new:
        if book.start is None or book.end is None or book.fx is None:
            raise ValueError("the book carries no price inputs to add a new series to")
        inputs = [*book.inputs]
        daily = dict(book.daily_closes)
        for ticker, (meta, closes) in new.items():
            inputs.append(rt.SeriesInput(ticker, closes, meta.currency))
            daily[ticker] = closes
        weekly = rt.build_return_matrix(inputs, book.fx, "weekly", book.start, book.end)
        out = replace(out, weekly=weekly.last(ea.WINDOW_WEEKS), daily_closes=daily, inputs=inputs)
    return out


def findings_for(
    book: ea.LoadedBook, strategy: StrategyConfig
) -> tuple[ea.ExAnteRisk, list[Finding]]:
    """The ex-ante report of a book and the risk-limit findings it raises."""
    inputs = RiskInputs()
    report = _book_inputs(book, strategy, inputs)
    return report, evaluate_risk(inputs, strategy.risk)


def change_of(before: Finding | None, after: Finding | None) -> str:
    if before is None and after is not None:
        return "new"
    if after is None:
        return "cleared"
    assert before is not None
    if before.observed is None or after.observed is None or after.limit is None:
        return "still_open"
    limit = float(after.limit)
    d_before = abs(float(before.observed) - limit)
    d_after = abs(float(after.observed) - limit)
    if d_after > d_before + 1e-12:
        return "deeper"
    if d_after < d_before - 1e-12:
        return "eased"
    return "still_open"


def compare(
    before: ea.ExAnteRisk,
    after: ea.ExAnteRisk,
    findings_before: list[Finding],
    findings_after: list[Finding],
    limits: ea.ExAnteLimits | None = None,
) -> TradeRisk:
    """Before and after as one comparison (pure)."""
    if before.volatility_forecast is None or after.volatility_forecast is None:
        return TradeRisk(
            available=False,
            reason=after.reason or before.reason or "no forecast could be made",
            warnings=after.warnings,
        )
    prior = {p.ticker: p for p in before.positions}
    lines = [
        RiskLine(
            ticker=p.ticker,
            name=p.name,
            tier=p.tier,
            weight_before=prior[p.ticker].weight if p.ticker in prior else 0.0,
            weight_after=p.weight,
            risk_share_before=prior[p.ticker].risk_share if p.ticker in prior else None,
            risk_share_after=p.risk_share,
            volatility=p.volatility,
            status_after=p.status,
        )
        for p in after.positions
    ]
    gone = [
        RiskLine(
            ticker=t,
            name=p.name,
            tier=p.tier,
            weight_before=p.weight,
            weight_after=0.0,
            risk_share_before=p.risk_share,
            risk_share_after=0.0,
            volatility=p.volatility,
            status_after=None,
        )
        for t, p in prior.items()
        if t not in {x.ticker for x in after.positions}
    ]
    lines = sorted(
        [*lines, *gone],
        key=lambda x: -max(x.risk_share_before or 0.0, x.risk_share_after or 0.0),
    )
    # Show the biggest contributors and every line the trade moved.
    keep = [
        x
        for i, x in enumerate(lines)
        if i < TOP_LINES or abs(x.weight_after - x.weight_before) > 1e-9
    ]
    budgets = after.limits.tier_risk_budgets or {}
    tier_before = {g.label: g.risk_share for g in before.by_tier}
    tier_after = {g.label: g.risk_share for g in after.by_tier}
    tiers = [
        TierLine(
            tier=t,
            risk_share_before=tier_before.get(t, 0.0),
            risk_share_after=tier_after.get(t, 0.0),
            budget=budgets.get(t),
        )
        for t in sorted(set(tier_before) | set(tier_after))
    ]
    fb = {f.key: f for f in findings_before}
    fa = {f.key: f for f in findings_after}
    limit_lines = [
        LimitLine(
            key=k,
            ticker=(fa.get(k) or fb[k]).ticker,
            detail=(fa.get(k) or fb[k]).detail,
            observed=(fa.get(k) or fb[k]).observed,
            limit=(fa.get(k) or fb[k]).limit,
            change=change_of(fb.get(k), fa.get(k)),
        )
        for k in sorted(set(fb) | set(fa))
    ]
    return TradeRisk(
        available=True,
        as_of=after.as_of,
        volatility_before=before.volatility_forecast,
        volatility_after=after.volatility_forecast,
        volatility_limit=after.limits.max_volatility,
        effective_bets_before=before.effective_bets,
        effective_bets_after=after.effective_bets,
        diversification_before=before.diversification_ratio,
        diversification_after=after.diversification_ratio,
        covered_share_after=after.coverage.covered_share if after.coverage else None,
        lines=keep,
        tiers=tiers,
        limits=limit_lines,
        warnings=after.warnings,
    )


def checks_from(risk: TradeRisk) -> list[PretradeCheck]:
    """The comparison as pre-trade checks citing principle 4h and the limits it tests."""
    if not risk.available:
        return [
            PretradeCheck(
                id="after.ex_ante",
                principle="4h",
                ticker=None,
                status="unavailable",
                title="Ex-ante risk after the trade not evaluated",
                detail=risk.reason or "no forecast",
            )
        ]
    out: list[PretradeCheck] = []
    for line in risk.limits:
        if line.change in ("new", "deeper"):
            status = "flag"
            title = (
                "Breached after the trade"
                if line.change == "new"
                else "Already breached, and the trade takes it further past the limit"
            )
        elif line.change == "cleared":
            status, title = "pass", "Breach cleared by the trade"
        elif line.change == "eased":
            status, title = "info", "Already breached, and the trade eases it"
        else:
            status, title = "info", "Already breached before the trade, still breached"
        out.append(
            PretradeCheck(
                id=f"after.exante.{line.key}",
                principle="4",
                ticker=line.ticker,
                status=status,
                title=title,
                detail=line.detail,
                values={"observed": line.observed, "limit": line.limit},
            )
        )
    if not any(line.change in ("new", "deeper") for line in risk.limits):
        out.append(
            PretradeCheck(
                id="after.exante",
                principle="4h",
                ticker=None,
                status="pass",
                title="No new or deeper ex-ante risk-limit breach after the trade",
                detail=(
                    f"Forecast volatility {risk.volatility_before:.1%} to "
                    f"{risk.volatility_after:.1%}; effective bets "
                    f"{risk.effective_bets_before or 0:.1f} to "
                    f"{risk.effective_bets_after or 0:.1f}."
                ),
                values={
                    "volatility_before": risk.volatility_before,
                    "volatility_after": risk.volatility_after,
                },
            )
        )
    return out


# ----------------------------------------------------------------------------- I/O


async def load_new_series(
    pool: asyncpg.Pool, tickers: list[str], horizon_days: int = 1500
) -> dict[str, tuple[NewSeries, list[tuple[dt.date, float]]]]:
    """Stored daily closes and instrument facts of tickers the book does not hold. A ticker with
    no stored series or no instrument record is simply absent."""
    if not tickers:
        return {}
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool, tickers, profiles.body if profiles else HoldingProfilesConfig()
    )
    chosen = await choose_series(pool, symbols)
    rows = await pool.fetch(
        "select t212_ticker, name, currency from public.instrument"
        " where t212_ticker = any($1::text[])",
        tickers,
    )
    meta = {r["t212_ticker"]: r for r in rows}
    since = dt.date.today() - dt.timedelta(days=horizon_days)
    out: dict[str, tuple[NewSeries, list[tuple[dt.date, float]]]] = {}
    for ticker in tickers:
        ref = chosen.get(ticker)
        row = meta.get(ticker)
        if ref is None or row is None or not row["currency"]:
            continue
        closes = await load_series(pool, ref, since)
        if len(closes) >= 2:
            out[ticker] = (
                NewSeries(ticker=ticker, name=row["name"], currency=row["currency"]),
                closes,
            )
    return out


async def candidate_tiers(
    pool: asyncpg.Pool,
    book: ea.LoadedBook,
    strategy: StrategyConfig,
    tickers: list[str],
) -> StrategyConfig:
    """The strategy with derived tiers for tickers that are not held, worked out the same way
    as for holdings but never stored."""
    if not strategy.risk.auto_tiers or not tickers:
        return strategy
    facts = await load_fundamentals(pool, tickers)
    vols = volatilities_of({t: book.daily_closes[t] for t in tickers if t in book.daily_closes})
    tiers = derive_tiers({t: vols.get(t) for t in tickers}, facts, strategy.risk)
    return apply_tiers(strategy, {t: d.tier for t, d in tiers.items()})


async def trade_risk(pool: asyncpg.Pool, trades: list[TradeLeg], deposit: float = 0.0) -> TradeRisk:
    """Before and after ex-ante risk for the proposed trades, on the latest synced book."""
    book, strategy = await ea.book_from_snapshot(pool)
    if book is None or strategy is None:
        return TradeRisk(
            available=False, reason="no portfolio snapshot yet; run the portfolio sync"
        )
    deltas: dict[str, float] = {}
    for t in trades:
        deltas[t.ticker] = deltas.get(t.ticker, 0.0) + (
            t.value_base if t.side == "buy" else -t.value_base
        )
    held = {p.ticker for p in book.positions}
    fresh = sorted(t for t, d in deltas.items() if t not in held and d > 0)
    new = await load_new_series(pool, fresh)
    missing = [t for t in fresh if t not in new]
    after_book = book_after(book, deltas, deposit, new)
    strategy_after = await candidate_tiers(pool, after_book, strategy, sorted(new))
    before, found_before = findings_for(book, strategy)
    after, found_after = findings_for(after_book, strategy_after)
    result = compare(before, after, found_before, found_after)
    for name in missing:
        result.warnings.append(
            f"{name}: no stored price series or instrument record, so it is left out of the "
            "after figures; the other trades are still modelled"
        )
    return result
