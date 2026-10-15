"""Chat can propose to close or settle a news story and to mark a fact; nothing runs until the
owner confirms, and each says how to undo it."""

import datetime as dt

import asyncpg
import pytest

from steward.actions import registry

NOW = dt.datetime.now(dt.UTC)


async def story(pool: asyncpg.Pool, status: str = "confirmed") -> int:
    return int(
        await pool.fetchval(
            "insert into public.story_cluster (ticker, headline, headline_key, best_tier, severity,"
            " status, first_seen, last_seen) values ('AAA_US_EQ', 'Chip plant fire', 'k', 2, 2, $1,"
            " $2, $2) returning id",
            status,
            NOW,
        )
    )


@pytest.mark.db
async def test_closing_a_story_waits_for_confirmation_and_can_be_reopened(
    backend_pool: asyncpg.Pool,
) -> None:
    story_id = await story(backend_pool)
    row = await registry.propose(
        backend_pool, "close_story", {"story_id": story_id, "closed": True}, via="chat"
    )
    assert "Chip plant fire" in row.summary
    lifecycle = "select lifecycle from public.story_cluster where id = $1"
    before = await backend_pool.fetchval(lifecycle, story_id)
    assert await backend_pool.fetchval(lifecycle, story_id) == before  # nothing yet
    await registry.confirm(backend_pool, row.id)
    assert await backend_pool.fetchval(lifecycle, story_id) == "resolved"
    reopen = await registry.propose(
        backend_pool, "close_story", {"story_id": story_id, "closed": False}, via="chat"
    )
    await registry.confirm(backend_pool, reopen.id)
    closed_at = "select lifecycle_closed_at from public.story_cluster where id = $1"
    assert await backend_pool.fetchval(closed_at, story_id) is None  # the next run re-judges it


@pytest.mark.db
async def test_a_story_that_is_not_disputed_cannot_be_settled(backend_pool: asyncpg.Pool) -> None:
    story_id = await story(backend_pool, "confirmed")
    row = await registry.propose(
        backend_pool,
        "resolve_story",
        {"story_id": story_id, "resolution": "false"},
        via="chat",
    )
    with pytest.raises(registry.ActionError):
        await registry.confirm(backend_pool, row.id)


@pytest.mark.db
async def test_unknown_stories_and_facts_are_refused_at_proposal(
    backend_pool: asyncpg.Pool,
) -> None:
    with pytest.raises(registry.ActionError):
        await registry.propose(backend_pool, "close_story", {"story_id": 999}, via="chat")
    with pytest.raises(registry.ActionError):
        await registry.propose(
            backend_pool, "mark_fact", {"fact_id": 999, "rating": "wrong"}, via="chat"
        )
