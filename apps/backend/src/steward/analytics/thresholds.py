"""Minimum coverage thresholds per feature (plan M0.5). One place, so a screen, a panel and a
report apply the same rule and say which one they applied."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Thresholds:
    # A full portfolio return needs every material holding and flow in the window.
    material_holding_weight: Decimal = Decimal("0.01")  # a position of >= 1 % is material
    max_missing_material_weight: Decimal = Decimal(0)
    # A peer median needs this many comparable peers on comparable fiscal periods.
    min_peers_for_median: int = 5
    peer_period_distance_days: int = 45
    peer_duration_tolerance_days: int = 10
    # Fund overlap reports both covered fractions; below this a covered fraction is flagged.
    min_fund_covered_fraction: Decimal = Decimal("0.9")
    fund_stale_after_days: int = 45
    # Risk statistics.
    min_obs_volatility: int = 60
    min_obs_drawdown: int = 20
    min_obs_beta: int = 60
    min_obs_var: int = 250
    # A screen metric is shown only when this share of the universe has it.
    min_metric_coverage_for_rank: Decimal = Decimal("0.5")
    min_universe_for_percentile: int = 20


THRESHOLDS = Thresholds()


def full_portfolio_return_allowed(missing_material_weight: Decimal) -> tuple[bool, str | None]:
    if missing_material_weight > THRESHOLDS.max_missing_material_weight:
        return False, (
            f"{missing_material_weight:.1%} of material portfolio value has a missing "
            "price, flow or corporate action"
        )
    return True, None


def peer_median_allowed(comparable_peers: int) -> tuple[bool, str | None]:
    if comparable_peers < THRESHOLDS.min_peers_for_median:
        return False, (
            f"{comparable_peers} comparable peers; a median needs {THRESHOLDS.min_peers_for_median}"
        )
    return True, None
