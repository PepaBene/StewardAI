"""Replay past events through the inbox agent without storing anything (AG4's check).

    python -m steward.inbox.replay --limit 10

Takes the newest escalation notes and thesis-breach alerts, runs the agent on each exactly as
the job would, prints the items it would have left, and checks that the replay wrote nothing:
no inbox item, no job. Whether the items are *useful* is the owner's reading of the printout.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from typing import Any

import asyncpg

from steward.chat.owner import owner_of
from steward.config.jobs import InboxSettings, JobsConfig
from steward.config.store import load_active
from steward.inbox import triggers
from steward.inbox.agent import InboxAgent
from steward.inbox.run import build_item


async def _counts(pool: asyncpg.Pool) -> tuple[int, int]:
    return (
        int(await pool.fetchval("select count(*) from public.inbox_item")),
        int(await pool.fetchval("select count(*) from public.job")),
    )


async def recent_triggers(pool: asyncpg.Pool, limit: int) -> list[triggers.Trigger]:
    rows = await pool.fetch(
        "select 'escalation' as kind, id, created_at from public.report where type = 'escalation'"
        " and ticker is not null union all select 'alert', id, created_at from public.alert"
        " where kind = 'thesis_breach' and ticker is not null"
        " union all select 'disclosure', id, known_at from public.disclosure_alert"
        " union all select 'breach', id, first_seen from public.portfolio_breach"
        " union all select 'verdict', id, created_at from public.verdict where status = 'breach'"
        " order by created_at desc limit $1",
        limit,
    )
    found = [await triggers.load(pool, r["kind"], r["id"]) for r in rows]
    return [t for t in found if t is not None]


async def replay(
    pool: asyncpg.Pool,
    agent: InboxAgent | None,
    limit: int = 10,
    settings: InboxSettings | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    stored = await load_active(pool, "jobs", JobsConfig)
    cfg = settings or (stored.body if stored else JobsConfig()).inbox_settings
    owner = await owner_of(pool)
    before = await _counts(pool)
    items = []
    for trigger in await recent_triggers(pool, limit):
        item = await build_item(pool, agent, trigger, cfg, now or dt.datetime.now(dt.UTC), owner)
        items.append(
            {
                "trigger": trigger.key,
                "ticker": trigger.ticker,
                "headline": item["headline"],
                "statements": len(item["body"]["statements"]),
                "dropped": item["body"]["dropped_statements"],
                "touches": item["body"]["touches"],
                "assessment": item["body"]["assessment"],
                "proposed_action": item["action"],
                "action_dropped": item["body"]["action_dropped"],
                "agent_note": item["body"]["agent"]["note"],
            }
        )
    after = await _counts(pool)
    return {
        "events": len(items),
        "with_cited_statements": sum(1 for i in items if i["statements"]),
        "with_proposed_action": sum(1 for i in items if i["proposed_action"]),
        "wrote_nothing": before == after,
        "items": items,
    }


async def _main(limit: int) -> dict[str, Any]:
    from steward.ai import AINotConfigured
    from steward.db import create_pool
    from steward.inbox.agent import LLMInboxAgent
    from steward.llm.client import create_llm
    from steward.settings import get_settings

    settings = get_settings()
    pool = await create_pool(settings)
    try:
        agent: InboxAgent | None
        try:
            agent = LLMInboxAgent(create_llm(settings))
        except AINotConfigured:
            agent = None
        return await replay(pool, agent, limit)
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    result = asyncio.run(_main(args.limit))
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["wrote_nothing"] else 1


if __name__ == "__main__":
    sys.exit(main())
