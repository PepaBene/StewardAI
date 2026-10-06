import datetime as dt

from steward.exposures.filing_text import FilingText
from steward.graph.dependencies import (
    DependencyFinding,
    check_findings,
    fiscal_label,
    year_start,
)
from steward.macro.commodity_series import series_keys_for
from steward.macro.exposure_series import MAX_POINTS, thinned


def test_a_share_of_the_counterpartys_own_revenue_is_rejected() -> None:
    quote = "Sales to Acme Corporation accounted for 22% of its revenue in fiscal 2025."
    filing = FilingText(
        "AAA_US_EQ",
        "0000000000-26-000001",
        "10-K",
        "https://sec.example/a.htm",
        dt.date(2025, 12, 31),
        quote * 3,
    )
    f = DependencyFinding.model_validate(
        {
            "relationship": "customer",
            "counterparty": "Acme Corporation",
            "share": 0.22,
            "period": "FY2025",
            "quote": quote,
        }
    )
    assert check_findings([f], filing).kept == []


def test_fiscal_year_labels_and_start() -> None:
    assert fiscal_label("fiscal 2025", dt.date(2026, 1, 3)) == "FY2025"
    assert fiscal_label(None, dt.date(2025, 12, 31)) == "FY2025"
    assert year_start(dt.date(2025, 12, 31)) == dt.date(2025, 1, 1)
    assert year_start(dt.date(2024, 2, 29)) == dt.date(2023, 3, 1)


def test_a_long_series_is_thinned_to_the_newest_points() -> None:
    points = [{"date": i} for i in range(1000)]
    out = thinned(points)
    assert len(out) <= MAX_POINTS and out[-1] == points[-1]
    assert thinned(points[:10]) == points[:10]


def test_unrelated_oils_have_no_series() -> None:
    assert series_keys_for("palm oil") == [] and series_keys_for("olive oil") == []
