"""Job handlers for the Phase 8 verification tooling (registered by the orchestrator).

    shadow_snapshot     daily: record yesterday's per-provider measurements (and optionally
                        backfill `days_back` earlier days)
    shadow_report       weekly / on demand: build and store the sign-off report
    raw_retention       daily: drop expired raw payloads, keep normalized observations
    zero_cost_gate      daily: audit the endpoint manifest; the job fails on any violation

Parameters come from `ctx.params` (optional): `date` (ISO, UTC day), `days_back`, `window_days`,
`dry_run`.
"""

from __future__ import annotations

import datetime as dt

from steward.jobs.runner import JobContext, JobFailed
from steward.verify_sources.lifecycle import RetentionPolicy, prune_raw_payloads
from steward.verify_sources.shadow import (
    WINDOW_DAYS,
    backfill_shadow_snapshots,
    generate_shadow_report,
    record_shadow_snapshot,
)
from steward.verify_sources.zero_cost import audit_zero_cost


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
            days_observed=report["days_observed"],
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
        counts = await prune_raw_payloads(ctx.pool, policy, dry_run=bool(ctx.params.get("dry_run")))
        step.detail.update(counts)


async def zero_cost_gate_handler(ctx: JobContext) -> None:
    async with ctx.step("audit") as step:
        report = await audit_zero_cost(ctx.pool)
        step.detail.update(report.to_json())
    if not report.passed:
        raise JobFailed(
            "zero-cost gate violations: "
            + "; ".join(f"{f.provider}/{f.endpoint}: {f.code}" for f in report.violations)
        )
