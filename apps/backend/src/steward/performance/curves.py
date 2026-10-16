"""Two chart-ready views over figures the app already computes (no new stored data):

- the underwater curve of the flow-adjusted portfolio index (`portfolio_history`), with the
  maximum drawdown, its peak, trough and recovery dates;
- the returns waterfall of the attribution bridge: start value, net deposits, price, FX, income,
  costs, anything the attribution could not place, and end value.

A part that cannot be computed is null with a reason, never zero.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from steward.performance.reports import AttributionResponse
from steward.portfolio.read import PortfolioHistory


class DrawdownPoint(BaseModel):
    date: dt.date
    drawdown: float = Field(description="Index / running peak - 1; 0 at a new high, else negative.")


class DrawdownResponse(BaseModel):
    as_of: dt.date | None
    base_currency: str | None
    days: int
    points: list[DrawdownPoint]
    max_drawdown: float | None = Field(description="The deepest drawdown as a negative fraction.")
    peak_date: dt.date | None = Field(description="High that preceded the deepest drawdown.")
    trough_date: dt.date | None
    recovery_date: dt.date | None = Field(
        description="First day the index regained the peak; null while still under water."
    )
    current_drawdown: float | None
    reasons: list[str] = Field(default_factory=list)


def drawdown_points(
    series: Sequence[tuple[dt.date, float]],
) -> tuple[list[DrawdownPoint], float | None, dt.date | None, dt.date | None, dt.date | None]:
    """(underwater points, max drawdown, peak date, trough date, recovery date)."""
    ordered = sorted(x for x in series if x[1] > 0)
    peak = 0.0
    peak_date: dt.date | None = None
    out: list[DrawdownPoint] = []
    worst = 0.0
    worst_peak: dt.date | None = None
    worst_peak_value = 0.0
    trough: dt.date | None = None
    for day, value in ordered:
        if value >= peak:
            peak, peak_date = value, day
        dd = value / peak - 1
        out.append(DrawdownPoint(date=day, drawdown=round(dd, 6)))
        if dd < worst:
            worst, worst_peak, worst_peak_value, trough = dd, peak_date, peak, day
    if trough is None:
        return out, (0.0 if out else None), None, None, None
    recovery = next((d for d, v in ordered if d > trough and v >= worst_peak_value), None)
    return out, round(worst, 6), worst_peak, trough, recovery


def drawdown_response(history: PortfolioHistory, days: int) -> DrawdownResponse:
    pts = [(p.date, float(p.close)) for p in history.points]
    if len(pts) < 2:
        return DrawdownResponse(
            as_of=history.as_of, base_currency=history.base_currency, days=days, points=[],
            max_drawdown=None, peak_date=None, trough_date=None, recovery_date=None,
            current_drawdown=None,
            reasons=[history.gap or "fewer than two index points in this window"],
        )  # fmt: skip
    points, worst, peak, trough, recovery = drawdown_points(pts)
    return DrawdownResponse(
        as_of=history.as_of, base_currency=history.base_currency, days=days, points=points,
        max_drawdown=worst, peak_date=peak, trough_date=trough, recovery_date=recovery,
        current_drawdown=points[-1].drawdown if points else None,
    )  # fmt: skip


WaterfallKey = Literal[
    "start", "net_deposits", "price", "fx", "income", "costs", "unattributed", "end"
]


class WaterfallStep(BaseModel):
    key: WaterfallKey
    label: str
    value: float | None = Field(description="Signed amount in the base currency; null if unknown.")
    kind: Literal["total", "delta"]
    reason: str | None = Field(default=None, description="Why the value is null.")


class WaterfallResponse(BaseModel):
    status: Literal["complete", "partial", "unavailable"]
    start: dt.date | None
    end: dt.date | None
    days: int
    base_currency: str | None
    steps: list[WaterfallStep]
    reasons: list[str] = Field(default_factory=list)


def waterfall_response(att: AttributionResponse) -> WaterfallResponse:
    """Re-cut the attribution bridge: end = start + deposits + price + FX + income - costs + rest."""
    b = att.bridge
    labels: list[tuple[WaterfallKey, str, Literal["total", "delta"]]] = [
        ("start", "Start value", "total"),
        ("net_deposits", "Net deposits", "delta"),
        ("price", "Price return", "delta"),
        ("fx", "FX effect", "delta"),
        ("income", "Income", "delta"),
        ("costs", "Costs", "delta"),
        ("unattributed", "Not attributed", "delta"),
        ("end", "End value", "total"),
    ]
    if b is None:
        why = att.gap or "the attribution bridge is unavailable"
        return WaterfallResponse(
            status="unavailable", start=att.start, end=att.end, days=att.days,
            base_currency=att.base_currency,
            steps=[WaterfallStep(key=k, label=lab, value=None, kind=kind, reason=why)
                   for k, lab, kind in labels],
            reasons=[why],
        )  # fmt: skip
    values: dict[str, float | None] = {
        "start": b.start_value,
        "net_deposits": b.net_external_flows,
        "price": b.components.get("price"),
        "fx": b.components.get("fx"),
        "income": b.components.get("income"),
        "costs": b.components.get("fees"),
        "unattributed": b.residual,
        "end": b.end_value,
    }
    steps: list[WaterfallStep] = []
    reasons: list[str] = []
    for key, label, kind in labels:
        v = values[key]
        reason = None if v is not None else f"{label.lower()} is not in the attribution"
        if reason:
            reasons.append(reason)
        steps.append(WaterfallStep(key=key, label=label, value=v, kind=kind, reason=reason))
    if b.unattributed_tickers:
        reasons.append("not attributed: " + ", ".join(sorted(b.unattributed_tickers)))
    return WaterfallResponse(
        status="complete" if att.status == "complete" and not reasons else "partial",
        start=att.start, end=att.end, days=att.days, base_currency=att.base_currency,
        steps=steps, reasons=reasons,
    )  # fmt: skip
