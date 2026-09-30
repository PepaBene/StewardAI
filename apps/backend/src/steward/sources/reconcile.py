"""Cross-source comparisons that preserve alternatives and surface material conflicts."""

from dataclasses import dataclass
from decimal import Decimal

import asyncpg

from steward.sources.base import SourceObservation


@dataclass(frozen=True, slots=True)
class Reconciliation:
    preferred: SourceObservation
    alternatives: tuple[SourceObservation, ...]
    conflicts: tuple[SourceObservation, ...]


def reconcile(
    records: list[SourceObservation], priorities: dict[str, int], tolerance: Decimal
) -> Reconciliation:
    if not records:
        raise ValueError("at least one observation is required")
    comparable = {(r.metric, r.unit, r.currency, r.period_end) for r in records}
    if len(comparable) != 1:
        raise ValueError("period, metric, unit and currency must match before reconciliation")
    ordered = sorted(records, key=lambda r: priorities.get(r.source, 10_000))
    preferred = ordered[0]
    conflicts = tuple(
        row
        for row in ordered[1:]
        if row.value is not None
        and preferred.value is not None
        and abs(row.value * row.scale - preferred.value * preferred.scale) > tolerance
    )
    return Reconciliation(preferred, tuple(ordered[1:]), conflicts)


async def record_conflicts(
    pool: asyncpg.Pool,
    preferred_observation_id: int,
    alternatives: list[tuple[int, Decimal]],
    *,
    metric: str,
    preferred_value: Decimal,
    tolerance: Decimal,
) -> int:
    """Persist material disagreements so publication never silently overrides them."""
    count = 0
    for observation_id, value in alternatives:
        difference = abs(value - preferred_value)
        if difference <= tolerance:
            continue
        result = await pool.execute(
            """insert into public.source_conflict
               (metric,preferred_observation_id,alternative_observation_id,tolerance,difference)
               values($1,$2,$3,$4,$5) on conflict do nothing""",
            metric,
            preferred_observation_id,
            observation_id,
            tolerance,
            difference,
        )
        count += result.endswith("1")
    return count
