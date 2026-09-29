"""Risk-limit checks (strategy `risk`) run after each sync, next to the drift and rule checks.

Each limit that does not hold becomes a `Finding` (kind `rule`, key `risk:<limit>[:<scope>]`), so
it opens, alerts once and clears through the same breach machinery as the strategy rules. The
limits are review prompts: a position down past `position_loss_review` needs its thesis
re-checked, not selling.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal

import asyncpg

from steward.config.strategy import RiskLimits, StrategyConfig
from steward.portfolio.breaches import Finding
from steward.portfolio.snapshot import PositionRow

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


@dataclass
class RiskInputs:
    volatility: float | None = None
    beta: float | None = None
    positions: list[PositionLoss] = field(default_factory=list)
    underlyings: list[Underlying] = field(default_factory=list)
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
    if limits.max_volatility is not None or limits.max_beta is not None:
        try:
            risk = await portfolio_risk(pool, RISK_WINDOW_DAYS)
            inputs.volatility = risk.portfolio.annualised_volatility
            inputs.beta = risk.beta
            if inputs.volatility is None:
                inputs.unavailable["volatility"] = risk.portfolio.reason or "no estimate"
            if limits.max_beta is not None and inputs.beta is None:
                inputs.unavailable["beta"] = "no benchmark or too few shared days"
        except Exception as exc:
            log.warning("risk measurement failed: %s", exc)
            inputs.unavailable["volatility"] = inputs.unavailable["beta"] = str(exc)
    if limits.max_look_through_weight is not None:
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
