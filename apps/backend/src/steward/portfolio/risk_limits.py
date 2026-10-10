"""Risk-limit checks (strategy `risk`) run after each sync, next to the drift and rule checks.

Each limit that does not hold becomes a `Finding` (kind `rule`, key `risk:<limit>[:<scope>]`), so
it opens, alerts once and clears through the same breach machinery as the strategy rules. The
limits are review prompts: a position down past `position_loss_review` needs its thesis
re-checked, not selling.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

import asyncpg

from steward.config.store import load_active
from steward.config.strategy import PositionWeightRule, RiskLimits, StrategyConfig
from steward.portfolio.breaches import Finding

if TYPE_CHECKING:
    from steward.portfolio.exante import ExAnteRisk, LoadedBook
    from steward.portfolio.tail import TailReport
from steward.portfolio.snapshot import PositionRow
from steward.settings import get_settings

log = logging.getLogger(__name__)

# Window of the volatility and beta measurements, in days.
RISK_WINDOW_DAYS = 365
# Trading days in the realized-volatility window of a thesis's volatility band (three months).
THESIS_VOLATILITY_DAYS = 63
THESIS_VOLATILITY_SEVERITY = 2


@dataclass(frozen=True)
class PositionLoss:
    ticker: str
    cost: float
    value: float


@dataclass(frozen=True)
class Underlying:
    name: str
    weight: float  # direct plus via funds, share of total portfolio value
    via_fund: bool  # reached through at least one fund


@dataclass(frozen=True)
class Exposure:
    dimension: str  # "sector" or "country"
    name: str
    weight: float  # share of total portfolio value, funds looked through


@dataclass(frozen=True)
class PairCorrelation:
    a: str
    b: str
    correlation: float
    combined_weight: float  # share of the invested value


@dataclass(frozen=True)
class ValuationFlag:
    ticker: str
    scenario: str
    what: str  # "terminal growth" or "explicit-year growth"
    value: float


@dataclass(frozen=True)
class TierFlag:
    ticker: str
    tier: str
    volatility: float
    threshold: float
    rule: str  # "speculative_above" or "core_above"


@dataclass(frozen=True)
class ThesisVolatilityFlag:
    ticker: str
    volatility: float  # three-month realized, annualised
    band: float  # the thesis's expected volatility
    multiple: float


@dataclass(frozen=True)
class StockRiskShare:
    ticker: str
    share: float  # of the portfolio's forecast variance


@dataclass
class RiskInputs:
    volatility: float | None = None
    beta: float | None = None
    drawdown: float | None = None
    var_95: float | None = None
    es_95_month: float | None = None  # one-month 95 % Expected Shortfall, share of the book
    drawdown_probability: float | None = None  # simulated chance of the max-drawdown fall
    tracking_error: float | None = None
    stress_loss: float | None = None  # positive loss, share of the portfolio
    credit_spread_bp: float | None = None
    credit_spread_date: dt.date | None = None
    volatility_forecast: float | None = None  # ex-ante, the higher of EWMA and Ledoit-Wolf
    forecast_model: str | None = None
    tier_risk_shares: dict[str, float] = field(default_factory=dict)  # tier -> share of risk
    effective_bets: float | None = None
    model_check: str | None = None  # why the risk model's own back-test failed (Q6.1)
    drawdown_drivers: list[tuple[str, float]] = field(default_factory=list)  # ticker, share lost
    reviews_due: list[str] = field(default_factory=list)  # tickers whose thesis review is due
    tier_flags: list[TierFlag] = field(default_factory=list)
    thesis_volatility: list[ThesisVolatilityFlag] = field(default_factory=list)
    stock_risk_shares: list[StockRiskShare] = field(default_factory=list)
    valuation_flags: list[ValuationFlag] = field(default_factory=list)
    correlations: list[PairCorrelation] = field(default_factory=list)
    positions: list[PositionLoss] = field(default_factory=list)
    underlyings: list[Underlying] = field(default_factory=list)
    exposures: list[Exposure] = field(default_factory=list)
    # Measurements that could not be taken, by limit name (reported, never a breach).
    unavailable: dict[str, str] = field(default_factory=dict)


# Which finding keys each unavailable measurement stands behind (a key matches a prefix when it
# equals it or continues after a colon).
_UNAVAILABLE_KEYS: dict[str, tuple[str, ...]] = {
    "volatility": ("risk:volatility",),
    "beta": ("risk:beta",),
    "drawdown": ("risk:drawdown", "risk:drawdown_warning"),
    "correlation": ("risk:correlation",),
    "volatility_forecast": ("risk:volatility_forecast", "risk:stock_share", "risk:tier"),
    "tier_risk": ("risk:tier_budget",),
    "effective_bets": ("risk:effective_bets",),
    "es": ("risk:es",),
    "drawdown_probability": ("risk:drawdown_probability",),
    "thesis_volatility": ("thesis:volatility",),
    "var": ("risk:var",),
    "tracking_error": ("risk:tracking_error",),
    "stress": ("risk:stress",),
    "credit_spread": ("risk:credit_spread",),
    "valuation": ("risk:valuation",),
    "xray": ("risk:sector", "risk:country", "risk:overlap"),
    "overlap": ("risk:overlap",),
}


def unmeasured_keys(unavailable: dict[str, str]) -> frozenset[str]:
    """Finding-key prefixes whose measurement failed this run: their open breaches are kept."""
    return frozenset(k for name in unavailable for k in _UNAVAILABLE_KEYS.get(name, ()))


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _model_name(model: str | None) -> str:
    return {"ewma": "EWMA model", "ledoit_wolf": "Ledoit-Wolf model"}.get(model or "", "model")


def drawdown_note(inputs: RiskInputs, rung: float, freeze: float) -> str:
    """The text of a drawdown early warning: the drivers and the theses due for review."""
    assert inputs.drawdown is not None
    text = (
        f"Early warning: the portfolio is {_pct(inputs.drawdown)} below its peak of the last "
        f"year, past the {_pct(rung)} note; the freeze is at {_pct(freeze)}."
    )
    if inputs.drawdown_drivers:
        drivers = ", ".join(f"{t} ({c:+.1%} of the portfolio)" for t, c in inputs.drawdown_drivers)
        text += f" Biggest drivers since the peak, on today's weights: {drivers}."
    if inputs.reviews_due:
        text += f" Theses due for re-confirmation: {', '.join(inputs.reviews_due)}."
    return text + " A fall in price alone is not a reason to sell."


def evaluate_risk(inputs: RiskInputs, limits: RiskLimits) -> list[Finding]:
    severity = int(limits.on_breach[-1])
    findings: list[Finding] = []
    if (
        limits.max_volatility is not None
        and inputs.volatility is not None
        and inputs.volatility > limits.max_volatility
    ):
        findings.append(
            Finding(
                "risk:volatility",
                "rule",
                None,
                severity,
                f"Risk limit: annualised portfolio volatility is {_pct(inputs.volatility)}; "
                f"the limit is {_pct(limits.max_volatility)}",
                inputs.volatility,
                limits.max_volatility,
            )
        )
    if (
        limits.max_volatility is not None
        and inputs.volatility_forecast is not None
        and inputs.volatility_forecast > limits.max_volatility
    ):
        findings.append(
            Finding(
                "risk:volatility_forecast",
                "rule",
                None,
                severity,
                f"Risk limit: the forecast annualised volatility of today's portfolio is "
                f"{_pct(inputs.volatility_forecast)} ({_model_name(inputs.forecast_model)}); "
                f"the limit is {_pct(limits.max_volatility)}. A review prompt, not an "
                "instruction to trade",
                inputs.volatility_forecast,
                limits.max_volatility,
            )
        )
    for tier, budget in sorted((limits.tier_risk_budgets or {}).items()):
        share = inputs.tier_risk_shares.get(tier)
        if share is not None and share > budget:
            findings.append(
                Finding(
                    f"risk:tier_budget:{tier}",
                    "rule",
                    None,
                    severity,
                    f"Risk limit: the {tier} tier accounts for {_pct(share)} of the portfolio's "
                    f"forecast volatility; its risk budget is {_pct(budget)}",
                    share,
                    budget,
                )
            )
    if (
        limits.min_effective_bets is not None
        and inputs.effective_bets is not None
        and inputs.effective_bets < limits.min_effective_bets
    ):
        findings.append(
            Finding(
                "risk:effective_bets",
                "rule",
                None,
                severity,
                f"Risk limit: the portfolio holds about {inputs.effective_bets:.1f} independent "
                f"bets; the floor is {limits.min_effective_bets:.1f}. Many names can still be "
                "one bet: check what the largest risk contributors have in common",
                inputs.effective_bets,
                limits.min_effective_bets,
            )
        )
    if inputs.model_check:
        # A review item about the measuring tool, not about the portfolio: lowest severity that
        # still reaches the review, whatever `on_breach` says.
        findings.append(
            Finding("risk:model_validation", "rule", None, 1, inputs.model_check, None, None)
        )
    # One finding per ticker, even when it breaches both tier rules (a core stock above 35 %).
    by_ticker: dict[str, list[TierFlag]] = {}
    for tf in inputs.tier_flags:
        by_ticker.setdefault(tf.ticker, []).append(tf)
    for ticker, flags in by_ticker.items():
        first = min(flags, key=lambda f: f.threshold)
        what = " and ".join(
            f"is above {_pct(f.threshold)}, so it is not a {f.tier} stock by the tier criteria "
            "(speculative above that volatility)"
            if f.rule == "speculative_above"
            else f"is above the core ceiling of {_pct(f.threshold)}"
            for f in sorted(flags, key=lambda f: f.threshold)
        )
        findings.append(
            Finding(
                f"risk:tier:{ticker}",
                "rule",
                ticker,
                severity,
                f"Tier criterion: {ticker} ({first.tier}) has annualised volatility "
                f"{_pct(first.volatility)} over the last year, which {what}. Re-check its tier "
                "at the thesis review; reclassify by hand",
                first.volatility,
                max(f.threshold for f in flags),
            )
        )
    cap = limits.max_stock_risk_share
    for rs in inputs.stock_risk_shares:
        if cap is not None and rs.share > cap:
            findings.append(
                Finding(
                    f"risk:stock_share:{rs.ticker}",
                    "rule",
                    rs.ticker,
                    severity,
                    f"Risk limit: {rs.ticker} accounts for {_pct(rs.share)} of the portfolio's "
                    f"forecast volatility; the ceiling per stock is {_pct(cap)}. A review "
                    "prompt for its size, not an instruction to trade",
                    rs.share,
                    cap,
                )
            )
    for tv in inputs.thesis_volatility:
        findings.append(
            Finding(
                f"thesis:volatility:{tv.ticker}",
                "rule",
                tv.ticker,
                THESIS_VOLATILITY_SEVERITY,
                f"Thesis risk profile changed: {tv.ticker} has realized volatility of "
                f"{_pct(tv.volatility)} over the last three months, above {tv.multiple:g} times "
                f"the {_pct(tv.band)} its thesis expects. Re-check the thesis; a move in price "
                "alone is not a reason to sell",
                tv.volatility,
                tv.band * tv.multiple,
            )
        )
    if limits.max_beta is not None and inputs.beta is not None and inputs.beta > limits.max_beta:
        findings.append(
            Finding(
                "risk:beta",
                "rule",
                None,
                severity,
                f"Risk limit: portfolio beta is {inputs.beta:.2f}; "
                f"the limit is {limits.max_beta:.2f}",
                inputs.beta,
                limits.max_beta,
            )
        )
    if (
        limits.max_drawdown is not None
        and inputs.drawdown is not None
        and inputs.drawdown > limits.max_drawdown
    ):
        findings.append(
            Finding(
                "risk:drawdown",
                "rule",
                None,
                severity,
                f"Risk limit: the portfolio is {_pct(inputs.drawdown)} below its peak of the "
                f"last year; the limit is {_pct(limits.max_drawdown)}. Review the theses; a "
                "fall in price alone is not a reason to sell",
                inputs.drawdown,
                limits.max_drawdown,
            )
        )
    if limits.max_drawdown is not None and inputs.drawdown is not None:
        # Early-warning rungs below the freeze: one note per rung reached, so each alerts when
        # it opens. The freeze above stays exactly as written.
        for rung in sorted(limits.drawdown_warnings):
            if rung >= limits.max_drawdown or inputs.drawdown < rung:
                continue
            findings.append(
                Finding(
                    f"risk:drawdown_warning:{round(rung * 100)}",
                    "rule",
                    None,
                    1,
                    drawdown_note(inputs, rung, limits.max_drawdown),
                    inputs.drawdown,
                    rung,
                )
            )
    for key, value, ceiling, what in (
        ("var", inputs.var_95, limits.max_var_95, "one-day historical 95% value at risk"),
        (
            "es",
            inputs.es_95_month,
            limits.max_es_95_month,
            "one-month 95% Expected Shortfall (average loss in the worst 5% of months)",
        ),
        (
            "drawdown_probability",
            inputs.drawdown_probability,
            limits.max_drawdown_probability,
            f"simulated chance of a {_pct(limits.max_drawdown or 0)} fall from a peak within "
            f"{limits.drawdown_probability_years} years",
        ),
        (
            "tracking_error",
            inputs.tracking_error,
            limits.max_tracking_error,
            "annualised tracking error against the benchmark",
        ),
        (
            "stress",
            inputs.stress_loss,
            limits.max_stress_loss,
            f"loss if equities fall {limits.stress_equity_fall:.0%}",
        ),
    ):
        if ceiling is not None and value is not None and value > ceiling:
            findings.append(
                Finding(
                    f"risk:{key}",
                    "rule",
                    None,
                    severity,
                    f"Risk limit: the {what} is {_pct(value)}; the limit is {_pct(ceiling)}. "
                    "A review prompt, not an instruction to trade",
                    value,
                    ceiling,
                )
            )
    if (
        limits.credit_spread_alert_bp is not None
        and inputs.credit_spread_bp is not None
        and inputs.credit_spread_bp >= limits.credit_spread_alert_bp
    ):
        findings.append(
            Finding(
                "risk:credit_spread",
                "rule",
                None,
                severity,
                f"Credit signal: the US high-yield spread is {inputs.credit_spread_bp:.0f} bp"
                f" ({inputs.credit_spread_date}); the alert level is "
                f"{limits.credit_spread_alert_bp:.0f} bp. Widening spreads often come before "
                "equity stress: check the holdings that rely on credit",
                inputs.credit_spread_bp,
                limits.credit_spread_alert_bp,
            )
        )
    for flag in inputs.valuation_flags:
        ceiling = (
            limits.max_valuation_terminal_growth
            if flag.what == "terminal growth"
            else limits.max_valuation_growth
        )
        if ceiling is None:
            continue
        findings.append(
            Finding(
                f"risk:valuation:{flag.ticker}:{flag.scenario}:{flag.what.split()[0]}",
                "rule",
                flag.ticker,
                severity,
                f"Valuation assumption: scenario '{flag.scenario}' for {flag.ticker} uses "
                f"{flag.what} of {_pct(flag.value)}; your ceiling is {_pct(ceiling or 0)}. "
                "Re-check the assumption before relying on the fair value",
                flag.value,
                ceiling,
            )
        )
    if limits.max_pair_correlation is not None:
        for c in inputs.correlations:
            if (
                c.correlation > limits.max_pair_correlation
                and c.combined_weight >= limits.correlation_min_combined_weight
            ):
                findings.append(
                    Finding(
                        f"risk:correlation:{c.a}|{c.b}",
                        "rule",
                        None,
                        severity,
                        f"Risk limit: {c.a} and {c.b} move together (correlation "
                        f"{c.correlation:.2f}, limit {limits.max_pair_correlation:.2f}) and "
                        f"weigh {_pct(c.combined_weight)} of the invested value: they "
                        "diversify little",
                        c.correlation,
                        limits.max_pair_correlation,
                    )
                )
    if limits.position_loss_review is not None:
        for p in inputs.positions:
            if p.cost <= 0:
                continue
            loss = 1 - p.value / p.cost
            if loss >= limits.position_loss_review:
                findings.append(
                    Finding(
                        f"risk:loss:{p.ticker}",
                        "rule",
                        p.ticker,
                        severity,
                        f"Risk limit: {p.ticker} is down {_pct(loss)} from cost (review at "
                        f"{_pct(limits.position_loss_review)}): re-check its thesis; a price "
                        "drop alone is not a reason to sell",
                        loss,
                        limits.position_loss_review,
                    )
                )
    if limits.max_look_through_weight is not None:
        for u in inputs.underlyings:
            if u.via_fund and u.weight > limits.max_look_through_weight:
                findings.append(
                    Finding(
                        f"risk:overlap:{u.name}",
                        "rule",
                        None,
                        severity,
                        f"Risk limit: {u.name} makes up {_pct(u.weight)} of the portfolio "
                        f"counting funds' holdings; the limit is "
                        f"{_pct(limits.max_look_through_weight)}",
                        u.weight,
                        limits.max_look_through_weight,
                    )
                )
    for dimension, ceiling in (
        ("sector", limits.max_sector_weight),
        ("country", limits.max_country_weight),
    ):
        if ceiling is None:
            continue
        for e in inputs.exposures:
            if e.dimension == dimension and e.weight > ceiling:
                findings.append(
                    Finding(
                        f"risk:{dimension}:{e.name}",
                        "rule",
                        None,
                        severity,
                        f"Risk limit: {e.name} is {_pct(e.weight)} of the portfolio counting "
                        f"funds' holdings; the {dimension} limit is {_pct(ceiling)}",
                        e.weight,
                        ceiling,
                    )
                )
    return findings


def position_losses(positions: list[PositionRow]) -> list[PositionLoss]:
    return [
        PositionLoss(p.ticker, float(p.cost_account), float(p.value_account))
        for p in positions
        if p.cost_account > Decimal(0)
    ]


async def _drawdown_context(
    pool: asyncpg.Pool,
    strategy: StrategyConfig,
    positions: list[PositionRow],
    inputs: RiskInputs,
    book: "LoadedBook | None",
) -> None:
    """The drivers and due reviews a drawdown note names; only when a rung is reached."""
    limits = strategy.risk
    if limits.max_drawdown is None or inputs.drawdown is None:
        return
    if not any(r < limits.max_drawdown and inputs.drawdown >= r for r in limits.drawdown_warnings):
        return
    try:
        from steward.config.thesis import ThesesConfig
        from steward.portfolio.drawdown_ladder import drivers
        from steward.portfolio.exante import book_from_positions

        loaded = book or await book_from_positions(pool, positions)
        inputs.drawdown_drivers = drivers(loaded, strategy)
        stored = await load_active(pool, "theses", ThesesConfig)
        today = dt.date.today()
        held = {p.ticker for p in positions}
        inputs.reviews_due = sorted(
            t.ticker
            for t in (stored.body.theses if stored else [])
            if t.review_date is not None and t.review_date <= today and t.ticker in held
        )
    except Exception as exc:
        log.warning("drawdown context failed: %s", exc)


async def gather_risk_inputs(
    pool: asyncpg.Pool,
    strategy: StrategyConfig,
    positions: list[PositionRow],
    book: "LoadedBook | None" = None,
) -> RiskInputs:
    """Measure only what an enabled limit needs. A failed measurement is noted, never raised:
    a missing price history must not fail the sync."""
    # Imported here: portfolio.read (via risk) imports the sync module that imports this one.
    from steward.lookthrough.read import portfolio_look_through
    from steward.portfolio.risk import portfolio_risk

    limits = strategy.risk
    inputs = RiskInputs(positions=position_losses(positions))
    if (
        limits.max_volatility is not None
        or limits.max_beta is not None
        or limits.max_drawdown is not None
    ):
        try:
            risk = await portfolio_risk(pool, RISK_WINDOW_DAYS)
            inputs.volatility = risk.portfolio.annualised_volatility
            inputs.beta = risk.beta
            inputs.drawdown = risk.current_drawdown
            if inputs.volatility is None:
                inputs.unavailable["volatility"] = risk.portfolio.reason or "no estimate"
            if limits.max_beta is not None and inputs.beta is None:
                inputs.unavailable["beta"] = "no benchmark or too few shared days"
        except Exception as exc:
            log.warning("risk measurement failed: %s", exc)
            inputs.unavailable["volatility"] = inputs.unavailable["beta"] = str(exc)
            inputs.unavailable["drawdown"] = str(exc)
    await _decision_inputs(pool, strategy, positions, inputs)
    wants_book = (
        limits.max_pair_correlation is not None
        or limits.max_volatility is not None
        or limits.tier_risk_budgets
        or limits.min_effective_bets is not None
        or limits.tier_speculative_volatility is not None
        or limits.tier_core_max_volatility is not None
        or limits.max_es_95_month is not None
        or limits.max_stock_risk_share is not None
        or limits.thesis_volatility_multiple is not None
        or (limits.max_drawdown_probability is not None and limits.max_drawdown is not None)
    )
    if wants_book:
        # One load of the weekly base-currency return matrix serves the correlation limit and
        # the ex-ante model.
        from steward.portfolio.risk_snapshots import save_snapshot

        def unmeasured(names: tuple[str, ...], exc: Exception) -> None:
            for name in names:
                inputs.unavailable.setdefault(name, str(exc))

        # Separate steps: one failing must not hide what the others measured, and each names
        # what it could not measure so its open breaches stay open.
        risk_report = None
        try:
            from steward.portfolio.exante import book_from_positions

            book = book or await book_from_positions(pool, positions)
        except Exception as exc:
            log.warning("book load failed: %s", exc)
            unmeasured(
                (
                    "correlation",
                    "volatility_forecast",
                    "tier_risk",
                    "effective_bets",
                    "es",
                    "drawdown_probability",
                    "thesis_volatility",
                ),
                exc,
            )
        if book is not None:
            try:
                risk_report = _book_inputs(book, strategy, inputs)
            except Exception as exc:
                log.warning("ex-ante measurement failed: %s", exc)
                unmeasured(
                    ("correlation", "volatility_forecast", "tier_risk", "effective_bets"), exc
                )
            try:
                from steward.portfolio.validation import validate_book

                inputs.model_check = validate_book(book, strategy).review_item
            except Exception as exc:
                log.warning("model validation failed: %s", exc)
            try:
                tail = _tail_inputs(book, strategy, inputs)
            except Exception as exc:
                log.warning("tail measurement failed: %s", exc)
                unmeasured(("es", "drawdown_probability"), exc)
                tail = None
            try:
                if tail is not None and tail.available:
                    await save_snapshot(pool, "tail", tail, tail.as_of, tail.method_version)
                if risk_report is not None and risk_report.volatility_forecast is not None:
                    await save_snapshot(
                        pool, "ex_ante", risk_report, risk_report.as_of, risk_report.method_version
                    )
            except Exception as exc:
                log.warning("risk snapshot not saved: %s", exc)
    await _drawdown_context(pool, strategy, positions, inputs, book)
    if limits.thesis_volatility_multiple is not None and book is not None:
        await _thesis_volatility(pool, limits.thesis_volatility_multiple, book, inputs)
    wants_xray = (
        limits.max_look_through_weight is not None
        or limits.max_sector_weight is not None
        or limits.max_country_weight is not None
    )
    xray_done = False
    owner = get_settings().owner_user_id
    if wants_xray and owner:
        try:
            await _xray_inputs(pool, owner, limits, inputs)
            xray_done = True
        except Exception as exc:
            log.warning("x-ray measurement failed: %s", exc)
            inputs.unavailable["xray"] = str(exc)
    if not xray_done:
        # A configured limit that could not be measured is reported, never silently dropped.
        why = inputs.unavailable.get("xray") or "no owner configured (OWNER_USER_ID)"
        if limits.max_sector_weight is not None:
            inputs.unavailable["sector"] = why
        if limits.max_country_weight is not None:
            inputs.unavailable["country"] = why
    if limits.max_look_through_weight is not None and not xray_done:
        # No owner configured or the X-Ray failed: the older look-through still answers overlap.
        try:
            report = await portfolio_look_through(pool)
            inputs.underlyings = [
                Underlying(h.name, h.weight, any(v.kind == "fund" for v in h.via))
                for h in report.top_holdings
            ]
        except Exception as exc:
            log.warning("look-through failed: %s", exc)
            inputs.unavailable["overlap"] = str(exc)
    return inputs


async def _thesis_volatility(
    pool: asyncpg.Pool, multiple: float, book: "LoadedBook", inputs: RiskInputs
) -> None:
    """Held stocks whose three-month realized volatility is above `multiple` times the band the
    thesis expects (plan Q8.8). A thesis without a band, or a stock with too short a history,
    is not judged."""
    from steward.config.thesis import ThesesConfig
    from steward.reference import risk as ref_risk

    stored = await load_active(pool, "theses", ThesesConfig)
    for thesis in stored.body.theses if stored else []:
        band = thesis.price_target.volatility_band if thesis.price_target else None
        closes = book.daily_closes.get(thesis.ticker)
        if band is None or not closes:
            continue
        vol = ref_risk.volatility(closes, THESIS_VOLATILITY_DAYS)
        if vol is not None and vol > band * multiple:
            inputs.thesis_volatility.append(
                ThesisVolatilityFlag(thesis.ticker, vol, band, multiple)
            )


async def _decision_inputs(
    pool: asyncpg.Pool,
    strategy: StrategyConfig,
    positions: list[PositionRow],
    inputs: RiskInputs,
) -> None:
    """The Phase 6 limits: value at risk, tracking error, stress loss, the credit signal and
    unusual valuation assumptions. Each is measured only when its limit is set, and a failed
    measurement is noted, never raised."""
    limits = strategy.risk
    if limits.max_var_95 is not None or limits.max_tracking_error is not None:
        try:
            from steward.decision.risk_report import build_report
            from steward.decision.series import load_bundle

            report = build_report(await load_bundle(pool, RISK_WINDOW_DAYS))
            if (got := report.stats.get("var_95")) is not None and got.value is not None:
                inputs.var_95 = float(got.value)
            elif limits.max_var_95 is not None:
                inputs.unavailable["var"] = (
                    got.warnings[0] if got and got.warnings else "no estimate"
                )
            if (got := report.stats.get("tracking_error")) is not None and got.value is not None:
                inputs.tracking_error = float(got.value)
            elif limits.max_tracking_error is not None:
                inputs.unavailable["tracking_error"] = (
                    got.warnings[0] if got and got.warnings else "no benchmark"
                )
        except Exception as exc:
            log.warning("risk report failed: %s", exc)
            inputs.unavailable["var"] = inputs.unavailable["tracking_error"] = str(exc)
    if limits.max_stress_loss is not None and (owner := get_settings().owner_user_id):
        try:
            from steward.decision.service import run_shock
            from steward.decision.shocks import ShockAssumptions, ShockSet

            run = await run_shock(
                pool, owner, ShockSet(equity_pct=-limits.stress_equity_fall), ShockAssumptions()
            )
            inputs.stress_loss = -run.result.pnl_pct
        except Exception as exc:
            log.warning("stress measurement failed: %s", exc)
            inputs.unavailable["stress"] = str(exc)
    if limits.credit_spread_alert_bp is not None:
        row = await pool.fetchrow(
            "select period, value from public.macro_series where series_id = 'us_hy_spread'"
            " and value is not null order by period desc limit 1"
        )
        if row is None:
            inputs.unavailable["credit_spread"] = (
                "no us_hy_spread series is stored; run the macro job with a FRED key"
            )
        else:
            # FRED publishes this spread in percent.
            inputs.credit_spread_bp = float(row["value"]) * 100
            inputs.credit_spread_date = row["period"]
    if limits.max_valuation_terminal_growth is not None or limits.max_valuation_growth is not None:
        held = sorted({p.ticker for p in positions if p.quantity > 0})
        # Scenarios are the owner's own; with no owner configured there is no one to read for.
        owner = get_settings().owner_user_id
        rows = (
            await pool.fetch(
                "select distinct on (ticker, name) ticker, name, assumptions"
                " from public.company_valuation_scenario"
                " where ticker = any($1::text[]) and owner_id = $2::uuid"
                " order by ticker, name, version desc",
                held,
                owner,
            )
            if owner
            else []
        )
        if not owner:
            inputs.unavailable["valuation"] = "no owner configured (OWNER_USER_ID)"
        for r in rows:
            body = r["assumptions"]
            if isinstance(body, str):
                import json

                body = json.loads(body)
            terminal = body.get("terminal_growth")
            ceiling = limits.max_valuation_terminal_growth
            if ceiling is not None and terminal is not None and float(terminal) > ceiling:
                inputs.valuation_flags.append(
                    ValuationFlag(r["ticker"], r["name"], "terminal growth", float(terminal))
                )
            growth = [float(g) for g in body.get("growth", [])]
            ceiling = limits.max_valuation_growth
            if ceiling is not None and growth and max(growth) > ceiling:
                inputs.valuation_flags.append(
                    ValuationFlag(r["ticker"], r["name"], "explicit-year growth", max(growth))
                )


async def _xray_inputs(
    pool: asyncpg.Pool, owner_id: str, limits: RiskLimits, inputs: RiskInputs
) -> None:
    """Concentration from the X-Ray, the same figures the pages show: one company's weight is
    summed over every fund that holds it, and a bucket with no real label is never a breach."""
    from steward.xray import service as xray_service
    from steward.xray.views import Dimension, XrayBucket, build_view

    loaded = await xray_service.load(pool, owner_id)
    ctx = xray_service.view_context(loaded)

    def classified(dimension: Dimension) -> list[XrayBucket]:
        return [b for b in build_view(dimension, ctx).buckets if b.kind == "classified"]

    if limits.max_look_through_weight is not None:
        inputs.underlyings = [
            Underlying(b.label, b.weight, b.fund_weight > 0) for b in classified("issuer")
        ]
    if limits.max_sector_weight is not None:
        inputs.exposures += [Exposure("sector", b.label, b.weight) for b in classified("sector")]
    if limits.max_country_weight is not None:
        inputs.exposures += [Exposure("country", b.label, b.weight) for b in classified("country")]


def _tail_inputs(
    book: "LoadedBook", strategy: StrategyConfig, inputs: RiskInputs, *, full: bool = True
) -> "TailReport":
    """The tail report of the loaded book, and the figures the limits need from it (Q2.4). A
    limit that cannot be measured is noted as unavailable, never a breach. The caller saves the
    report."""
    from steward.portfolio.tail import tail_report

    limits = strategy.risk
    wants_dd = limits.max_drawdown_probability is not None and limits.max_drawdown is not None
    report = tail_report(book, strategy, full=full)
    if not report.available:
        why = report.reason or "no estimate"
        if limits.max_es_95_month is not None:
            inputs.unavailable["es"] = why
        if wants_dd:
            inputs.unavailable["drawdown_probability"] = why
        return report
    inputs.es_95_month = report.es_95_month
    if wants_dd:
        horizon = next(
            (d for d in report.drawdown if d.years == limits.drawdown_probability_years), None
        )
        if horizon is not None:
            inputs.drawdown_probability = horizon.probability
    return report


def _book_inputs(book: "LoadedBook", strategy: StrategyConfig, inputs: RiskInputs) -> "ExAnteRisk":
    """Fill the weekly-correlation and ex-ante inputs from a loaded book; returns the report
    they came from."""
    # Imported here: portfolio.exante imports portfolio.snapshot, as this module's callers do.
    from steward.portfolio.exante import build_report, weekly_pair_correlations

    limits = strategy.risk
    invested = {p.ticker: p.value_base for p in book.positions}
    total = sum(invested.values())
    if limits.max_pair_correlation is not None and len(invested) >= 2 and total > 0:
        inputs.correlations = [
            PairCorrelation(a, b, corr, (invested[a] + invested[b]) / total)
            for a, b, corr, _ in weekly_pair_correlations(book)
        ]
    report = build_report(book, strategy)
    if report.volatility_forecast is None:
        reason = report.reason or "no estimate"
        for name in ("volatility_forecast", "tier_risk", "effective_bets"):
            inputs.unavailable[name] = reason
    else:
        inputs.volatility_forecast = report.volatility_forecast
        inputs.forecast_model = report.forecast_model
        inputs.effective_bets = report.effective_bets
        inputs.tier_risk_shares = {g.label: g.risk_share for g in report.by_tier}
    if limits.max_stock_risk_share is not None:
        exempt = {
            t
            for r in strategy.enabled_rules
            if isinstance(r, PositionWeightRule) and r.ticker is None
            for t in r.exclude
        }
        inputs.stock_risk_shares = [
            StockRiskShare(p.ticker, p.risk_share)
            for p in report.positions
            if p.risk_share is not None and p.ticker not in exempt
        ]
    inputs.tier_flags = [
        TierFlag(c.ticker, c.tier, c.volatility, c.threshold, c.rule)
        for c in report.tier_checks
        if c.breached
    ]
    return report
