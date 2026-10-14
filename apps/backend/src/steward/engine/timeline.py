"""One "what the engine did" list (autonomous engine plan, L1), read across every place the
engine or the owner leaves a trace. It is a read model, not a second ledger: each writer keeps its
own table, so a new writer shows up here by adding one branch to `SOURCES`. A test lists every
module that writes autonomously and fails when one is not mapped to a source here.

Each entry says how to undo it when it can be: `undo_action` is an owner-action name from the
registry and `undo_args` its arguments, so a screen or the chat undoes it the same way.
"""

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

import asyncpg
from pydantic import BaseModel, computed_field

# source -> the SQL branch. Every branch yields: at, source, kind, ref, summary, status,
# undo_action, undo_args (both null when the owner cannot undo it from the app).
SOURCES: dict[str, str] = {
    "autopilot": (
        "select decided_at as at, 'autopilot' as source, kind, 'auto_decision:' || id as ref,"
        " summary, status,"
        " case when status = 'applied' and revert_payload is not null"
        "  then 'revert_decision' end as undo_action,"
        " case when status = 'applied' and revert_payload is not null"
        "  then jsonb_build_object('decision_id', id) end as undo_args"
        " from public.auto_decision where decided_at >= $1 and status <> 'escalated'"
    ),
    "watchdog": (
        "select coalesce(resolved_at, last_seen) as at, 'watchdog' as source, kind,"
        " 'ops_task:' || id as ref, title as summary, status,"
        " null::text as undo_action, null::jsonb as undo_args"
        " from public.ops_task where coalesce(resolved_at, last_seen) >= $1"
    ),
    "owner_action": (
        "select coalesce(decided_at, created_at) as at, 'owner_action' as source, action as kind,"
        " 'owner_action:' || id as ref, summary, status,"
        " null::text as undo_action, null::jsonb as undo_args"
        " from public.owner_action where coalesce(decided_at, created_at) >= $1"
        " and status in ('done', 'failed')"
    ),
    "engine_change": (
        "select coalesce(decided_at, created_at) as at, 'engine_change' as source,"
        " change_kind as kind, 'engine_change:' || id as ref, hypothesis as summary, status,"
        " case when status = 'promoted' then 'roll_back_change' end as undo_action,"
        " case when status = 'promoted' then jsonb_build_object('change_id', id) end as undo_args"
        " from public.engine_change where coalesce(decided_at, created_at) >= $1"
    ),
    # Versions the engine wrote itself (no author). Imports and rollbacks are not self-changes.
    "config": (
        "select created_at as at, 'config' as source, domain as kind,"
        " 'config_version:' || id as ref, coalesce(note, 'Configuration changed') as summary,"
        " case when active then 'active' else 'superseded' end as status,"
        " case when active then 'rollback_config' end as undo_action,"
        " case when active then jsonb_build_object('domain', domain, 'version_id', parent_id)"
        " end as undo_args"
        " from public.config_version where created_at >= $1 and created_by is null"
        " and parent_id is not null and coalesce(note, '') not like 'Imported%'"
        " and coalesce(note, '') not like 'Rolled back%'"
    ),
    "override": (
        "select created_at as at, 'override' as source, subject_kind as kind,"
        " 'owner_override:' || id as ref,"
        " 'You ' || replace(action, '_', ' ') || ' ' || subject_ref"
        " || coalesce(' (' || replace(reason_code, '_', ' ') || ')', '') as summary,"
        " action as status, null::text as undo_action, null::jsonb as undo_args"
        " from public.owner_override where created_at >= $1"
    ),
    "delivery": (
        "select coalesce(sent_at, created_at) as at, 'delivery' as source, channel as kind,"
        " 'alert:' || id as ref, left(message, 160) as summary, status,"
        " null::text as undo_action, null::jsonb as undo_args"
        " from public.alert where coalesce(sent_at, created_at) >= $1"
        " and status in ('sent', 'failed')"
    ),
    "trigger": (
        "select created_at as at, 'trigger' as source, kind, 'engine_trigger:' || id as ref,"
        " action || ': ' || reason || coalesce(' (held back: ' || skip_reason || ')', '')"
        " as summary, status, null::text as undo_action, null::jsonb as undo_args"
        " from public.engine_trigger where created_at >= $1"
    ),
    "journal": (
        "select created_at as at, 'journal' as source, 'trade entry' as kind,"
        " 'decision_journal:' || id as ref,"
        " 'Logged a ' || decision || ' of ' || coalesce(ticker, 'the portfolio')"
        " || ' from your broker sync' as summary, 'logged' as status,"
        " null::text as undo_action, null::jsonb as undo_args"
        " from public.decision_journal where created_at >= $1 and snapshot->>'origin' = 'auto'"
        " union all"
        " select created_at, 'journal', 'switch entry', 'switch_log:' || id,"
        " 'Logged a switch from ' || sold_ticker || ' to ' || bought_ticker"
        " || ' from your broker sync', 'logged', null::text, null::jsonb"
        " from public.switch_log where created_at >= $1 and snapshot->>'origin' = 'auto'"
    ),
}


class Entry(BaseModel):
    at: dt.datetime
    source: str
    kind: str
    ref: str
    summary: str
    status: str
    undo_action: str | None = None
    undo_args: dict[str, Any] | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reversible(self) -> bool:
        return self.undo_action is not None


@dataclass(frozen=True)
class Query:
    since: dt.datetime
    limit: int = 50
    source: str | None = None
    kind: str | None = None
    offset: int = 0


async def recent(pool: asyncpg.Pool, q: Query) -> list[Entry]:
    names = [q.source] if q.source else list(SOURCES)
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        raise ValueError(f"Unknown engine ledger source {unknown[0]!r}")
    union = " union all ".join(f"({SOURCES[n]})" for n in names)
    rows = await pool.fetch(
        f"select * from ({union}) t where ($3::text is null or kind = $3)"  # noqa: S608
        " order by at desc limit $2 offset $4",
        q.since,
        max(1, min(q.limit, 200)),
        q.kind,
        max(0, min(q.offset, 100_000)),
    )
    out = []
    for r in rows:
        data = dict(r)
        if isinstance(data["undo_args"], str):
            data["undo_args"] = json.loads(data["undo_args"])
        out.append(Entry(**data))
    return out
