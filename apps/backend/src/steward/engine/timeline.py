"""One "what the engine did" list (autonomous engine plan, L1), read across every place the
engine or the owner leaves a trace. It is a read model, not a second ledger: each writer keeps its
own table, so a new writer shows up here by adding one branch to `SOURCES`, and a test fails if a
ledger table is missing from it.
"""

import datetime as dt
from dataclasses import dataclass

import asyncpg
from pydantic import BaseModel

# source -> the SQL branch. Every branch yields: at, source, kind, ref, summary, status,
# reversible (the owner can undo it from the app).
SOURCES: dict[str, str] = {
    "autopilot": (
        "select decided_at as at, 'autopilot' as source, kind, 'auto_decision:' || id as ref,"
        " summary, status, (status = 'applied' and revert_payload is not null) as reversible"
        " from public.auto_decision where decided_at >= $1 and status <> 'escalated'"
    ),
    "watchdog": (
        "select coalesce(resolved_at, last_seen) as at, 'watchdog' as source, kind,"
        " 'ops_task:' || id as ref, title as summary, status, false as reversible"
        " from public.ops_task where coalesce(resolved_at, last_seen) >= $1"
    ),
    "owner_action": (
        "select coalesce(decided_at, created_at) as at, 'owner_action' as source, action as kind,"
        " 'owner_action:' || id as ref, summary, status, false as reversible"
        " from public.owner_action where coalesce(decided_at, created_at) >= $1"
        " and status in ('done', 'failed')"
    ),
    "engine_change": (
        "select coalesce(decided_at, created_at) as at, 'engine_change' as source,"
        " change_kind as kind, 'engine_change:' || id as ref, hypothesis as summary, status,"
        " (status = 'promoted') as reversible"
        " from public.engine_change where coalesce(decided_at, created_at) >= $1"
    ),
    "config": (
        "select created_at as at, 'config' as source, domain as kind,"
        " 'config_version:' || id as ref, coalesce(note, 'Configuration changed') as summary,"
        " case when active then 'active' else 'superseded' end as status, true as reversible"
        " from public.config_version where created_at >= $1 and created_by is null"
        " and parent_id is not null"
    ),
}


class Entry(BaseModel):
    at: dt.datetime
    source: str
    kind: str
    ref: str
    summary: str
    status: str
    reversible: bool


@dataclass(frozen=True)
class Query:
    since: dt.datetime
    limit: int = 50
    source: str | None = None


async def recent(pool: asyncpg.Pool, q: Query) -> list[Entry]:
    names = [q.source] if q.source else list(SOURCES)
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        raise ValueError(f"Unknown engine ledger source {unknown[0]!r}")
    union = " union all ".join(f"({SOURCES[n]})" for n in names)
    rows = await pool.fetch(
        f"select * from ({union}) t order by at desc limit $2",  # noqa: S608
        q.since,
        max(1, min(q.limit, 200)),
    )
    return [Entry(**dict(r)) for r in rows]
