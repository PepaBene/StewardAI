"""Risk-aware starting size for a position and the quarter-Kelly bound (plan Q5.2, Q5.3).

The suggested size is the smaller of

- the tier's starting cap (principle 8b: 5 % core, 3 % growth, 1.5 % speculative), which stays
  authoritative, and
- the largest weight at which buying the stock (paid from cash) raises no new ex-ante risk-limit
  breach and makes no standing breach deeper (volatility forecast, tier risk budgets, effective
  bets, pair correlation, tier criteria), and keeps the stock's own share of portfolio
  volatility within the strategy's per-stock cap when one is set.

Kelly is shown beside it as an upper bound with its inputs, never as a target: a quarter of
(expected excess return / variance), where the expected return is the thesis return annualised
to a year and pulled toward what the market pays for the stock's beta, by a confidence taken
from the thesis's conviction. A stock with no thesis has no Kelly figure and says why.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

import asyncpg
import numpy as np
from pydantic import BaseModel, Field

from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig
from steward.portfolio import exante as ea
from steward.portfolio import trade_risk as tr
from steward.portfolio.breaches import Finding
from steward.portfolio.risk import _risk_free
from steward.quant import sizing as sz

METHOD_VERSION = "sizing-1"
EQUITY_PREMIUM = 0.05
BETA_MIN_WEEKS = 40
SEARCH_STEPS = 14


class KellyOut(BaseModel):
    available: bool
    reason: str | None = None
    thesis_return: float | None = Field(
        default=None, description="Return to the thesis sell target, annualised."
    )
    equilibrium_return: float | None = None
    confidence: float | None = Field(
        default=None, description="Weight on the thesis return; from the thesis's conviction."
    )
    expected_return: float | None = Field(
        default=None, description="Thesis and equilibrium returns blended by the confidence."
    )
    risk_free: float | None = None
    beta: float | None = None
    volatility: float | None = None
    full_kelly: float | None = None
    fraction: float = sz.KELLY_FRACTION
    bound: float | None = Field(default=None, description="Upper bound, share of the portfolio.")


class StartSize(BaseModel):
    method_version: str = METHOD_VERSION
    ticker: str
    name: str | None = None
    status: Literal["ok", "unavailable"]
    reason: str | None = None
    tier: str | None = None
    tier_cap: float | None = Field(default=None, description="Principle 8b starting cap.")
    risk_ceiling: float | None = Field(
        default=None, description="Largest weight inside the ex-ante limits (up to the tier cap)."
    )
    suggested: float | None = Field(default=None, description="The smaller of the two.")
    suggested_value: float | None = None
    binding: Literal["tier_cap", "risk_limits", "none"] = "none"
    limits_reached: list[str] = Field(default_factory=list)
    base_currency: str | None = None
    kelly: KellyOut | None = None
    warnings: list[str] = Field(default_factory=list)


def conviction_confidence(conviction: int | None) -> float:
    """1 to 5 maps to 0.2 to 0.8; no conviction stored counts as the middle."""
    return 0.5 if conviction is None else 0.2 + 0.15 * (min(max(conviction, 1), 5) - 1)


def weekly_beta(book: ea.LoadedBook, ticker: str) -> float | None:
    """Slope of the stock's weekly base-currency returns on the benchmark's, own weeks only."""
    if ticker not in book.weekly.keys or ea.BENCHMARK_KEY not in book.weekly.keys:
        return None
    y = book.weekly.column(ticker)
    x = book.weekly.column(ea.BENCHMARK_KEY)
    ok = ~(np.isnan(x) | np.isnan(y))
    if int(ok.sum()) < BETA_MIN_WEEKS or float(np.var(x[ok])) <= 0:
        return None
    return float(np.cov(x[ok], y[ok], ddof=1)[0, 1] / np.var(x[ok], ddof=1))


def _bad(before: list[Finding], after: list[Finding]) -> list[str]:
    """Keys of findings that are new or deeper after than before."""
    fb = {f.key: f for f in before}
    return [f.key for f in after if tr.change_of(fb.get(f.key), f) in ("new", "deeper")]


async def start_size(pool: asyncpg.Pool, ticker: str, premium: float = EQUITY_PREMIUM) -> StartSize:
    book, strategy = await ea.book_from_snapshot(pool)
    if book is None or strategy is None:
        return StartSize(
            ticker=ticker,
            status="unavailable",
            reason="no portfolio snapshot yet; run the portfolio sync",
        )
    held = {p.ticker: p for p in book.positions}
    new: dict[str, tuple[tr.NewSeries, list[tuple[dt.date, float]]]] = {}
    if ticker not in held:
        new = await tr.load_new_series(pool, [ticker])
        if ticker not in new:
            return StartSize(
                ticker=ticker,
                status="unavailable",
                reason="no stored price series or instrument record for this ticker",
            )
    name = held[ticker].name if ticker in held else new[ticker][0].name
    probe = tr.book_after(book, {ticker: 0.0}, 0.0, new) if new else book
    strategy = await tr.candidate_tiers(pool, probe, strategy, [ticker])
    tier = strategy.classifications.get(ticker, {}).get("risk")
    rules = strategy.pretrade
    cap_by = {
        "core": rules.start_size_core,
        "growth": rules.start_size_growth,
        "speculative": rules.start_size_speculative,
    }
    tier_cap = cap_by.get((tier or "").lower())
    out = StartSize(
        ticker=ticker,
        name=name,
        status="ok",
        tier=tier,
        tier_cap=tier_cap,
        base_currency=book.base_currency,
    )
    if tier_cap is None:
        out.status = "unavailable"
        out.reason = (
            "the stock has no risk tier, so there is no starting cap to size against; enter "
            "one in the strategy classifications or turn on derived tiers"
        )
        return out
    before_report, found_before = tr.findings_for(book, strategy)
    if before_report.volatility_forecast is None:
        out.status = "unavailable"
        out.reason = before_report.reason or "no ex-ante forecast for the current book"
        return out
    total = book.total_value
    current = held[ticker].value_base if ticker in held else 0.0
    contribution_cap = rules.risk_contribution_cap
    breached: list[str] = []

    def model(w: float) -> tuple[ea.ExAnteRisk, list[Finding]]:
        target = w * total
        after_book = tr.book_after(book, {ticker: target - current}, 0.0, new)
        return tr.findings_for(after_book, strategy)

    def ok(w: float) -> bool:
        report, found = model(w)
        if _bad(found_before, found):
            return False
        if contribution_cap is not None:
            row = next((p for p in report.positions if p.ticker == ticker), None)
            if row is not None and row.risk_share is not None and row.risk_share > contribution_cap:
                return False
        return True

    ceiling = sz.largest_feasible(ok, tier_cap, SEARCH_STEPS)
    if ceiling < tier_cap - 1e-9:
        _, found = model(min(tier_cap, ceiling * 1.05 + 1e-4))
        breached = _bad(found_before, found)
        if contribution_cap is not None and not breached:
            breached = ["position_risk_share"]
    out.risk_ceiling = ceiling
    out.suggested = min(tier_cap, ceiling)
    out.suggested_value = out.suggested * total
    out.binding = "risk_limits" if ceiling < tier_cap - 1e-9 else "tier_cap"
    out.limits_reached = breached
    final_report, _ = model(out.suggested)
    out.warnings = final_report.warnings
    out.kelly = await _kelly(pool, strategy, book, new, ticker, final_report, premium)
    return out


async def _kelly(
    pool: asyncpg.Pool,
    strategy: StrategyConfig,
    book: ea.LoadedBook,
    new: dict[str, tuple[tr.NewSeries, list[tuple[dt.date, float]]]],
    ticker: str,
    report: ea.ExAnteRisk,
    premium: float,
) -> KellyOut:
    theses = await load_active(pool, "theses", ThesesConfig)
    thesis = next((t for t in (theses.body.theses if theses else []) if t.ticker == ticker), None)
    if thesis is None or thesis.price_target is None or thesis.price_target.sell_target is None:
        return KellyOut(available=False, reason="no thesis with a sell target is stored")
    closes = new[ticker][1] if ticker in new else book.daily_closes.get(ticker)
    currency = (
        new[ticker][0].currency
        if ticker in new
        else next((p.currency for p in book.positions if p.ticker == ticker), None)
    )
    if not closes:
        return KellyOut(available=False, reason="no stored price to compare the target with")
    target = thesis.price_target
    if currency and target.currency != currency:
        return KellyOut(
            available=False,
            reason=f"the target is in {target.currency} but the price is in {currency}",
        )
    price = closes[-1][1]
    years = float(strategy.horizon_years or 3)
    sell = target.sell_target
    assert sell is not None
    thesis_return = (sell / price) ** (1.0 / years) - 1.0
    found = await _risk_free(pool, book.base_currency, dt.date.today() - dt.timedelta(days=30))
    rf = found[0].latest_rate if found is not None and found[0].latest_rate is not None else None
    if rf is None:
        return KellyOut(available=False, reason="no risk-free rate is stored for the base currency")
    after_book = tr.book_after(book, {ticker: 0.0}, 0.0, new) if new else book
    beta = weekly_beta(after_book, ticker)
    row = next((p for p in report.positions if p.ticker == ticker), None)
    vol = row.volatility if row is not None else None
    if vol is None or vol <= 0:
        return KellyOut(available=False, reason="no volatility estimate for this stock")
    eq = sz.equilibrium_return(rf, beta if beta is not None else 1.0, premium)
    confidence = conviction_confidence(thesis.conviction)
    blended = sz.shrink_expected_return(thesis_return, eq, confidence)
    k = sz.kelly_bound(blended, rf, vol)
    return KellyOut(
        available=True,
        reason=None if beta is not None else "beta unavailable (too few weeks); 1.0 assumed",
        thesis_return=thesis_return,
        equilibrium_return=eq,
        confidence=confidence,
        expected_return=blended,
        risk_free=rf,
        beta=beta,
        volatility=vol,
        full_kelly=k.full,
        bound=k.bound,
    )
