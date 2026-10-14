"""Phase 1 of the autonomous engine: one ledger view, checks before and after applying, and
delivery receipts."""

import datetime as dt

import asyncpg
import pytest

from steward.autopilot import ledger, reverify
from steward.autopilot.judge import Candidate
from steward.autopilot.kinds import all_kinds
from steward.autopilot.kinds.facts import fact_recheck, fact_verify, value_in_excerpt
from steward.engine import timeline
from steward.ops import delivery, tasks
from tests.test_phase2_followups import seed_fact

NOW = dt.datetime.now(dt.UTC)


async def applied(pool: asyncpg.Pool, fact_id: int, decision: str = "accept") -> int:
    return await ledger.record(
        pool,
        kind="fact",
        subject_ref=f"fact:{fact_id}",
        ticker="AAPL",
        decision=decision,
        confidence=0.97,
        decided_by="rule:x",
        summary="Accepted revenue",
        evidence={"fact_id": fact_id},
        status="applied",
        revert_payload={"fact_id": fact_id},
    )


def test_the_ledger_view_reads_every_place_the_engine_leaves_a_trace() -> None:
    assert set(timeline.SOURCES) == {
        "autopilot",
        "watchdog",
        "owner_action",
        "engine_change",
        "config",
        "override",
        "delivery",
        "journal",
    }


def test_delivery_problems_are_described_only_when_there_are_some() -> None:
    assert delivery.problem(0, 0) is None
    assert delivery.problem(2, 0) == "2 failed to send"
    assert delivery.problem(1, 3) == "1 failed to send and 3 still waiting long after their time"
    assert delivery.problem(0, 0, 2) == "2 bounced or were marked as spam"


@pytest.mark.db
async def test_the_ledger_view_lists_the_autopilot_and_the_watchdog_newest_first(
    backend_pool: asyncpg.Pool,
) -> None:
    await applied(backend_pool, 1)
    await tasks.see(backend_pool, "quality", "x", title="Data looks stale", detail="d")
    found = await timeline.recent(backend_pool, timeline.Query(NOW - dt.timedelta(days=1)))
    assert {e.source for e in found} == {"autopilot", "watchdog"}
    assert found == sorted(found, key=lambda e: e.at, reverse=True)
    assert next(e for e in found if e.source == "autopilot").reversible
    only = await timeline.recent(
        backend_pool, timeline.Query(NOW - dt.timedelta(days=1), source="watchdog")
    )
    assert {e.source for e in only} == {"watchdog"}
    with pytest.raises(ValueError, match="Unknown"):
        await timeline.recent(backend_pool, timeline.Query(NOW, source="nope"))


@pytest.mark.db
async def test_a_fact_is_not_accepted_when_its_value_is_not_in_the_stored_excerpt(
    backend_pool: asyncpg.Pool,
) -> None:
    fact_id = await seed_fact(backend_pool, status="unverified")
    cand = Candidate(f"fact:{fact_id}", "AAPL", "p", {"fact_id": fact_id})
    assert await fact_verify(backend_pool, cand, "accept") == []
    assert await fact_verify(backend_pool, cand, "reject") == []


def test_a_printed_value_must_appear_in_the_excerpt() -> None:
    assert value_in_excerpt("94,036", "Revenue was $94,036 million")
    assert not value_in_excerpt("94,036", "Revenue grew strongly")
    assert not value_in_excerpt("", "Revenue was 5")
    assert not value_in_excerpt(None, None)


@pytest.mark.db
async def test_the_sweep_flags_an_applied_decision_that_no_longer_holds(
    backend_pool: asyncpg.Pool,
) -> None:
    fact_id = await seed_fact(backend_pool, status="verified")
    did = await applied(backend_pool, fact_id)
    row = await ledger.get(backend_pool, did)
    assert row is not None
    assert await fact_recheck(backend_pool, row) == []
    assert await reverify.sweep(backend_pool, all_kinds(), NOW) == {"checked": 1, "flagged": 0}

    await seed_fact(backend_pool, status="verified", restates=fact_id, fp="Q4")
    assert await fact_recheck(backend_pool, row) == ["a later filing restated it"]
    assert await reverify.sweep(backend_pool, all_kinds(), NOW) == {"skipped": 1}  # not due yet
    assert await reverify.sweep(backend_pool, all_kinds(), NOW, force=True) == {
        "checked": 1,
        "flagged": 1,
    }
    notice = await backend_pool.fetchrow(
        "select status, subject from public.ops_task where kind = 'reverify'"
    )
    assert (notice["status"], notice["subject"]) == ("needs_owner", f"auto_decision:{did}")


@pytest.mark.db
async def test_failed_alerts_open_a_notice_that_clears_when_they_are_gone(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute(
        "insert into public.alert (kind, severity, channel, message, status, error)"
        " values ('test', 1, 'telegram', 'm', 'failed', 'HTTP 500')"
    )
    assert await delivery.check(backend_pool, NOW) == {"telegram": "1 failed to send"}
    assert (
        await backend_pool.fetchval(
            "select status from public.ops_task where kind = 'delivery' and subject = 'telegram'"
        )
        == "needs_owner"
    )
    await backend_pool.execute("update public.alert set status = 'sent', sent_at = now()")
    assert await delivery.check(backend_pool, NOW) == {}
    assert (
        await backend_pool.fetchval(
            "select status from public.ops_task where kind = 'delivery' and subject = 'telegram'"
        )
        == "resolved"
    )
