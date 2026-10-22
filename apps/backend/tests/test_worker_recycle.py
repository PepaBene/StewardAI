"""An idle worker whose private memory has grown exits so the launcher forks a fresh one; a busy
or young worker, or a single idle moment, does not."""

import asyncio
from typing import Any

import pytest

import worker.__main__ as worker_main

GROWN = worker_main.RECYCLE_PRIVATE_MB + 100
OLD = worker_main.RECYCLE_MIN_UPTIME_SECONDS + 1


def _worker() -> worker_main.Worker:
    return worker_main.Worker(pool=None)


def test_recycles_on_the_second_idle_check_when_grown() -> None:
    worker = _worker()
    assert worker.should_recycle(OLD, GROWN) is False
    assert worker.should_recycle(OLD, GROWN) is True


@pytest.mark.parametrize(
    ("uptime", "private_mb"),
    [
        (OLD, worker_main.RECYCLE_PRIVATE_MB - 1),  # small enough
        (worker_main.RECYCLE_MIN_UPTIME_SECONDS - 1, GROWN),  # just started
        (OLD, None),  # no /proc (not Linux)
    ],
)
def test_does_not_recycle(uptime: float, private_mb: float | None) -> None:
    worker = _worker()
    assert not any(worker.should_recycle(uptime, private_mb) for _ in range(3))


def test_a_busy_check_resets_the_idle_count() -> None:
    worker = _worker()
    assert worker.should_recycle(OLD, GROWN) is False
    worker._active = 1
    assert worker.should_recycle(OLD, GROWN) is False
    worker._active = 0
    assert worker.should_recycle(OLD, GROWN) is False
    assert worker.should_recycle(OLD, GROWN) is True


async def test_a_running_scheduled_job_keeps_the_worker_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()
    seen: list[bool] = []

    async def fake_run(job_type: str, trigger: str, job_id: int | None, **_: Any) -> str:
        seen.append(worker.is_idle())
        await asyncio.sleep(0)
        return "succeeded"

    monkeypatch.setattr(worker, "_run", fake_run)
    await worker.run_scheduled("portfolio_sync")
    assert seen == [False]
    assert worker.is_idle()


def test_private_memory_counts_only_private_pages() -> None:
    rollup = [
        "Rss:              512000 kB\n",
        "Shared_Clean:     204800 kB\n",
        "Private_Clean:      1024 kB\n",
        "Private_Dirty:    306176 kB\n",
    ]
    assert worker_main.private_memory_mb(rollup) == 300.0
    assert worker_main.private_memory_mb(["Private_Dirty: garbled\n"]) is None
