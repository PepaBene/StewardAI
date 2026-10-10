"""N5 against a real Postgres: provenance stored per item, lifecycle recorded with its evidence,
a correction notice, the owner closing a story, indirect-link alerting and portfolio topics."""

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest

from steward.config import JobsConfig
from steward.config.store import save_config
from steward.jobs.runner import JobContext, run_job
from steward.news.pipeline import (
    load_holdings,
    load_news_config,
    load_story,
    route_story,
)
from steward.news.read import set_story_closed, story_evidence
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


@pytest.fixture
async def feeds() -> Feeds:
    return Feeds()


@pytest.fixture
async def http(feeds: Feeds) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(feeds.handler)) as client:
        yield client


async def story_id(pool: asyncpg.Pool, headline_start: str) -> int:
    found = await pool.fetchval(
        "select id from story_cluster where headline like $1", f"{headline_start}%"
    )
    assert found is not None
    return int(found)


async def test_provenance_lifecycle_correction_and_closing(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any
) -> None:
    await setup_config(backend_pool)
    clock = Clock(NOON)
    deps = make_deps(http, clock, FakeCorroborator(), {"telegram": FakeChannel()})
    first_run_feeds(feeds, NOON)
    assert await ingest(backend_pool, deps) == "succeeded"

    kinds = {
        r["url"]: (r["evidence_kind"], r["independence_key"], r["is_correction"])
        for r in await backend_pool.fetch(
            "select url, evidence_kind, independence_key, is_correction from news_item"
        )
    }
    assert kinds["https://ir.apple.test/n/1"][0] == "company_release"
    assert kinds["https://www.reuters.com/tech/msft-contoso"][0] == "wire"
    # Reuters and the CNBC copy of the Reuters story may differ in group, but each has a key.
    assert all(k[1] for k in kinds.values()) and not any(k[2] for k in kinds.values())

    guidance = await story_id(backend_pool, "Apple cuts full-year guidance")
    deal = await story_id(backend_pool, "Microsoft to acquire Contoso")
    assert (
        await backend_pool.fetchval("select lifecycle from story_cluster where id = $1", guidance)
        == "primary_confirmed"
    )
    assert (
        await backend_pool.fetchval("select lifecycle from story_cluster where id = $1", deal)
        == "reported"
    )

    # An independent outlet confirms the deal, but one of the deal's items is a correction
    # notice: the story reads "corrected", and the history keeps both steps.
    await backend_pool.execute(
        "update news_item set is_correction = true where cluster_id = $1", deal
    )
    clock.now = NOON + dt.timedelta(hours=2)
    first_run_feeds(
        feeds,
        NOON,
        [("Microsoft agrees Contoso deal worth $5 billion in cash and stock",
          "https://apnews.com/article/msft", NOON + dt.timedelta(hours=1))],
    )  # fmt: skip
    assert await ingest(backend_pool, deps) == "succeeded"
    evidence = await story_evidence(backend_pool, deal)
    assert evidence is not None
    assert evidence.lifecycle == "corrected"
    assert [(s.from_state, s.to_state) for s in evidence.history] == [("reported", "corrected")]
    assert evidence.history[0].evidence and evidence.history[0].evidence[0]["url"]
    assert evidence.independent_sources >= 1
    assert evidence.items[0].links and evidence.items[0].links[0].confidence is not None

    # The owner closes it: resolved now, and reopening clears the closing time.
    assert await set_story_closed(backend_pool, deal, True)
    closed = await story_evidence(backend_pool, deal)
    assert closed is not None and closed.lifecycle == "resolved" and closed.closed_at is not None
    assert [s.to_state for s in closed.history] == ["corrected", "resolved"]
    assert await set_story_closed(backend_pool, deal, False)
    reopened = await story_evidence(backend_pool, deal)
    assert reopened is not None and reopened.closed_at is None
    assert not await set_story_closed(backend_pool, 999_999, True)


async def test_wire_items_are_tagged_with_portfolio_topics_and_never_become_stories(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any
) -> None:
    await setup_config(backend_pool)
    clock = Clock(NOON)
    deps = make_deps(http, clock, FakeCorroborator(), {"telegram": FakeChannel()})
    first_run_feeds(
        feeds,
        NOON,
        [("Central bank signals an interest rate cut next month",
          "https://www.reuters.com/markets/rates", NOON - dt.timedelta(hours=1))],
    )  # fmt: skip
    assert await ingest(backend_pool, deps) == "succeeded"
    row = await backend_pool.fetchrow(
        "select topics, linked from news_article where url = $1",
        "https://www.reuters.com/markets/rates",
    )
    assert row["topics"] == ["rates"] and row["linked"] is False
    assert (
        await backend_pool.fetchval(
            "select count(*) from news_item where url = $1", "https://www.reuters.com/markets/rates"
        )
        == 0
    )
    assert (
        await backend_pool.fetchval(
            "select topics from news_article where url = $1", "https://www.reuters.com/food/bananas"
        )
        == []
    )


async def test_a_story_reached_only_through_a_competitor_needs_a_higher_severity(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any
) -> None:
    await setup_config(backend_pool)
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "jobs",
            JobsConfig.model_validate(
                {"escalation": {"enabled": False}, "news": {"indirect_alert_min_severity": 3}}
            ),
            created_by=None,
        )
    clock = Clock(NOON)
    deps = make_deps(http, clock, FakeCorroborator(), {"telegram": FakeChannel()})
    feeds.docs[WIRE_FEED] = rss(
        [("Apple unveils a thinner watch", "https://www.reuters.com/tech/apple-watch", NOON)]
    )
    assert await ingest(backend_pool, deps) == "succeeded"
    sid = await story_id(backend_pool, "Apple unveils")

    outcome: dict[str, Any] = {}

    async def handler(ctx: JobContext) -> None:
        config = await load_news_config(ctx)
        holdings = await load_holdings(ctx.pool, config)
        story = await load_story(ctx.pool, sid, config.event_rules)
        assert story is not None
        assert story.indirect_only is False  # Apple is named in the headline
        story.status, story.severity = "confirmed", 2
        outcome["direct"] = await route_story(ctx, story, config, holdings, NOON, None)
        story.indirect_only = True
        outcome["indirect"] = await route_story(ctx, story, config, holdings, NOON, None)

    await run_job(backend_pool, "news_ingest", handler, trigger="manual")
    assert outcome["indirect"] == f"story {sid}: indirect link only, severity 2 is below 3"
    assert outcome["direct"] != outcome["indirect"]
