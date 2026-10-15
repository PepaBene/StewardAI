"""Liveness: the autopilot and the watchdog each tell the owner when the other has stopped."""

import datetime as dt

import asyncpg
import pytest

from steward.ops import liveness, tasks

NOW = dt.datetime.now(dt.UTC)


def hours_ago(h: float) -> dt.datetime:
    return NOW - dt.timedelta(hours=h)


def test_a_job_is_overdue_only_after_the_grace_window() -> None:
    w = liveness.AUTOPILOT  # every 6 hours, 3 runs of grace: 18 hours
    assert liveness.overdue_hours(hours_ago(10), hours_ago(100), w, NOW) is None
    late = liveness.overdue_hours(hours_ago(20), hours_ago(100), w, NOW)
    assert late is not None and round(late) == 20


def test_a_job_that_never_ran_is_not_overdue_but_one_that_never_succeeded_is() -> None:
    w = liveness.WATCHDOG  # hourly, 3 hours of grace
    assert liveness.overdue_hours(None, None, w, NOW) is None
    assert liveness.overdue_hours(None, hours_ago(5), w, NOW) is not None
    assert liveness.overdue_hours(None, hours_ago(1), w, NOW) is None


async def run(pool: asyncpg.Pool, job_type: str, status: str, finished: dt.datetime) -> None:
    await pool.execute(
        "insert into public.job (job_type, trigger, status, created_at, finished_at)"
        " values ($1, 'schedule', $2, $3, $3)",
        job_type,
        status,
        finished,
    )


@pytest.mark.db
async def test_a_stopped_watchdog_opens_a_notice_that_clears_when_it_runs(
    backend_pool: asyncpg.Pool,
) -> None:
    assert await liveness.check(backend_pool, liveness.WATCHDOG, NOW) is None  # never ran

    await run(backend_pool, "watchdog", "succeeded", hours_ago(30))
    assert await liveness.check(backend_pool, liveness.WATCHDOG, NOW) is not None
    notice = (await tasks.needs_owner(backend_pool))[0]
    assert notice.kind == "liveness" and "stopped" in notice.title

    await run(backend_pool, "watchdog", "succeeded", hours_ago(0.5))
    assert await liveness.check(backend_pool, liveness.WATCHDOG, NOW) is None
    assert await tasks.needs_owner(backend_pool) == []
