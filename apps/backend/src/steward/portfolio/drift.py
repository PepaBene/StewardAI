"""Allocation and drift against the strategy's targets.

Pure functions over one day's base-currency values and the strategy config: no database,
no I/O. Targets, bands, hard limits, categories and the weight basis all come from the
config, so changing any of them changes the result with no code change.

Weights are fractions of the weight basis: positions + cash ('total') or positions only
('invested'). Drift is weight minus target, in the same fraction units.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from steward.config.strategy import StrategyConfig, TargetBand
from steward.fx.convert import normalise_currency

DriftStatus = Literal["in_band", "above_band", "below_band", "above_hard_limit", "no_target"]

UNCLASSIFIED = "Unclassified"

# Weights come from Decimal arithmetic, targets from config floats: ignore differences
# below this when comparing against band edges.
_EPSILON = 1e-9


@dataclass(frozen=True)
class HoldingValue:
    ticker: str
    instrument_currency: str
    value_base: Decimal


class PositionDrift(BaseModel):
    ticker: str
    weight: float
    target_weight: float | None
    band: float | None
    lower: float | None
    upper: float | None
    hard_limit: float | None
    drift: float | None
    status: DriftStatus
    held: bool


class BucketDrift(BaseModel):
    bucket: str
    value_base: float
    weight: float
    target_weight: float | None
    band: float | None
    lower: float | None
    upper: float | None
    drift: float | None
    status: DriftStatus


class DimensionAllocation(BaseModel):
    dimension: str
    buckets: list[BucketDrift]


class CashDrift(BaseModel):
    value_base: float
    weight: float | None
    target_weight: float | None
    band: float | None
    lower: float | None
    upper: float | None
    drift: float | None
    status: DriftStatus


class DriftReport(BaseModel):
    weight_basis: Literal["total", "invested"]
    basis_value_base: float
    positions: list[PositionDrift]
    categories: list[DimensionAllocation]
    cash: CashDrift
    out_of_band: int


def weight_denominator(
    invested_base: Decimal, cash_base: Decimal, basis: Literal["total", "invested"]
) -> Decimal:
    return invested_base + cash_base if basis == "total" else invested_base


def _band_status(
    weight: float, target: TargetBand | None, hard_limit: float | None = None
) -> tuple[DriftStatus, float | None, float | None, float | None]:
    """(status, lower, upper, drift) of a weight against an optional target band."""
    if target is None:
        return "no_target", None, None, None
    lower = max(0.0, target.target_weight - target.band)
    upper = target.target_weight + target.band
    drift = weight - target.target_weight
    if hard_limit is not None and weight > hard_limit + _EPSILON:
        return "above_hard_limit", lower, upper, drift
    if weight > upper + _EPSILON:
        return "above_band", lower, upper, drift
    if weight < lower - _EPSILON:
        return "below_band", lower, upper, drift
    return "in_band", lower, upper, drift


def bucket_for(strategy: StrategyConfig, holding: HoldingValue, dimension: str) -> str:
    assigned = strategy.classifications.get(holding.ticker, {}).get(dimension)
    if assigned:
        return assigned
    if dimension == "currency":
        # Derived from the instrument unless a classification overrides it (GBX -> GBP).
        return normalise_currency(holding.instrument_currency)[0]
    return UNCLASSIFIED


def compute_drift(
    holdings: list[HoldingValue], cash_base: Decimal, strategy: StrategyConfig
) -> DriftReport:
    invested = sum((h.value_base for h in holdings), Decimal(0))
    denominator = weight_denominator(invested, cash_base, strategy.weight_basis)

    def weight_of(value: Decimal) -> float:
        return float(value / denominator) if denominator > 0 else 0.0

    targets = {t.ticker: t for t in strategy.target_positions}
    positions: list[PositionDrift] = []
    held = {h.ticker for h in holdings}
    for h in holdings:
        target = targets.get(h.ticker)
        hard_limit = target.hard_limit if target else None
        weight = weight_of(h.value_base)
        status, lower, upper, drift = _band_status(weight, target, hard_limit)
        positions.append(
            PositionDrift(
                ticker=h.ticker,
                weight=weight,
                target_weight=target.target_weight if target else None,
                band=target.band if target else None,
                lower=lower,
                upper=upper,
                hard_limit=hard_limit,
                drift=drift,
                status=status,
                held=True,
            )
        )
    # Targets for positions not held (yet): weight 0.
    for ticker, target in targets.items():
        if ticker in held:
            continue
        status, lower, upper, drift = _band_status(0.0, target, target.hard_limit)
        positions.append(
            PositionDrift(
                ticker=ticker,
                weight=0.0,
                target_weight=target.target_weight,
                band=target.band,
                lower=lower,
                upper=upper,
                hard_limit=target.hard_limit,
                drift=drift,
                status=status,
                held=False,
            )
        )
    positions.sort(key=lambda p: (-p.weight, p.ticker))

    categories: list[DimensionAllocation] = []
    for dimension in strategy.categories:
        values: dict[str, Decimal] = defaultdict(Decimal)
        for h in holdings:
            values[bucket_for(strategy, h, dimension)] += h.value_base
        bucket_targets = {
            t.bucket: t for t in strategy.target_categories if t.dimension == dimension
        }
        for bucket in bucket_targets:
            values.setdefault(bucket, Decimal(0))
        buckets = []
        for bucket, value in values.items():
            weight = weight_of(value)
            bucket_target = bucket_targets.get(bucket)
            status, lower, upper, drift = _band_status(weight, bucket_target)
            buckets.append(
                BucketDrift(
                    bucket=bucket,
                    value_base=float(value),
                    weight=weight,
                    target_weight=bucket_target.target_weight if bucket_target else None,
                    band=bucket_target.band if bucket_target else None,
                    lower=lower,
                    upper=upper,
                    drift=drift,
                    status=status,
                )
            )
        buckets.sort(key=lambda b: (-b.weight, b.bucket))
        categories.append(DimensionAllocation(dimension=dimension, buckets=buckets))

    cash_weight: float | None
    if strategy.weight_basis == "total":
        cash_weight = weight_of(cash_base)
        cash_status, c_lower, c_upper, c_drift = _band_status(
            weight_of(cash_base), strategy.cash_target
        )
    else:
        # Cash is outside the basis: it has no weight to compare with a target.
        cash_weight, cash_status, c_lower, c_upper, c_drift = None, "no_target", None, None, None
    cash = CashDrift(
        value_base=float(cash_base),
        weight=cash_weight,
        target_weight=strategy.cash_target.target_weight if strategy.cash_target else None,
        band=strategy.cash_target.band if strategy.cash_target else None,
        lower=c_lower,
        upper=c_upper,
        drift=c_drift,
        status=cash_status,
    )

    flagged: set[DriftStatus] = {"above_band", "below_band", "above_hard_limit"}
    out_of_band = sum(p.status in flagged for p in positions)
    out_of_band += sum(b.status in flagged for c in categories for b in c.buckets)
    out_of_band += cash.status in flagged
    return DriftReport(
        weight_basis=strategy.weight_basis,
        basis_value_base=float(denominator),
        positions=positions,
        categories=categories,
        cash=cash,
        out_of_band=out_of_band,
    )
