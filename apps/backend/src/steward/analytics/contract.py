"""Calculation contract shared by every scored or aggregated figure (plan M0.3).

Every result carries `value`, `unit`, `as_of`, `known_at`, `method_version`, `sources`,
`coverage` and `warnings`. `None` means unavailable: it is never replaced by zero or a guess.
Calculators work in full `Decimal` precision and round once, at the edge, with `rounded`
(one policy for the whole app: banker's rounding, places by unit).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, is_dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Status(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    PAID_ONLY = "paid_only"
    DISPUTED = "disputed"


# Decimal places a published figure is rounded to, by unit. Rounding happens once, on output.
PLACES: dict[str, int] = {
    "fraction": 8,  # 0.1234 = 12.34 %
    "ratio": 6,
    "multiple": 4,
    "money": 2,
    "per_share": 4,
    "percentage_points": 4,
    "days": 0,
    "count": 0,
}
ROUNDING = ROUND_HALF_EVEN


def rounded(value: Decimal | None, unit: str) -> Decimal | None:
    """The single rounding policy. An unknown unit is a programming error, not a default."""
    if value is None:
        return None
    return value.quantize(Decimal(1).scaleb(-PLACES[unit]), rounding=ROUNDING)


class SourceRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: str = Field(description="e.g. filing, price_bar, etf_snapshot, owner_input")
    ref: str = Field(description="Identifier a reader can chase: accession, symbol, snapshot id.")
    url: str | None = None


class Coverage(BaseModel):
    """`numerator` of `denominator` inputs were usable. The denominator is always stated."""

    model_config = ConfigDict(frozen=True)
    numerator: Decimal
    denominator: Decimal
    basis: str = Field(default="count", description="count, weight or market_value")

    @property
    def fraction(self) -> Decimal | None:
        return None if self.denominator == 0 else self.numerator / self.denominator


class Exclusion(BaseModel):
    subject: str
    reason: str


class Explain(BaseModel):
    exclusions: list[Exclusion] = Field(default_factory=list)
    formula_version: str
    formula: str = ""
    citations: list[SourceRef] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class AnalyticsResult(BaseModel):
    value: Decimal | None
    unit: str
    as_of: dt.date | None = None
    known_at: dt.datetime | None = None
    method_version: str
    status: Status
    sources: list[SourceRef] = Field(default_factory=list)
    coverage: Coverage | None = None
    warnings: list[str] = Field(default_factory=list)
    input_hash: str | None = Field(
        default=None, description="Immutable hash of the inputs the figure was computed from."
    )
    explain: Explain | None = None


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical(asdict(value))
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    return value


def input_hash(inputs: Any) -> str:
    """Stable SHA-256 of the inputs, so a saved result names exactly what it was built from."""
    text = json.dumps(_canonical(inputs), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def unavailable(
    unit: str,
    method_version: str,
    reason: str,
    *,
    as_of: dt.date | None = None,
    inputs: Any = None,
    status: Status = Status.UNAVAILABLE,
) -> AnalyticsResult:
    """A figure that could not be produced, with the reason; never a zero."""
    return AnalyticsResult(
        value=None,
        unit=unit,
        as_of=as_of,
        method_version=method_version,
        status=status,
        warnings=[reason],
        input_hash=input_hash(inputs) if inputs is not None else None,
    )


def result(
    value: Decimal | None,
    unit: str,
    method_version: str,
    *,
    as_of: dt.date | None = None,
    known_at: dt.datetime | None = None,
    sources: list[SourceRef] | None = None,
    coverage: Coverage | None = None,
    warnings: list[str] | None = None,
    inputs: Any = None,
    status: Status | None = None,
    explain: Explain | None = None,
) -> AnalyticsResult:
    """Round once and set the status: complete only when the coverage is full and nothing warns."""
    notes = list(warnings or [])
    if status is None:
        if value is None:
            status = Status.UNAVAILABLE
        elif (coverage is not None and coverage.numerator < coverage.denominator) or notes:
            status = Status.PARTIAL
        else:
            status = Status.COMPLETE
    return AnalyticsResult(
        value=rounded(value, unit),
        unit=unit,
        as_of=as_of,
        known_at=known_at,
        method_version=method_version,
        status=status,
        sources=sources or [],
        coverage=coverage,
        warnings=notes,
        input_hash=input_hash(inputs) if inputs is not None else None,
        explain=explain,
    )
