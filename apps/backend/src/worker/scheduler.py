"""APScheduler setup for timed jobs.

Jobs are registered from the Jobs config domain (schedule, enabled), and the worker
re-reads that config every few minutes, so a saved schedule change takes effect without a
restart or deploy. With no saved Jobs config the defaults in `JobsConfig` apply.
"""

import logging
from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from steward.config.jobs import JobsConfig

TIMEZONE = "Europe/Prague"

# Prefix for scheduler job ids that come from the Jobs config (vs. internal housekeeping).
CONFIG_JOB_PREFIX = "config:"

logger = logging.getLogger(__name__)


def build_scheduler() -> AsyncIOScheduler:
    # coalesce: a job missed while the worker was down runs once on restart, not once
    # per missed slot. max_instances=1: a slow run never overlaps the next one in this
    # process (a Postgres advisory lock covers the cross-process case).
    return AsyncIOScheduler(
        timezone=TIMEZONE,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )


def apply_jobs_config(
    scheduler: AsyncIOScheduler,
    config: JobsConfig,
    run: Callable[[str], Awaitable[None]],
) -> list[str]:
    """Make the scheduler's config jobs match `config`. Returns the job types scheduled."""
    wanted = {
        job_type: schedule for job_type, schedule in config.schedules().items() if schedule.enabled
    }
    for job in scheduler.get_jobs():
        if job.id.startswith(CONFIG_JOB_PREFIX) and job.id[len(CONFIG_JOB_PREFIX) :] not in wanted:
            scheduler.remove_job(job.id)
            logger.info("unscheduled %s", job.id)
    for job_type, schedule in wanted.items():
        job_id = CONFIG_JOB_PREFIX + job_type
        trigger = schedule.trigger()
        existing = scheduler.get_job(job_id)
        if existing is None:
            scheduler.add_job(run, trigger, args=[job_type], id=job_id, name=job_type)
            logger.info("scheduled %s: %s (%s)", job_type, schedule.cron, schedule.timezone)
        elif str(existing.trigger) != str(trigger):
            scheduler.reschedule_job(job_id, trigger=trigger)
            logger.info("rescheduled %s: %s (%s)", job_type, schedule.cron, schedule.timezone)
    return sorted(wanted)
