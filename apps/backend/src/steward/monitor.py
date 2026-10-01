"""Monitoring (`monitor`, every 15 minutes): make silent failures loud.

- A failed run of a watched job type (a failed sync, collector or digest) alerts once, with
  the job's error.
- A missed run: a scheduled job whose last slot passed more than the grace period ago
  without a successful (or still running) run alerts once for that slot. Collectors with
  nothing to collect on a market holiday (prices, the sync when every holding's exchange is
  closed) are expected gaps, not missed runs.
- Coverage: a holding no successful news run has covered for `news_coverage_hours` alerts
  once for that gap.

Alerts are `system` alerts at the notifications config's `system_alert_severity`, routed by
the rules like any event, and each has a dedupe key, so a condition never alerts twice.
"""

import datetime as dt

import asyncpg
from apscheduler.triggers.cron import CronTrigger

from steward.activity import held_since, news_last_covered
from steward.config.jobs import JobsConfig
from steward.config.notifications import NotificationsConfig
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.notify.alerts import create_alerts
from steward.notify.route import AlertRequest
from steward.reference.calendar import exchange_open, load_sessions
from steward.settings import get_settings

# Job types whose schedule is watched for missed runs (the monitor watches itself only
# through the external uptime check).
UNWATCHED = {"monitor", "intraday_prices"}
# Collectors that have nothing to collect when the markets are closed.
MARKET_JOBS = {"portfolio_sync", "price_history"}
SLOT_LOOKBACK = dt.timedelta(hours=48)
# A run still open this long after it was created is taken to be dead (its worker was
# killed), not as having run: its slot can be reported missed.
MAX_OPEN_RUN = dt.timedelta(hours=6)


def last_slot(
    trigger: CronTrigger, before: dt.datetime, lookback: dt.timedelta = dt.timedelta(days=8)
) -> dt.datetime | None:
    """The latest fire time of an APScheduler trigger at or before `before`, looking back
    at most `lookback`."""
    # Widen a window ending at `before` until it holds a fire time, so a frequent schedule
    # is walked over a few minutes, not days of slots.
    window = dt.timedelta(minutes=1)
    while True:
        first: dt.datetime | None = trigger.get_next_fire_time(None, before - window)
        if first is not None and first <= before:
            break
        if window >= lookback:
            return None
        window = min(window * 2, lookback)
    last = first
    moment = first + dt.timedelta(seconds=1)
    for _ in range(2000):
        nxt: dt.datetime | None = trigger.get_next_fire_time(None, moment)
        if nxt is None or nxt > before:
            break
        last = nxt
        moment = nxt + dt.timedelta(seconds=1)
    return last


async def _alert(
    pool: asyncpg.Pool,
    notify: NotificationsConfig,
    version_id: int | None,
    severity: int,
    event: str,
    ticker: str | None,
    lines: list[str],
    key: str,
    now: dt.datetime,
    job_id: int,
) -> str | None:
    if severity == 0:
        return None
    exists = await pool.fetchval(
        "select exists (select 1 from public.alert where dedupe_key = $1)", key
    )
    if exists:
        return None
    created = await create_alerts(
        pool,
        AlertRequest("system", severity, ticker, event),
        "\n".join([f"[Severity {severity}] StewardAI monitoring: {lines[0]}", *lines[1:]]),
        notify,
        now=now,
        config_version_id=version_id,
        job_id=job_id,
        dedupe_key=key,
    )
    return f"{key}: {created.explanation}"


async def _market_closed(pool: asyncpg.Pool, jobs: JobsConfig, day: dt.date) -> bool:
    mics = [
        r["home_mic"]
        for r in await pool.fetch(
            "select distinct i.home_mic from public.instrument i join public.position_snapshot p"
            " on p.ticker = i.t212_ticker where p.snapshot_date = coalesce("
            " (select max(snapshot_date) from public.account_snapshot),"
            " (select max(snapshot_date) from public.position_snapshot))"
        )
    ]
    if not mics:
        return day.weekday() >= 5
    sessions = await load_sessions(pool, mics, day, day)
    return not any(exchange_open(jobs.calendar, mic, day, sessions) for mic in mics)


async def run_monitor(ctx: JobContext, now: dt.datetime) -> None:
    pool = ctx.pool
    async with ctx.step("load_config") as step:
        jobs_stored = await load_active(pool, "jobs", JobsConfig)
        notify_stored = await load_active(pool, "notifications", NotificationsConfig)
        for stored in (jobs_stored, notify_stored):
            await ctx.record_config_version(stored.id if stored else None)
        jobs = jobs_stored.body if jobs_stored else JobsConfig()
        notify = notify_stored.body if notify_stored else NotificationsConfig()
        version = notify_stored.id if notify_stored else None
        severity = notify.system_alert_severity
        settings = jobs.monitoring
        step.detail["severity"] = severity
    app_url = get_settings().app_url

    async with ctx.step("failed_jobs") as step:
        outcomes = []
        rows = await pool.fetch(
            "select id, job_type, trigger, error, finished_at from public.job"
            " where status = 'failed' and job_type = any($1::text[]) and finished_at >= $2"
            " order by id",
            settings.alert_failed_jobs,
            now - dt.timedelta(hours=24),
        )
        for r in rows:
            lines = [
                f"job {r['job_type']} failed",
                f"Job {r['id']} ({r['trigger']}) failed at "
                f"{r['finished_at'].strftime('%Y-%m-%d %H:%M UTC')}: {(r['error'] or '')[:400]}",
            ]
            if app_url:
                lines.append(f"{app_url.rstrip('/')}/status")
            outcome = await _alert(
                pool,
                notify,
                version,
                severity,
                "job_failed",
                None,
                lines,
                f"job:{r['id']}",
                now,
                ctx.job_id,
            )
            if outcome:
                outcomes.append(outcome)
        step.detail["alerts"] = outcomes

    async with ctx.step("missed_runs") as step:
        outcomes = []
        first_job = await pool.fetchval("select min(created_at) from public.job")
        grace = dt.timedelta(minutes=settings.missed_run_grace_minutes)
        checked = {}
        for job_type, schedule in jobs.schedules().items():
            if not schedule.enabled or job_type in UNWATCHED:
                continue
            slot = last_slot(schedule.trigger(), now - grace)
            if slot is None or slot < now - SLOT_LOOKBACK or (first_job and slot < first_job):
                continue
            ran = await pool.fetchval(
                "select exists (select 1 from public.job where job_type = $1"
                " and created_at >= $2 and (status in ('succeeded', 'failed')"
                " or (status in ('running', 'waiting', 'queued') and created_at >= $3)))",
                job_type,
                slot - dt.timedelta(minutes=5),
                now - MAX_OPEN_RUN,
            )
            checked[job_type] = slot.isoformat()
            if ran:
                continue  # a failed run alerts as a failure, not as a missed run
            if job_type in MARKET_JOBS and await _market_closed(pool, jobs, slot.date()):
                continue
            lines = [
                f"scheduled job {job_type} did not run",
                f"Its {slot.strftime('%Y-%m-%d %H:%M %Z')} run has not started; the worker may "
                "be down or stuck.",
            ]
            outcome = await _alert(
                pool,
                notify,
                version,
                severity,
                "missed_run",
                None,
                lines,
                f"missed:{job_type}:{slot.isoformat()}",
                now,
                ctx.job_id,
            )
            if outcome:
                outcomes.append(outcome)
        step.detail.update(slots=checked, alerts=outcomes)

    async with ctx.step("coverage") as step:
        outcomes = []
        if jobs.news_ingest.enabled:
            window = dt.timedelta(hours=settings.news_coverage_hours)
            held = await held_since(pool)
            covered = await news_last_covered(pool, now - window - dt.timedelta(days=7))
            for ticker, held_from in sorted(held.items()):
                last = covered.get(ticker)
                if last is not None and last >= now - window:
                    continue
                if held_from > now - window:
                    continue  # held for less than the window
                since = last.isoformat() if last else "never"
                lines = [
                    f"no news coverage for {ticker}",
                    f"No successful news run has covered {ticker} since {since} "
                    f"(window {settings.news_coverage_hours} h).",
                ]
                outcome = await _alert(
                    pool,
                    notify,
                    version,
                    severity,
                    "coverage_gap",
                    ticker,
                    lines,
                    f"coverage:news:{ticker}:{since}",
                    now,
                    ctx.job_id,
                )
                if outcome:
                    outcomes.append(outcome)
        step.detail["alerts"] = outcomes


async def monitor_handler(ctx: JobContext) -> None:
    await run_monitor(ctx, dt.datetime.now(dt.UTC))
