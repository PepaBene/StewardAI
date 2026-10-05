"""Catch-up on worker start: backfill the data jobs a fresh or stale database is missing.

The scheduler only runs a job at its cron time, so after a first start (or a long stop) the
holidays would wait for 2 January, reference data for Monday, 13F for the 5th. When the
Jobs config's `catch_up_on_start` is on, the worker runs, once at start and one at a time in
the backfill order (worker.backfill.DATA_SEQUENCE, then fact_reconcile), each data job
whose schedule is enabled and that either never succeeded or whose latest scheduled slot
is later than its last successful run.

The runs are ordinary `manual` jobs through the job runner, marked with `catch_up` in their
params: the advisory lock keeps one from running alongside a scheduled or queued run of the
same type, and the monitor's missed-run check counts any run created after a slot, whatever
its trigger. AI report jobs (digests, the review, research, earnings checks) are left to
their schedules and triggers.
"""

import asyncio
import datetime as dt
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

import asyncpg

from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.jobs.runner import lock_connection, lock_key, try_lock, unlock
from steward.monitor import MAX_OPEN_RUN, last_slot
from steward.settings import get_settings
from steward.sources.audit import credentials_changed
from worker.backfill import DATA_SEQUENCE, MAX_OWNERSHIP_RUNS, NEEDS_CLAUDE, ownership_remaining

logger = logging.getLogger("worker.catchup")

# Free-source jobs that run when their provider's key is set. The audit runs first, because the
# collectors only call an endpoint the audit has verified; the rest follow the core data jobs.
SOURCE_AUDIT = "source_audit"
SOURCE_JOBS = [
    "public_data_collect",
    "fmp_company",
    "alpha_vantage_statements",
    "epo_patents",
    "companies_house_filings",
    "edinet_filings",
    "twelve_data_corporate_actions",
    "insider_trades",
    "control_stakes",
    "nport_holdings",
]
CATCH_UP_SEQUENCE = [SOURCE_AUDIT, *DATA_SEQUENCE, *SOURCE_JOBS, "fact_reconcile"]
CATCH_UP_PARAMS: dict[str, Any] = {"catch_up": True}
# How far back a job's latest slot is looked for: the rarest default schedules are
# half-yearly (holidays) and quarterly (the full earnings calendar).
SLOT_LOOKBACK = dt.timedelta(days=400)
# A run that started this shortly before its slot still counts for it.
SLOT_TOLERANCE = dt.timedelta(minutes=5)
# The earnings calendar has two schedules: the full (every holding) and the weekly one.
EARNINGS = ("earnings_calendar_full", "earnings_calendar")
CANDIDATES = [*CATCH_UP_SEQUENCE, "earnings_calendar"]

RunCatchUp = Callable[[str, dict[str, Any]], Awaitable[str | None]]


@dataclass(frozen=True)
class CatchUp:
    job_type: str
    reason: str


def plan_catch_up(
    config: JobsConfig,
    last_success: dict[str, dt.datetime],
    now: dt.datetime,
    *,
    in_flight: Iterable[str] = (),
    has_claude: bool = True,
    credentials_changed: bool = False,
) -> list[CatchUp]:
    """The jobs to run at start, in order. `last_success` maps a job type to the start of
    its latest successful run; `in_flight` names types with a run open now (left to it).
    `credentials_changed` (a key was added or replaced since the last source audit) re-runs
    the audit and every source job, so the new key is verified and used at once."""
    if not config.catch_up_on_start:
        return []
    schedules = config.schedules()
    busy = set(in_flight)
    forced = {SOURCE_AUDIT, *SOURCE_JOBS} if credentials_changed else set()

    def due(job_type: str) -> str | None:
        schedule = schedules[job_type]
        if not schedule.enabled:
            return None
        if job_type in forced:
            return "an API key was added or changed"
        last = last_success.get(job_type)
        if last is None:
            return "never succeeded"
        slot = last_slot(schedule.trigger(), now, SLOT_LOOKBACK)
        if slot is not None and last < slot - SLOT_TOLERANCE:
            return f"missed its {slot.strftime('%Y-%m-%d %H:%M %Z')} run"
        return None

    plan: list[CatchUp] = []
    for job_type in CATCH_UP_SEQUENCE:
        if job_type == "earnings_calendar_full":
            item = _earnings(schedules, last_success, due)
        else:
            reason = due(job_type)
            item = CatchUp(job_type, reason) if reason else None
        if item is None:
            continue
        if item.job_type in busy:
            logger.info("catch-up: %s is already running; left to that run", item.job_type)
            continue
        if item.job_type in NEEDS_CLAUDE and not has_claude:
            logger.info("catch-up: skipping %s (ANTHROPIC_API_KEY is not set)", item.job_type)
            continue
        plan.append(item)
    return plan


def _earnings(
    schedules: dict[str, Any],
    last_success: dict[str, dt.datetime],
    due: Callable[[str], str | None],
) -> CatchUp | None:
    """Never any report dates: the full refresh (the weekly one if the full is off). Else
    the full refresh if it missed its slot (it covers every holding), or the weekly one."""
    full, weekly = EARNINGS
    if not any(t in last_success for t in EARNINGS):
        for job_type in EARNINGS:
            if schedules[job_type].enabled:
                return CatchUp(job_type, "never succeeded")
        return None
    if full in last_success and (reason := due(full)):
        return CatchUp(full, reason)
    if reason := due(weekly):
        return CatchUp(weekly, reason)
    return None


async def job_history(
    pool: asyncpg.Pool, job_types: list[str], now: dt.datetime
) -> tuple[dict[str, dt.datetime], set[str]]:
    """Each type's latest successful start, and the types with a run open now."""
    rows = await pool.fetch(
        "select job_type, max(coalesce(started_at, created_at)) as last from public.job"
        " where status = 'succeeded' and job_type = any($1::text[]) group by job_type",
        job_types,
    )
    open_rows = await pool.fetch(
        "select distinct job_type from public.job where job_type = any($1::text[])"
        " and status in ('queued', 'waiting', 'running') and created_at >= $2",
        job_types,
        now - MAX_OPEN_RUN,
    )
    return {r["job_type"]: r["last"] for r in rows}, {r["job_type"] for r in open_rows}


async def catch_up(
    pool: asyncpg.Pool,
    run: RunCatchUp,
    stop: asyncio.Event | None = None,
    *,
    now: dt.datetime | None = None,
) -> list[tuple[str, str | None]]:
    """Plan the catch-up and run it through `run` (the worker's job path), one job at a
    time; stops starting new ones once `stop` is set. Returns each job's outcome."""
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    if not config.catch_up_on_start:
        logger.info("catch-up on start is off")
        return []
    now = now or dt.datetime.now(dt.UTC)
    last, in_flight = await job_history(pool, CANDIDATES, now)
    settings = get_settings()
    plan = plan_catch_up(
        config,
        last,
        now,
        in_flight=in_flight,
        has_claude=settings.anthropic_api_key is not None,
        credentials_changed=await credentials_changed(pool, settings),
    )
    if not plan:
        logger.info("catch-up: every data job is up to date")
        return []
    logger.info("catch-up: %s", "; ".join(f"{p.job_type} ({p.reason})" for p in plan))
    outcomes: list[tuple[str, str | None]] = []
    for item in plan:
        if stop is not None and stop.is_set():
            logger.info("catch-up stopped before %s", item.job_type)
            break
        runs = 0
        while True:
            started = time.monotonic()
            outcome = await run(item.job_type, dict(CATCH_UP_PARAMS))
            runs += 1
            logger.info(
                "catch-up %s: %s in %.0fs", item.job_type, outcome, time.monotonic() - started
            )
            outcomes.append((item.job_type, outcome))
            if (
                item.job_type != "ownership_13f"
                or outcome != "succeeded"
                or runs >= MAX_OWNERSHIP_RUNS
                or (stop is not None and stop.is_set())
                or await ownership_remaining(pool) <= 0
            ):
                break
    return outcomes


async def close_interrupted_catch_up(pool: asyncpg.Pool) -> list[int]:
    """At start: fail catch-up runs a stopped worker left open. Unlike queued runs no
    message brings them back, so they would show as running forever. One counts only when
    nobody holds its lock (no live run of that key anywhere)."""
    rows = await pool.fetch(
        "select id, job_type, params from public.job where trigger = 'manual'"
        " and params @> '{\"catch_up\": true}'::jsonb"
        " and status in ('queued', 'waiting', 'running') order by id"
    )
    failed: list[int] = []
    if not rows:
        return failed
    conn = await lock_connection(pool)
    try:
        for r in rows:
            params = json.loads(r["params"]) if r["params"] else {}
            key = lock_key(r["job_type"], params if isinstance(params, dict) else {})
            if not await try_lock(conn, key):
                continue
            try:
                async with pool.acquire() as c, c.transaction():
                    await c.execute(
                        "update public.job_step set status = 'failed',"
                        " finished_at = clock_timestamp(),"
                        " error = 'interrupted: the worker stopped during this step'"
                        " where job_id = $1 and status = 'running'",
                        r["id"],
                    )
                    result: str = await c.execute(
                        "update public.job set status = 'failed',"
                        " finished_at = clock_timestamp(), error = 'interrupted (worker"
                        " restarted)' where id = $1 and status in ('queued', 'waiting', 'running')",
                        r["id"],
                    )
            finally:
                await unlock(conn, key)
            if result != "UPDATE 0":
                failed.append(r["id"])
                logger.warning("catch-up job %s (%s) was left open by a stopped worker;"
                               " marked failed", r["id"], r["job_type"])  # fmt: skip
    finally:
        await conn.close()
    return failed
