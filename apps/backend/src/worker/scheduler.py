"""APScheduler setup for timed jobs.

Jobs are registered from the Jobs config domain (schedule, enabled, model, step cap),
so they change without a deploy. Phase 0 registers none.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

TIMEZONE = "Europe/Prague"


def build_scheduler() -> AsyncIOScheduler:
    # coalesce: a job missed while the worker was down runs once on restart, not once
    # per missed slot. max_instances=1: a slow run never overlaps the next one in this
    # process (a Postgres advisory lock covers the cross-process case).
    return AsyncIOScheduler(
        timezone=TIMEZONE,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
