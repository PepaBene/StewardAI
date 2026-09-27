"""Regressions for the job runner, worker, scheduler, monitor and config store fixes."""

import asyncio
import datetime as dt
from typing import Any

import asyncpg
import pytest
from apscheduler.triggers.cron import CronTrigger

from steward.config import JobsConfig, StrategyConfig
from steward.config.store import ConfigConflictError, load_active, save_config
from steward.jobs import queue
from steward.jobs.runner import (
    LOCK_NAMESPACE,
    JobContext,
    create_job,
    fail_interrupted_scheduled_runs,
    run_job,
)
from steward.monitor import last_slot
from worker import __main__ as worker_main
from worker.scheduler import apply_jobs_config, build_scheduler


async def _status(pool: asyncpg.Pool, job_id: int) -> str:
    status: str = await pool.fetchval("select status from job where id = $1", job_id)
    return status


@pytest.mark.db
async def test_redelivered_copy_waits_and_does_not_rerun_or_disturb_the_live_run(
    backend_pool: asyncpg.Pool,
) -> None:
    calls: list[int] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(ctx: JobContext) -> None:
        calls.append(ctx.job_id)
        started.set()
        async with ctx.step("work"):
            await release.wait()

    job_id = await create_job(backend_pool, "probe", "manual")
    first = asyncio.create_task(
        run_job(backend_pool, "probe", handler, trigger="manual", job_id=job_id)
    )
    await started.wait()
    # The queue redelivers the same message while the first run still holds the lock.
    copy = asyncio.create_task(
        run_job(backend_pool, "probe", handler, trigger="manual", job_id=job_id)
    )
    await asyncio.sleep(0.3)
    assert not copy.done()
    assert await _status(backend_pool, job_id) == "running"
    step = await backend_pool.fetchval("select status from job_step where job_id = $1", job_id)
    assert step == "running"  # not closed as interrupted by the copy

    release.set()
    assert await asyncio.gather(first, copy) == ["succeeded", "succeeded"]
    assert calls == [job_id]  # the handler ran once
    steps = await backend_pool.fetch("select status from job_step where job_id = $1", job_id)
    assert [s["status"] for s in steps] == ["succeeded"]


@pytest.mark.db
async def test_lock_wait_timeout_of_a_copy_does_not_fail_the_live_run(
    backend_pool: asyncpg.Pool,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(ctx: JobContext) -> None:
        started.set()
        await release.wait()

    job_id = await create_job(backend_pool, "probe", "manual")
    first = asyncio.create_task(
        run_job(backend_pool, "probe", handler, trigger="manual", job_id=job_id)
    )
    await started.wait()
    result = await run_job(
        backend_pool,
        "probe",
        handler,
        trigger="manual",
        job_id=job_id,
        lock_timeout=dt.timedelta(milliseconds=200),
    )
    assert result == "failed"
    assert await _status(backend_pool, job_id) == "running"
    release.set()
    assert await first == "succeeded"
    assert await _status(backend_pool, job_id) == "succeeded"


@pytest.mark.db
async def test_busy_lock_returns_busy_without_waiting(backend_pool: asyncpg.Pool) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocker(ctx: JobContext) -> None:
        started.set()
        await release.wait()

    async def never(ctx: JobContext) -> None:
        raise AssertionError("must not run")

    first = asyncio.create_task(run_job(backend_pool, "probe", blocker, trigger="manual"))
    await started.wait()
    queued = await create_job(backend_pool, "probe", "manual")
    result = await run_job(
        backend_pool, "probe", never, trigger="manual", job_id=queued, wait=False
    )
    assert result == "busy"
    assert await _status(backend_pool, queued) == "waiting"
    release.set()
    assert await first == "succeeded"


@pytest.mark.db
async def test_lock_is_held_outside_the_pool(migrated_db: Any) -> None:
    """A single-connection pool can still run a job: the lock does not take a pool
    connection the job's own writes then wait for."""
    pool = await asyncpg.create_pool(migrated_db.backend_url, min_size=1, max_size=1)
    try:

        async def handler(ctx: JobContext) -> None:
            async with ctx.step("write"):
                await ctx.pool.execute("select 1")

        async with asyncio.timeout(10):
            assert await run_job(pool, "probe", handler, trigger="manual") == "succeeded"
    finally:
        await pool.close()


@pytest.mark.db
async def test_startup_sweep_fails_dead_scheduled_runs_only(backend_pool: asyncpg.Pool) -> None:
    async def insert(job_type: str, trigger: str, status: str, age_hours: float = 0) -> int:
        job_id: int = await backend_pool.fetchval(
            "insert into job (job_type, trigger, status, created_at)"
            " values ($1, $2, $3, now() - make_interval(secs => $4)) returning id",
            job_type,
            trigger,
            status,
            age_hours * 3600,
        )
        return job_id

    dead = await insert("price_history", "schedule", "running")
    await backend_pool.execute(
        "insert into job_step (job_id, step_index, name, status) values ($1, 0, 's', 'running')",
        dead,
    )
    live = await insert("portfolio_sync", "schedule", "running")
    manual = await insert("news_ingest", "manual", "running")
    old_waiting = await insert("daily_digest", "schedule", "waiting", age_hours=2)
    fresh_waiting = await insert("weekly_digest", "schedule", "waiting")

    async with backend_pool.acquire() as holder:
        await holder.execute(
            "select pg_advisory_lock($1, hashtext($2))", LOCK_NAMESPACE, "portfolio_sync"
        )
        failed = await fail_interrupted_scheduled_runs(backend_pool)
        await holder.execute(
            "select pg_advisory_unlock($1, hashtext($2))", LOCK_NAMESPACE, "portfolio_sync"
        )

    assert sorted(failed) == sorted([dead, old_waiting])
    assert await _status(backend_pool, dead) == "failed"
    assert (
        await backend_pool.fetchval("select error from job where id = $1", dead)
        == "interrupted (worker restarted)"
    )
    assert (
        await backend_pool.fetchval("select status from job_step where job_id = $1", dead)
        == "failed"
    )
    assert await _status(backend_pool, live) == "running"
    assert await _status(backend_pool, manual) == "running"
    assert await _status(backend_pool, fresh_waiting) == "waiting"


@pytest.mark.db
async def test_concurrent_first_saves_of_a_domain_both_succeed(
    backend_pool: asyncpg.Pool,
) -> None:
    async def save(basis: str) -> int:
        async with backend_pool.acquire() as conn:
            stored = await save_config(
                conn,
                "strategy",
                StrategyConfig(base_currency="CZK", weight_basis=basis),  # type: ignore[arg-type]
                created_by=None,
            )
            return stored.id

    ids = await asyncio.gather(save("total"), save("invested"), save("total"))
    assert len(set(ids)) >= 2
    assert (
        await backend_pool.fetchval(
            "select count(*) from config_version where domain = 'strategy' and active"
        )
        == 1
    )


@pytest.mark.db
async def test_save_with_a_stale_base_version_conflicts(backend_pool: asyncpg.Pool) -> None:
    async with backend_pool.acquire() as conn:
        first = await save_config(
            conn, "strategy", StrategyConfig(base_currency="CZK"), created_by=None
        )
        second = await save_config(
            conn,
            "strategy",
            StrategyConfig(base_currency="CZK", weight_basis="invested"),
            created_by=None,
            expected_active_id=first.id,
        )
        with pytest.raises(ConfigConflictError) as err:
            await save_config(
                conn,
                "strategy",
                StrategyConfig(base_currency="EUR"),
                created_by=None,
                expected_active_id=first.id,
            )
        assert err.value.active_id == second.id
    active = await load_active(backend_pool, "strategy", StrategyConfig)
    assert active is not None and active.id == second.id


async def test_timezone_only_schedule_change_reschedules() -> None:
    scheduler = build_scheduler()

    async def run(job_type: str) -> None:
        return None

    apply_jobs_config(
        scheduler,
        JobsConfig.model_validate(
            {"portfolio_sync": {"cron": "30 22 * * *", "timezone": "Europe/Prague"}}
        ),
        run,
    )
    apply_jobs_config(
        scheduler,
        JobsConfig.model_validate(
            {"portfolio_sync": {"cron": "30 22 * * *", "timezone": "America/New_York"}}
        ),
        run,
    )
    job = scheduler.get_job("config:portfolio_sync")
    assert job is not None and str(job.trigger.timezone) == "America/New_York"


def test_last_slot_handles_frequent_and_rare_schedules() -> None:
    now = dt.datetime(2026, 9, 26, 12, 7, 30, tzinfo=dt.UTC)
    every_minute = CronTrigger.from_crontab("* * * * *", timezone="UTC")
    assert last_slot(every_minute, now) == dt.datetime(2026, 9, 26, 12, 7, tzinfo=dt.UTC)
    every_five = CronTrigger.from_crontab("*/5 * * * *", timezone="UTC")
    assert last_slot(every_five, now) == dt.datetime(2026, 9, 26, 12, 5, tzinfo=dt.UTC)
    daily = CronTrigger.from_crontab("30 22 * * *", timezone="UTC")
    assert last_slot(daily, now) == dt.datetime(2026, 9, 25, 22, 30, tzinfo=dt.UTC)
    monthly = CronTrigger.from_crontab("0 6 1 * *", timezone="UTC")
    assert last_slot(monthly, now) is None  # beyond the 8-day lookback


class _FakeQueue:
    def __init__(self) -> None:
        self.archived: list[int] = []
        self.sent: list[tuple[int, str, int]] = []
        self.hidden: list[tuple[int, int]] = []

    async def archive(self, conn: Any, msg_id: int) -> None:
        self.archived.append(msg_id)

    async def enqueue(self, conn: Any, job_id: int, job_type: str, *, delay: int = 0) -> int:
        self.sent.append((job_id, job_type, delay))
        return 99

    async def set_visibility(self, conn: Any, msg_id: int, seconds: int) -> None:
        self.hidden.append((msg_id, seconds))


@pytest.mark.parametrize(
    ("outcome", "archived", "requeued"),
    [("succeeded", True, False), ("busy", True, True), (RuntimeError("db gone"), False, False)],
)
async def test_worker_handle_archives_requeues_or_leaves_for_retry(
    monkeypatch: pytest.MonkeyPatch, outcome: Any, archived: bool, requeued: bool
) -> None:
    fake = _FakeQueue()
    monkeypatch.setattr(queue, "archive", fake.archive)
    monkeypatch.setattr(queue, "enqueue", fake.enqueue)
    monkeypatch.setattr(queue, "set_visibility", fake.set_visibility)
    worker = worker_main.Worker(pool=None)  # type: ignore[arg-type]
    waits: list[bool] = []

    async def fake_run(
        job_type: str, trigger: str, job_id: int | None, *, wait: bool = True
    ) -> str | None:
        waits.append(wait)
        if isinstance(outcome, Exception):
            raise outcome
        return str(outcome)

    monkeypatch.setattr(worker, "_run", fake_run)
    await worker._handle(queue.QueuedJob(msg_id=7, read_count=1, job_id=3, job_type="x"))
    assert waits == [False]
    assert fake.archived == ([7] if archived else [])
    assert fake.sent == ([(3, "x", worker_main.QUEUE_BUSY_DELAY_SECONDS)] if requeued else [])
    if not archived:
        assert fake.hidden == [(7, worker_main.QUEUE_RETRY_SECONDS)]
