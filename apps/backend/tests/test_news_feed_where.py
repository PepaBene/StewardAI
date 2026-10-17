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


def test_min_severity_keeps_stories_not_classified_yet() -> None:
    sql, args = feed_where(FeedFilters(min_severity=1))
    assert "(c.severity is null or c.severity >= $1)" in sql
    assert args == [1]


def test_event_types_match_the_story_type_or_unclassified() -> None:
    sql, args = feed_where(FeedFilters(event_types=["m_and_a", "unclassified"]))
    assert "coalesce(c.event_type, 'unclassified') = any($1::text[])" in sql
    assert args == [["m_and_a", "unclassified"]]


def test_no_filters_no_where() -> None:
    assert feed_where(FeedFilters()) == ("", [])
