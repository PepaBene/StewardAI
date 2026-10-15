"""Snoozing a notice: it stays closed until its date, then a problem still there opens a new one,
and it is not counted as a dismissal."""

import asyncpg

from steward.actions import registry
from steward.ops import tasks


async def open_notice(pool: asyncpg.Pool) -> tasks.OpsTask:
    return await tasks.see(pool, "demo", "x", title="Something", detail="d", status="needs_owner")


async def test_a_snoozed_notice_is_not_reopened_by_the_next_run(
    backend_pool: asyncpg.Pool,
) -> None:
    task = await open_notice(backend_pool)
    assert await tasks.snooze(backend_pool, task.id, 3)
    again = await open_notice(backend_pool)
    assert again.status == "resolved"
    assert await tasks.needs_owner(backend_pool) == []
    assert await backend_pool.fetchval("select count(*) from public.ops_task") == 1


async def test_after_the_date_the_problem_opens_a_new_notice(backend_pool: asyncpg.Pool) -> None:
    task = await open_notice(backend_pool)
    await tasks.snooze(backend_pool, task.id, 3)
    await backend_pool.execute(
        "update public.ops_task set snoozed_until = now() - interval '1 minute'"
    )
    reopened = await open_notice(backend_pool)
    assert reopened.status == "needs_owner"
    assert reopened.id != task.id


async def test_a_snooze_is_not_a_dismissal_and_only_open_notices_can_be_snoozed(
    backend_pool: asyncpg.Pool,
) -> None:
    task = await open_notice(backend_pool)
    await tasks.snooze(backend_pool, task.id, 7)
    action = await backend_pool.fetchval(
        "select last_action from public.ops_task where id = $1", task.id
    )
    assert action == "snoozed"
    assert not await tasks.snooze(backend_pool, task.id, 7)  # already closed


async def test_chat_can_propose_a_snooze_and_it_is_described(backend_pool: asyncpg.Pool) -> None:
    task = await open_notice(backend_pool)
    row = await registry.propose(
        backend_pool, "snooze_notice", {"task_id": task.id, "days": 5}, via="chat"
    )
    assert "5 days" in row.summary
