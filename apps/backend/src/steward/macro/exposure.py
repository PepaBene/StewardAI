"""Exposure calculations never convert unknown national data into issuer facts.

The 100% cap is per dimension (geography, HS product, geography+HS cells), matching
`public.check_exposure_total`: a revenue-by-region cut and a revenue-by-product cut describe
the same revenue twice and are never added together.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Dimension = Literal["geography", "hs_product", "geography_hs_product"]
DIMENSIONS: tuple[Dimension, ...] = ("geography", "hs_product", "geography_hs_product")


@dataclass(frozen=True, slots=True)
class Exposure:
    segment: str
    weight: Decimal
    basis: Literal["reported", "estimated", "user_assigned"]
    geography: str | None = None
    hs_code: str | None = None

    @property
    def dimension(self) -> Dimension:
        return dimension_of(self.geography, self.hs_code)


def dimension_of(geography: str | None, hs_code: str | None) -> Dimension:
    if geography is not None and hs_code is None:
        return "geography"
    if hs_code is not None and geography is None:
        return "hs_product"
    return "geography_hs_product"


def exposure_summary(rows: list[Exposure]) -> dict[str, object]:
    """Mapped and unknown share per dimension. `mapped` / `unknown` describe the best-covered
    dimension (the largest mapped share): that is how much of the issuer is attributed at all."""
    per_dimension: dict[str, dict[str, Decimal]] = {}
    for dimension in DIMENSIONS:
        total = sum((r.weight for r in rows if r.dimension == dimension), Decimal(0))
        if total > 1:
            raise ValueError(f"mapped {dimension} exposure weights exceed 100%")
        if any(r.dimension == dimension for r in rows):
            per_dimension[dimension] = {"mapped": total, "unknown": Decimal(1) - total}
    mapped = max((v["mapped"] for v in per_dimension.values()), default=Decimal(0))
    return {
        "mapped": mapped,
        "unknown": Decimal(1) - mapped,
        "by_dimension": per_dimension,
        "by_basis": {
            basis: sum((r.weight for r in rows if r.basis == basis), Decimal(0))
            for basis in ("reported", "estimated", "user_assigned")
        },
    }


def weighted_effect(
    rows: list[Exposure], changes: dict[str, Decimal], dimension: Dimension = "geography"
) -> Decimal | None:
    """Sum of weight x change over the rows of one dimension whose key (geography, or the HS
    code for the product dimension) has a change. None when no mapped key has one - an
    unmapped share contributes nothing and is never assumed to move."""
    effects = []
    for row in rows:
        if row.dimension != dimension:
            continue
        key = row.geography if dimension == "geography" else row.hs_code
        if key is not None and key in changes:
            effects.append(row.weight * changes[key])
    return sum(effects, Decimal(0)) if effects else None
