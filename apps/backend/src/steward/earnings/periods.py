"""Fiscal periods and their calendar months.

A fiscal year is labelled by the calendar year it ends in (Apple's FY2025 ends in
September 2025, Microsoft's FY2026 in June 2026, NVIDIA's FY2026 in January 2026), which is
how those companies label them. With the month the fiscal year ends in (from the holding
profile), every fiscal quarter maps to the calendar month it ends in, so "Q3" is never
compared across different companies' calendars.
"""

import calendar
import datetime as dt
from typing import Literal

FiscalPeriod = Literal["Q1", "Q2", "Q3", "Q4", "H1", "H2", "FY"]
QUARTERS: tuple[FiscalPeriod, ...] = ("Q1", "Q2", "Q3", "Q4")

# The quarter each period ends with.
_END_QUARTER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "H1": 2, "H2": 4, "FY": 4}


def period_label(fiscal_year: int, fiscal_period: str) -> str:
    return f"FY{fiscal_year}" if fiscal_period == "FY" else f"FY{fiscal_year} {fiscal_period}"


def calendar_period_end(fiscal_year: int, fiscal_period: str, fye_month: int) -> dt.date:
    """Last day of the calendar month the fiscal period ends in."""
    quarters_before_year_end = 4 - _END_QUARTER[fiscal_period]
    index = fiscal_year * 12 + (fye_month - 1) - 3 * quarters_before_year_end
    year, month = divmod(index, 12)
    month += 1
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def label_year_for_end(
    fiscal_year: int,
    fiscal_period: str,
    fye_month: int,
    stated_end: dt.date | None,
    tolerance_days: int,
) -> int:
    """The fiscal year in this module's convention (the year it ends in) for a period the
    document labels `fiscal_year`. Companies that name the year by its start (Daiichi Sankyo's
    FY2026 runs April 2026 to March 2027) print an end date a year off the expected one; the
    printed date is trusted when it fits exactly one neighbouring year, else the label stays."""
    if stated_end is None:
        return fiscal_year

    def near(year: int) -> bool:
        end = calendar_period_end(year, fiscal_period, fye_month)
        start = end.replace(day=1)
        return min(abs((stated_end - end).days), abs((stated_end - start).days)) <= tolerance_days

    if near(fiscal_year):
        return fiscal_year
    fits = [y for y in (fiscal_year + 1, fiscal_year - 1) if near(y)]
    return fits[0] if len(fits) == 1 else fiscal_year


def previous_quarter(fiscal_year: int, fiscal_period: str) -> tuple[int, str]:
    q = _END_QUARTER[fiscal_period]
    return (fiscal_year - 1, "Q4") if q == 1 else (fiscal_year, f"Q{q - 1}")


def quarter_index(fiscal_year: int, fiscal_period: str) -> int:
    """Consecutive quarters have consecutive indexes."""
    return fiscal_year * 4 + _END_QUARTER[fiscal_period] - 1


def sort_key(fiscal_year: int, fiscal_period: str) -> tuple[int, int, int]:
    # Within a fiscal year: quarters, then halves, then the full year at its end.
    kind = 0 if fiscal_period in QUARTERS else 1 if fiscal_period in ("H1", "H2") else 2
    return (fiscal_year, _END_QUARTER[fiscal_period], kind)


def likely_reported_period(report_date: dt.date, fye_month: int) -> tuple[int, str]:
    """The fiscal quarter a release on `report_date` most likely covers: the latest quarter
    that ended at least a week before it. Used only as a hint in the extraction prompt."""
    cutoff = report_date - dt.timedelta(days=7)
    fiscal_year = cutoff.year + 1
    while True:
        for q in reversed(QUARTERS):
            if calendar_period_end(fiscal_year, q, fye_month) <= cutoff:
                return fiscal_year, q
        fiscal_year -= 1
