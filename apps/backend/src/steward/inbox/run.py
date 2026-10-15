"""The `inbox_scan` and `inbox_agent` jobs.

`inbox_scan` (scheduled) finds triggers that have no inbox item and queues one `inbox_agent`
job per trigger, at most `max_per_scan`. `inbox_agent` runs the bounded agent on one trigger
and stores one inbox item. Neither starts any other job: a proposed action waits in the item
until the owner confirms it (see api.inbox_routes).

Without a model key, or when the run fails, the item is still written from the trigger's own
text with no proposed action, so an event is never lost to a provider error.
"""

import datetime as dt
import json
from typing import Any

import asyncpg

from steward.ai import AINotConfigured, Usage
from steward.chat.owner import owner_of
from steward.chat.tools import ChatTools
from steward.config.jobs import InboxSettings, JobsConfig
from steward.config.store import load_active
from steward.inbox import agent as agent_mod
from steward.inbox import triggers
from steward.inbox.agent import AgentRequest, AgentResult, InboxAgent, LLMInboxAgent
from steward.inbox.triggers import Trigger
from steward.jobs import queue
from steward.jobs.runner import JobContext, JobFailed, JobTrigger, create_job
from steward.llm.client import create_llm
from steward.research.sources import Registry
from steward.settings import get_settings


async def build_item(
    pool: asyncpg.Pool,
    agent: InboxAgent | None,
    trigger: Trigger,
    settings: InboxSettings,
    now: dt.datetime,
    owner_id: str | None,
) -> dict[str, Any]:
    """Run the agent on one trigger and return the item as it would be stored. Writes nothing."""
    registry = Registry()
    tools = ChatTools(pool, registry, None, None, owner_id=owner_id)
    result = AgentResult(None, Usage(model=settings.model), note="no AI provider key is set")
    if agent is not None:
        result = await agent.run(
            AgentRequest(
                trigger=trigger,
                today=now.date().isoformat(),
                model=settings.model,
                effort=settings.effort,
                max_steps=settings.max_steps,
                caller=tools.call,
                registry=registry,
            )
        )
    body: dict[str, Any] = {
        "trigger": {"kind": trigger.kind, "ref": trigger.ref, "text": trigger.material},
        "statements": [],
        "touches": [],
        "assessment": "",
        "nothing_to_do": False,
        "suggested_note": None,
        "note_status": None,
        "sources": [],
        "dropped_statements": 0,
        "action_dropped": None,
        "agent": {
            "model": settings.model,
            "steps": result.steps,
            "tool_calls": result.tool_calls,
            "ms": result.ms,
            "note": result.note,
        },
    }
    headline, action = trigger.headline, None
    if result.draft is not None:
        checked = await agent_mod.check_draft(pool, result.draft, trigger, registry)
        headline = result.draft.headline.strip()[:200] or trigger.headline
        action = checked.action
        body.update(
            statements=[s.model_dump() for s in checked.statements],
            touches=result.draft.touches[:10],
            assessment=result.draft.assessment[:800],
            nothing_to_do=result.draft.nothing_to_do,
            suggested_note=(result.draft.suggested_note or "").strip()[:500] or None,
            note_status=("suggested" if (result.draft.suggested_note or "").strip() else None),
            dropped_statements=checked.dropped,
            action_dropped=checked.action_dropped,
            sources=[s.model_dump() for s in agent_mod.cited_sources(registry, checked.statements)],
        )
        if not checked.statements:
            body["assessment"] = (
                body["assessment"] or "The run found nothing it could cite."
            ) + " The trigger text above is the only record."
    else:
        body["assessment"] = f"No agent assessment: {result.note or 'the run produced nothing'}."
    return {
        "headline": headline,
        "body": body,
        "action": action,
        "status": "read" if body["nothing_to_do"] else "new",
        "usage": result.usage,
    }


async def store_item(
    pool: asyncpg.Pool, trigger: Trigger, item: dict[str, Any], job_id: int | None
) -> int | None:
    """Insert the item; None when the trigger already has one."""
    action = item["action"]
    row = await pool.fetchval(
        "insert into public.inbox_item (trigger_key, trigger_kind, trigger_ref, ticker, severity,"
        " headline, body, status, proposed_action, action_status, job_id)"
        " values ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9::jsonb, $10, $11)"
        " on conflict (trigger_key) do nothing returning id",
        trigger.key,
        trigger.kind,
        trigger.ref,
        trigger.ticker,
        trigger.severity,
        item["headline"],
        json.dumps(item["body"], default=str),
        item["status"],
        json.dumps(action) if action else None,
        "proposed" if action else None,
        job_id,
    )
    return int(row) if row is not None else None


async def _settings(ctx: JobContext) -> InboxSettings:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    return (stored.body if stored else JobsConfig()).inbox_settings


async def queue_runs(
    pool: asyncpg.Pool,
    found: list[Trigger],
    trigger: JobTrigger,
    requested_by: str | None = None,
) -> list[str]:
    """Queue one `inbox_agent` job per trigger; the keys queued. A trigger that already has
    an item or a run queued is skipped, so a repeated call cannot double-run an event."""
    queued: list[str] = []
    for t in found:
        async with pool.acquire() as conn, conn.transaction():
            # One transaction per trigger, serialised on its key, so two callers cannot both
            # pass the check.
            await conn.execute("select pg_advisory_xact_lock(hashtext($1))", t.key)
            taken = await conn.fetchval(
                "select exists (select 1 from public.inbox_item where trigger_key = $1)"
                " or exists (select 1 from public.job where job_type = 'inbox_agent'"
                " and status in ('queued', 'waiting', 'running')"
                " and params ->> 'trigger_key' = $1)",
                t.key,
            )
            if taken:
                continue
            job_id = await create_job(
                conn,
                "inbox_agent",
                trigger,
                requested_by=requested_by,
                params={
                    "trigger_key": t.key,
                    "trigger_kind": t.kind,
                    "trigger_ref": t.ref,
                    "ticker": t.ticker,
                },
            )
            await queue.enqueue(conn, job_id, "inbox_agent")
        queued.append(t.key)
    return queued


async def inbox_scan_handler(ctx: JobContext) -> None:
    cfg = await _settings(ctx)
    async with ctx.step("scan") as step:
        if not cfg.enabled:
            step.detail["skipped"] = "inbox_settings.enabled is off"
            return
        found = await triggers.pending(
            ctx.pool, cfg.lookback_days, cfg.max_per_scan, cfg.min_severity
        )
        queued = await queue_runs(ctx.pool, found, "event")
        step.detail.update(queued=queued, found=len(found))


async def inbox_agent_handler(ctx: JobContext) -> None:
    cfg = await _settings(ctx)
    kind, ref = ctx.params.get("trigger_kind"), ctx.params.get("trigger_ref")
    if not isinstance(kind, str) or not isinstance(ref, int):
        raise JobFailed("needs trigger_kind and trigger_ref")
    async with ctx.step("load_trigger") as step:
        trigger = await triggers.load(ctx.pool, kind, ref)
        if trigger is None:
            raise JobFailed(f"no {kind} {ref}")
        step.detail.update(key=trigger.key, ticker=trigger.ticker)
    agent: InboxAgent | None = None
    try:
        agent = LLMInboxAgent(create_llm(get_settings()))
    except AINotConfigured:
        agent = None
    async with ctx.step("agent") as step:
        item = await build_item(
            ctx.pool, agent, trigger, cfg, dt.datetime.now(dt.UTC), await owner_of(ctx.pool)
        )
        step.usage = item["usage"].step_usage()
        step.detail.update(
            statements=len(item["body"]["statements"]),
            proposed=item["action"]["kind"] if item["action"] else None,
            note=item["body"]["agent"]["note"],
        )
    async with ctx.step("store") as step:
        item_id = await store_item(ctx.pool, trigger, item, ctx.job_id)
        step.detail["inbox_item_id"] = item_id
