"""What the monthly review shows from Phase 4: the satellites-versus-core gap (M4.9) and the
holding-period dates (M4.8). Kept apart from `review.monthly` so the calculators never import the
review module. Facts and the owner's own thresholds, never a trade or a tax figure."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

import asyncpg

from steward.performance import service
from steward.performance.settings import PerformanceSettings, load_latest_settings

Status = Literal["pass", "breach", "unknown"]
SHIFT_POINTS = 10


@dataclass(frozen=True)
class Finding:
    id: str
    description: str
    status: Status
    detail: str


@dataclass(frozen=True)
class HoldingPeriodRow:
    ticker: str
    bought_on: dt.date
    passes_on: dt.date
    months_remaining: float
    passed: bool
    quantity: float
    forfeits_exemption_warning: bool


async def performance_review(
    pool: asyncpg.Pool, settings: PerformanceSettings | None = None
) -> tuple[list[Finding], list[HoldingPeriodRow]]:
    """Compliance rows and open-lot dates for the monthly review."""
    settings = settings or await load_latest_settings(pool)
    findings: list[Finding] = []
    sleeves = await service.sleeves_report(pool, settings)
    description = (
        f"Stocks within {settings.sleeve_gap_points} points a year of the core ETFs "
        f"over {settings.sleeve_lookback_years} years"
    )
    if sleeves.core is None or sleeves.stocks is None:
        findings.append(Finding("sleeves:gap", description, "unknown", sleeves.message))
    elif sleeves.review_item:
        findings.append(
            Finding(
                "sleeves:gap",
                description,
                "breach",
                f"{sleeves.message} Consider moving {SHIFT_POINTS} percentage points of the "
                "portfolio from stocks to the core ETFs, and decide whether to act.",
            )
        )
    elif sleeves.too_early:
        findings.append(Finding("sleeves:gap", description, "unknown", sleeves.message))
    else:
        findings.append(Finding("sleeves:gap", description, "pass", sleeves.message))

    lots = await service.lots_report(pool, settings)
    rows = [
        HoldingPeriodRow(
            ticker=lot.ticker,
            bought_on=lot.bought_on,
            passes_on=lot.passes_on,
            months_remaining=lot.months_remaining,
            passed=lot.passed,
            quantity=lot.quantity,
            forfeits_exemption_warning=lot.forfeits_exemption_warning,
        )
        for lot in lots.lots
    ]
    for lot in lots.lots:
        if lot.forfeits_exemption_warning:
            findings.append(
                Finding(
                    f"time_test:{lot.ticker}:{lot.lot_id}",
                    f"{lot.ticker} lot bought {lot.bought_on} passes the "
                    f"{settings.time_test_years}-year test on {lot.passes_on}",
                    "breach",
                    f"sell target reached with {lot.months_remaining:.1f} months to go: selling "
                    "all of this lot now would give up the exemption",
                )
            )
    return findings, rows
