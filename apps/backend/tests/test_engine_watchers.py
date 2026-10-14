import datetime as dt

from steward.engine import watchers


def test_review_is_near_only_in_the_last_days_of_the_month() -> None:
    assert watchers.review_is_near(dt.date(2026, 10, 30))
    assert watchers.review_is_near(dt.date(2026, 10, 31))
    assert not watchers.review_is_near(dt.date(2026, 10, 14))
    assert not watchers.review_is_near(dt.date(2026, 11, 1))


def test_month_end_handles_february() -> None:
    assert watchers.month_end(dt.date(2028, 2, 3)) == dt.date(2028, 2, 29)
