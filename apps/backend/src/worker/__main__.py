"""Worker process: `uv run python -m worker`.

Runs until SIGINT or SIGTERM:
- the in-process scheduler for timed jobs, registered from the Jobs config;
- a config refresher that re-applies the Jobs config every few minutes;
- the pgmq consumer for on-demand jobs the API enqueues;
- at start, a catch-up that runs the data jobs a fresh or stale database is missing
  (worker.catchup), in the background and one job at a time;
- the alert dispatcher, which sends due alerts (new ones, and ones held for quiet hours)
  every few seconds;
- a heartbeat (a `sync_state` row the API's `/health/ready` reads, plus an optional ping to
  an external dead-man's-switch monitor), so a stuck worker is noticed off the host.

Without DATABASE_URL there is nothing to run: the scheduler starts with zero jobs and the
worker says why.
"""

import asyncio
import contextlib
import datetime as dt
import logging
import signal
from typing import Any

import asyncpg
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from steward import __version__
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.db import create_pool
from steward.heartbeat import HEARTBEAT_SECONDS, beat
from steward.jobs import queue
from steward.jobs.registry import JOB_HANDLERS
from steward.jobs.runner import JobTrigger, fail_interrupted_scheduled_runs, run_job
from steward.logging import configure_logging
from steward.notify.alerts import dispatch_due
from steward.notify.channels import channels_from_settings
from steward.settings import get_settings
from worker.catchup import catch_up, close_interrupted_catch_up
from worker.scheduler import apply_jobs_config, build_scheduler

logger = logging.getLogger("worker")

CONFIG_REFRESH_SECONDS = 300
QUEUE_POLL_SECONDS = 1.0
# A queued job is hidden from other readers for this long; a crashed worker's message
# becomes visible again afterwards and is retried. While its job runs, the worker keeps
# pushing the timeout out every QUEUE_EXTEND_SECONDS, so a long run is not redelivered.
QUEUE_VISIBILITY_SECONDS = 3600
QUEUE_EXTEND_SECONDS = 600
# A job whose lock another run holds goes back to the queue for this long instead of
# holding one of the consumer's slots while it waits.
QUEUE_BUSY_DELAY_SECONDS = 30
# A message whose run hit an error outside the job (the database, say) is retried after this.
QUEUE_RETRY_SECONDS = 60
QUEUE_MAX_READS = 3
MAX_CONCURRENT_QUEUED_JOBS = 4
DISPATCH_SECONDS = 15


class Worker:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._tasks: set[asyncio.Task[None]] = set()
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_QUEUED_JOBS)

    async def run_scheduled(self, job_type: str) -> None:
        await self._run(job_type, "schedule", None)

    async def _run(
        self,
        job_type: str,
        trigger: JobTrigger,
        job_id: int | None,
        *,
        wait: bool = True,
        params: dict[str, Any] | None = None,
    ) -> str | None:
        handler = JOB_HANDLERS.get(job_type)
        if handler is None:
            logger.error("no handler for job type %r", job_type)
            if job_id is not None:
                await self.pool.execute(
                    "update public.job set status = 'failed', finished_at = now(),"
                    " error = 'unknown job type' where id = $1",
                    job_id,
                )
            return None
        return await run_job(
            self.pool, job_type, handler, trigger=trigger, job_id=job_id, params=params, wait=wait
        )

    async def recover(self) -> None:
        """At start: close scheduled and catch-up runs a killed worker left `running`."""
        try:
            await fail_interrupted_scheduled_runs(self.pool)
            await close_interrupted_catch_up(self.pool)
        except (OSError, asyncpg.PostgresError, TimeoutError):
            logger.exception("closing interrupted scheduled runs failed")

    async def catch_up(self, stop: asyncio.Event) -> None:
        """Run the data jobs that never succeeded or missed their last slot (in the
        background: the heartbeat, alerts and queue go on meanwhile)."""

        async def run_one(job_type: str, params: dict[str, Any]) -> str | None:
            return await self._run(job_type, "manual", None, params=params)

        try:
            await catch_up(self.pool, run_one, stop)
        except Exception:
            logger.exception("catch-up on start failed")

    async def refresh_schedule(self, scheduler: AsyncIOScheduler) -> None:
        stored = await load_active(self.pool, "jobs", JobsConfig)
        config = stored.body if stored else JobsConfig()
        apply_jobs_config(scheduler, config, self.run_scheduled)

    async def consume(self, stop: asyncio.Event) -> None:
        """Poll the queue; run each message's job in its own task (the job-type lock
        serialises runs of one type, different types may run side by side)."""
        while not stop.is_set():
            try:
                await self._slots.acquire()
                self._slots.release()
                messages = await queue.read(
                    self.pool, visibility_timeout=QUEUE_VISIBILITY_SECONDS, limit=1
                )
            except (OSError, asyncpg.PostgresError, TimeoutError):
                logger.exception("reading the job queue failed; retrying")
                messages = []
                await _sleep_or_stop(stop, 5)
            if not messages:
                await _sleep_or_stop(stop, QUEUE_POLL_SECONDS)
                continue
            for message in messages:
                if isinstance(message, int):
                    logger.error("archiving unreadable queue message %s", message)
                    await queue.archive(self.pool, message)
                    continue
                task = asyncio.create_task(self._handle(message))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _handle(self, message: queue.QueuedJob) -> None:
        async with self._slots:
            try:
                if message.read_count > QUEUE_MAX_READS:
                    logger.error("job %s was retried too often; giving up", message.job_id)
                    await self.pool.execute(
                        "update public.job set status = 'failed', finished_at = now(),"
                        " error = 'gave up after repeated worker crashes' where id = $1"
                        " and status not in ('succeeded', 'failed')",
                        message.job_id,
                    )
                    await queue.archive(self.pool, message.msg_id)
                    return
                extender = asyncio.create_task(self._keep_hidden(message.msg_id))
                try:
                    outcome = await self._run(
                        message.job_type, "manual", message.job_id, wait=False
                    )
                finally:
                    extender.cancel()
                if outcome == "busy":
                    # Another run holds the lock: requeue (a fresh message, so waiting is
                    # not counted as a crash) rather than hold a slot while it finishes.
                    await queue.enqueue(
                        self.pool,
                        message.job_id,
                        message.job_type,
                        delay=QUEUE_BUSY_DELAY_SECONDS,
                    )
                await queue.archive(self.pool, message.msg_id)
            except Exception:
                # Not archived: the message comes back and is retried (the read count stops
                # a job that keeps failing this way).
                logger.exception("running queued job %s failed; will retry", message.job_id)
                with contextlib.suppress(OSError, asyncpg.PostgresError, TimeoutError):
                    await queue.set_visibility(self.pool, message.msg_id, QUEUE_RETRY_SECONDS)

    async def _keep_hidden(self, msg_id: int) -> None:
        """Push the message's visibility timeout out while its job runs."""
        while True:
            await asyncio.sleep(QUEUE_EXTEND_SECONDS)
            try:
                await queue.set_visibility(self.pool, msg_id, QUEUE_VISIBILITY_SECONDS)
            except (OSError, asyncpg.PostgresError, TimeoutError):
                logger.warning("extending queue message %s failed; retrying", msg_id)

    async def dispatch_alerts(self, http: httpx.AsyncClient) -> None:
        try:
            result = await dispatch_due(self.pool, channels_from_settings(http, get_settings()))
        except (OSError, asyncpg.PostgresError, TimeoutError):
            logger.exception("dispatching alerts failed; retrying next round")
            return
        if result.sent or result.failed:
            logger.info("alerts sent %s, failed %s", result.sent, result.failed)

    async def heartbeat(self, http: httpx.AsyncClient) -> None:
        try:
            await beat(self.pool)
        except (OSError, asyncpg.PostgresError, TimeoutError):
            logger.exception("writing the heartbeat failed")
            return
        url = get_settings().heartbeat_ping_url
        if url is not None:
            try:
                await http.get(url.get_secret_value(), timeout=10.0)
            except httpx.HTTPError as exc:
                logger.warning("heartbeat ping failed (%s)", type(exc).__name__)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


def install_stop_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """Set `stop` on SIGINT (Ctrl+C) or SIGTERM.

    asyncio's add_signal_handler exists only on Unix event loops; on Windows it raises
    NotImplementedError, so fall back to plain signal handlers that hand the event back
    to the loop thread-safely.
    """
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda _signum, _frame: loop.call_soon_threadsafe(stop.set))


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    install_stop_handlers(loop, stop)

    settings = get_settings()
    scheduler = build_scheduler()
    worker: Worker | None = None
    consumer: asyncio.Task[None] | None = None
    catching_up: asyncio.Task[None] | None = None
    http = httpx.AsyncClient(timeout=30.0)

    if settings.database_url is None:
        logger.warning("DATABASE_URL is not set: no jobs will run")
    else:
        pool = await create_pool(settings, max_size=10)
        worker = Worker(pool)
        await worker.recover()
        await worker.refresh_schedule(scheduler)
        scheduler.add_job(
            worker.refresh_schedule,
            "interval",
            seconds=CONFIG_REFRESH_SECONDS,
            args=[scheduler],
            id="internal:refresh_schedule",
            name="refresh_schedule",
        )
        scheduler.add_job(
            worker.dispatch_alerts,
            "interval",
            seconds=DISPATCH_SECONDS,
            args=[http],
            id="internal:dispatch_alerts",
            name="dispatch_alerts",
        )
        scheduler.add_job(
            worker.heartbeat,
            "interval",
            seconds=HEARTBEAT_SECONDS,
            args=[http],
            id="internal:heartbeat",
            name="heartbeat",
            next_run_time=dt.datetime.now(dt.UTC),
        )
        consumer = asyncio.create_task(worker.consume(stop))

    scheduler.start()
    if worker is not None:
        catching_up = asyncio.create_task(worker.catch_up(stop))
    config_jobs = [j for j in scheduler.get_jobs() if not j.id.startswith("internal:")]
    logger.info(
        "scheduler started (version %s, %d jobs registered, tz %s)",
        __version__,
        len(config_jobs),
        scheduler.timezone,
    )
    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)
        if catching_up is not None and not catching_up.done():
            # A run cut off here is failed at the next start and caught up again then.
            catching_up.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await catching_up
        if consumer is not None:
            await consumer
        if worker is not None:
            await worker.drain()
            await worker.pool.close()
        await http.aclose()
        logger.info("scheduler stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.secret_values())
    asyncio.run(run())


if __name__ == "__main__":
    main()
