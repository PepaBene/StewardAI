"""Regression tests for the "backfill looked frozen" investigation:

- a classification call now fails fast on a slow/flaky connection instead of sitting on the
  client's full default timeout;
- `worker.backfill` tags its own jobs and cleans up ones a stopped run left open;
- it prints the job's current step while a job is still running, instead of nothing until
  the whole job finishes.
"""

import asyncio
import datetime as dt
import json
from typing import Any

import asyncpg
import pytest

from steward.jobs.runner import LOCK_NAMESPACE, JobContext, close_interrupted_runs, create_job
from steward.news.classify import REQUEST_TIMEOUT, ClaudeClassifier
from worker import backfill as backfill_module


async def _status(pool: asyncpg.Pool, job_id: int) -> str:
    value: str = await pool.fetchval("select status from job where id = $1", job_id)
    return value


class _FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        raise RuntimeError("not reached")


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


async def test_classification_requests_use_the_short_timeout() -> None:
    import datetime as dt

    from steward.news.classify import ClassifyItem

    client = _FakeClient()
    item = ClassifyItem(
        index=0,
        headline="Example",
        summary=None,
        outlet="example.com",
        tier=2,
        published_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        candidates=(),
    )
    with pytest.raises(RuntimeError, match="not reached"):
        await ClaudeClassifier(client, "claude-haiku-4-5").classify(
            items=[item], holdings=[], today=dt.date(2026, 1, 1)
        )
    assert len(client.messages.calls) == 1
    assert client.messages.calls[0]["timeout"] == REQUEST_TIMEOUT == 90.0


@pytest.mark.db
async def test_close_interrupted_runs_scopes_to_trigger_and_tag(
    backend_pool: asyncpg.Pool,
) -> None:
    async def insert(
        job_type: str, trigger: str, params: dict[str, Any] | None, status: str = "running"
    ) -> int:
        job_id: int = await backend_pool.fetchval(
            "insert into job (job_type, trigger, status, params) values"
            " ($1, $2, $3, $4::jsonb) returning id",
            job_type,
            trigger,
            status,
            json.dumps(params or {}),
        )
        return job_id

    tagged_dead = await insert("price_history", "manual", {"backfill": True})
    # A different job type, standing in for a same-tagged run that is still alive.
    tagged_live = await insert("macro_collect", "manual", {"backfill": True})
    untagged = await insert("price_history", "manual", None)
    scheduled = await insert("price_history", "schedule", None)

    async with backend_pool.acquire() as holder:
        await holder.execute(
            "select pg_advisory_lock($1, hashtext($2))", LOCK_NAMESPACE, "macro_collect"
        )
        closed = await close_interrupted_runs(
            backend_pool,
            trigger="manual",
            tag={"backfill": True},
            stale_after=dt.timedelta(0),
        )
        await holder.execute(
            "select pg_advisory_unlock($1, hashtext($2))", LOCK_NAMESPACE, "macro_collect"
        )

    assert closed == [tagged_dead]
    assert await _status(backend_pool, tagged_dead) == "failed"
    assert await _status(backend_pool, tagged_live) == "running"
    assert await _status(backend_pool, untagged) == "running"
    assert await _status(backend_pool, scheduled) == "running"


class _UnclosablePool:
    """`backend_pool` outlives this call: the fixture closes it after the test."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pool, name)

    async def close(self) -> None:
        pass


async def _unclosable_pool(pool: asyncpg.Pool) -> _UnclosablePool:
    return _UnclosablePool(pool)


@pytest.mark.db
async def test_backfill_closes_its_own_stale_runs_and_tags_new_ones(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = await create_job(backend_pool, "price_history", "manual", params={"backfill": True})

    async def fast_handler(ctx: JobContext) -> None:
        async with ctx.step("done"):
            pass

    class _FakeSettings:
        database_url = "postgresql://test"
        anthropic_api_key = None
        log_level = "INFO"

        def secret_values(self) -> list[str]:
            return []

    monkeypatch.setattr(backfill_module, "JOB_HANDLERS", {"price_history": fast_handler})
    monkeypatch.setattr(backfill_module, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(
        backfill_module, "create_pool", lambda *_a, **_k: _unclosable_pool(backend_pool)
    )
    rc = await backfill_module.backfill(["price_history"], 1)

    assert rc == 0
    assert await _status(backend_pool, stale) == "failed"
    row = await backend_pool.fetchrow(
        "select id, params from job where job_type = 'price_history' order by id desc limit 1"
    )
    assert row["id"] != stale
    assert json.loads(row["params"]) == {"backfill": True}


@pytest.mark.db
async def test_backfill_prints_progress_while_a_step_is_still_running(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def slow_handler(ctx: JobContext) -> None:
        async with ctx.step("first"):
            pass
        async with ctx.step("second"):
            await asyncio.sleep(0.2)

    monkeypatch.setattr(backfill_module, "JOB_HANDLERS", {"price_history": slow_handler})
    monkeypatch.setattr(backfill_module, "PROGRESS_SECONDS", 0.02)
    job_id = await create_job(backend_pool, "price_history", "manual", params={"backfill": True})

    outcome = await backfill_module._run_with_progress(backend_pool, "price_history", job_id)

    assert outcome == "succeeded"
    out = capsys.readouterr().out
    assert "second (running)" in out
