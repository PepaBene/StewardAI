# ruff: noqa: E501
"""Base-currency view of company figures (plan M2.4 and M2.14).

Filed statements stay in the issuer's own currency; this module produces a *separate* converted
view at stated reference rates and never alters the stored facts. The rate basis is chosen and
shown with the result:

* `period_end`: every figure at the reference rate on (or within `max_age` before) the period end;
* `average`: income and cash-flow lines at the mean of weekly reference rates across the period,
  balance-sheet lines at the period-end rate (the usual translation convention).

A period with no usable rate shows no value and says why, instead of borrowing a rate from far
away. Ratios and growth rates are unaffected by conversion and are not translated.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from steward.company.statements import Column, StatementTable
from steward.fx.history import FxHistory

RateBasis = Literal["period_end", "average"]
FLOW_STATEMENTS = ("income", "cash_flow")
SAMPLE_DAYS = 7
PERIOD_LENGTH = {"annual": 365, "quarter": 91, "ttm": 365}
BASIS_TEXT = {
    "period_end": "each period's closing reference rate",
    "average": "the average reference rate over the period for income and cash-flow lines and the closing rate for balance-sheet lines",
}


@dataclass
class Translator:
    target: str
    basis: RateBasis
    source: str  # "ecb" or "cnb"
    history: FxHistory

    def rate(self, native: str, column: Column, statement: str) -> Decimal | None:
        """Units of the target currency per unit of `native` for one column and statement."""
        end = column.end
        if self.basis == "period_end" or statement not in FLOW_STATEMENTS:
            return self.history.conversion(native, self.target, end)
        start = end - dt.timedelta(days=PERIOD_LENGTH.get(column.kind, 365))
        days = [start + dt.timedelta(days=i) for i in range(0, (end - start).days, SAMPLE_DAYS)]
        days.append(end)
        rates = [r for d in days if (r := self.history.conversion(native, self.target, d))]
        # Fewer than half the sample days with a rate is not an average of the period.
        if len(rates) * 2 < len(days):
            return None
        return sum(rates, Decimal(0)) / len(rates)


def translate(
    tables: list[StatementTable], columns: list[Column], native: str, translator: Translator
) -> list[str]:
    """Convert money and per-share cells in place; returns the warnings to show."""
    by_id = {c.id: c for c in columns}
    missing: set[str] = set()
    for table in tables:
        for line in table.lines:
            for col_id, cell in line.cells.items():
                if cell.unit not in ("money", "per_share"):
                    continue
                column = by_id.get(col_id)
                if cell.value is None or column is None:
                    continue
                rate = translator.rate(native, column, table.statement)
                if rate is None:
                    cell.value = None
                    cell.original_value = None
                    cell.reason = (
                        f"no {translator.source.upper()} {native}/{translator.target} rate near "
                        f"{column.end:%d %b %Y}"
                    )
                    missing.add(column.label)
                    continue
                cell.value = cell.value * rate
                if cell.original_value is not None:
                    cell.original_value = cell.original_value * rate
            kept = []
            for detail in line.dimensions:
                column = by_id.get(detail.column)
                rate = translator.rate(native, column, table.statement) if column else None
                if rate is not None:
                    detail.value = detail.value * rate
                    kept.append(detail)
            line.dimensions = (
                kept  # a detail with no rate is dropped, never left in the old currency
            )
    notes = [
        f"Converted from {native} to {translator.target} at {translator.source.upper()} reference "
        f"rates, using {BASIS_TEXT[translator.basis]}. The filed figures are in {native}; "
        "ratios and growth rates are not affected."
    ]
    if missing:
        notes.append(
            "No reference rate was available for: "
            + ", ".join(sorted(missing))
            + ". Those cells are empty."
        )
    return notes
