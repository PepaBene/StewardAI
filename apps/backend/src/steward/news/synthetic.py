"""Events raised by code from structured data (reference-data lifecycle changes, macro
releases), stored as tier-1 news items so they flow through the same assessment, routing,
digests and feed as ingested news.

Each event's URL points at its source record and is unique, so a replayed change never
raises a second item. Its story starts `pending`; the next news run assesses it (tier 1:
confirmed) and routes it by the event rules and notification rules.
"""

import datetime as dt
import json
from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class SyntheticSource:
    id: str
    name: str
    type: str  # reference, macro


@dataclass(frozen=True)
class SyntheticEvent:
    ticker: str | None
    event_type: str
    headline: str
    summary: str
    url: str
    event_date: dt.date | None
    outlet: str
    outlet_name: str


async def ensure_source(pool: asyncpg.Pool | asyncpg.Connection, source: SyntheticSource) -> None:
    await pool.execute(
        "insert into public.source (id, name, tier, type, enabled) values ($1, $2, 1, $3, true)"
        " on conflict (id) do nothing",
        source.id,
        source.name,
        source.type,
    )


async def raise_events(
    pool: asyncpg.Pool,
    source: SyntheticSource,
    events: list[SyntheticEvent],
    now: dt.datetime,
    job_id: int | None,
) -> list[int]:
    """Store each event once (by URL). Returns the new news item ids."""
    created: list[int] = []
    if not events:
        return created
    await ensure_source(pool, source)
    for event in events:
        tickers = [event.ticker] if event.ticker else []
        links = (
            [{"ticker": event.ticker, "via": "source", "term": event.ticker}]
            if event.ticker
            else []
        )
        async with pool.acquire() as conn, conn.transaction():
            if await conn.fetchval("select id from public.news_item where url = $1", event.url):
                continue
            cluster = await conn.fetchval(
                "insert into public.story_cluster (ticker, event_type, headline, headline_key,"
                " best_tier, first_seen, last_seen, event_date) values ($1, $2, $3, $4, 1, $5,"
                " $5, $6) returning id",
                event.ticker,
                event.event_type,
                event.headline,
                event.url,
                now,
                event.event_date,
            )
            item = await conn.fetchval(
                "insert into public.news_item (url, source_id, outlet, outlet_name, tier,"
                " published_at, first_seen_at, headline, summary, language, cluster_id, tickers,"
                " links, event_type, affected_ticker, rumour, relevant, event_date,"
                " classified_at, classified_by, job_id) values ($1, $2, $3, $4, 1, $5, $5, $6,"
                " $7, 'en', $8, $9, $10::jsonb, $11, $12, false, true, $13, $5, $14, $15)"
                " returning id",
                event.url,
                source.id,
                event.outlet,
                event.outlet_name,
                now,
                event.headline,
                event.summary,
                cluster,
                tickers,
                json.dumps(links),
                event.event_type,
                event.ticker,
                event.event_date,
                f"rules:{source.type}",
                job_id,
            )
            created.append(item)
    return created
