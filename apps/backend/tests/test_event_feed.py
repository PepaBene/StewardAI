import datetime as dt

from steward.events.feed import Story, build, state_of
from steward.xray.supply_chain import SupplyChain, SupplyEdge, SupplyNode

TODAY = dt.date(2026, 10, 8)


def _chain() -> SupplyChain:
    return SupplyChain.model_construct(
        nodes=[
            SupplyNode.model_construct(symbol="TSM", weight=0.0),
            SupplyNode.model_construct(symbol="AAPL", weight=0.05),
        ],
        edges=[
            SupplyEdge.model_construct(
                supplier="TSM",
                customer="AAPL",
                product="chips",
                customer_share=0.2,
                source_url="https://sec.gov/x",
                as_of=dt.date(2026, 2, 1),
                origin="filing",
            )
        ],
    )


def _story(tier: int, rumour: bool, ticker: str = "TSM_US_EQ") -> Story:
    return Story(
        id=1,
        headline="Fab outage",
        url="https://example.com",
        outlet="x",
        published_at=dt.datetime(2026, 10, 7, tzinfo=dt.UTC),
        event_type="product_business",
        ticker=ticker,
        tier=tier,
        rumour=rumour,
    )


def test_state_follows_tier_and_rumour() -> None:
    assert state_of(_story(1, False)) == "confirmed"
    assert state_of(_story(2, False)) == "reported"
    assert state_of(_story(1, True)) == "rumoured"


def test_confirmed_event_on_high_links_would_alert() -> None:
    feed = build([_story(1, False)], _chain(), {}, TODAY, TODAY - dt.timedelta(days=14))
    assert len(feed.items) == 1
    assert feed.items[0].direction == "downstream"
    assert feed.items[0].would_alert


def test_reported_event_never_would_alert() -> None:
    feed = build([_story(2, False)], _chain(), {}, TODAY, TODAY - dt.timedelta(days=14))
    assert feed.items and not feed.items[0].would_alert


def test_story_without_links_is_left_out() -> None:
    feed = build([_story(1, False, "XYZ_US_EQ")], _chain(), {}, TODAY, TODAY)
    assert feed.stories_read == 1 and feed.items == []
