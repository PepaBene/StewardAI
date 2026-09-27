"""The owner's handling of escalations (impact notes): open -> acknowledged -> resolved, and
the figures an oversight screen shows about it.

The state lives on the escalation's `report` row (`escalation_status`, `acknowledged_at`,
`resolved_at`). A new escalation starts open. `acknowledged_at` is the first
acknowledgement (set too when an escalation is resolved without one) and survives a reopen,
so time-to-acknowledge measures the first response; `resolved_at` is cleared on reopening.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import asyncpg
from pydantic import BaseModel, Field

from steward.news.read import EscalationStatus

# Days of the stats are calendar days here, as the budget month and portfolio snapshots are.
STATS_TIMEZONE = "Europe/Prague"


async def set_escalation_status(
    pool: asyncpg.Pool | asyncpg.Connection, report_id: int, status: EscalationStatus
) -> bool:
    """Move escalation `report_id` to `status`. Setting the status it already has changes
    nothing. False when there is no such escalation."""
    updated = await pool.fetchval(
        "update public.report set escalation_status = $2,"
        " acknowledged_at = case when $2 in ('acknowledged', 'resolved')"
        " then coalesce(acknowledged_at, now()) else acknowledged_at end,"
        " resolved_at = case when $2 = 'resolved' then coalesce(resolved_at, now()) end"
        " where id = $1 and type = 'escalation' returning id",
        report_id,
        status,
    )
    return updated is not None


class EscalationDay(BaseModel):
    date: dt.date
    opened: int = Field(description="Escalations written that day.")
    acknowledged: int = Field(description="Escalations first acknowledged that day.")


class EscalationStats(BaseModel):
    open: int = Field(description="Escalations open now (any age).")
    acknowledged: int = Field(description="Escalations acknowledged and not resolved now.")
    resolved: int = Field(description="Escalations resolved now.")
    median_minutes_to_ack: float | None = Field(
        description="Median minutes from an escalation being written to its first "
        "acknowledgement, over those acknowledged in the window. Null when none was."
    )
    per_day: list[EscalationDay] = Field(
        description="Every day of the window, oldest first, today last (zeros included)."
    )
    days: int = Field(description="Length of the window in days, today included.")
    timezone: str = Field(description="Time zone the days are counted in.")


async def escalation_stats(
    pool: asyncpg.Pool, days: int, *, now: dt.datetime | None = None
) -> EscalationStats:
    tz = ZoneInfo(STATS_TIMEZONE)
    today = (now or dt.datetime.now(dt.UTC)).astimezone(tz).date()
    first = today - dt.timedelta(days=days - 1)
    since = dt.datetime.combine(first, dt.time(), tz)
    counts = await pool.fetchrow(
        "select count(*) filter (where escalation_status = 'open') as open,"
        " count(*) filter (where escalation_status = 'acknowledged') as acknowledged,"
        " count(*) filter (where escalation_status = 'resolved') as resolved,"
        " percentile_cont(0.5) within group (order by extract(epoch from"
        " acknowledged_at - created_at) / 60) filter (where acknowledged_at >= $1)"
        " as median_minutes_to_ack"
        " from public.report where type = 'escalation'",
        since,
    )
    rows = await pool.fetch(
        "with d as (select generate_series($1::date, $2::date, interval '1 day')::date as day),"
        " o as (select (created_at at time zone $3)::date as day, count(*) as n"
        " from public.report where type = 'escalation' and created_at >= $4 group by 1),"
        " a as (select (acknowledged_at at time zone $3)::date as day, count(*) as n"
        " from public.report where type = 'escalation' and acknowledged_at >= $4 group by 1)"
        " select d.day, coalesce(o.n, 0) as opened, coalesce(a.n, 0) as acknowledged"
        " from d left join o using (day) left join a using (day) order by d.day",
        first,
        today,
        STATS_TIMEZONE,
        since,
    )
    median = counts["median_minutes_to_ack"]
    return EscalationStats(
        open=counts["open"],
        acknowledged=counts["acknowledged"],
        resolved=counts["resolved"],
        median_minutes_to_ack=float(median) if median is not None else None,
        per_day=[
            EscalationDay(date=r["day"], opened=r["opened"], acknowledged=r["acknowledged"])
            for r in rows
        ],
        days=days,
        timezone=STATS_TIMEZONE,
    )
