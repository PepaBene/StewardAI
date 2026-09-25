"""Running a job: one `job` row, a `job_step` row per step, one run per job type at a time.

Serialisation uses a Postgres advisory lock keyed by job type, held on a dedicated
connection for the whole run. A second run of the same type waits (status `waiting`)
until the first releases it; it never runs alongside it. The lock lives in Postgres, so
this holds across processes too (worker and a second worker, scheduler and queue).

Steps are written through the pool, autocommitted, so the frontend sees each step start
and finish through Realtime while the job is still running.
"""

import datetime as dt
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import asyncpg

logger = logging.getLogger(__name__)

# First key of the two-key advisory lock: a constant namespace for job-type locks, so they
# cannot collide with advisory locks taken for anything else.
LOCK_NAMESPACE = 0x57A7

JobTrigger = Literal["schedule", "manual", "event"]

# How long a run waits for the previous run of its type before giving up.
DEFAULT_LOCK_TIMEOUT = dt.timedelta(hours=1)


class JobFailed(Exception):
    """Raised by a job to fail with a message and no traceback in the log."""


@dataclass
class StepHandle:
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobContext:
    pool: asyncpg.Pool
    job_id: int
    job_type: str
    trigger: JobTrigger
    _next_step: int = 0
    config_version_ids: list[int] = field(default_factory=list)

    @asynccontextmanager
    async def step(self, name: str) -> AsyncIterator[StepHandle]:
        """Record a step: a `running` row now, `succeeded` or `failed` when the block exits.

        Put anything worth keeping (counts, dates, warnings) into `handle.detail`.
        """
        index = self._next_step
        self._next_step += 1
        step_id = await self.pool.fetchval(
            "insert into public.job_step (job_id, step_index, name, status)"
            " values ($1, $2, $3, 'running') returning id",
            self.job_id,
            index,
            name,
        )
        handle = StepHandle()
        try:
            yield handle
        except BaseException as exc:
            await self.pool.execute(
                "update public.job_step set status = 'failed', finished_at = clock_timestamp(),"
                " detail = $2::jsonb, error = $3 where id = $1",
                step_id,
                _json(handle.detail),
                _error_text(exc),
            )
            raise
        await self.pool.execute(
            "update public.job_step set status = 'succeeded', finished_at = clock_timestamp(),"
            " detail = $2::jsonb where id = $1",
            step_id,
            _json(handle.detail),
        )

    async def record_config_version(self, version_id: int | None) -> None:
        if version_id is None or version_id in self.config_version_ids:
            return
        self.config_version_ids.append(version_id)
        await self.pool.execute(
            "update public.job set config_version_ids = $2 where id = $1",
            self.job_id,
            self.config_version_ids,
        )


JobHandler = Callable[[JobContext], Awaitable[None]]


async def create_job(
    conn: asyncpg.Connection | asyncpg.Pool,
    job_type: str,
    trigger: JobTrigger,
    requested_by: str | None = None,
) -> int:
    job_id: int = await conn.fetchval(
        "insert into public.job (job_type, trigger, requested_by) values ($1, $2, $3::uuid)"
        " returning id",
        job_type,
        trigger,
        requested_by,
    )
    return job_id


async def run_job(
    pool: asyncpg.Pool,
    job_type: str,
    handler: JobHandler,
    *,
    trigger: JobTrigger,
    job_id: int | None = None,
    lock_timeout: dt.timedelta = DEFAULT_LOCK_TIMEOUT,
) -> Literal["succeeded", "failed"]:
    """Run `handler` as job `job_id` (created here if None). Never raises for a job failure:
    the failure is recorded on the job and its step, and "failed" is returned."""
    if job_id is None:
        job_id = await create_job(pool, job_type, trigger)
    ctx = JobContext(pool=pool, job_id=job_id, job_type=job_type, trigger=trigger)

    async with pool.acquire() as lock_conn:
        await pool.execute("update public.job set status = 'waiting' where id = $1", job_id)
        try:
            await lock_conn.execute(
                "select pg_advisory_lock($1, hashtext($2))",
                LOCK_NAMESPACE,
                job_type,
                timeout=lock_timeout.total_seconds(),
            )
        except TimeoutError:
            await _finish(pool, job_id, "failed", f"gave up waiting {lock_timeout} for the lock")
            return "failed"
        try:
            await pool.execute(
                "update public.job set status = 'running', started_at = clock_timestamp()"
                " where id = $1",
                job_id,
            )
            logger.info("job %s (%s, %s) started", job_id, job_type, trigger)
            try:
                await handler(ctx)
            except Exception as exc:
                if isinstance(exc, JobFailed):
                    logger.error("job %s (%s) failed: %s", job_id, job_type, exc)
                else:
                    logger.exception("job %s (%s) failed", job_id, job_type)
                await _finish(pool, job_id, "failed", _error_text(exc))
                return "failed"
            await _finish(pool, job_id, "succeeded", None)
            logger.info("job %s (%s) succeeded", job_id, job_type)
            return "succeeded"
        finally:
            await lock_conn.execute(
                "select pg_advisory_unlock($1, hashtext($2))", LOCK_NAMESPACE, job_type
            )


async def _finish(pool: asyncpg.Pool, job_id: int, status: str, error: str | None) -> None:
    await pool.execute(
        "update public.job set status = $2, error = $3, finished_at = clock_timestamp()"
        " where id = $1",
        job_id,
        status,
        error,
    )


def _error_text(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    return text[:2000]


def _json(detail: dict[str, Any]) -> str | None:
    return json.dumps(detail, default=str) if detail else None
