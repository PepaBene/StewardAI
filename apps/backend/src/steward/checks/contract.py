"""Four-state check contract (insight plan I3.1, engineering rule 3).

A check is `pass`, `fail`, `na` (does not apply: a dividend check for a non-payer) or `unknown`
(an input is missing). An axis score counts passes over the *applicable* checks only: `na` and
`unknown` are out of the denominator, and `unknown` lowers the shown coverage, never the score.
An axis with no applicable check has no score (an open gap on the mark), never a zero.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

State = Literal["pass", "fail", "na", "unknown"]
Axis = Literal["value", "future", "past", "health", "dividend", "risk"]
SCORED_AXES: tuple[Axis, ...] = ("value", "future", "past", "health", "dividend")
AXIS_LABELS: dict[str, str] = {
    "value": "Value",
    "future": "Future",
    "past": "Past",
    "health": "Health",
    "dividend": "Dividend",
    "risk": "Risks",
}
AXIS_MAX = 6


class Check(BaseModel):
    id: str
    axis: Axis
    label: str
    state: State
    value: Decimal | None = None
    threshold: Decimal | None = None
    unit: str | None = None
    basis: str | None = Field(default=None, description="What the value is compared with.")
    sentence: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    section_ref: str | None = Field(
        default=None, description="Where in the app the inputs are shown (a path)."
    )
    method_version: str
    informational: bool = Field(
        default=False, description="Shown in the list but never counted (earnings date)."
    )


class AxisScore(BaseModel):
    axis: Axis
    label: str
    passes: int
    applicable: int
    unknown: int
    na: int
    max: int = AXIS_MAX
    score: Decimal | None = Field(
        description="Passes over applicable checks scaled to 0-6; null when none applies."
    )
    coverage: Decimal | None = Field(
        description="Checks with a result over checks that apply (unknown lowers it)."
    )


def axis_score(axis: Axis, checks: list[Check]) -> AxisScore:
    own = [c for c in checks if c.axis == axis and not c.informational]
    passes = sum(1 for c in own if c.state == "pass")
    fails = sum(1 for c in own if c.state == "fail")
    unknown = sum(1 for c in own if c.state == "unknown")
    na = sum(1 for c in own if c.state == "na")
    applicable = passes + fails
    relevant = applicable + unknown
    return AxisScore(
        axis=axis,
        label=AXIS_LABELS[axis],
        passes=passes,
        applicable=applicable,
        unknown=unknown,
        na=na,
        max=len(own) or AXIS_MAX,
        score=(Decimal(AXIS_MAX) * passes / applicable).quantize(Decimal("0.01"))
        if applicable
        else None,
        coverage=(Decimal(applicable) / relevant).quantize(Decimal("0.0001")) if relevant else None,
    )


def score_text(s: AxisScore) -> str:
    """'4 of 5 applicable' — the denominator is always shown."""
    if s.applicable == 0:
        return "not applicable" if s.unknown == 0 else "no data"
    return f"{s.passes} of {s.applicable} applicable"
