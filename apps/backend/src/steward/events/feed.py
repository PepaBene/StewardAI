"""Event feed (M7.4): classified news about a company, followed along the supply-chain links.

Each stored news story that is relevant and tied to one company is run through `event_paths` in
both directions (a supplier problem flows to its customers, a customer problem to its suppliers).
Only stories that reach another company you hold are listed. The state of the event comes from
the story: a rumour is rumoured, a regulator or company source (tier 1) is confirmed, anything
else is reported. `would_alert` marks a story that meets the rule for a high-severity alert
(confirmed, every link high confidence, at least 2% of the portfolio); this module sends nothing.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

import asyncpg
from pydantic import BaseModel

from steward.events.paths import EventImpact, EventPath, EventState, event_paths
from steward.xray.supply_chain import SupplyChain, symbol_of

# Event types that can plausibly move another company; macro, insider filings and unclassified
# "other" stories are not followed along supply links.
FOLLOWED = (
    "product_business",
    "regulatory_legal",
    "capital_allocation",
    "m_and_a",
    "earnings_release",
    "sector_competitor",
)
DEFAULT_DAYS = 14
MAX_STORIES = 300


class Story(BaseModel):
    id: int
    headline: str
    url: str
    outlet: str | None
    published_at: dt.datetime
    event_type: str
    ticker: str
    tier: int
    rumour: bool


class FeedItem(BaseModel):
    story: Story
    state: EventState
    direction: Literal["downstream", "upstream"]
    impact: EventImpact
    top: EventPath
    would_alert: bool


class EventFeed(BaseModel):
    since: dt.date
    stories_read: int
    items: list[FeedItem]
    note: str


def state_of(story: Story) -> EventState:
    if story.rumour:
        return "rumoured"
    return "confirmed" if story.tier == 1 else "reported"


def build(
    stories: list[Story],
    chain: SupplyChain,
    indicators: dict[str, list[str]],
    today: dt.date,
    since: dt.date,
) -> EventFeed:
    items: list[FeedItem] = []
    for story in stories:
        state = state_of(story)
        for direction in ("downstream", "upstream"):
            impact = event_paths(
                chain, symbol_of(story.ticker), direction, state, indicators, today
            )
            if not impact.paths:
                continue
            top = impact.paths[0]
            items.append(
                FeedItem(
                    story=story,
                    state=state,
                    direction=direction,
                    impact=impact,
                    top=top,
                    would_alert=any(p.severity == "high" for p in impact.paths),
                )
            )
    items.sort(
        key=lambda i: (
            not i.would_alert,
            -i.impact.reached_weight,
            -i.story.published_at.timestamp(),
        )
    )
    note = (
        "Stories are classified automatically. Only stories about a company that has supply-chain "
        "links to something you hold are listed; nothing here sends an alert."
    )
    return EventFeed(since=since, stories_read=len(stories), items=items, note=note)


async def load_stories(pool: asyncpg.Pool, since: dt.date) -> list[Story]:
    rows = await pool.fetch(
        "select distinct on (coalesce(cluster_id::text, id::text)) id, headline, url,"
        " outlet_name as outlet, published_at, event_type, affected_ticker as ticker, tier, rumour"
        " from public.news_item"
        " where relevant and affected_ticker is not null and event_type = any($1::text[])"
        " and published_at >= $2"
        " order by coalesce(cluster_id::text, id::text), tier, published_at desc",
        list(FOLLOWED),
        dt.datetime.combine(since, dt.time.min, tzinfo=dt.UTC),
    )
    stories = [Story(**dict(r)) for r in rows]
    stories.sort(key=lambda s: s.published_at, reverse=True)
    return stories[:MAX_STORIES]
