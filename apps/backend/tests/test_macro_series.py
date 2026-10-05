import datetime as dt
from decimal import Decimal

from steward.macro.series import assemble_series

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
T1 = dt.datetime(2026, 2, 1, tzinfo=dt.UTC)


def row(period: dt.date, first: str, latest: str, vintages: int, source: str = "eurostat") -> dict:
    return {
        "source": source,
        "series_id": "cpi",
        "geography": "DE",
        "period": period,
        "unit": "index",
        "frequency": "M",
        "seasonal_adjustment": None,
        "source_url": None,
        "release_at": None,
        "first_value": Decimal(first),
        "first_known_at": T0,
        "latest_value": Decimal(latest),
        "latest_known_at": T1,
        "vintages": vintages,
    }


def test_revision_flagged_only_when_value_differs() -> None:
    rows = [row(dt.date(2025, 11, 1), "2.0", "2.0", 2), row(dt.date(2025, 12, 1), "2.0", "2.4", 2)]
    (series,) = assemble_series(rows, periods=10, meta={("eurostat", "cpi"): {"label": "CPI"}})
    assert [p.revised for p in series.points] == [False, True]
    assert series.revised_periods == 1
    assert series.label == "CPI"
    assert series.first_basis == "first_recorded"


def test_alfred_is_provider_vintage_and_periods_are_trimmed() -> None:
    rows = [row(dt.date(2025, m, 1), "1", "1", 1, "alfred") for m in range(1, 7)]
    (series,) = assemble_series(rows, periods=3, meta={})
    assert series.first_basis == "provider_vintage"
    assert [p.period.month for p in series.points] == [4, 5, 6]
