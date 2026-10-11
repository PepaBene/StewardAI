"""Formal evaluation of the strategy rules for the claim ledger (Phase 4 gap 4).

The rule engine (`portfolio/breaches.py`) decides compliance from the current drift report.
This module only reads: it builds that report with the portfolio's own overview function, asks
the engine for its findings, and turns them into one status per rule for the researched ticker:
pass, breach, warn (holds, but within `WARN_MARGIN` of the limit) or unknown, with the measured
value, the limit and the snapshot date. No snapshot, or a ticker that is not held, leaves a rule
unknown with the reason. A rule that names another ticker, or excludes this one, is unknown with
"does not apply". The report's qualitative reading of the same rule stays in the claims that
name it; the two are not merged.
"""

import datetime as dt
from typing import Literal

import asyncpg
from pydantic import BaseModel

from steward.config.strategy import (
    CashWeightRule,
    CategoryWeightRule,
    PositionCountRule,
    PositionWeightRule,
    StrategyConfig,
)
from steward.portfolio.breaches import _holds, evaluate
from steward.portfolio.drift import DriftReport

WARN_MARGIN = 0.10  # holds, but within 10 % of the limit

RuleStatus = Literal["pass", "breach", "warn", "unknown"]


class RuleCompliance(BaseModel):
    status: RuleStatus
    measured: float | None = None
    limit: float | None = None
    operator: str | None = None
    unit: Literal["weight", "count"] = "weight"
    as_of: str | None = None
    reason: str
    scope: Literal["ticker", "portfolio"] = "ticker"


def _near(value: float, operator: str, limit: float) -> bool:
    if limit == 0:  # a margin of a fraction of zero is zero: nothing is "near" it
        return False
    if operator in ("<", "<="):
        return value >= limit * (1 - WARN_MARGIN)
    if operator in (">", ">="):
        return value <= limit * (1 + WARN_MARGIN)
    return False


def rule_compliance(
    strategy: StrategyConfig, drift: DriftReport | None, ticker: str, as_of: str | None
) -> dict[str, RuleCompliance]:
    """One entry per enabled rule, keyed by rule id."""
    out: dict[str, RuleCompliance] = {}
    findings = evaluate(drift, strategy) if drift is not None else []
    held = {p.ticker: p for p in drift.positions if p.held} if drift is not None else {}
    classes = strategy.classifications.get(ticker, {})
    for rule in strategy.enabled_rules:
        limit = float(rule.value)
        base = {"limit": limit, "operator": rule.operator, "as_of": as_of}
        scope: Literal["ticker", "portfolio"] = "ticker"
        unit: Literal["weight", "count"] = "weight"
        suffix: str
        measured: float | None = None
        unknown: str | None = None
        if drift is None:
            unknown = "no drift report"
        if isinstance(rule, PositionWeightRule):
            suffix = ticker
            same = rule.ticker is None or rule.ticker.casefold() == ticker.casefold()
            if not same or ticker.casefold() in {x.casefold() for x in rule.exclude}:
                unknown = "does not apply to this ticker"
            elif unknown is None and ticker not in held:
                unknown = "not held"
            elif unknown is None:
                measured = held[ticker].weight
        elif isinstance(rule, CategoryWeightRule):
            bucket = classes.get(rule.dimension)
            suffix = f"{rule.dimension}:{bucket}"
            if rule.bucket is not None and bucket != rule.bucket:
                unknown = "does not apply to this ticker"
            elif unknown is None and ticker not in held:
                unknown = "not held"
            elif unknown is None and bucket is None:
                unknown = f"the ticker has no {rule.dimension} classification"
            elif drift is not None and unknown is None:
                found = next((d for d in drift.categories if d.dimension == rule.dimension), None)
                hit = next(
                    (b for b in (found.buckets if found else []) if b.bucket == bucket), None
                )
                if hit is None:
                    unknown = f"no {rule.dimension} {bucket} bucket in the report"
                else:
                    measured = hit.weight
        elif isinstance(rule, CashWeightRule):
            scope, suffix = "portfolio", "cash"
            if unknown is None and drift is not None:
                measured = drift.cash.weight
                if measured is None:
                    unknown = "cash weight unknown in the latest snapshot"
        elif isinstance(rule, PositionCountRule):
            scope, unit, suffix = "portfolio", "count", "count"
            if unknown is None:
                measured = float(len(held))
        else:  # a rule type this module does not know: never guess
            out[rule.id] = RuleCompliance(
                status="unknown", reason="rule type not evaluated", **base
            )  # type: ignore[arg-type]
            continue
        if unknown is not None or measured is None:
            out[rule.id] = RuleCompliance(
                status="unknown",
                reason=unknown or "no measure",
                scope=scope,
                unit=unit,
                **base,  # type: ignore[arg-type]
            )
            continue
        hit_finding = next((f for f in findings if f.key == f"rule:{rule.id}:{suffix}"), None)
        if hit_finding is not None:
            status: RuleStatus = "breach"
            reason = hit_finding.detail
        elif not _holds(measured, rule.operator, limit):
            status, reason = "breach", f"{measured} does not satisfy {rule.operator} {limit}"
        elif _near(measured, rule.operator, limit):
            status, reason = "warn", f"holds, within {int(WARN_MARGIN * 100)} % of the limit"
        else:
            status, reason = "pass", "holds"
        out[rule.id] = RuleCompliance(
            status=status,
            measured=measured,
            reason=reason,
            scope=scope,
            unit=unit,
            **base,  # type: ignore[arg-type]
        )
    return out


async def load_compliance(
    pool: asyncpg.Pool, strategy: StrategyConfig | None, ticker: str
) -> dict[str, RuleCompliance]:
    """Compliance of the strategy's rules for `ticker` from the latest snapshot. Read-only; a
    failure to read the portfolio leaves every rule unknown with the reason."""
    if strategy is None:
        return {}
    from steward.portfolio.read import portfolio_overview  # local: keeps imports light

    try:
        overview = await portfolio_overview(pool)
    except Exception as exc:  # a missing portfolio must not fail the research run
        return rule_compliance(strategy, None, ticker, None) | {
            r.id: RuleCompliance(status="unknown", reason=f"no drift report: {exc}"[:160])
            for r in strategy.enabled_rules
        }
    snap = overview.freshness.snapshot_date if overview.freshness else None
    as_of = snap.isoformat() if isinstance(snap, dt.date) else (str(snap) if snap else None)
    return rule_compliance(strategy, overview.drift, ticker, as_of)
