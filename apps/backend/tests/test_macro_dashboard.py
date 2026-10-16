import datetime as dt
from decimal import Decimal
from typing import Any

from steward.macro.dashboard import build_cards, geography_of


def _rows(series_id: str, days: list[dt.date], values: list[float]) -> list[dict[str, Any]]:
    return [
        {
            "series_id": series_id,
            "source": "fred",
            "period": d,
            "value": Decimal(str(v)),
            "label": "US 10-year Treasury yield",
            "unit": "%",
            "url": None,
            "fetched_at": None,
        }
        for d, v in zip(days, values, strict=True)
    ]


def test_a_daily_series_card_has_changes_ranges_and_a_monthly_line() -> None:
    start = dt.date(2016, 1, 4)
    days = [start + dt.timedelta(days=i) for i in range(0, 3900)]
    values = [2 + (i % 400) / 100 for i in range(len(days))]
    (card,) = build_cards(_rows("us_10y", days, values))
    assert card.geography == "US"
    assert card.latest_period == days[-1] and card.previous_period == days[-2]
    assert (
        card.year_ago_period is not None
        and abs((card.latest_period - card.year_ago_period).days - 365) <= 1
    )
    assert [r.years for r in card.ranges] == [1, 5, 10]
    one = card.ranges[0]
    assert one.low <= card.latest_value <= one.high and 0 < one.percentile <= 1
    # Five years of daily readings thinned to month ends, ending on the latest reading.
    assert 55 <= len(card.spark) <= 62 and card.spark[-1].period == days[-1]


def test_a_short_history_gets_no_long_range_and_no_year_ago() -> None:
    days = [dt.date(2026, 4, 1), dt.date(2026, 7, 1), dt.date(2026, 10, 1)]
    (card,) = build_cards(_rows("gdp:DE", days, [0.1, 0.3, -0.2]))
    assert card.geography == "DE"
    assert card.ranges == [] and card.year_ago_value is None
    assert card.previous_value == Decimal("0.3")
    assert [p.value for p in card.spark] == [Decimal("0.1"), Decimal("0.3"), Decimal("-0.2")]


def test_geography_is_read_from_the_series_key() -> None:
    assert geography_of("commodity_brent") == "WLD"
    assert geography_of("jp_10y") == "JP"
    assert geography_of("euro_area_m3_growth") == "EA"
    assert geography_of("czeonia") == "CZ"
    assert geography_of("usd_jpy") == "FX"
    assert geography_of("fed_funds") == "US"
