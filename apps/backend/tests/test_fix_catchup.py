"""Catch-up on worker start: data jobs that never succeeded or missed their last slot run
once, in the backfill order, in the background."""

import asyncio
import datetime as dt
import json
from typing import Any

import asyncpg
import pytest

from steward.config.jobs import JobsConfig
from worker.backfill import DATA_SEQUENCE, DEFAULT_SEQUENCE
from worker.catchup import (
    CATCH_UP_SEQUENCE,
    catch_up,
    close_interrupted_catch_up,
    plan_catch_up,
)

# Saturday 26 September 2026, 12:00 UTC (14:00 in Prague).
NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)
ALL_TYPES = [*CATCH_UP_SEQUENCE, "earnings_calendar"]


def fresh(minutes: int = 1) -> dict[str, dt.datetime]:
    return {t: NOW - dt.timedelta(minutes=minutes) for t in ALL_TYPES}


def names(plan: list[Any]) -> list[str]:
    return [p.job_type for p in plan]


def test_backfill_and_catch_up_share_one_order() -> None:
    assert DEFAULT_SEQUENCE == [*DATA_SEQUENCE, "monitor"]
    assert CATCH_UP_SEQUENCE[: len(DATA_SEQUENCE)] == DATA_SEQUENCE
    assert JobsConfig().catch_up_on_start is True


def test_never_succeeded_runs_everything_in_order() -> None:
    plan = plan_catch_up(JobsConfig(), {}, NOW)
    assert names(plan) == CATCH_UP_SEQUENCE
    assert names(plan)[0] == "portfolio_sync"
    assert all(p.reason == "never succeeded" for p in plan)


def test_succeeded_after_last_slot_is_left_alone() -> None:
    assert plan_catch_up(JobsConfig(), fresh(), NOW) == []


def test_missed_slot_is_caught_up() -> None:
    last = fresh()
    # The 22:30 Prague sync of 25 September (20:30 UTC) came after the last success.
    last["portfolio_sync"] = dt.datetime(2026, 9, 25, 10, 0, tzinfo=dt.UTC)
    # Holidays last loaded before the 2 July run.
    last["holiday_refresh"] = dt.datetime(2026, 1, 2, 3, 0, tzinfo=dt.UTC)
    plan = plan_catch_up(JobsConfig(), last, NOW)
    assert names(plan) == ["portfolio_sync", "holiday_refresh"]
    assert "2026-09-25 22:30" in plan[0].reason


def test_disabled_schedule_and_flag_off() -> None:
    config = JobsConfig.model_validate({"reference_data": {"enabled": False, "cron": "30 4 * * 1"}})
    assert "reference_data" not in names(plan_catch_up(config, {}, NOW))
    off = JobsConfig(catch_up_on_start=False)
    assert plan_catch_up(off, {}, NOW) == []


def test_news_needs_claude_and_running_jobs_are_left_alone() -> None:
    plan = plan_catch_up(JobsConfig(), {}, NOW, has_claude=False, in_flight={"price_history"})
    assert "news_ingest" not in names(plan)
    assert "price_history" not in names(plan)
    assert "portfolio_sync" in names(plan)


def test_earnings_calendar_choice() -> None:
    # Never any report dates: the full refresh.
    assert "earnings_calendar_full" in names(plan_catch_up(JobsConfig(), {}, NOW))
    # The weekly one succeeded recently: the full one is not forced.
    last = fresh()
    del last["earnings_calendar_full"]
    assert plan_catch_up(JobsConfig(), last, NOW) == []
    # The weekly one missed its Sunday run.
    last["earnings_calendar"] = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    assert names(plan_catch_up(JobsConfig(), last, NOW)) == ["earnings_calendar"]
    # The full one missed its 1 July run: it covers every holding, the weekly is not added.
    last["earnings_calendar_full"] = dt.datetime(2026, 4, 2, tzinfo=dt.UTC)
    assert names(plan_catch_up(JobsConfig(), last, NOW)) == ["earnings_calendar_full"]


async def _succeeded(pool: asyncpg.Pool, job_type: str, started: dt.datetime) -> None:
    await pool.execute(
        "insert into public.job (job_type, trigger, status, created_at, started_at, finished_at)"
        " values ($1, 'schedule', 'succeeded', $2, $2, $2)",
        job_type,
        started,
    )


@pytest.mark.db
async def test_catch_up_runs_missing_jobs_through_the_worker_path(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for job_type in ALL_TYPES:
        await _succeeded(backend_pool, job_type, NOW - dt.timedelta(minutes=10))
    await _succeeded(backend_pool, "price_history", NOW - dt.timedelta(days=30))  # older too
    await backend_pool.execute(
        "update public.job set started_at = $1 where job_type = 'macro_collect'",
        NOW - dt.timedelta(days=3),
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    async def run(job_type: str, params: dict[str, Any]) -> str:
        calls.append((job_type, params))
        return "succeeded"

    outcomes = await catch_up(backend_pool, run, now=NOW)
    assert calls == [("macro_collect", {"catch_up": True})]
    assert outcomes == [("macro_collect", "succeeded")]


@pytest.mark.db
async def test_interrupted_catch_up_runs_are_closed_at_start(backend_pool: asyncpg.Pool) -> None:
    ids = []
    for trigger, params in (("manual", {"catch_up": True}), ("manual", {})):
        ids.append(
            await backend_pool.fetchval(
                "insert into public.job (job_type, trigger, status, params, started_at)"
                " values ('price_history', $1, 'running', $2::jsonb, now()) returning id",
                trigger,
                json.dumps(params),
            )
        )
    assert await close_interrupted_catch_up(backend_pool) == [ids[0]]
    statuses = await backend_pool.fetch(
        "select id, status from public.job where id = any($1::bigint[]) order by id", ids
    )
    assert [r["status"] for r in statuses] == ["failed", "running"]


async def test_worker_runs_catch_up_in_the_background(monkeypatch: pytest.MonkeyPatch) -> None:
    """The heartbeat and queue consumer start while a catch-up job is still running, and a
    stop cancels the catch-up instead of waiting for it."""
    import worker.__main__ as main

    class FakePool:
        async def close(self) -> None:
            pass

    class FakeSettings:
        database_url = "postgresql://unused"
        heartbeat_ping_url = None

    stops: list[asyncio.Event] = []
    beat = asyncio.Event()
    consuming = asyncio.Event()
    job_started = asyncio.Event()
    job_calls: list[dict[str, Any]] = []

    async def fake_create_pool(*_a: object, **_k: object) -> FakePool:
        return FakePool()

    async def noop(*_a: object, **_k: object) -> None:
        return None

    async def fake_heartbeat(self: object, http: object) -> None:
        beat.set()

    async def fake_consume(self: object, stop: asyncio.Event) -> None:
        consuming.set()
        await stop.wait()

    async def fake_run_job(pool: object, job_type: str, handler: object, **kw: Any) -> str:
        job_calls.append({"job_type": job_type, **kw})
        job_started.set()
        await asyncio.Event().wait()  # a long job
        return "succeeded"

    async def fake_catch_up(pool: object, run: Any, stop: asyncio.Event) -> None:
        await run("reference_data", {"catch_up": True})

    monkeypatch.setattr(main, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(main, "create_pool", fake_create_pool)
    monkeypatch.setattr(main, "install_stop_handlers", lambda _loop, stop: stops.append(stop))
    monkeypatch.setattr(main.Worker, "recover", noop)
    monkeypatch.setattr(main.Worker, "refresh_schedule", noop)
    monkeypatch.setattr(main.Worker, "dispatch_alerts", noop)
    monkeypatch.setattr(main.Worker, "heartbeat", fake_heartbeat)
    monkeypatch.setattr(main.Worker, "consume", fake_consume)
    monkeypatch.setattr(main, "run_job", fake_run_job)
    monkeypatch.setattr(main, "catch_up", fake_catch_up)

    task = asyncio.create_task(main.run())
    await asyncio.wait_for(job_started.wait(), timeout=5)
    await asyncio.wait_for(beat.wait(), timeout=5)
    await asyncio.wait_for(consuming.wait(), timeout=5)
    assert job_calls[0]["job_type"] == "reference_data"
    assert job_calls[0]["trigger"] == "manual"
    assert job_calls[0]["params"] == {"catch_up": True}
    stops[0].set()
    await asyncio.wait_for(task, timeout=5)


@pytest.mark.db
async def test_catch_up_repeats_13f_until_its_history_is_loaded(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from worker import catchup

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for job_type in ALL_TYPES:
        if job_type != "ownership_13f":  # 13F never ran: it is caught up
            await _succeeded(backend_pool, job_type, NOW - dt.timedelta(minutes=10))
    left = iter([4, 2, 0])  # data sets still missing after each run

    async def remaining(_pool: asyncpg.Pool) -> int:
        return next(left)

    monkeypatch.setattr(catchup, "ownership_remaining", remaining)
    calls: list[str] = []

    async def run(job_type: str, params: dict[str, Any]) -> str:
        calls.append(job_type)
        return "succeeded"

    await catch_up(backend_pool, run, now=NOW)
    assert calls == ["ownership_13f"] * 3
