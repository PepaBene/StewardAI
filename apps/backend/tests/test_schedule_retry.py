"""A scheduled run that a database restart killed is tried again a few minutes later; a run
that failed on its own account is not."""

import asyncio
from typing import Any

import pytest

import worker.__main__ as worker_main
from steward.jobs.runner import is_database_outage


@pytest.mark.parametrize(
    ("error", "outage"),
    [
        ("[Errno 104] Connection reset by peer", True),
        ("terminating connection due to administrator command", True),
        ("the database system is shutting down", True),
        ("the database system is starting up", True),
        ("connection was closed in the middle of operation", True),
        ("'float' object has no attribute 'quantize'", False),
        ("Trading 212 returned 401", False),
        (None, False),
    ],
)
def test_database_outage_is_told_from_the_jobs_own_failures(
    error: str | None, outage: bool
) -> None:
    assert is_database_outage(error) is outage


class _Pool:
    def __init__(self, error: str | None) -> None:
        self.error = error

    async def fetchval(self, sql: str, *args: Any) -> str | None:
        return self.error


@pytest.mark.parametrize(
    ("first", "error", "runs"),
    [
        ("succeeded", None, 1),
        ("failed", "Trading 212 returned 401", 1),
        ("failed", "[Errno 104] Connection reset by peer", 2),
        (OSError("connect failed"), None, 2),
    ],
)
async def test_scheduled_run_retries_only_after_a_database_outage(
    monkeypatch: pytest.MonkeyPatch, first: Any, error: str | None, runs: int
) -> None:
    worker = worker_main.Worker(pool=_Pool(error))
    calls: list[str] = []

    async def fake_run(job_type: str, trigger: str, job_id: int | None, **_: Any) -> str:
        calls.append(trigger)
        if len(calls) == 1 and isinstance(first, Exception):
            raise first
        return "succeeded" if len(calls) > 1 else str(first)

    async def no_sleep(seconds: float) -> None:
        assert seconds == worker_main.SCHEDULE_RETRY_SECONDS

    monkeypatch.setattr(worker, "_run", fake_run)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    await worker.run_scheduled("portfolio_sync")
    assert calls == ["schedule"] * runs
