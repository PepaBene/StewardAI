"""Job runner, queue consumer, config store and schedule refresh against a real Postgres.

Phase 1 AC: manually triggering the same job twice concurrently results in one run
executing and one waiting, never two writing at once.
"""

import asyncio
import datetime as dt
import json
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from steward.config import JobSchedule, JobsConfig, StrategyConfig
from steward.config.store import json_diff, load_active, save_config
from steward.jobs import queue, registry
from steward.jobs.runner import JobContext, JobFailed, create_job, run_job
from tests.conftest import MigratedDatabase
from worker.__main__ import Worker
from worker.scheduler import apply_jobs_config, build_scheduler

pytestmark = pytest.mark.db


async def _job(pool: asyncpg.Pool, job_id: int) -> asyncpg.Record:
    row = await pool.fetchrow("select * from job where id = $1", job_id)
    assert row is not None
    return row


async def test_same_job_type_runs_one_at_a_time_across_processes(
    migrated_db: MigratedDatabase,
) -> None:
    # Two pools stand in for two processes (e.g. the scheduler and a queued manual run).
    pool_a = await asyncpg.create_pool(migrated_db.backend_url, min_size=1, max_size=4)
    pool_b = await asyncpg.create_pool(migrated_db.backend_url, min_size=1, max_size=4)
    events: list[tuple[str, int]] = []
    first_running = asyncio.Event()
    release_first = asyncio.Event()

    async def handler(ctx: JobContext) -> None:
        events.append(("start", ctx.job_id))
        first_running.set()
        async with ctx.step("write"):
            await ctx.pool.execute(
                "insert into sync_state (key, value) values ($1, '{}'::jsonb)",
                f"probe:{ctx.job_id}",
            )
            if not release_first.is_set():
                await release_first.wait()
        events.append(("end", ctx.job_id))

    try:
        job_a = await create_job(pool_a, "probe", "manual")
        job_b = await create_job(pool_b, "probe", "manual")
        run_a = asyncio.create_task(
            run_job(pool_a, "probe", handler, trigger="manual", job_id=job_a)
        )
        await first_running.wait()
        run_b = asyncio.create_task(
            run_job(pool_b, "probe", handler, trigger="manual", job_id=job_b)
        )

        # B is parked on the lock while A is still running.
        for _ in range(50):
            if (await _job(pool_a, job_b))["status"] == "waiting":
                break
            await asyncio.sleep(0.02)
        assert (await _job(pool_a, job_a))["status"] == "running"
        assert (await _job(pool_a, job_b))["status"] == "waiting"
        assert events == [("start", job_a)]

        release_first.set()
        assert await asyncio.gather(run_a, run_b) == ["succeeded", "succeeded"]
        assert events == [("start", job_a), ("end", job_a), ("start", job_b), ("end", job_b)]
        a, b = await _job(pool_a, job_a), await _job(pool_a, job_b)
        assert a["finished_at"] <= b["started_at"]
    finally:
        await pool_a.close()
        await pool_b.close()


async def test_different_job_types_do_not_block_each_other(backend_pool: asyncpg.Pool) -> None:
    both_running = asyncio.Barrier(2)

    async def handler(ctx: JobContext) -> None:
        async with asyncio.timeout(5):
            await both_running.wait()

    results = await asyncio.gather(
        run_job(backend_pool, "type_one", handler, trigger="manual"),
        run_job(backend_pool, "type_two", handler, trigger="manual"),
    )
    assert results == ["succeeded", "succeeded"]


async def test_failures_are_recorded_on_the_job_and_the_step(backend_pool: asyncpg.Pool) -> None:
    async def handler(ctx: JobContext) -> None:
        async with ctx.step("ok") as step:
            step.detail["n"] = 1
        async with ctx.step("boom"):
            raise JobFailed("upstream said no")

    job_id = await create_job(backend_pool, "failing", "schedule")
    assert await run_job(backend_pool, "failing", handler, trigger="schedule", job_id=job_id) == (
        "failed"
    )
    job = await _job(backend_pool, job_id)
    assert job["status"] == "failed" and job["error"] == "upstream said no"
    steps = await backend_pool.fetch(
        "select name, status, detail, error, finished_at from job_step where job_id = $1"
        " order by step_index",
        job_id,
    )
    assert [(s["name"], s["status"]) for s in steps] == [("ok", "succeeded"), ("boom", "failed")]
    assert json.loads(steps[0]["detail"]) == {"n": 1}
    assert steps[1]["error"] == "upstream said no"
    assert all(s["finished_at"] is not None for s in steps)
    # The lock was released: the next run of the type goes straight through.
    assert await run_job(backend_pool, "failing", _noop, trigger="manual") == "succeeded"


async def _noop(ctx: JobContext) -> None:
    return None


async def test_queue_consumer_runs_enqueued_jobs(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not any("pgmq" in name for name in migrated_db.applied):
        pytest.skip("pgmq extension not available on this server")
    ran: list[int] = []

    async def fake_sync(ctx: JobContext) -> None:
        async with ctx.step("fake"):
            ran.append(ctx.job_id)

    monkeypatch.setitem(registry.JOB_HANDLERS, "portfolio_sync", fake_sync)
    job_id = await create_job(backend_pool, "portfolio_sync", "manual")
    await queue.enqueue(backend_pool, job_id, "portfolio_sync")

    worker = Worker(backend_pool)
    stop = asyncio.Event()
    consumer = asyncio.create_task(worker.consume(stop))
    try:
        for _ in range(200):  # up to 10 s
            if (await _job(backend_pool, job_id))["status"] == "succeeded":
                break
            await asyncio.sleep(0.05)
    finally:
        stop.set()
        await consumer
        await worker.drain()
    assert ran == [job_id]
    job = await _job(backend_pool, job_id)
    assert job["trigger"] == "manual"
    # Handled messages are archived, not left to reappear.
    assert await backend_pool.fetchval("select count(*) from pgmq.q_jobs") == 0
    assert await backend_pool.fetchval("select count(*) from pgmq.a_jobs") == 1


async def test_config_versions_chain_with_diffs(backend_pool: asyncpg.Pool) -> None:
    owner = "00000000-0000-0000-0000-00000000000a"
    async with backend_pool.acquire() as conn:
        first = await save_config(
            conn, "strategy", StrategyConfig(base_currency="CZK"), created_by=owner
        )
        same = await save_config(
            conn, "strategy", StrategyConfig(base_currency="CZK"), created_by=owner
        )
        assert same.id == first.id  # identical body: no new version
        second = await save_config(
            conn,
            "strategy",
            StrategyConfig(base_currency="CZK", weight_basis="invested"),
            created_by=owner,
            note="ignore cash",
        )
    row = await backend_pool.fetchrow(
        "select parent_id, diff, note, active from config_version where id = $1", second.id
    )
    assert row["parent_id"] == first.id and row["active"] and row["note"] == "ignore cash"
    assert json.loads(row["diff"]) == [
        {"path": "/weight_basis", "op": "replace", "old": "total", "new": "invested"}
    ]
    active = await load_active(backend_pool, "strategy", StrategyConfig)
    assert active is not None and active.id == second.id
    assert active.body.weight_basis == "invested"
    assert (
        await backend_pool.fetchval(
            "select count(*) from config_version where domain = 'strategy' and active"
        )
        == 1
    )


def test_json_diff_reports_adds_removes_and_nested_replacements() -> None:
    old: dict[str, Any] = {"a": 1, "b": {"c": 2, "d": 3}, "e": [1]}
    new: dict[str, Any] = {"a": 1, "b": {"c": 5}, "e": [1, 2], "f": True}
    assert json_diff(old, new) == [
        {"path": "/b/c", "op": "replace", "old": 2, "new": 5},
        {"path": "/b/d", "op": "remove", "old": 3},
        {"path": "/e/1", "op": "add", "new": 2},
        {"path": "/f", "op": "add", "new": True},
    ]


async def test_schedule_follows_the_jobs_config() -> None:
    scheduler = build_scheduler()

    async def run(job_type: str) -> None:
        return None

    assert apply_jobs_config(scheduler, JobsConfig(), run) == [
        "daily_digest",
        "earnings_calendar",
        "earnings_calendar_full",
        "earnings_due",
        "fact_reconcile",
        "holiday_refresh",
        "news_ingest",
        "portfolio_sync",
        "price_history",
        "reference_data",
        "weekly_digest",
    ]
    job = scheduler.get_job("config:portfolio_sync")
    assert job is not None and "hour='22'" in str(job.trigger) and "minute='30'" in str(job.trigger)

    changed = JobsConfig.model_validate({"portfolio_sync": {"cron": "15 6 * * 1-5"}})
    apply_jobs_config(scheduler, changed, run)
    job = scheduler.get_job("config:portfolio_sync")
    assert job is not None and "hour='6'" in str(job.trigger)

    disabled = JobsConfig.model_validate(
        {"portfolio_sync": {"cron": "15 6 * * *", "enabled": False}}
    )
    assert "portfolio_sync" not in apply_jobs_config(scheduler, disabled, run)
    assert scheduler.get_job("config:portfolio_sync") is None


@pytest.mark.parametrize(
    "body",
    [
        {"portfolio_sync": {"cron": "not a cron"}},
        {"portfolio_sync": {"cron": "30 22 * * *", "timezone": "Mars/Olympus"}},
        {"portfolio_sync": {"cron": "30 22 * * *", "typo": 1}},
    ],
)
def test_jobs_config_rejects_bad_schedules(body: dict[str, Any]) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JobsConfig.model_validate(body)


@pytest.mark.parametrize(
    ("cron", "days"),
    [
        ("0 7 * * 1-5", ["Mon", "Tue", "Wed", "Thu", "Fri"]),
        ("0 6 * * 0", ["Sun"]),
        ("0 6 * * 7", ["Sun"]),
        ("0 6 * * 1,3,5", ["Mon", "Wed", "Fri"]),
        ("0 6 * * */2", ["Sun", "Tue", "Thu", "Sat"]),
        ("0 6 * * mon-fri", ["Mon", "Tue", "Wed", "Thu", "Fri"]),
        ("0 6 * * *", ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]),
    ],
)
def test_cron_weekdays_follow_crontab_numbering(cron: str, days: list[str]) -> None:
    # APScheduler 3 counts Monday as 0; the Jobs config uses standard crontab (0 = Sunday).
    trigger = JobSchedule(cron=cron).trigger()
    moment = dt.datetime(2026, 9, 27, 0, 0, tzinfo=ZoneInfo("Europe/Prague"))  # a Sunday
    fired = set()
    for _ in range(14):
        moment = trigger.get_next_fire_time(None, moment)
        fired.add(moment.strftime("%a"))
        moment += dt.timedelta(minutes=1)
    assert sorted(fired, key=["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].index) == days
