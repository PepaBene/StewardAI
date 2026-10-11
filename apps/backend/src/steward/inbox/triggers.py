"""What starts an inbox run: a new escalation note, or a thesis-breach alert.

A trigger is identified by `kind:ref` (the report id or the alert id), which is also the unique
key of its inbox item, so a rescan or a retried job cannot leave two items for one event.
Both kinds are records the app already wrote; their text is untrusted data to the agent (it
came from news and web pages), so it is neutralised before a model reads it.
"""

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Literal

import asyncpg

from steward.research.untrusted import neutralise

TriggerKind = Literal["escalation", "alert"]
KINDS: tuple[TriggerKind, ...] = ("escalation", "alert")
MATERIAL_CHARS = 4000


@dataclass(frozen=True)
class Trigger:
    kind: TriggerKind
    ref: int
    ticker: str | None
    severity: int | None
    headline: str
    material: str
    created_at: dt.datetime

    @property
    def key(self) -> str:
        return trigger_key(self.kind, self.ref)


def trigger_key(kind: str, ref: int) -> str:
    return f"{kind}:{ref}"


async def pending(pool: asyncpg.Pool, days: int, limit: int, min_severity: int) -> list[Trigger]:
    """Triggers from the last `days` with no inbox item and no run already queued, newest first."""
    rows = await pool.fetch(
        """
        select 'escalation' as kind, r.id as ref from public.report r
         where r.type = 'escalation' and r.ticker is not null
           and r.created_at > now() - make_interval(days => $1)
           and not exists (select 1 from public.inbox_item i
                            where i.trigger_key = 'escalation:' || r.id)
           and not exists (select 1 from public.job j
                            where j.job_type = 'inbox_agent'
                              and j.status in ('queued', 'waiting', 'running')
                              and j.params ->> 'trigger_key' = 'escalation:' || r.id)
        union all
        select 'alert', a.id from public.alert a
         where a.kind = 'thesis_breach' and a.ticker is not null and a.severity >= $2
           and a.created_at > now() - make_interval(days => $1)
           and not exists (select 1 from public.inbox_item i where i.trigger_key = 'alert:' || a.id)
           and not exists (select 1 from public.job j
                            where j.job_type = 'inbox_agent'
                              and j.status in ('queued', 'waiting', 'running')
                              and j.params ->> 'trigger_key' = 'alert:' || a.id)
        """,
        days,
        min_severity,
    )
    found = [t for r in rows if (t := await load(pool, r["kind"], r["ref"])) is not None]
    found.sort(key=lambda t: t.created_at, reverse=True)
    return found[:limit]


async def load(pool: asyncpg.Pool, kind: str, ref: int) -> Trigger | None:
    if kind == "escalation":
        return await _escalation(pool, ref)
    if kind == "alert":
        return await _alert(pool, ref)
    return None


async def _escalation(pool: asyncpg.Pool, ref: int) -> Trigger | None:
    row = await pool.fetchrow(
        "select id, ticker, body, created_at from public.report"
        " where id = $1 and type = 'escalation'",
        ref,
    )
    if row is None:
        return None
    body: dict[str, Any] = json.loads(row["body"]) if isinstance(row["body"], str) else row["body"]
    sentences = " ".join(s.get("text", "") for s in body.get("sentences", []))
    kpis = "; ".join(f"{k.get('id')}: {k.get('description')}" for k in body.get("kpis", []))
    material = (
        f"Escalation note on {row['ticker']} (report {ref}), event type "
        f"{body.get('event_type') or 'unknown'}, severity {body.get('severity')}.\n"
        f"Headline: {body.get('headline', '')}\n{sentences}\n"
        f"Thesis link: {body.get('thesis_link', 'none')}. KPIs touched: {kpis or 'none'}. "
        f"Exit conditions touched: {'; '.join(body.get('exit_conditions', [])) or 'none'}.\n"
        f"Rationale: {body.get('rationale', '')}"
    )
    return Trigger(
        "escalation",
        ref,
        row["ticker"],
        body.get("severity"),
        str(body.get("headline", "Escalation"))[:200],
        neutralise(material)[:MATERIAL_CHARS],
        row["created_at"],
    )


async def _alert(pool: asyncpg.Pool, ref: int) -> Trigger | None:
    row = await pool.fetchrow(
        "select id, ticker, severity, message, created_at from public.alert"
        " where id = $1 and kind = 'thesis_breach'",
        ref,
    )
    if row is None:
        return None
    return Trigger(
        "alert",
        ref,
        row["ticker"],
        row["severity"],
        str(row["message"]).splitlines()[0][:200],
        neutralise(f"Thesis-breach alert on {row['ticker']}:\n{row['message']}")[:MATERIAL_CHARS],
        row["created_at"],
    )
