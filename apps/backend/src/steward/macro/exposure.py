"""Exposure calculations never convert unknown national data into issuer facts."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True, slots=True)
class Exposure:
    segment: str
    weight: Decimal
    basis: Literal["reported", "estimated", "user_assigned"]
    geography: str | None = None
    hs_code: str | None = None


def exposure_summary(rows: list[Exposure]) -> dict[str, object]:
    total = sum((row.weight for row in rows), Decimal(0))
    if total > 1:
        raise ValueError("mapped exposure weights exceed 100%")
    return {
        "mapped": total,
        "unknown": Decimal(1) - total,
        "by_basis": {
            basis: sum((r.weight for r in rows if r.basis == basis), Decimal(0))
            for basis in ("reported", "estimated", "user_assigned")
        },
    }


def weighted_effect(rows: list[Exposure], changes: dict[str, Decimal]) -> Decimal | None:
    effects = [
        row.weight * changes[key]
        for row in rows
        if (key := (row.geography or row.hs_code or "")) in changes
    ]
    return sum(effects, Decimal(0)) if effects else None
