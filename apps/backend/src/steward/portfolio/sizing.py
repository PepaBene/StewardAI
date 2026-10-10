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
from steward.decision import expected_return as er
from steward.decision import ranking as rk
from steward.decision.ranking_service import _report_dates
from steward.fx.convert import price_in_unit
from steward.portfolio import exante as ea
from steward.portfolio import trade_risk as tr
from steward.portfolio.breaches import Finding
from steward.portfolio.risk import _risk_free
from steward.quant import sizing as sz

METHOD_VERSION = "sizing-1"
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
    binding: Literal["tier_cap", "risk_limits", "event_risk", "score", "none"] = "none"
    score: float | None = Field(
        default=None,
        description="(annualised thesis return - cash rate) / volatility, the risk-adjusted "
        "score of Q8.4 on the sizing inputs.",
    )
    score_size: float | None = Field(
        default=None,
        description="The tier cap scaled by score / `pretrade.score_for_full_start`; a ceiling.",
    )
    limits_reached: list[str] = Field(default_factory=list)
    event_move: float | None = Field(
        default=None, description="Largest past move around a report date (a fraction)."
    )
    event_cap: float | None = Field(
        default=None,
        description="Weight at which that move costs `pretrade.event_loss_budget` of the book.",
    )
    base_currency: str | None = None
    kelly: KellyOut | None = None
    warnings: list[str] = Field(default_factory=list)


def conviction_confidence(conviction: int | None, low: float = 0.2, high: float = 0.8) -> float:
    """Conviction 1 to 5 runs from `low` to `high` (default 0.2 to 0.8); none stored is midway."""
    if conviction is None:
        return (low + high) / 2
    return low + (high - low) * (min(max(conviction, 1), 5) - 1) / 4


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


async def start_size(pool: asyncpg.Pool, ticker: str, premium: float | None = None) -> StartSize:
    """`premium` overrides the strategy's `pretrade.equity_premium` for this call."""
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
    # The pre-trade cap if set, else the strategy's per-stock risk share (principle 4e).
    contribution_cap = (
        rules.risk_contribution_cap
        if rules.risk_contribution_cap is not None
        else strategy.risk.max_stock_risk_share
    )
    breached: list[str] = []

    def model(w: float) -> tuple[ea.ExAnteRisk, list[Finding]]:
        target = w * total
        # The probe already carries the new stock's series, so only the weights change here: the
        # return matrix (a rate lookup per day and series) is built once, not at every step.
        after_book = tr.book_after(
            probe, {ticker: target - current}, 0.0, known={t: n[0] for t, n in new.items()}
        )
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
    out.binding = "risk_limits" if ceiling < tier_cap - 1e-9 else "tier_cap"
    out.limits_reached = breached
    event = await _event_cap(pool, ticker, rules.event_loss_budget, new, book)
    if event is not None:
        out.event_move, out.event_cap = event
        if out.event_cap < out.suggested:
            out.suggested = out.event_cap
            out.binding = "event_risk"
            out.limits_reached = [*breached, "earnings_move"]
    out.suggested_value = out.suggested * total
    final_report, _ = model(out.suggested)
    out.warnings = final_report.warnings
    out.kelly = await _kelly(pool, strategy, book, new, ticker, final_report, premium)
    k = out.kelly
    if k.available and k.thesis_return is not None and k.risk_free is not None and k.volatility:
        out.score = (k.thesis_return - k.risk_free) / k.volatility
        full = rules.score_for_full_start
        if full is not None:
            out.score_size = tier_cap * min(max(out.score / full, 0.0), 1.0)
            if out.score_size < out.suggested:
                out.suggested = out.score_size
                out.suggested_value = out.suggested * total
                out.binding = "score"
    return out


async def _event_cap(
    pool: asyncpg.Pool,
    ticker: str,
    budget: float | None,
    new: dict[str, tuple[tr.NewSeries, list[tuple[dt.date, float]]]],
    book: ea.LoadedBook,
) -> tuple[float, float] | None:
    """(largest past move on a report date, the weight at which that move costs the budget);
    None when no budget is set or the stock has no past report inside its price history."""
    if budget is None:
        return None
    closes = new[ticker][1] if ticker in new else book.daily_closes.get(ticker)
    if not closes:
        return None
    dates = (await _report_dates(pool, [ticker])).get(ticker, [])
    moves = rk.earnings_moves(closes, dates)
    if moves is None or moves.largest_abs <= 0:
        return None
    return moves.largest_abs, budget / moves.largest_abs


async def _kelly(
    pool: asyncpg.Pool,
    strategy: StrategyConfig,
    book: ea.LoadedBook,
    new: dict[str, tuple[tr.NewSeries, list[tuple[dt.date, float]]]],
    ticker: str,
    report: ea.ExAnteRisk,
    premium: float | None,
) -> KellyOut:
    theses = await load_active(pool, "theses", ThesesConfig)
    thesis = next((t for t in (theses.body.theses if theses else []) if t.ticker == ticker), None)
    target = thesis.price_target if thesis is not None else None
    if target is None or (target.sell_target is None and target.scenarios is None):
        return KellyOut(available=False, reason="no thesis with a sell target or scenarios")
    assert thesis is not None
    closes = new[ticker][1] if ticker in new else book.daily_closes.get(ticker)
    currency = (
        new[ticker][0].currency
        if ticker in new
        else next((p.currency for p in book.positions if p.ticker == ticker), None)
    )
    if not closes:
        return KellyOut(available=False, reason="no stored price to compare the target with")
    scaled = price_in_unit(closes[-1][1], currency, target.currency)
    if scaled is None:
        return KellyOut(
            available=False,
            reason=f"the target is in {target.currency} but the price is in {currency}",
        )
    price = scaled
    if target.scenarios is not None:
        # The probability-weighted value, annualised to the catalyst (Q8.2), not one target.
        thesis_return = er.expected_return(
            target.scenarios,
            price,
            catalyst=target.catalyst_date,
            today=dt.date.today(),
            hurdle=None,
            asymmetry_gate=None,
        ).annualized
    else:
        # The same horizon as the ranking (catalyst date, else two years), so a thesis has one
        # annualised return on every page.
        years, _ = er.horizon_years(target.catalyst_date, dt.date.today())
        sell = target.sell_target
        assert sell is not None
        thesis_return = er.annualize(sell / price - 1.0, years)
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
    rules = strategy.pretrade
    eq = sz.equilibrium_return(
        rf, beta if beta is not None else 1.0, rules.equity_premium if premium is None else premium
    )
    confidence = conviction_confidence(
        thesis.conviction, rules.confidence_low, rules.confidence_high
    )
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
