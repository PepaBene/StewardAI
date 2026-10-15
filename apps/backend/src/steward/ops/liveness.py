"""Who watches the watcher (autonomous engine plan, L6): the autopilot and the ops watchdog
each check that the other is still running.

A job that has not succeeded within `GRACE` times its schedule is overdue. The owner gets a
notice in Needs-you (a stopped watchdog cannot tell anyone, so the autopilot says so, and the
other way round); the notice resolves itself when the job runs again.
"""

import datetime as dt
import math
from dataclasses import dataclass

import asyncpg

from steward.config.jobs import JobSchedule, JobsConfig
from steward.ops import tasks

KIND = "liveness"
GRACE = 3  # runs that may be missed before it counts as stopped


@dataclass(frozen=True)
class Watched:
    job_type: str
    cadence_hours: int
    label: str


AUTOPILOT = Watched("autopilot", 6, "The autopilot")
WATCHDOG = Watched("watchdog", 1, "The ops watchdog")


def overdue_hours(
    last_ok: dt.datetime | None,
    first_run: dt.datetime | None,
    watched: Watched,
    now: dt.datetime,
) -> float | None:
    """Hours since the job last succeeded when that is past the grace window, else None.
    A job that never ran at all is not overdue (it was never switched on); one that ran but
    never succeeded counts from its first run."""
    since = last_ok or first_run
    if since is None:
        return None
    hours = (now - since).total_seconds() / 3600
    return hours if hours > watched.cadence_hours * GRACE else None


def cadence_hours(schedule: JobSchedule, now: dt.datetime) -> int | None:
    """Hours between two runs of a scheduled job, rounded up; None when it cannot be told."""
    trigger = schedule.trigger()
    first = trigger.get_next_fire_time(None, now)
    if first is None:
        return None
    second = trigger.get_next_fire_time(first, first)
    if second is None:
        return None
    return int(max(1, math.ceil((second - first).total_seconds() / 3600)))


async def check_all(pool: asyncpg.Pool, jobs: JobsConfig, now: dt.datetime) -> dict[str, float]:
    """Every scheduled job is watched, not only the two that watch each other. A job whose
    schedule is switched off is no longer watched and its notice resolves. Interval jobs (the
    intraday poll) are not watched: they pause outside market hours, and a skipped poll is
    covered by the next one."""
    late: dict[str, float] = {}
    watched_types = {AUTOPILOT.job_type, WATCHDOG.job_type}
    for job_type, schedule in jobs.schedules().items():
        if not schedule.enabled or job_type in watched_types:
            continue
        hours = cadence_hours(schedule, now)
        if hours is None:
            continue
        watched_types.add(job_type)
        overdue = await check(pool, Watched(job_type, hours, f"The {job_type} job"), now)
        if overdue is not None:
            late[job_type] = overdue
    await pool.execute(
        "update public.ops_task set status = 'resolved', resolved_at = now()"
        " where kind = $1 and status <> 'resolved' and subject <> all($2::text[])",
        KIND,
        sorted(watched_types),
    )
    return late


async def check(pool: asyncpg.Pool, watched: Watched, now: dt.datetime) -> float | None:
    """Look at one job; open or resolve its notice. Returns the overdue hours, if any."""
    row = await pool.fetchrow(
        "select max(finished_at) filter (where status = 'succeeded') as last_ok,"
        " min(created_at) as first_run from public.job where job_type = $1",
        watched.job_type,
    )
    late = overdue_hours(
        row["last_ok"] if row else None, row["first_run"] if row else None, watched, now
    )
    if late is None:
        await pool.execute(
            "update public.ops_task set status = 'resolved', resolved_at = now()"
            " where kind = $1 and subject = $2 and status <> 'resolved'",
            KIND,
            watched.job_type,
        )
        return None
    await tasks.see(
        pool,
        KIND,
        watched.job_type,
        title=f"{watched.label} has stopped",
        detail=(
            f"It has not completed a run for {late:.0f} hours (it should run every "
            f"{watched.cadence_hours}). Open Jobs to see why."
        ),
        href="/jobs",
        weight=1.0,
        status="needs_owner",
    )
    return late
