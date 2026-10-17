"""The ops watchdog job (D1, D2, D3, D5, D7): retry-first health.

Each run looks for five kinds of problem. For the first three it acts, tries again after
`retry_hours`, and only after `max_attempts` hands the problem to the owner:

- `job_failed`: the latest run of a watched collector failed. Retried once per window.
- `quality`: a data-quality issue that a refresh job fixes (stale prices, unresolved
  identities, fund weights that do not add up, open source conflicts). The refresh is queued.
- `provider_block`: a provider endpoint blocked for `block_reset_days`. The block is lifted so
  the next call probes it; a block that comes back goes to the owner.

It also rebuilds the benchmark index weights and the fund coverage ledger from stored fund
holdings (C2), which follows every look-through refresh without a button.

The other two are for the owner from the start, because only they can act:

- `missing_key`: an API key a switched-on job needs, with what it unlocks and the signup page.
- `budget_forecast`: AI spend on course to pass the monthly cap.

A problem that is gone on the next run resolves its task. Everything it queues is an ordinary
job, so it shows in the job list like any other run.
"""

import datetime as dt
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import asyncpg
import httpx

from steward.autopilot import ledger
from steward.budget_guard import month_of, month_spend
from steward.company.case_autofill import fill_case_items
from steward.company.segment_autofill import fill_country_maps
from steward.config.budget import BudgetConfig
from steward.config.jobs import JobsConfig, OpsSettings
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig
from steward.graph.provider_gate import reset_block
from steward.jobs import queue
from steward.jobs.runner import JobContext, create_job, fail_interrupted_scheduled_runs
from steward.lookthrough.coverage import refresh_ledger
from steward.lookthrough.read import load_portfolio
from steward.notify.channels import channels_from_settings
from steward.ops import anticipate, delivery, journal_gaps, keys, liveness, tasks
from steward.ops import budget as budget_forecast
from steward.ops.opportunities import check_opportunities
from steward.ops.release_gate import check_release_gate
from steward.ops.suggestions import suggest_rebalance, suggest_thesis_breaks
from steward.ops.tuning import tune_alerts, tune_inbox, tune_notices
from steward.quality.cockpit import build_cockpit
from steward.settings import get_settings
from steward.xray.derive import derive_benchmark_indexes

logger = logging.getLogger("steward.ops")

# Cockpit issue kind -> the job that refreshes what the issue is about. One queued job covers
# every issue of its kind, so a run queues each at most once.
QUALITY_REPAIRS: dict[str, str] = {
    "stale_price": "price_history",
    "identity_unresolved": "reference_data",
    "fund_weights": "etf_look_through",
    "source_conflict": "fact_reconcile",
}
# Failed runs of these are not retried: they need parameters (a ticker) the failed run held.
NO_RETRY = frozenset({"earnings_check", "thesis_verdicts", "research", "chat"})
OPEN_STATES = ("queued", "waiting", "running")


async def queue_job(pool: asyncpg.Pool, job_type: str) -> int | None:
    """Queue one run of `job_type` unless one is already open; its id, or None."""
    if await pool.fetchval(
        "select exists (select 1 from public.job where job_type = $1 and status = any($2))",
        job_type,
        list(OPEN_STATES),
    ):
        return None
    async with pool.acquire() as conn, conn.transaction():
        job_id = await create_job(conn, job_type, "event", params={})
        await queue.enqueue(conn, job_id, job_type)
    return int(job_id)


async def _act(
    pool: asyncpg.Pool,
    task: tasks.OpsTask,
    cfg: OpsSettings,
    now: dt.datetime,
    *,
    can_repair: bool,
    repair: Any,
    summary: dict[str, Any],
) -> None:
    """Apply the retry policy to one seen problem. `repair` is an async callable returning a
    short description of what it did, or None when it had nothing to do."""
    step = tasks.decide(
        task, now, retry_hours=cfg.retry_hours, max_attempts=cfg.max_attempts, can_repair=can_repair
    )
    if step == "repair" and cfg.repair:
        done = await repair()
        if done:
            await tasks.acted(pool, task.id, done)
            summary.setdefault("repaired", []).append(f"{task.kind}:{task.subject}")
    elif step == "escalate":
        await tasks.hand_over(pool, task.id)
        summary.setdefault("handed_over", []).append(f"{task.kind}:{task.subject}")


# -------------------------------------------------------------------------- failed collectors


async def check_failed_jobs(
    pool: asyncpg.Pool, jobs: JobsConfig, cfg: OpsSettings, now: dt.datetime
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    # The primaries that have a standby are watched even when they are not on the alert list,
    # or their failover could never start.
    watched = sorted((set(jobs.monitoring.alert_failed_jobs) | set(FAILOVER)) - NO_RETRY)
    rows = await pool.fetch(
        "select distinct on (job_type) job_type, status, error, finished_at from public.job"
        " where job_type = any($1::text[]) and created_at >= $2"
        " order by job_type, id desc",
        watched,
        now - dt.timedelta(days=2),
    )
    failing: set[str] = set()
    # Failing although a retry was already tried: the standby takes over for these.
    after_retry: set[str] = set()
    # A retry that is still queued or running keeps its task (and its attempts) open.
    in_flight: set[str] = set()
    for r in rows:
        if r["status"] in OPEN_STATES:
            in_flight.add(r["job_type"])
            continue
        # A run a deploy interrupted is not a failure; the worker's catch-up reruns it.
        if r["status"] != "failed" or (r["error"] or "").startswith("interrupted"):
            continue
        failing.add(r["job_type"])
        task = await tasks.see(
            pool,
            "job_failed",
            r["job_type"],
            title=f"{r['job_type']} keeps failing",
            detail=f"Its latest run failed: {(r['error'] or 'no error recorded')[:300]}",
            href="/status",
            weight=0.3,
        )
        if task.attempts > 0:
            after_retry.add(r["job_type"])

        async def retry(job_type: str = r["job_type"]) -> str | None:
            job_id = await queue_job(pool, job_type)
            return None if job_id is None else f"retried as job {job_id}"

        await _act(pool, task, cfg, now, can_repair=True, repair=retry, summary=summary)
    summary["resolved"] = await tasks.settle(pool, "job_failed", failing | in_flight)
    summary["failover"] = await run_failover(pool, jobs, after_retry, now, failing | in_flight)
    return summary


# A primary collector that is still failing after its retry, and the standby jobs that read the
# same kind of data from another provider (D1). The standby must already be switched on.
FAILOVER: dict[str, tuple[str, ...]] = {
    "sec_xbrl": ("fmp_company", "alpha_vantage_statements"),
    "disclosure_estimates": ("fmp_company",),
    "news_ingest": ("news_ai_search",),
}
STANDBY_EVERY = dt.timedelta(hours=12)


async def run_failover(
    pool: asyncpg.Pool,
    jobs: JobsConfig,
    failing: set[str],
    now: dt.datetime,
    still_down: set[str] | None = None,
) -> dict[str, list[str]]:
    """Run the standby for each primary in `failing` (still failing after its retry), at most
    once per `STANDBY_EVERY` and never one whose own last run failed. A notice says the data is
    coming from the standby; it resolves when the primary runs again (it stays while the
    primary is in `still_down`, which defaults to `failing`)."""
    still_down = failing if still_down is None else still_down
    enabled = {name for name, s in jobs.schedules().items() if s.enabled}
    started: list[str] = []
    for primary, standbys in FAILOVER.items():
        if primary not in failing:
            continue
        for standby in standbys:
            if standby not in enabled:
                continue
            last = await pool.fetchrow(
                "select status, created_at from public.job where job_type = $1"
                " order by id desc limit 1",
                standby,
            )
            if last is not None and last["status"] == "failed":
                continue  # this standby is down too: try the next one
            if last is not None and now - last["created_at"] < STANDBY_EVERY:
                break  # it already ran (or is running) for this outage
            if await queue_job(pool, standby) is not None:
                started.append(f"{primary}->{standby}")
                await tasks.see(
                    pool,
                    "failover",
                    primary,
                    title=f"{primary} is down; {standby} is filling in",
                    detail=f"{primary} is still failing after a retry, so {standby} was run to "
                    "keep the data fresh from another provider. This resolves when "
                    f"{primary} runs again.",
                    href="/status",
                    weight=0.2,
                )
            break
    await pool.execute(
        "update public.ops_task set status = 'resolved', resolved_at = now()"
        " where kind = 'failover' and status <> 'resolved' and subject <> all($1::text[])",
        sorted(still_down),
    )
    return {"started": started}


# ------------------------------------------------------------------------------ data quality


async def check_quality(pool: asyncpg.Pool, cfg: OpsSettings, now: dt.datetime) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    cockpit = await build_cockpit(pool)
    seen: set[str] = set()
    queued: dict[str, int | None] = {}
    for issue in cockpit.issues:
        job_type = QUALITY_REPAIRS.get(issue.kind)
        if job_type is None:
            continue
        subject = f"{issue.kind}:{issue.subject}"
        seen.add(subject)
        task = await tasks.see(
            pool,
            "quality",
            subject,
            title=f"{issue.kind.replace('_', ' ')}: {issue.subject}",
            detail=issue.detail,
            href="/quality",
            weight=issue.weight,
        )

        async def refresh(job_type: str = job_type) -> str | None:
            if job_type not in queued:
                queued[job_type] = await queue_job(pool, job_type)
            job_id = queued[job_type]
            return None if job_id is None else f"queued {job_type} (job {job_id})"

        await _act(pool, task, cfg, now, can_repair=True, repair=refresh, summary=summary)
    summary["resolved"] = await tasks.settle(pool, "quality", seen)
    return summary


# ------------------------------------------------------------------------- provider blocks


async def check_blocks(pool: asyncpg.Pool, cfg: OpsSettings, now: dt.datetime) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    rows = await pool.fetch(
        "select provider, endpoint, detail, updated_at from public.provider_access"
        " where hard_blocked"
    )
    blocked: set[str] = set()
    age = dt.timedelta(days=cfg.block_reset_days)
    for r in rows:
        subject = f"{r['provider']}/{r['endpoint']}"
        blocked.add(subject)
        task = await tasks.see(
            pool,
            "provider_block",
            subject,
            title=f"{subject} is blocked",
            detail=(r["detail"] or "The provider refused repeated calls.")[:300],
            href="/status",
            weight=0.2,
        )
        # A block that is back after it was lifted keeps the attempts already spent on it.
        task = await tasks.carry_attempts(pool, task, max(age, dt.timedelta(days=30)))
        aged = cfg.block_reset_days > 0 and now - r["updated_at"] >= age

        async def lift(provider: str = r["provider"], endpoint: str = r["endpoint"]) -> str | None:
            await reset_block(pool, provider, endpoint)
            return "lifted the block to probe again"

        await _act(
            pool,
            task,
            cfg,
            now,
            can_repair=aged or task.attempts > 0,
            repair=lift,
            summary=summary,
        )
    summary["resolved"] = await tasks.settle(pool, "provider_block", blocked)
    return summary


# ----------------------------------------------------------------------- derived inputs


async def refresh_derived(pool: asyncpg.Pool, jobs: JobsConfig) -> dict[str, Any]:
    """Rebuild what the X-Ray and the fund pages derive from stored fund holdings (C2). Both
    are idempotent: a rebuild with nothing new changes nothing."""
    out: dict[str, Any] = {}
    owner_id = get_settings().owner_user_id
    if owner_id:
        out["index_weights_saved"] = await derive_benchmark_indexes(pool, owner_id)
    else:
        out["index_weights_saved"] = "skipped: OWNER_USER_ID is not set"
    portfolio = await load_portfolio(pool)
    entries = await refresh_ledger(
        pool, jobs.look_through, jobs.prices.benchmark_symbols, portfolio
    )
    out["ledger_entries"] = len(entries)
    return out


# ----------------------------------------------------------------------------------- keys


async def check_keys(pool: asyncpg.Pool, jobs: JobsConfig) -> dict[str, Any]:
    enabled = {name for name, s in jobs.schedules().items() if s.enabled}
    dismissed = {
        r["subject"]
        for r in await pool.fetch(
            "select subject from public.ops_task where kind = 'missing_key'"
            " and status = 'resolved' and last_action = 'dismissed'"
        )
    }
    missing = [k for k in keys.missing(get_settings(), enabled) if k.setting not in dismissed]
    for k in missing:
        await tasks.see(
            pool,
            "missing_key",
            k.setting,
            title=f"Add the {k.name} key",
            detail=f"Unlocks {k.unlocks}. Get a key at {k.url}, then set {k.env} where the "
            "backend runs.",
            href=k.url,
            weight=k.priority * 0.1,
            status="needs_owner",
        )
    resolved = await tasks.settle(pool, "missing_key", {k.setting for k in missing})
    return {"missing": [k.setting for k in missing], "resolved": resolved}


# --------------------------------------------------------------------------------- budget


async def check_budget(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, Any]:
    stored = await load_active(pool, "budget", BudgetConfig)
    cap = stored.body.monthly_cap_usd if stored else None
    if cap is None:
        return {"cap": None, "resolved": await tasks.settle(pool, "budget_forecast", set())}
    month = month_of(now)
    spent: Decimal = await month_spend(pool, month)
    fc = budget_forecast.forecast(month, spent, cap, now)
    if fc is None or not fc.over:
        return {
            "projected": None if fc is None else round(fc.projected, 2),
            "resolved": await tasks.settle(pool, "budget_forecast", set()),
        }
    spenders = await budget_forecast.top_spenders(pool, month)
    await tasks.see(
        pool,
        "budget_forecast",
        month,
        title="AI spend is on course to pass the monthly cap",
        detail=budget_forecast.describe(fc, spenders),
        href="/config/budget",
        weight=0.5,
        status="needs_owner",
        keep_dismissed=True,  # the subject is the month: dismissed means for this month
    )
    return {"projected": round(fc.projected, 2), "cap": cap}


# ---------------------------------------------------------------------------------- handler


class Steps:
    """Runs each check as its own job step. A check that raises is recorded as a failed step
    and the run goes on to the next one, so one broken check does not silence the rest every
    hour; the job still ends failed, naming the checks that failed."""

    def __init__(self, ctx: JobContext) -> None:
        self.ctx = ctx
        self.failed: list[str] = []

    @asynccontextmanager
    async def run(self, name: str) -> AsyncIterator[Any]:
        try:
            async with self.ctx.step(name) as step:
                yield step
        except Exception:
            logger.exception("watchdog check %s failed", name)
            self.failed.append(name)


async def watchdog_handler(ctx: JobContext) -> None:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    jobs = stored.body if stored else JobsConfig()
    cfg = jobs.ops_settings
    if not cfg.enabled:
        return
    now = dt.datetime.now(dt.UTC)
    steps = Steps(ctx)
    await _checks(ctx, steps, jobs, cfg, now)
    if steps.failed:
        raise RuntimeError(f"watchdog checks failed: {', '.join(steps.failed)}")


async def _checks(
    ctx: JobContext, steps: Steps, jobs: JobsConfig, cfg: OpsSettings, now: dt.datetime
) -> None:
    # The worker closes a dead run's row at start, but only once nobody holds its lock: in a
    # deploy's overlap the old container still holds it, then dies, and the run stays
    # "running" while catch-up skips it as already running. A run whose lock is free here has
    # no process left; failing it lets `failed_jobs` retry it.
    async with steps.run("interrupted_runs") as step:
        step.detail["failed"] = await fail_interrupted_scheduled_runs(ctx.pool)
    async with steps.run("failed_jobs") as step:
        step.detail.update(await check_failed_jobs(ctx.pool, jobs, cfg, now))
    async with steps.run("quality") as step:
        step.detail.update(await check_quality(ctx.pool, cfg, now))
    async with steps.run("provider_blocks") as step:
        step.detail.update(await check_blocks(ctx.pool, cfg, now))
    if jobs.autopilot_settings.enabled:
        async with steps.run("liveness") as step:
            step.detail["autopilot_overdue_hours"] = await liveness.check(
                ctx.pool, liveness.AUTOPILOT, now
            )
    async with steps.run("delivery") as step:
        async with httpx.AsyncClient(timeout=20.0) as http:
            step.detail["undelivered"] = await delivery.check(
                ctx.pool, now, channels_from_settings(http, get_settings())
            )
    async with steps.run("job_liveness") as step:
        step.detail["overdue"] = await liveness.check_all(ctx.pool, jobs, now)
    if cfg.refresh_derived:
        async with steps.run("derived_inputs") as step:
            step.detail.update(await refresh_derived(ctx.pool, jobs))
    if cfg.suggest_rebalance and now.hour == cfg.suggest_hour_utc:
        async with steps.run("suggestions") as step:
            step.detail.update(await suggest_rebalance(ctx.pool))
    # Every hour, not once a day: a breach the verdict job has since passed (or a rewritten
    # thesis dropped) must not stay in "Needs you" until the next morning.
    async with steps.run("thesis_breaks") as step:
        step.detail.update(await suggest_thesis_breaks(ctx.pool))
    owner_id = get_settings().owner_user_id
    if cfg.fill_segment_map and owner_id and now.hour == cfg.suggest_hour_utc:
        async with steps.run("company_inputs") as step:
            held = sorted(await ledger.holding_weights(ctx.pool))
            step.detail.update(await fill_country_maps(ctx.pool, uuid.UUID(owner_id), held))
    if cfg.tune_alerts and now.hour == cfg.suggest_hour_utc:
        async with steps.run("tune_alerts") as step:
            step.detail.update(await tune_alerts(ctx.pool, now))
            step.detail["inbox"] = await tune_inbox(ctx.pool, now)
            step.detail["notices"] = await tune_notices(ctx.pool, now)
    if cfg.anticipate_earnings and now.hour == cfg.suggest_hour_utc:
        async with steps.run("anticipate") as step:
            step.detail.update(await anticipate.announce(ctx.pool, now.date()))
    if cfg.suggest_opportunities and owner_id and now.hour == cfg.suggest_hour_utc:
        async with steps.run("opportunities") as step:
            step.detail.update(await check_opportunities(ctx.pool, uuid.UUID(owner_id)))
    if cfg.release_gate and owner_id:
        async with steps.run("release_gate") as step:
            step.detail.update(await check_release_gate(ctx.pool, owner_id, now, cfg.retry_hours))
    if cfg.draft_case_items and owner_id and now.hour == cfg.suggest_hour_utc:
        theses = await load_active(ctx.pool, "theses", ThesesConfig)
        if theses is not None:
            async with steps.run("case_items") as step:
                step.detail.update(
                    await fill_case_items(
                        ctx.pool,
                        uuid.UUID(owner_id),
                        theses.body,
                        set(await ledger.holding_weights(ctx.pool)),
                    )
                )
    if cfg.key_notices:
        async with steps.run("keys") as step:
            step.detail.update(await check_keys(ctx.pool, jobs))
    if cfg.journal_gaps:
        async with steps.run("journal_gaps") as step:
            step.detail.update(
                await journal_gaps.check_journal_gaps(
                    ctx.pool, now.date(), owner_id if cfg.autolog_trades else None
                )
            )
    if cfg.budget_forecast:
        async with steps.run("budget") as step:
            step.detail.update(await check_budget(ctx.pool, now))
