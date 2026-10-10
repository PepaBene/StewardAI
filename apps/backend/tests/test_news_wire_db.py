"""The wire lane against a real Postgres: unmatched items are stored (never alerted), per-source
run accounting and the circuit breaker, and article text with paywall stubs and expiry."""

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest

from steward.config.news_sources import FetchPolicy, NewsSourcesConfig
from steward.news.fulltext import (
    Extracted,
    FulltextResult,
    purge_expired,
    run_fulltext,
    store_result,
)
from steward.research.polite import PoliteFetcher
from tests.test_news_pipeline import (
    NOON,
    WIRE_FEED,
    Clock,
    FakeChannel,
    FakeCorroborator,
    Feeds,
    first_run_feeds,
    ingest,
    make_deps,
    rss,
    setup_config,
)

pytestmark = pytest.mark.db

BODY = " ".join(f"Sentence {i} reports the quarterly results in detail." for i in range(40))


@pytest.fixture
async def feeds() -> Feeds:
    return Feeds()


@pytest.fixture
async def http(feeds: Feeds) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(feeds.handler)) as client:
        yield client


async def test_unmatched_item_is_kept_on_the_wire_but_never_becomes_a_story(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any
) -> None:
    await setup_config(backend_pool)
    deps = make_deps(http, Clock(NOON), FakeCorroborator(), {"telegram": FakeChannel()})
    first_run_feeds(feeds, NOON)

    assert await ingest(backend_pool, deps) == "succeeded"

    bananas = "https://www.reuters.com/food/bananas"
    assert await backend_pool.fetchval("select 1 from news_item where url = $1", bananas) is None
    wire = await backend_pool.fetchrow(
        "select linked, news_item_id, source_id, tier from news_article where url = $1", bananas
    )
    assert wire is not None and wire["linked"] is False and wire["news_item_id"] is None
    assert wire["source_id"] == "wire"
    # A linked item is on the wire too, pointing at its story item.
    linked = await backend_pool.fetchrow(
        "select a.linked, a.news_item_id, i.url from news_article a join news_item i"
        " on i.id = a.news_item_id where i.url = 'https://www.reuters.com/tech/apple-watch'"
    )
    assert linked is not None and linked["linked"] is True
    assert await backend_pool.fetchval("select count(*) from alert where kind = 'news'") == 1
    # Alerts and stories come only from linked items: the wire-only row has no cluster.
    assert await backend_pool.fetchval("select count(*) from news_article where not linked") >= 1


async def test_a_wire_item_that_later_matches_a_holding_becomes_linked(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any
) -> None:
    await setup_config(backend_pool)
    clock = Clock(NOON)
    deps = make_deps(http, clock, FakeCorroborator(), {"telegram": FakeChannel()})
    url = "https://www.reuters.com/food/later"
    feeds.docs[WIRE_FEED] = rss([("Weekly food prices", url, NOON - dt.timedelta(hours=1))])
    await ingest(backend_pool, deps)
    assert await backend_pool.fetchval("select linked from news_article where url = $1", url) is (
        False
    )

    clock.now = NOON + dt.timedelta(hours=1)
    feeds.docs[WIRE_FEED] = rss(
        [("Apple unveils a food-tracking watch", url, NOON - dt.timedelta(hours=1))]
    )
    await ingest(backend_pool, deps)
    row = await backend_pool.fetchrow(
        "select linked, news_item_id from news_article where url = $1", url
    )
    assert row is not None and row["linked"] is True and row["news_item_id"] is not None


async def test_source_run_rows_count_items_and_drops_by_reason(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any
) -> None:
    await setup_config(backend_pool)
    deps = make_deps(http, Clock(NOON), FakeCorroborator(), {"telegram": FakeChannel()})
    first_run_feeds(
        feeds,
        NOON,
        [("Old news about Apple", "https://www.reuters.com/old", NOON - dt.timedelta(days=30))],
    )

    await ingest(backend_pool, deps)

    run = await backend_pool.fetchrow(
        "select fetched, stored_linked, stored_wire, drops, error from news_source_run"
        " where source_id = 'wire'"
    )
    assert run is not None and run["error"] is None
    assert run["fetched"] == 5
    # The deal story, its syndicated copy and the watch item are linked; bananas are wire only;
    # the month-old item is stale and goes nowhere.
    assert run["stored_linked"] == 3 and run["stored_wire"] == 1
    import json

    drops = json.loads(run["drops"])
    assert drops == {"stale": 1, "unmatched_wire_only": 1}
    # The view's seven days run on the database clock; the test's pipeline clock is fixed.
    await backend_pool.execute("update news_source_run set started_at = now()")
    health = await backend_pool.fetchrow(
        "select health, runs_7d, fetched_7d from news_source_health where source_id = 'wire'"
    )
    assert health is not None and health["health"] == "ok" and health["runs_7d"] == 1


async def test_failing_feed_opens_the_breaker_and_never_looks_like_no_news(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any
) -> None:
    await setup_config(backend_pool)
    clock = Clock(NOON)
    deps = make_deps(http, clock, FakeCorroborator(), {"telegram": FakeChannel()})
    first_run_feeds(feeds, NOON)
    await ingest(backend_pool, deps)  # healthy once

    del feeds.docs[WIRE_FEED]  # the feed now answers 404
    for hour in range(1, 4):
        clock.now = NOON + dt.timedelta(minutes=hour * 5)
        await ingest(backend_pool, deps)

    health = await backend_pool.fetchrow(
        "select health, consecutive_failures, last_error, backoff_until"
        " from news_source_health where source_id = 'wire'"
    )
    assert health is not None
    assert health["consecutive_failures"] == 3 and health["backoff_until"] is not None
    assert health["health"] == "down" or health["backoff_until"] <= dt.datetime.now(dt.UTC)
    assert "404" in health["last_error"]

    # While the breaker is open the feed is not requested again.
    before = len([u for u in feeds.requests if u == WIRE_FEED])
    clock.now = NOON + dt.timedelta(minutes=20)
    await ingest(backend_pool, deps)
    assert len([u for u in feeds.requests if u == WIRE_FEED]) == before
    skipped = await backend_pool.fetchval(
        "select drops ->> 'skipped_backoff' from news_source_run where source_id = 'wire'"
        " order by started_at desc limit 1"
    )
    assert skipped == "1"


async def test_unchanged_feed_is_a_not_modified_success(
    backend_pool: asyncpg.Pool, feeds: Feeds
) -> None:
    await setup_config(backend_pool)
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in feeds.docs:
            calls.append(dict(request.headers))
            if request.headers.get("if-none-match") == '"e1"':
                return httpx.Response(304)
            return httpx.Response(200, content=feeds.docs[url], headers={"etag": '"e1"'})
        return httpx.Response(404)

    first_run_feeds(feeds, NOON)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        clock = Clock(NOON)
        deps = make_deps(client, clock, FakeCorroborator(), {"telegram": FakeChannel()})
        await ingest(backend_pool, deps)
        clock.now = NOON + dt.timedelta(minutes=10)
        await ingest(backend_pool, deps)

    assert any("if-none-match" in c for c in calls)
    row = await backend_pool.fetchrow(
        "select drops ->> 'not_modified' as nm, error from news_source_run"
        " where source_id = 'wire' order by started_at desc limit 1"
    )
    assert row is not None and row["nm"] == "1" and row["error"] is None
    state = await backend_pool.fetchrow(
        "select consecutive_failures, last_item_count from source where id = 'wire'"
    )
    assert state is not None and state["consecutive_failures"] == 0
    assert state["last_item_count"] == 4  # a 304 does not reset the item count to zero


# ---------------------------------------------------------------------------
# Article text
# ---------------------------------------------------------------------------


async def seed_article(pool: asyncpg.Pool, url: str, *, linked: bool, source: str = "wire") -> int:
    await pool.execute(
        "insert into source (id, name, tier, type, url) values ($1, $1, 2, 'rss', 'https://x.test')"
        " on conflict do nothing",
        source,
    )
    article_id: int = await pool.fetchval(
        "insert into news_article (url, source_id, tier, published_at, headline, summary, linked)"
        " values ($1, $2, 2, now(), 'Headline', 'Summary', $3) returning id",
        url,
        source,
        linked,
    )
    return article_id


async def test_full_text_is_stored_with_provenance_and_a_stub_is_flagged(
    backend_pool: asyncpg.Pool,
) -> None:
    now = dt.datetime(2026, 10, 10, 12, tzinfo=dt.UTC)
    article = await seed_article(backend_pool, "https://news.test/a", linked=False)
    policy = FetchPolicy(retention_days=30)

    await store_result(
        backend_pool,
        article,
        None,
        FulltextResult(
            "https://news.test/a", "full", 2, Extracted(BODY, "trafilatura 2.0", "A. Writer", "en")
        ),
        policy,
        now,
    )
    row = await backend_pool.fetchrow(
        "select rung, extractor, byline, truncated, char_count, expires_at, content_hash"
        " from news_fulltext where article_id = $1",
        article,
    )
    assert row is not None
    assert (row["rung"], row["extractor"], row["truncated"]) == (2, "trafilatura 2.0", False)
    assert row["char_count"] == len(BODY) and len(row["content_hash"]) == 64
    assert row["expires_at"] == now + dt.timedelta(days=30)
    assert (
        await backend_pool.fetchval("select fetch_state from news_article where id = $1", article)
        == "full"
    )

    stub = await seed_article(backend_pool, "https://news.test/b", linked=False)
    await store_result(
        backend_pool,
        stub,
        None,
        FulltextResult(
            "https://news.test/b", "stub", 2, Extracted("Teaser.", "basic"), "too_short"
        ),
        policy,
        now,
    )
    flagged = await backend_pool.fetchrow(
        "select truncated, truncation_reason from news_fulltext where article_id = $1", stub
    )
    assert flagged is not None
    assert (flagged["truncated"], flagged["truncation_reason"]) == (True, "too_short")
    assert (
        await backend_pool.fetchval("select fetch_state from news_article where id = $1", stub)
        == "stub"
    )


async def test_expired_bodies_are_cleared_but_a_kept_story_and_the_hash_stay(
    backend_pool: asyncpg.Pool,
) -> None:
    now = dt.datetime(2026, 10, 10, 12, tzinfo=dt.UTC)
    policy = FetchPolicy(retention_days=1)
    loose = await seed_article(backend_pool, "https://news.test/loose", linked=False)
    item_id: int = await backend_pool.fetchval(
        "insert into news_item (url, source_id, tier, published_at, headline)"
        " values ('https://news.test/kept', 'wire', 2, now(), 'Kept') returning id"
    )
    kept = await seed_article(backend_pool, "https://news.test/kept", linked=True)
    for article, url, item in ((loose, "https://news.test/loose", None),
                               (kept, "https://news.test/kept", item_id)):  # fmt: skip
        await store_result(
            backend_pool,
            article,
            item,
            FulltextResult(url, "full", 2, Extracted(BODY, "basic")),
            policy,
            now,
        )

    cleared = await purge_expired(backend_pool, now + dt.timedelta(days=2))

    assert cleared == 1
    rows = {
        r["url"]: r
        for r in await backend_pool.fetch(
            "select url, body, content_hash, char_count from news_fulltext"
        )
    }
    assert rows["https://news.test/loose"]["body"] is None
    assert rows["https://news.test/loose"]["content_hash"]  # the audit trail stays
    assert rows["https://news.test/kept"]["body"] == BODY  # linked to a holding: no expiry


async def test_run_fulltext_fetches_linked_items_within_the_source_allowance(
    backend_pool: asyncpg.Pool,
) -> None:
    now = dt.datetime(2026, 10, 10, 12, tzinfo=dt.UTC)
    pages = {
        "https://open.test/a": BODY,
        "https://open.test/b": BODY,
        "https://open.test/c": BODY,
    }
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        requested.append(str(request.url))
        html = f"<html><body><article><p>{pages[str(request.url)]}</p></article></body></html>"
        return httpx.Response(200, content=html.encode(), headers={"content-type": "text/html"})

    for url in pages:
        await seed_article(backend_pool, url, linked=True, source="open-wire")
    await seed_article(backend_pool, "https://open.test/unlinked", linked=False, source="open-wire")
    await seed_article(backend_pool, "https://paywalled.test/x", linked=True, source="hard-wire")
    sources = NewsSourcesConfig.model_validate(
        {
            "sources": [
                {"id": "open-wire", "name": "Open", "tier": 2, "type": "rss",
                 "url": "https://open.test/rss",
                 "fetch": {"rungs": [2], "max_requests_per_hour": 2}},
                {"id": "hard-wire", "name": "Hard", "tier": 2, "type": "rss",
                 "url": "https://paywalled.test/rss", "paywall": "hard", "fetch": {"rungs": []}},
            ]
        }
    )  # fmt: skip
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = PoliteFetcher(client, min_interval=0)
        counts = await run_fulltext(backend_pool, sources, fetcher, now, limit=10)

    assert len(requested) == 2  # the hourly allowance of the open source, not three
    assert counts["full"] == 2 and counts["skipped"] == 1
    assert "https://open.test/unlinked" not in requested  # only linked items are fetched
    states = {
        r["url"]: r["fetch_state"]
        for r in await backend_pool.fetch("select url, fetch_state from news_article")
    }
    assert states["https://paywalled.test/x"] == "skipped"
    assert sorted(v for v in states.values() if v == "full") == ["full", "full"]

    # The next run picks up the third item once the allowance allows.
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = PoliteFetcher(client, min_interval=0)
        later = await run_fulltext(
            backend_pool, sources, fetcher, now + dt.timedelta(hours=2), limit=10
        )
    assert later["full"] == 1
