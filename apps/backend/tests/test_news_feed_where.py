"""The news feed's WHERE clause: "direct" narrows to items about a holding itself, with or
without a ticker."""

from steward.news.link import INDIRECT_VIA
from steward.news.read import FeedFilters, feed_where


def test_direct_without_a_ticker_keeps_items_about_any_holding_itself() -> None:
    sql, args = feed_where(FeedFilters(direct=True, severities=[1, 2, 3]))
    assert "i.relevant is not false" in sql
    assert "jsonb_array_elements(i.links)" in sql and "$1::text[]" in sql
    assert args == [sorted(INDIRECT_VIA), [1, 2, 3]]


def test_direct_with_a_ticker_matches_that_ticker() -> None:
    sql, args = feed_where(FeedFilters(ticker="AAPL_US_EQ", direct=True))
    assert "l->>'ticker' = $1" in sql
    assert args == ["AAPL_US_EQ", sorted(INDIRECT_VIA)]


def test_no_filters_no_where() -> None:
    assert feed_where(FeedFilters()) == ("", [])
