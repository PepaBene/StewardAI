"""Period series: a share count is a period average, so it is never summed or differenced."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from itertools import count

from steward.company.series import Obs, build_series

D = Decimal
KNOWN = dt.datetime(2025, 3, 1, tzinfo=dt.UTC)
_ids = count(1)


def obs(key: str, unit: str, start: dt.date, end: dt.date, value: str) -> Obs:
    return Obs(
        key=key,
        concept=key,
        label=key,
        statement="income",
        start=start,
        end=end,
        value=D(value),
        unit=unit,
        currency="USD",
        known_at=KNOWN,
        accession="0000000000-25-000001",
        form="10-K",
        filing_url=None,
        source_url=None,
        reconciled=True,
        fact_id=next(_ids),
    )


def facts(key: str, unit: str, values: tuple[str, str, str, str, str]) -> list[Obs]:
    """FY2024 as filed, then three filed quarters of 2025 (a quarter newer than the year)."""
    fy, q1, q2, q3, ytd = values
    return [
        obs(key, unit, dt.date(2024, 1, 1), dt.date(2024, 12, 31), fy),
        obs(key, unit, dt.date(2025, 1, 1), dt.date(2025, 3, 31), q1),
        obs(key, unit, dt.date(2025, 4, 1), dt.date(2025, 6, 30), q2),
        obs(key, unit, dt.date(2025, 7, 1), dt.date(2025, 9, 30), q3),
        obs(key, unit, dt.date(2025, 1, 1), dt.date(2025, 9, 30), ytd),
    ]


def test_ttm_of_a_flow_still_sums_the_last_four_quarters() -> None:
    data = [
        *facts("revenue", "USD", ("400", "100", "110", "120", "330")),
        obs("revenue", "USD", dt.date(2024, 1, 1), dt.date(2024, 9, 30), "270"),
    ]
    s = build_series("revenue", data)
    # Q4 2024 = FY 400 - 9M 270 = 130; with 100 + 110 + 120 the trailing year is 460.
    assert s.ttm is not None and s.ttm.value == D("460")
    assert s.ttm.derived == "sum of the last four quarters"


def test_ttm_share_count_is_the_latest_quarter_not_a_sum() -> None:
    s = build_series("shares", facts("shares", "shares", ("1000", "1000", "1010", "1020", "1010")))
    assert s.ttm is not None and s.ttm_end == dt.date(2025, 9, 30)
    assert s.ttm.value == D("1020")  # not about 4 x 1020
    assert "not summed" in (s.ttm.derived or "")


def test_a_year_to_date_average_does_not_become_a_quarter() -> None:
    # Only the 9M year-to-date and the FY are filed for 2024: FY - 9M is not Q4's average.
    data = [
        obs("shares", "shares", dt.date(2024, 1, 1), dt.date(2024, 9, 30), "900"),
        obs("shares", "shares", dt.date(2024, 1, 1), dt.date(2024, 12, 31), "1000"),
        obs("shares", "shares", dt.date(2024, 1, 1), dt.date(2024, 6, 30), "880"),
    ]
    s = build_series("shares", data)
    assert s.quarter == {}
    flow = build_series(
        "revenue", [obs("revenue", "USD", o.start, o.end, str(o.value)) for o in data if o.start]
    )
    assert flow.quarter  # the same shape for a flow derives quarters
