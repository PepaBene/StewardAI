"""Running a job: one `job` row, a `job_step` row per step, one run per job type at a time.

Serialisation uses a Postgres advisory lock keyed by job type (and by ticker for runs
about one holding, so an earnings check of one company never waits for another's), held
on a dedicated connection for the whole run. A second run with the same key waits
(status `waiting`) until the first releases it; it never runs alongside it. The lock lives
in Postgres, so this holds across processes too (worker and a second worker, scheduler and
queue).

Steps are written through the pool, autocommitted, so the frontend sees each step start
and finish through Realtime while the job is still running.
"""

import datetime as dt
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Literal

import asyncpg

from steward.config.budget import BudgetConfig
from steward.config.store import load_active
from steward.costs import PriceTable

logger = logging.getLogger(__name__)

# First key of the two-key advisory lock: a constant namespace for job-type locks, so they
# cannot collide with advisory locks taken for anything else.
LOCK_NAMESPACE = 0x57A7

JobTrigger = Literal["schedule", "manual", "event", "chat"]

# How long a run waits for the previous run of its type before giving up.
DEFAULT_LOCK_TIMEOUT = dt.timedelta(hours=1)


class JobFailed(Exception):
    """Raised by a job to fail with a message and no traceback in the log."""


@dataclass
class StepUsage:
    """Model usage of one step (AI steps only), stored on its `job_step` row. The runner
    prices it with the Budget config when the step finishes."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    searches: int = 0
    batch: bool = False


@dataclass
class StepHandle:
    detail: dict[str, Any] = field(default_factory=dict)
    usage: StepUsage | None = None


@dataclass
class JobContext:
    pool: asyncpg.Pool
    job_id: int
    job_type: str
    trigger: JobTrigger
    params: dict[str, Any] = field(default_factory=dict)
    _next_step: int = 0
    config_version_ids: list[int] = field(default_factory=list)
    _prices: PriceTable | None = None
    # A job the queue redelivered after the worker died mid-run (see run_job).
    resumed: bool = False
    # The Budget guard's state for this run (steward.budget_guard), made on the first AI
    # request.
    budget: Any = None

    async def checkpoint(self, key: str, compute: Callable[[], Awaitable[Any]]) -> Any:
        """The stored output of step `key` if an earlier attempt of this job completed it;
        else compute it, store it, and return it. Outputs must be JSON-serialisable."""
        stored = await self.pool.fetchval(
            "select output from public.job_checkpoint where job_id = $1 and key = $2",
            self.job_id,
            key,
        )
        if stored is not None:
            return json.loads(stored)
        output = await compute()
        await self.pool.execute(
            "insert into public.job_checkpoint (job_id, key, output) values ($1, $2, $3::jsonb)"
            " on conflict (job_id, key) do update set output = excluded.output",
            self.job_id,
            key,
            json.dumps(output, default=str),
        )
        return json.loads(json.dumps(output, default=str))

    async def has_checkpoint(self, key: str) -> bool:
        return bool(
            await self.pool.fetchval(
                "select exists (select 1 from public.job_checkpoint where job_id = $1"
                " and key = $2)",
                self.job_id,
                key,
            )
        )

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
            await self._finish_step(step_id, "failed", handle, _error_text(exc))
            raise
        await self._finish_step(step_id, "succeeded", handle, None)

    async def price_table(self) -> PriceTable:
        """The Budget config's prices, read once per run (its version is recorded)."""
        if self._prices is None:
            stored = await load_active(self.pool, "budget", BudgetConfig)
            await self.record_config_version(stored.id if stored else None)
            self._prices = PriceTable(stored.body if stored else BudgetConfig())
        return self._prices

    async def _finish_step(
        self, step_id: int, status: str, handle: StepHandle, error: str | None
    ) -> None:
        usage = handle.usage
        prices: dict[str, Any] | None = None
        cost: Decimal | None = None
        downgraded: dict[str, str] = getattr(self.budget, "downgraded", None) or {}
        if usage is not None and usage.model in downgraded:
            # The budget guard sent this run's requests of the step's model to a cheaper one
            # (monthly cap reached): the step is recorded and priced as that model.
            handle.detail["budget_downgrade"] = {"from": usage.model, "to": downgraded[usage.model]}
            usage = replace(usage, model=downgraded[usage.model])
        if usage is not None:
            table = await self.price_table()
            prices = table.prices_for(usage.model)
            cost = table.cost(
                usage.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                searches=usage.searches,
                batch=usage.batch,
            )
        await self.pool.execute(
            "update public.job_step set status = $2, finished_at = clock_timestamp(),"
            " detail = $3::jsonb, error = $4, model = $5, input_tokens = $6,"
            " output_tokens = $7, searches = $8, cost_usd = $9, cache_write_tokens = $10,"
            " cache_read_tokens = $11, batch = $12, prices = $13::jsonb where id = $1",
            step_id,
            status,
            _json(handle.detail),
            error,
            usage.model if usage else None,
            usage.input_tokens if usage else None,
            usage.output_tokens if usage else None,
            usage.searches if usage else None,
            cost,
            usage.cache_write_tokens if usage else None,
            usage.cache_read_tokens if usage else None,
            usage.batch if usage else None,
            json.dumps(prices) if prices else None,
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

# The job whose handler is running in this task (and the tasks it starts): the Anthropic
# client in steward.ai reads it to apply the Budget config's caps to every request of a job.
current_job: ContextVar[JobContext | None] = ContextVar("current_job", default=None)


async def create_job(
    conn: asyncpg.Connection | asyncpg.Pool,
    job_type: str,
    trigger: JobTrigger,
    requested_by: str | None = None,
    params: dict[str, Any] | None = None,
) -> int:
    job_id: int = await conn.fetchval(
        "insert into public.job (job_type, trigger, requested_by, params)"
        " values ($1, $2, $3::uuid, $4::jsonb) returning id",
        job_type,
        trigger,
        requested_by,
        json.dumps(params or {}, default=str),
    )
    return job_id


def lock_key(job_type: str, params: dict[str, Any]) -> str:
    """Runs of one job type about one ticker exclude each other; different tickers don't."""
    ticker = params.get("ticker")
    return f"{job_type}:{ticker}" if isinstance(ticker, str) and ticker else job_type


async def lock_connection(pool: asyncpg.Pool) -> asyncpg.Connection:
    """A new connection with the pool's settings but outside it, for holding a job's
    advisory lock. Holding the lock on a pooled connection would leave each running job
    needing two pool connections, so enough concurrent runs could take every connection
    as lock holders and then wait forever for one to write with. The caller closes it."""
    # asyncpg has no public "connect like this pool" call; this is the pool's own factory
    # (same DSN, server settings, init/setup hooks), without adding it to the pool.
    conn: asyncpg.Connection = await pool._get_new_connection()
    return conn


async def try_lock(conn: asyncpg.Connection, key: str) -> bool:
    return bool(
        await conn.fetchval("select pg_try_advisory_lock($1, hashtext($2))", LOCK_NAMESPACE, key)
    )


async def unlock(conn: asyncpg.Connection, key: str) -> None:
    await conn.execute("select pg_advisory_unlock($1, hashtext($2))", LOCK_NAMESPACE, key)


async def run_job(
    pool: asyncpg.Pool,
    job_type: str,
    handler: JobHandler,
    *,
    trigger: JobTrigger,
    job_id: int | None = None,
    params: dict[str, Any] | None = None,
    lock_timeout: dt.timedelta = DEFAULT_LOCK_TIMEOUT,
    wait: bool = True,
) -> Literal["succeeded", "failed", "busy"]:
    """Run `handler` as job `job_id` (created here with `params` if None; an existing job's
    params are read from its row). Never raises for a job failure: the failure is recorded
    on the job and its step, and "failed" is returned.

    With `wait=False` a run whose lock is held by another run does not wait for it: the job
    is left `waiting`, untouched otherwise, and "busy" is returned so the caller can retry
    later (the queue consumer does, rather than parking a slot on the lock)."""
    if job_id is None:
        job_id = await create_job(pool, job_type, trigger, params=params)
    else:
        row = await pool.fetchrow("select params, status from public.job where id = $1", job_id)
        params = json.loads(row["params"]) if row and row["params"] else {}
        if row is not None and row["status"] in _TERMINAL:
            # Redelivered after it finished (the worker died before archiving the message).
            logger.info("job %s already %s; not run again", job_id, row["status"])
            return "succeeded" if row["status"] == "succeeded" else "failed"
    key = lock_key(job_type, params or {})

    lock_conn = await lock_connection(pool)
    try:
        # Only a job nobody has started yet shows as waiting: a redelivered copy of a job
        # that is running elsewhere must not relabel the live run.
        await pool.execute(
            "update public.job set status = 'waiting' where id = $1 and status = 'queued'",
            job_id,
        )
        if not wait:
            if not await try_lock(lock_conn, key):
                return "busy"
        else:
            try:
                await lock_conn.execute(
                    "select pg_advisory_lock($1, hashtext($2))",
                    LOCK_NAMESPACE,
                    key,
                    timeout=lock_timeout.total_seconds(),
                )
            except TimeoutError:
                # Only fail a job that was waiting: a `running` one is a live run elsewhere
                # (this is a redelivered copy of it) and is left alone.
                await pool.execute(
                    "update public.job set status = 'failed', error = $2,"
                    " finished_at = clock_timestamp() where id = $1"
                    " and status in ('queued', 'waiting')",
                    job_id,
                    f"gave up waiting {lock_timeout} for the lock",
                )
                return "failed"
        try:
            return await _run_locked(pool, job_type, handler, trigger, job_id, params or {})
        finally:
            await unlock(lock_conn, key)
    finally:
        await lock_conn.close()


_TERMINAL = ("succeeded", "failed")


async def _run_locked(
    pool: asyncpg.Pool,
    job_type: str,
    handler: JobHandler,
    trigger: JobTrigger,
    job_id: int,
    params: dict[str, Any],
) -> Literal["succeeded", "failed"]:
    """The run itself, with the job's lock held. Everything about the job's earlier
    attempts is read only now: while another run of this job (the same queue message,
    redelivered while the first was still going) held the lock, it could still change."""
    row = await pool.fetchrow(
        "select status, config_version_ids,"
        " (select max(step_index) from public.job_step where job_id = j.id) as last_step"
        " from public.job j where id = $1",
        job_id,
    )
    resumed = False
    next_step = 0
    versions: list[int] = []
    if row is not None and row["status"] in _TERMINAL:
        # Another copy of this job ran it to completion while this one waited for the lock.
        logger.info("job %s already %s; not run again", job_id, row["status"])
        return "succeeded" if row["status"] == "succeeded" else "failed"
    if row is not None and row["last_step"] is not None:
        # A run the worker was killed in: its open steps are closed, numbering continues,
        # and the handler can pick up completed work from its checkpoints. Nobody else
        # can be running it (they would hold the lock).
        resumed = True
        next_step = row["last_step"] + 1
        versions = list(row["config_version_ids"] or [])
        await pool.execute(
            "update public.job_step set status = 'failed', finished_at = clock_timestamp(),"
            " error = 'interrupted: the worker stopped during this step' where job_id = $1"
            " and status = 'running'",
            job_id,
        )
    ctx = JobContext(
        pool=pool,
        job_id=job_id,
        job_type=job_type,
        trigger=trigger,
        params=params,
        _next_step=next_step,
        config_version_ids=versions,
        resumed=resumed,
    )
    await pool.execute(
        "update public.job set status = 'running', started_at = clock_timestamp() where id = $1",
        job_id,
    )
    logger.info("job %s (%s, %s) started", job_id, job_type, trigger)
    token = current_job.set(ctx)
    try:
        await handler(ctx)
    except Exception as exc:
        if isinstance(exc, JobFailed):
            logger.error("job %s (%s) failed: %s", job_id, job_type, exc)
        else:
            logger.exception("job %s (%s) failed", job_id, job_type)
        await _finish(pool, job_id, "failed", _error_text(exc))
        return "failed"
    finally:
        current_job.reset(token)
    await _finish(pool, job_id, "succeeded", None)
    logger.info("job %s (%s) succeeded", job_id, job_type)
    return "succeeded"


async def fail_interrupted_scheduled_runs(
    pool: asyncpg.Pool, *, stale_after: dt.timedelta = DEFAULT_LOCK_TIMEOUT
) -> list[int]:
    """At worker start: fail scheduled runs a killed worker left open, so they don't show as
    running forever (a scheduled run is not redelivered by any queue). A `running` one
    counts only when nobody holds its lock (no live run of that key anywhere); a `queued`
    or `waiting` one only when older than `stale_after`, by which time a live one would have
    started or given up. Returns the job ids failed."""
    rows = await pool.fetch(
        "select id, job_type, params, status from public.job where trigger = 'schedule'"
        " and (status = 'running' or (status in ('queued', 'waiting')"
        " and created_at < now() - $1::interval))"
        " order by id",
        stale_after,
    )
    failed: list[int] = []
    if not rows:
        return failed
    conn = await lock_connection(pool)
    try:
        for r in rows:
            params = json.loads(r["params"]) if r["params"] else {}
            key = lock_key(r["job_type"], params if isinstance(params, dict) else {})
            if r["status"] == "running":
                if not await try_lock(conn, key):
                    continue  # a live run holds the key; this one may be it
                try:
                    done = await _fail_open(pool, r["id"])
                finally:
                    await unlock(conn, key)
            else:
                done = await _fail_open(pool, r["id"])
            if done:
                failed.append(r["id"])
                logger.warning("job %s (%s) was left open by a stopped worker; marked failed",
                               r["id"], r["job_type"])  # fmt: skip
    finally:
        await conn.close()
    return failed


async def _fail_open(pool: asyncpg.Pool, job_id: int) -> bool:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "update public.job_step set status = 'failed', finished_at = clock_timestamp(),"
            " error = 'interrupted: the worker stopped during this step' where job_id = $1"
            " and status = 'running'",
            job_id,
        )
        result: str = await conn.execute(
            "update public.job set status = 'failed', finished_at = clock_timestamp(),"
            " error = 'interrupted (worker restarted)' where id = $1"
            " and status in ('queued', 'waiting', 'running')",
            job_id,
        )
    return result != "UPDATE 0"


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
