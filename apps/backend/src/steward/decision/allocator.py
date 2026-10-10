"""Where the next deposit should go (strategy principle 8c; plan Q5.4). Read-only.

Order of preference, applied one purchase at a time on the portfolio as it would stand after the
purchases before it:

0. the core, while the core tier is below its target (a core ETF, or a core stock that passes
   principle 8a);
1. the most under-represented risk tier, sector or country, and within it the highest-ranked
   holding or bench idea whose thesis passes the return hurdle;
2. any holding or bench idea that passes the return hurdle, by expected return;
3. a core ETF.

A purchase is made in whole shares and is shrunk (halved until it fits, else the candidate is
set aside) when the portfolio after it would show a new or deeper breach of a rule, cap or band
(the same evaluation as after a sync), or of an ex-ante risk limit. Nothing is ever bought into a
position already above its cap, because that breach would only get deeper. The result lists the
purchases with the principle behind each and the ex-ante volatility before and after.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig, ThesisConfig
from steward.fx.convert import normalise_currency, price_in_unit
from steward.portfolio import exante as ea
from steward.portfolio import trade_risk as tr
from steward.portfolio.breaches import Finding, evaluate
from steward.portfolio.drift import (
    DriftReport,
    HoldingValue,
    bucket_for,
    compute_drift,
)
from steward.portfolio.risk_tier import apply_tiers, instrument_is_fund

METHOD_VERSION = "allocator-1"
MAX_STEPS = 60
DIMENSIONS = ("risk", "sector", "geography")
Stage = Literal["core_below_target", "under_represented", "above_hurdle", "core_fallback"]

STAGE_TEXT: dict[str, str] = {
    "core_below_target": "8c: the core is below its target, so deposits go to it first",
    "under_represented": "8c: fills the most under-represented tier, sector or country",
    "above_hurdle": "8c: highest expected return among the ideas that pass the hurdle",
    "core_fallback": "8c: no idea is ready, so the money goes to the core",
}


@dataclass(frozen=True)
class Candidate:
    ticker: str
    name: str | None
    currency: str
    price_base: float  # price of one share in the base currency
    is_etf: bool
    held_value: float
    thesis_return: float | None  # to the sell target, total; None: no usable thesis
    unavailable: str | None = None  # why it cannot be bought


@dataclass
class Check:
    """Result of an ex-ante test of a portfolio: the keys of limits newly breached or deepened,
    the forecast volatility and the effective bets."""

    bad: list[str]
    volatility: float | None
    effective_bets: float | None


ExAnte = Callable[[dict[str, float]], Check]


class AllocationLine(BaseModel):
    ticker: str
    name: str | None
    stage: Stage
    principle: str
    shares: int
    price_base: float
    value: float = Field(description="Shares times price, base currency.")
    weight_before: float
    weight_after: float
    expected_return: float | None = Field(description="Return to the thesis sell target.")
    volatility_change: float | None = Field(
        description="Change in ex-ante portfolio volatility from this line (percentage points "
        "of annual volatility, as a fraction)."
    )


class SetAside(BaseModel):
    ticker: str
    reason: str


class AllocationPlan(BaseModel):
    method_version: str = METHOD_VERSION
    amount: float
    spent: float
    left_over: float
    base_currency: str
    lines: list[AllocationLine]
    set_aside: list[SetAside]
    volatility_before: float | None
    volatility_after: float | None
    effective_bets_before: float | None
    effective_bets_after: float | None
    rule_findings_after: list[str] = Field(
        description="Rule, cap and band breaches that exist after the purchases and did not get "
        "deeper (already open before)."
    )
    notes: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "A ranked suggestion against your own principles. Nothing is bought; whole shares at the "
        "last stored price, so the real fill will differ a little."
    )


@dataclass
class _State:
    values: dict[str, float]
    currency: dict[str, str]
    spent: float = 0.0
    shares: dict[str, int] = field(default_factory=dict)
    stage: dict[str, Stage] = field(default_factory=dict)
    vol_change: dict[str, float] = field(default_factory=dict)


def _holdings(values: dict[str, float], currency: dict[str, str]) -> list[HoldingValue]:
    return [
        HoldingValue(t, currency.get(t) or "CZK", Decimal(str(v)))
        for t, v in values.items()
        if v > 1e-9
    ]


def _drift(
    values: dict[str, float], currency: dict[str, str], cash: float, strategy: StrategyConfig
) -> DriftReport:
    return compute_drift(_holdings(values, currency), Decimal(str(max(cash, 0.0))), strategy)


def _deficits(
    report: DriftReport, strategy: StrategyConfig, cand: Candidate, tier_of: dict[str, str]
) -> tuple[float, float, float]:
    """(core deficit, total deficit over the candidate's buckets, as shares of the weight basis;
    the basis value)."""
    holding = HoldingValue(cand.ticker, cand.currency, Decimal(0))
    buckets = {dim: bucket_for(strategy, holding, dim) for dim in DIMENSIONS}
    core = 0.0
    total = 0.0
    for dim in report.categories:
        if dim.dimension not in buckets:
            continue
        for b in dim.buckets:
            if b.bucket != buckets[dim.dimension] or b.target_weight is None:
                continue
            gap = max(0.0, b.target_weight - b.weight)
            total += gap
            if dim.dimension == "risk" and b.bucket == "core":
                core = gap
    return core, total, report.basis_value_base


def _worse(before: dict[str, Finding], after: dict[str, Finding]) -> list[str]:
    out: list[str] = []
    for key, f in after.items():
        prior = before.get(key)
        if prior is None:
            out.append(key)
        elif (
            prior.observed is not None
            and f.observed is not None
            and f.limit is not None
            and abs(float(f.observed) - float(f.limit))
            > abs(float(prior.observed) - float(f.limit)) + 1e-12
        ):
            out.append(key)
    return out


def allocate(
    amount: float,
    base_currency: str,
    cash_before: float,
    held: dict[str, float],
    currency: dict[str, str],
    candidates: list[Candidate],
    strategy: StrategyConfig,
    tier_of: dict[str, str],
    return_hurdle: float,
    exante: ExAnte | None = None,
) -> AllocationPlan:
    """Split `amount` over the candidates by the order of principle 8c (pure)."""
    strategy_t = apply_tiers(strategy, tier_of)
    reserve = cash_reserve(amount, cash_before, held, strategy_t)
    state = _State(values=dict(held), currency=dict(currency))
    for c in candidates:
        state.currency.setdefault(c.ticker, c.currency)
    before_rules = {
        f.key: f
        # The baseline is the deposit left in cash: it dilutes every weight by itself.
        for f in evaluate(
            _drift(held, state.currency, cash_before + amount, strategy_t), strategy_t
        )
    }
    base_check = exante(dict(held)) if exante else Check([], None, None)
    check_now = base_check
    set_aside: dict[str, str] = {c.ticker: c.unavailable for c in candidates if c.unavailable}
    by_ticker = {c.ticker: c for c in candidates}
    start_weight = _weights(held, cash_before, strategy_t)

    def eligible(c: Candidate) -> str | None:
        if c.unavailable:
            return c.unavailable
        if c.price_base <= 0:
            return "no price to size whole shares with"
        if c.is_etf:
            return None
        if c.thesis_return is None:
            return "no thesis with a sell target (principle 8a)"
        if c.thesis_return < return_hurdle:
            return f"expected return {c.thesis_return:.0%} is under the {return_hurdle:.0%} hurdle"
        return None

    for c in candidates:
        why = eligible(c)
        if why and c.ticker not in set_aside:
            set_aside[c.ticker] = why
    for _ in range(MAX_STEPS):
        remaining = amount - reserve - state.spent
        live = [c for c in candidates if c.ticker not in set_aside and c.price_base <= remaining]
        if not live:
            break
        report = _drift(
            state.values, state.currency, cash_before + amount - state.spent, strategy_t
        )
        ranked: list[tuple[tuple[int, float, float], Candidate, Stage, float]] = []
        for c in live:
            core_gap, gap, basis = _deficits(report, strategy_t, c, tier_of)
            is_core = tier_of.get(c.ticker) == "core" or (
                strategy_t.classifications.get(c.ticker, {}).get("risk") == "core"
            )
            ret = c.thesis_return if c.thesis_return is not None else 0.0
            if core_gap > 0 and is_core:
                stage: Stage = "core_below_target"
                key = (0, -core_gap, -ret)
                want = core_gap * basis
            elif gap > 0:
                stage = "under_represented"
                key = (1, -gap, -ret)
                want = gap * basis
            elif c.thesis_return is not None and not c.is_etf:
                stage = "above_hurdle"
                key = (2, -ret, 0.0)
                want = remaining
            elif c.is_etf and is_core:
                stage = "core_fallback"
                key = (3, 0.0, 0.0)
                want = remaining
            else:
                continue
            ranked.append((key, c, stage, want))
        if not ranked:
            break
        ranked.sort(key=lambda r: r[0])
        _, cand, stage, want = ranked[0]
        shares = max(1, math.floor(min(remaining, max(want, cand.price_base)) / cand.price_base))
        shares = min(shares, math.floor(remaining / cand.price_base))
        accepted = False
        bad: list[str] = []
        while shares >= 1:
            trial = dict(state.values)
            trial[cand.ticker] = trial.get(cand.ticker, 0.0) + shares * cand.price_base
            after = {
                f.key: f
                for f in evaluate(
                    _drift(
                        trial,
                        state.currency,
                        cash_before + amount - state.spent - shares * cand.price_base,
                        strategy_t,
                    ),
                    strategy_t,
                )
            }
            bad = _worse(before_rules, after)
            risk = exante(trial) if exante and not bad else None
            if risk is not None:
                bad = list(risk.bad)
            if not bad:
                state.values = trial
                state.spent += shares * cand.price_base
                state.shares[cand.ticker] = state.shares.get(cand.ticker, 0) + shares
                state.stage.setdefault(cand.ticker, stage)
                if risk is not None:
                    if risk.volatility is not None and check_now.volatility is not None:
                        state.vol_change[cand.ticker] = state.vol_change.get(cand.ticker, 0.0) + (
                            risk.volatility - check_now.volatility
                        )
                    check_now = risk
                accepted = True
                break
            shares //= 2
        if not accepted:
            set_aside[cand.ticker] = "a purchase would breach or deepen a limit: " + ", ".join(
                bad[:3]
            )
    end_weight = _weights(state.values, cash_before + amount - state.spent, strategy_t)
    lines = [
        AllocationLine(
            ticker=t,
            name=by_ticker[t].name,
            stage=state.stage[t],
            principle=STAGE_TEXT[state.stage[t]],
            shares=n,
            price_base=by_ticker[t].price_base,
            value=n * by_ticker[t].price_base,
            weight_before=start_weight.get(t, 0.0),
            weight_after=end_weight.get(t, 0.0),
            expected_return=by_ticker[t].thesis_return,
            volatility_change=state.vol_change.get(t),
        )
        for t, n in sorted(
            state.shares.items(), key=lambda kv: (_stage_order(state.stage[kv[0]]), -kv[1])
        )
    ]
    after_findings = evaluate(
        _drift(state.values, state.currency, cash_before + amount - state.spent, strategy_t),
        strategy_t,
    )
    notes: list[str] = []
    if reserve > 0:
        notes.append(
            f"{reserve:,.0f} {base_currency} of the deposit stays in cash: the cash target "
            "(principle 2c) is below its lower band edge without it."
        )
    if state.spent <= 0:
        notes.append(
            "Nothing could be bought: no candidate passes the thesis and return tests and fits "
            "the limits. Hold the cash, or add a thesis for a bench idea."
        )
    return AllocationPlan(
        amount=amount,
        spent=state.spent,
        left_over=amount - state.spent,
        base_currency=base_currency,
        lines=lines,
        set_aside=[SetAside(ticker=t, reason=r) for t, r in sorted(set_aside.items())],
        volatility_before=base_check.volatility,
        volatility_after=check_now.volatility,
        effective_bets_before=base_check.effective_bets,
        effective_bets_after=check_now.effective_bets,
        rule_findings_after=[f.detail for f in after_findings if f.key in before_rules],
        notes=notes,
    )


def cash_reserve(
    amount: float, cash_before: float, held: dict[str, float], strategy: StrategyConfig
) -> float:
    """How much of the deposit to leave in cash (principle 2c): what lifts cash back to the
    lower edge of the strategy's cash band, measured on the book after the deposit. Zero when
    the strategy sets no cash target, or cash is already at or above that edge."""
    target = strategy.cash_target
    if target is None:
        return 0.0
    total = sum(v for v in held.values() if v > 0) + max(cash_before, 0.0) + amount
    floor = max(target.target_weight - target.band, 0.0) * total
    return min(amount, max(0.0, floor - max(cash_before, 0.0)))


def _stage_order(stage: Stage) -> int:
    return ["core_below_target", "under_represented", "above_hurdle", "core_fallback"].index(stage)


def _weights(values: dict[str, float], cash: float, strategy: StrategyConfig) -> dict[str, float]:
    invested = sum(v for v in values.values() if v > 0)
    denominator = invested + max(cash, 0.0) if strategy.weight_basis == "total" else invested
    if denominator <= 0:
        return {}
    return {t: v / denominator for t, v in values.items() if v > 0}


# ------------------------------------------------------------------------------------------ I/O


async def allocate_deposit(pool: asyncpg.Pool, amount: float) -> AllocationPlan:
    """The plan for a deposit of `amount` (base currency) on the latest synced book. Candidates
    are the holdings and the bench (theses for stocks not held) with a stored price series."""
    book, strategy = await ea.book_from_snapshot(pool)
    if book is None or strategy is None:
        raise LookupError("no portfolio snapshot yet; run the portfolio sync")
    held_values = {p.ticker: p.value_base for p in book.positions}
    currency = {p.ticker: p.currency for p in book.positions}
    cash_before = max(book.total_value - sum(held_values.values()), 0.0)
    records = await pool.fetch(
        "select p.ticker, p.quantity, p.value_base from public.position_snapshot p"
        " where p.snapshot_date = (select max(snapshot_date) from public.position_snapshot)"
    )
    quantity = {r["ticker"]: float(r["quantity"] or 0) for r in records}
    theses = await load_active(pool, "theses", ThesesConfig)
    thesis_by = {t.ticker: t for t in (theses.body.theses if theses else [])}
    bench = sorted(t for t in thesis_by if t not in held_values)
    new = await tr.load_new_series(pool, bench)
    names = await pool.fetch(
        "select t212_ticker, instrument_type, security_type from public.instrument"
        " where t212_ticker = any($1::text[])",
        sorted(set(held_values) | set(new)),
    )
    is_etf = {
        r["t212_ticker"]: instrument_is_fund(r["instrument_type"], r["security_type"])
        for r in names
    }
    probe = tr.book_after(book, {}, 0.0, new) if new else book
    strategy2 = await tr.candidate_tiers(pool, probe, strategy, sorted(new))
    tier_of = {t: b["risk"] for t, b in strategy2.classifications.items() if b.get("risk")}
    known = {t: meta for t, (meta, _) in new.items()}
    baseline_report, baseline = tr.findings_for(book, strategy2)
    baseline_keys = {f.key: f for f in baseline}

    candidates: list[Candidate] = []
    for ticker, value in held_values.items():
        qty = quantity.get(ticker, 0.0)
        candidates.append(
            _candidate(
                book,
                thesis_by.get(ticker),
                ticker,
                None,
                currency[ticker],
                value / qty if qty > 0 else 0.0,
                None,
                is_etf.get(ticker, False),
                value,
            )
        )
    for ticker, (meta, closes) in new.items():
        iso, factor = normalise_currency(meta.currency)
        day, close = closes[-1]
        rate = book.fx(iso, day) if book.fx else None
        price = float(close) * float(factor) * rate if rate else 0.0
        candidates.append(
            _candidate(
                book,
                thesis_by.get(ticker),
                ticker,
                meta.name,
                meta.currency,
                price,
                float(close),
                is_etf.get(ticker, False),
                0.0,
            )
        )
    for ticker in bench:
        if ticker not in new:
            candidates.append(
                Candidate(
                    ticker,
                    None,
                    "",
                    0.0,
                    False,
                    0.0,
                    None,
                    "no stored price series or instrument record",
                )
            )

    def exante(values: dict[str, float]) -> Check:
        deltas = {
            t: values.get(t, 0.0) - held_values.get(t, 0.0) for t in set(values) | set(held_values)
        }
        after_book = tr.book_after(probe, deltas, 0.0, None, known)
        report, found = tr.findings_for(after_book, strategy2)
        bad = [
            f.key for f in found if tr.change_of(baseline_keys.get(f.key), f) in ("new", "deeper")
        ]
        return Check(bad, report.volatility_forecast, report.effective_bets)

    plan = allocate(
        amount,
        book.base_currency,
        cash_before,
        held_values,
        currency,
        candidates,
        strategy2,
        tier_of,
        strategy.pretrade.return_hurdle,
        exante if baseline_report.volatility_forecast is not None else None,
    )
    if baseline_report.volatility_forecast is None:
        plan.notes.append(
            "The ex-ante risk model has no forecast for the current book, so the risk limits were "
            "not applied; only the rules, caps and bands were."
        )
    return plan


def _candidate(
    book: ea.LoadedBook,
    thesis: ThesisConfig | None,
    ticker: str,
    name: str | None,
    currency: str,
    price_base: float,
    price_local: float | None,
    etf: bool,
    held_value: float,
) -> Candidate:
    held_name = next((p.name for p in book.positions if p.ticker == ticker), name)
    target = thesis.price_target if thesis else None
    ret: float | None = None
    # Scenarios (Q8.1) give the probability-weighted value; without them the sell target does.
    value = (
        target.scenarios.expected_value
        if target is not None and target.scenarios is not None
        else (target.sell_target if target is not None else None)
    )
    if target is not None and value is not None:
        price = price_local if price_local is not None else _local_price(book, ticker)
        # The price in the target's unit: a GBX quote against a GBP target is scaled by 100.
        scaled = price_in_unit(price, currency, target.currency) if price and price > 0 else None
        if scaled is not None and scaled > 0:
            ret = value / scaled - 1.0
    return Candidate(ticker, held_name, currency, price_base, etf, held_value, ret)


def _local_price(book: ea.LoadedBook, ticker: str) -> float | None:
    closes = book.daily_closes.get(ticker)
    return float(closes[-1][1]) if closes else None
