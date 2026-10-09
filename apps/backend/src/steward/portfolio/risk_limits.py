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

import asyncpg

from steward.config.strategy import RiskLimits, StrategyConfig
from steward.portfolio.breaches import Finding
from steward.portfolio.snapshot import PositionRow
from steward.settings import get_settings

log = logging.getLogger(__name__)

# Window of the volatility and beta measurements, in days.
RISK_WINDOW_DAYS = 365


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


@dataclass
class RiskInputs:
    volatility: float | None = None
    beta: float | None = None
    drawdown: float | None = None
    var_95: float | None = None
    tracking_error: float | None = None
    stress_loss: float | None = None  # positive loss, share of the portfolio
    credit_spread_bp: float | None = None
    credit_spread_date: dt.date | None = None
    valuation_flags: list[ValuationFlag] = field(default_factory=list)
    correlations: list[PairCorrelation] = field(default_factory=list)
    positions: list[PositionLoss] = field(default_factory=list)
    underlyings: list[Underlying] = field(default_factory=list)
    exposures: list[Exposure] = field(default_factory=list)
    # Measurements that could not be taken, by limit name (reported, never a breach).
    unavailable: dict[str, str] = field(default_factory=dict)


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


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
    for key, value, ceiling, what in (
        ("var", inputs.var_95, limits.max_var_95, "one-day historical 95% value at risk"),
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


async def gather_risk_inputs(
    pool: asyncpg.Pool, strategy: StrategyConfig, positions: list[PositionRow]
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
    if limits.max_pair_correlation is not None:
        try:
            inputs.correlations = await pair_correlations(pool, positions)
        except Exception as exc:
            log.warning("correlation failed: %s", exc)
            inputs.unavailable["correlation"] = str(exc)
    wants_xray = (
        limits.max_look_through_weight is not None
        or limits.max_sector_weight is not None
        or limits.max_country_weight is not None
    )
    xray_done = False
    if wants_xray and (owner := get_settings().owner_user_id):
        try:
            await _xray_inputs(pool, owner, limits, inputs)
            xray_done = True
        except Exception as exc:
            log.warning("x-ray measurement failed: %s", exc)
            inputs.unavailable["xray"] = str(exc)
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


async def pair_correlations(
    pool: asyncpg.Pool, positions: list[PositionRow]
) -> list[PairCorrelation]:
    """Correlation of daily returns (last year) for every pair of held positions that both have
    a stored price series."""
    # Imported here for the same cycle as in gather_risk_inputs.
    from steward.config.holding_profiles import HoldingProfilesConfig
    from steward.config.store import load_active
    from steward.reference import risk as ref_risk
    from steward.reference.price_series import choose_series, holding_symbols
    from steward.reference.read import load_series

    held = {p.ticker: float(p.value_base) for p in positions if p.value_base > 0}
    total = sum(held.values())
    if len(held) < 2 or total <= 0:
        return []
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool, sorted(held), profiles.body if profiles else HoldingProfilesConfig()
    )
    chosen = await choose_series(pool, symbols)
    series = {}
    for ticker, ref in chosen.items():
        if ref is not None:
            series[ticker] = await load_series(pool, ref)
    out: list[PairCorrelation] = []
    tickers = sorted(series)
    for i, a in enumerate(tickers):
        for b in tickers[i + 1 :]:
            corr = ref_risk.correlation(series[a], series[b])
            if corr is not None:
                out.append(PairCorrelation(a, b, corr, (held[a] + held[b]) / total))
    return out
