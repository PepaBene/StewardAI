"""Browser-pass item 9: the insider_trades job is bounded, resumable and leaves no open rows."""

import datetime as dt
import json
from typing import Any

import asyncpg
import pytest

from steward.jobs.runner import JobContext, run_job
from steward.ownership.insider_collect import run_insider_filings
from steward.sources.edgar import Filing
from tests.test_funds_stream_hardening import FakeEdgar, form4, ownership, seed_dual_class


class Clock:
    """A fake monotonic clock: every download costs `step` seconds."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        return self.now


class TimedEdgar(FakeEdgar):
    def __init__(self, filings: list[Filing], body: bytes, clock: Clock) -> None:
        super().__init__(filings, {f.accession: body for f in filings})
        self.clock = clock
        self.file_list_calls = 0

    async def filing_files(self, filing: Filing) -> list[str]:
        self.file_list_calls += 1
        return await super().filing_files(filing)

    async def document(self, url: str, max_bytes: int = 0) -> bytes:
        self.clock.now += self.clock.step
        return await super().document(url, max_bytes)


def _filings(n: int) -> list[Filing]:
    start = dt.date(2026, 8, 1)
    return [
        form4(f"0001222222-26-{i:06d}", (start + dt.timedelta(days=i)).isoformat())
        for i in range(1, n + 1)
    ]


async def _run(
    pool: asyncpg.Pool, edgar: TimedEdgar, clock: Clock, **limits: Any
) -> dict[str, Any]:
    async def handler(ctx: JobContext) -> None:
        await run_insider_filings(ctx, edgar, clock=clock, **limits)  # type: ignore[arg-type]

    assert await run_job(pool, "insider_trades", handler, trigger="manual") == "succeeded"
    job_id = await pool.fetchval("select max(id) from job")
    raw = await pool.fetchval(
        "select detail from job_step where job_id=$1 and name='issuer-filings'", job_id
    )
    return dict(json.loads(raw) if isinstance(raw, str) else raw)


async def _stored(pool: asyncpg.Pool) -> list[str]:
    rows = await pool.fetch("select accession from insider_filing order by filed, accession")
    return [r["accession"] for r in rows]


@pytest.mark.db
async def test_the_wall_clock_budget_stops_the_run_and_the_next_run_resumes(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_dual_class(backend_pool)
    filings = _filings(6)
    clock = Clock(step=100.0)
    edgar = TimedEdgar(filings, ownership(), clock)
    detail = await _run(backend_pool, edgar, clock, max_seconds=250.0, concurrency=1)
    stored = await _stored(backend_pool)
    # Starts at t=0, 100 and 200 are inside the 250 s budget; the other three are deferred.
    assert stored == [f.accession for f in filings[:3]]
    assert detail["filings_imported"] == 3 and detail["deferred_by_issuer"] == {"0000111111": 3}
    assert "run budget" in detail["deferred_reason"]
    assert detail["elapsed_seconds"] == 300.0
    # Oldest first: what is left is newer than what was stored, so the next run picks it up.
    clock.now = 0.0
    second = await _run(backend_pool, edgar, clock, max_seconds=250.0, concurrency=1)
    assert second["filings_imported"] == 3 and "deferred_by_issuer" not in second
    assert await _stored(backend_pool) == [f.accession for f in filings]


@pytest.mark.db
async def test_new_filings_per_issuer_are_capped_per_run(backend_pool: asyncpg.Pool) -> None:
    await seed_dual_class(backend_pool)
    filings = _filings(5)
    clock = Clock(step=0.0)
    edgar = TimedEdgar(filings, ownership(), clock)
    detail = await _run(backend_pool, edgar, clock, max_new_per_issuer=2)
    assert await _stored(backend_pool) == [f.accession for f in filings[:2]]
    assert detail["deferred_by_issuer"] == {"0000111111": 3}
    await _run(backend_pool, edgar, clock, max_new_per_issuer=2)
    await _run(backend_pool, edgar, clock, max_new_per_issuer=2)
    assert await _stored(backend_pool) == [f.accession for f in filings]


@pytest.mark.db
async def test_a_filing_costs_one_request_and_concurrency_keeps_every_filing(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_dual_class(backend_pool)
    filings = _filings(9)
    clock = Clock(step=0.0)
    edgar = TimedEdgar(filings, ownership(), clock)
    detail = await _run(backend_pool, edgar, clock, concurrency=4)
    assert detail["filings_imported"] == 9 and not detail.get("failures")
    assert len(edgar.document_calls) == 9  # one download each ...
    assert edgar.file_list_calls == 0  # ... and no index.json for a filing whose XML is found
    assert await _stored(backend_pool) == [f.accession for f in filings]


@pytest.mark.db
async def test_a_killed_run_is_closed_as_interrupted_by_the_next_start(
    backend_pool: asyncpg.Pool,
) -> None:
    dead = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status, started_at) values"
        " ('insider_trades', 'manual', 'running', now() - interval '3 hours') returning id"
    )
    await backend_pool.execute(
        "insert into job_step (job_id, step_index, name, status) values ($1, 0,"
        " 'issuer-filings', 'running')",
        dead,
    )
    other = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status) values ('news_ingest', 'manual',"
        " 'running') returning id"
    )

    async def handler(ctx: JobContext) -> None:
        return None

    assert await run_job(backend_pool, "insider_trades", handler, trigger="manual") == "succeeded"
    row = await backend_pool.fetchrow(
        "select status, error, finished_at from job where id = $1", dead
    )
    assert row["status"] == "failed" and row["finished_at"] is not None
    assert row["error"].startswith("interrupted")
    step = await backend_pool.fetchrow("select status, error from job_step where job_id = $1", dead)
    assert step["status"] == "failed" and "interrupted" in step["error"]
    # Another job type's open row is not this job's to close.
    assert await backend_pool.fetchval("select status from job where id = $1", other) == "running"
