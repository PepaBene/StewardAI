"""What selling a number of units would do to the open tax lots (plan M6.4 and M6.7, principle 10b).

Pure. Open lots are consumed oldest first (the default FIFO method); the result lists each lot
touched, the realised gain, and the part of the gross proceeds from lots that have not yet passed
the holding-period test (the part that counts toward the annual exemption). These are
holding-period facts and owner-set thresholds. They are not a tax liability and not advice.
Lots whose method is average cost are still consumed oldest first for this estimate, and the
result says so.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, Field

from steward.performance.lots import MONTH_DAYS, time_test_date

ZERO = Decimal(0)


@dataclass(frozen=True)
class OpenLot:
    lot_id: str
    bought_on: dt.date
    quantity: Decimal
    unit_cost: Decimal  # account currency per unit, fees included


class LotUse(BaseModel):
    lot_id: str
    bought_on: dt.date
    quantity: float
    cost: float
    proceeds: float
    gain: float
    passes_on: dt.date
    passed: bool
    months_to_test: float = Field(description="0 once the lot has passed the test.")
    near_test: bool = Field(
        description="Not passed, and passes within the warning window: waiting may keep the "
        "exemption."
    )


class SaleImpact(BaseModel):
    ticker: str
    units: float
    units_matched: float
    units_unmatched: float
    gross_proceeds: float = Field(description="Account currency, at the price given.")
    cost: float
    gain: float
    gross_within_time_test: float
    lots: list[LotUse]
    near_test_lots: int
    earliest_pass: dt.date | None = Field(
        description="When the first lot that has not passed yet will pass; null if none is waiting."
    )
    method_note: str


def consume(
    ticker: str,
    lots: list[OpenLot],
    units: Decimal,
    unit_price: Decimal,
    as_of: dt.date,
    *,
    years: int = 3,
    warn_months: Decimal = Decimal(6),
    average_cost: bool = False,
) -> SaleImpact:
    """Sell `units` at `unit_price` (account currency) from the open lots, oldest first."""
    remaining = units
    uses: list[LotUse] = []
    gross = cost = within = ZERO
    for lot in sorted(lots, key=lambda x: (x.bought_on, x.lot_id)):
        if remaining <= 0:
            break
        take = min(remaining, lot.quantity)
        if take <= 0:
            continue
        remaining -= take
        proceeds = take * unit_price
        lot_cost = take * lot.unit_cost
        passes = time_test_date(lot.bought_on, years)
        days = (passes - as_of).days
        passed = days <= 0
        months = ZERO if passed else (Decimal(days) / MONTH_DAYS).quantize(Decimal("0.1"))
        gross += proceeds
        cost += lot_cost
        if not passed:
            within += proceeds
        uses.append(
            LotUse(
                lot_id=lot.lot_id,
                bought_on=lot.bought_on,
                quantity=float(take),
                cost=float(lot_cost),
                proceeds=float(proceeds),
                gain=float(proceeds - lot_cost),
                passes_on=passes,
                passed=passed,
                months_to_test=float(months),
                near_test=(not passed) and months <= warn_months,
            )
        )
    waiting = [u.passes_on for u in uses if not u.passed]
    return SaleImpact(
        ticker=ticker,
        units=float(units),
        units_matched=float(units - remaining),
        units_unmatched=float(remaining),
        gross_proceeds=float(gross),
        cost=float(cost),
        gain=float(gross - cost),
        gross_within_time_test=float(within),
        lots=uses,
        near_test_lots=sum(u.near_test for u in uses),
        earliest_pass=min(waiting) if waiting else None,
        method_note=(
            "Lots are consumed oldest first. Your cost method is average cost, so the broker's "
            "own basis can differ."
            if average_cost
            else "Lots are consumed oldest first (FIFO)."
        ),
    )
