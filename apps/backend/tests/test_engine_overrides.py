"""Owner overrides: recorded with the reason, and only a pattern of them is acted on."""

import datetime as dt
import json

import asyncpg
import pytest

from steward.actions import registry
from steward.autopilot import ledger
from steward.engine import override_watch, overrides

NOW = dt.datetime.now(dt.UTC)


async def decision(pool: asyncpg.Pool, ref: str = "fact:1", by: str = "rule:x") -> int:
    return await ledger.record(
        pool,
        kind="fact",
        subject_ref=ref,
        ticker=None,
        decision="accept",
        confidence=0.95,
        decided_by=by,
        summary="s",
        evidence={"prompt": "p"},
        status="applied",
    )


def test_a_pattern_needs_enough_overrides_and_a_big_enough_share() -> None:
    assert not overrides.is_pattern(overrides.PATTERN_MIN - 1, 4)
    assert not overrides.is_pattern(4, 100)
    assert overrides.is_pattern(4, 10)
    assert not overrides.is_pattern(4, 0)


@pytest.mark.db
async def test_a_wrong_review_through_the_registry_is_recorded_with_its_reason(
    backend_pool: asyncpg.Pool,
) -> None:
    did = await decision(backend_pool)
    ok = await registry.execute(
        backend_pool, "review_decision", {"decision_id": did, "verdict": "ok"}, via="chat"
    )
    assert ok.status == "done"
    assert await backend_pool.fetchval("select count(*) from public.owner_override") == 0

    await registry.execute(
        backend_pool,
        "review_decision",
        {"decision_id": did, "verdict": "wrong", "reason_code": "wrong_source", "note": "old"},
        via="chat",
    )
    row = await backend_pool.fetchrow("select * from public.owner_override")
    assert (row["action"], row["reason_code"], row["via"]) == (
        "marked_wrong",
        "wrong_source",
        "chat",
    )
    assert (row["subject_kind"], row["decided_by"], row["decision_id"]) == ("fact", "rule:x", did)


@pytest.mark.db
async def test_an_overridden_source_shows_up_as_a_pattern_only_when_it_keeps_happening(
    backend_pool: asyncpg.Pool,
) -> None:
    ids = [await decision(backend_pool, f"fact:{i}") for i in range(8)]
    for did in ids[:3]:
        await overrides.record(
            backend_pool,
            area="autopilot",
            subject_kind="fact",
            subject_ref="x",
            action="reverted",
            via="screen",
            reason_code="wrong_data",
            decided_by="rule:x",
            decision_id=did,
        )
    assert await overrides.patterns(backend_pool, NOW) == []

    await overrides.record(
        backend_pool,
        area="autopilot",
        subject_kind="fact",
        subject_ref="x",
        action="reverted",
        via="screen",
        reason_code="wrong_data",
        decided_by="rule:x",
        decision_id=ids[3],
    )
    found = await overrides.patterns(backend_pool, NOW)
    assert [(p.kind, p.decided_by, p.overridden, p.top_reason) for p in found] == [
        ("fact", "rule:x", 4, "wrong_data")
    ]


@pytest.mark.db
async def test_a_pattern_opens_one_notice_and_one_proposal(
    backend_pool: asyncpg.Pool,
) -> None:
    ids = [await decision(backend_pool, f"fact:{i}") for i in range(6)]
    for did in ids[:4]:
        await overrides.record(
            backend_pool,
            area="autopilot",
            subject_kind="fact",
            subject_ref="x",
            action="reverted",
            via="screen",
            reason_code="too_early",
            decided_by="rule:x",
            decision_id=did,
        )
    for _ in range(2):
        done = await override_watch.act(backend_pool, {"fact": 0.9}, NOW)
        assert done["notices"] == 1
    assert done["proposals"] == 0  # the second run found its proposal already open
    change = await backend_pool.fetchrow("select * from public.engine_change")
    assert (change["target"], change["status"]) == ("autopilot:fact", "shadow")
    assert json.loads(change["after"]) == {"min_confidence": 0.93}
    assert await backend_pool.fetchval("select count(*) from public.engine_change") == 1
    assert await backend_pool.fetchval("select count(*) from public.ops_task") == 1


def test_the_notice_names_the_main_reason() -> None:
    p = overrides.Pattern("fact", "rule:x", 4, 10, "too_early")
    assert "40%" in override_watch.describe(p) and "too early" in override_watch.describe(p)
    assert override_watch.proposed_floor(0.985) == 0.99
