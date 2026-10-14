"""The autopilot jobs: judge every queue, and calibrate which kinds may act alone."""

import datetime as dt
import logging

from steward.ai import AINotConfigured
from steward.autopilot import ledger, reverify
from steward.autopilot.calibration import audit, calibrate, review_from_outcomes
from steward.autopilot.judge import effective_mode, expire_stale, run_kind
from steward.autopilot.kinds import all_kinds
from steward.autopilot.lessons import lessons_for, relearn
from steward.autopilot.research_trigger import queue_stale_research
from steward.autopilot.thesis_upkeep import (
    announce_drafts,
    queue_thesis_drafts,
    refresh_review_dates,
)
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.engine import audit as engine_audit
from steward.engine import challenger, override_watch
from steward.jobs.runner import JobContext
from steward.llm.cache import ResultCache
from steward.llm.client import create_llm
from steward.llm.routing import RoutingClient, routing_client
from steward.ops import liveness
from steward.settings import get_settings

logger = logging.getLogger("steward.autopilot")


async def _settings(ctx: JobContext) -> JobsConfig:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    return stored.body if stored else JobsConfig()


def _client(ctx: JobContext) -> RoutingClient | None:
    """None without a configured model: rules still run, nothing else is judged."""
    try:
        return routing_client(create_llm(get_settings()), ResultCache(ctx.pool, "autopilot"))
    except AINotConfigured:
        return None


async def autopilot_handler(ctx: JobContext) -> None:
    jobs = await _settings(ctx)
    cfg = jobs.autopilot_settings
    if not cfg.enabled:
        return
    if jobs.ops_settings.enabled:
        # The watchdog checks this job; this job checks the watchdog.
        async with ctx.step("liveness") as step:
            step.detail["watchdog_overdue_hours"] = await liveness.check(
                ctx.pool, liveness.WATCHDOG, dt.datetime.now(dt.UTC)
            )
    overrides = await ledger.mode_overrides(ctx.pool)
    learned = await ledger.learned_thresholds(ctx.pool)
    client = _client(ctx)
    budget = {"calls": cfg.max_calls_per_run}
    for name, kind in all_kinds().items():
        policy = cfg.policy(name)
        mode = effective_mode(policy.mode, overrides.get(name))
        async with ctx.step(name) as step:
            # An escalation the owner (or another job) already settled leaves the list.
            refs = [e.subject_ref for e in await ledger.escalations(ctx.pool) if e.kind == name]
            open_now = await kind.is_open(ctx.pool, refs) if refs else set()
            for ref in refs:
                if ref not in open_now:
                    await ledger.close_escalation(ctx.pool, name, ref)
            done = await run_kind(
                ctx.pool,
                client,
                kind,
                cfg,
                mode,
                budget,
                lessons=await lessons_for(ctx.pool, kind),
                learned_floor=learned.get(name),
            )
            step.usage = done.pop("usage").step_usage()
            done.pop("usage_second", None)
            done["expired"] = await expire_stale(ctx.pool, kind, policy, mode)
            step.detail.update(done)
    if cfg.research_stale:
        async with ctx.step("stale_research") as step:
            step.detail.update(
                await queue_stale_research(
                    ctx.pool,
                    min_days=cfg.research_min_days,
                    max_per_run=cfg.research_max_per_run,
                )
            )
    if cfg.draft_theses:
        async with ctx.step("thesis_drafts") as step:
            step.detail.update(
                await queue_thesis_drafts(
                    ctx.pool, max_per_run=cfg.draft_max_per_run, min_days=cfg.draft_min_days
                )
            )
            step.detail["announced"] = await announce_drafts(ctx.pool)
    if cfg.refresh_review_dates:
        async with ctx.step("review_dates") as step:
            step.detail.update(await refresh_review_dates(ctx.pool, days=cfg.review_extend_days))


async def autopilot_calibration_handler(ctx: JobContext) -> None:
    cfg = (await _settings(ctx)).autopilot_settings
    if not cfg.enabled:
        return
    now = dt.datetime.now(dt.UTC)
    kinds = all_kinds()
    async with ctx.step("owner_outcomes") as step:
        step.detail["reviewed"] = await review_from_outcomes(ctx.pool, kinds, now)
    client = _client(ctx)
    if client is not None and cfg.audit_per_run:
        async with ctx.step("audit") as step:
            done = await audit(ctx.pool, client, kinds, cfg, now)
            step.usage = done.pop("usage").step_usage()
            step.detail.update(done)
    async with ctx.step("learn") as step:
        configured = {name: cfg.policy(name).min_confidence for name in kinds}
        step.detail["floors"] = await relearn(ctx.pool, kinds, configured, now)
    async with ctx.step("reward_audit") as step:
        step.detail["flagged"] = await engine_audit.audit(ctx.pool, list(kinds), now)
    async with ctx.step("change_rollbacks") as step:
        step.detail.update(await challenger.check_rollbacks(ctx.pool, now))
    async with ctx.step("override_patterns") as step:
        in_force = await ledger.learned_thresholds(ctx.pool)
        floors = {
            name: max(cfg.policy(name).min_confidence, in_force.get(name, 0.0)) for name in kinds
        }
        step.detail.update(await override_watch.act(ctx.pool, floors, now))
    async with ctx.step("challengers") as step:
        step.detail.update(await challenger.settle_floors(ctx.pool, now))
    async with ctx.step("reverify") as step:  # runs weekly; the step says "skipped" otherwise
        step.detail.update(await reverify.sweep(ctx.pool, kinds, now))
    if cfg.auto_calibrate:
        async with ctx.step("calibrate") as step:
            step.detail["moves"] = await calibrate(ctx.pool, kinds, cfg, now)
