"""Job handlers for the Phase 8 verification tooling (registered by the orchestrator).

    shadow_snapshot     records the PREVIOUS UTC day's per-provider measurements each time it
                        runs (a run on day D measures D-1; day D is measured on D+1).  Its
                        default schedule is daily at 23:50 and it ships DISABLED: the 30-day
                        shadow run does not start until the owner enables it.  `days_back`
                        backfills earlier days; backfilled rows are marked as such (their
                        coverage columns are current-state) and never count toward sign-off.
    shadow_report       builds and stores the sign-off report for the 30 days ending yesterday
                        (default: Mondays 08:00, DISABLED until the run is started).  A
                        `window_days` below 30 produces an informational report that can never
                        be signed off.
    raw_retention       daily: blanks expired raw observation payloads and macro payloads
                        (normalized rows stay) and DELETES provider_fetch_audit rows older than
                        the retention horizon; open conflicts keep their raw evidence.
    zero_cost_gate      daily: audits the endpoint ledger and the budgets the collectors and the
                        job configuration pass to the governor; reads only, calls no provider.
                        The job fails on any violation, never on a warning.

Parameters come from `ctx.params` (optional): `date` (ISO, UTC day), `days_back`, `window_days`,
`dry_run` (a real boolean or the strings true/false/1/0/yes/no/on/off).
"""

from __future__ import annotations

import datetime as dt

from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.jobs.runner import JobContext, JobFailed
from steward.verify_sources.lifecycle import RetentionPolicy, prune_raw_payloads
from steward.verify_sources.shadow import (
    WINDOW_DAYS,
    backfill_shadow_snapshots,
    generate_shadow_report,
    record_shadow_snapshot,
)
from steward.verify_sources.zero_cost import audit_zero_cost


def parse_flag(value: object, *, default: bool = False) -> bool:
    """A job parameter as a boolean.  ``bool("false")`` is True in Python, so a flag that came
    from JSON text or a command line must be parsed, not truthiness-tested."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    raise JobFailed(f"cannot read {value!r} as a boolean parameter")


def _yesterday() -> dt.date:
    return dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)


def _day(ctx: JobContext) -> dt.date:
    raw = ctx.params.get("date")
    return dt.date.fromisoformat(str(raw)) if raw else _yesterday()


async def shadow_snapshot_handler(ctx: JobContext) -> None:
    day = _day(ctx)
    days_back = int(ctx.params.get("days_back", 0))
    async with ctx.step("snapshot") as step:
        if days_back > 0:
            recorded = await backfill_shadow_snapshots(ctx.pool, day, days_back + 1)
        else:
            recorded = await record_shadow_snapshot(ctx.pool, day)
        step.detail.update(day=day.isoformat(), days_back=days_back, provider_rows=recorded)


async def shadow_report_handler(ctx: JobContext) -> None:
    end = _day(ctx)
    window = int(ctx.params.get("window_days", WINDOW_DAYS))
    async with ctx.step("report") as step:
        report = await generate_shadow_report(ctx.pool, end, window_days=window)
        step.detail.update(
            window_end=end.isoformat(),
            window_days=window,
            days_observed=report["days_observed"],
            real_daily_snapshots=report["real_daily_snapshots"],
            sign_off_ready=report["sign_off_ready"],
            providers=len(report["providers"]),
            blockers=report["blockers"],
        )


async def raw_retention_handler(ctx: JobContext) -> None:
    overrides = {
        k: int(v) for k, v in ctx.params.items() if k in RetentionPolicy.__dataclass_fields__
    }
    policy = RetentionPolicy(**overrides)
    async with ctx.step("prune") as step:
        counts = await prune_raw_payloads(
            ctx.pool, policy, dry_run=parse_flag(ctx.params.get("dry_run"))
        )
        step.detail.update(counts)


async def zero_cost_gate_handler(ctx: JobContext) -> None:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    providers = (stored.body if stored else JobsConfig()).providers
    async with ctx.step("audit") as step:
        report = await audit_zero_cost(ctx.pool, providers=providers)
        step.detail.update(report.to_json())
    if not report.passed:
        raise JobFailed(
            "zero-cost gate violations: "
            + "; ".join(f"{f.provider}/{f.endpoint}: {f.code}" for f in report.violations)
        )
