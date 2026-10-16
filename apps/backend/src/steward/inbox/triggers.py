"""What starts an inbox run: an escalation note, a thesis-breach alert, an open disclosure alert,
an open portfolio breach or a thesis KPI verdict that came out as a breach.

A trigger is identified by `kind:ref` (the record's id), which is also the unique key of its
inbox item, so a rescan or a retried job cannot leave two items for one event. All five kinds are
records the app already wrote; their text is untrusted data to the agent (it came from news and
web pages), so it is neutralised before a model reads it.
"""

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Literal

import asyncpg

from steward.earnings.verdicts import is_current
from steward.research.untrusted import neutralise

TriggerKind = Literal["escalation", "alert", "disclosure", "breach", "verdict"]
KINDS: tuple[TriggerKind, ...] = ("escalation", "alert", "disclosure", "breach", "verdict")
MATERIAL_CHARS = 4000
BACKLOG_DAYS = 60


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


# Every event that may start a run: (kind, ref, created_at). $1 is the severity floor. A kind
# that has no severity is not filtered by it.
EVENTS_SQL = f"""
        select 'escalation' as kind, r.id as ref, r.created_at from public.report r
         where r.type = 'escalation' and r.ticker is not null
        union all
        select 'alert', a.id, a.created_at from public.alert a
         where a.kind = 'thesis_breach' and a.ticker is not null and a.severity >= $1
        union all
        select 'disclosure', d.id, d.known_at from public.disclosure_alert d
         where d.acknowledged_at is null
        union all
        select 'breach', b.id, b.first_seen from public.portfolio_breach b
         where b.cleared_at is null and b.severity >= $1
        union all
        select 'verdict', v.id, v.created_at from public.verdict v
         where v.status = 'breach' and {is_current("v")}
           and not exists (select 1 from public.alert a
                            where a.kind = 'thesis_breach' and a.ticker = v.ticker
                              and a.created_at between v.created_at - interval '2 days'
                                                   and v.created_at + interval '2 days')
        """  # noqa: S608

# Events with no inbox item and no run already queued or running.
OPEN_SQL = f"""
        select e.kind, e.ref, e.created_at from ({EVENTS_SQL}) e
         where not exists (select 1 from public.inbox_item i
                            where i.trigger_key = e.kind || ':' || e.ref)
           and not exists (select 1 from public.job j
                            where j.job_type = 'inbox_agent'
                              and j.status in ('queued', 'waiting', 'running')
                              and j.params ->> 'trigger_key' = e.kind || ':' || e.ref)
        """  # noqa: S608 (module constants)


async def pending(pool: asyncpg.Pool, days: int, limit: int, min_severity: int) -> list[Trigger]:
    """Open events from the last `days`, newest first."""
    rows = await pool.fetch(
        f"select kind, ref from ({OPEN_SQL}) o"  # noqa: S608 (module constants)
        " where created_at > now() - make_interval(days => $2)",
        min_severity,
        days,
    )
    found = [t for r in rows if (t := await load(pool, r["kind"], r["ref"])) is not None]
    found.sort(key=lambda t: t.created_at, reverse=True)
    return found[:limit]


async def missed(
    pool: asyncpg.Pool, lookback_days: int, min_severity: int, limit: int = 50
) -> list[Trigger]:
    """Open events older than the scan's lookback (up to BACKLOG_DAYS): the ones no scan will
    pick up. Newest first."""
    rows = await pool.fetch(
        f"select kind, ref from ({OPEN_SQL}) o"  # noqa: S608 (module constants)
        " where created_at <= now() - make_interval(days => $2)"
        " and created_at > now() - make_interval(days => $3)"
        " order by created_at desc limit $4",
        min_severity,
        lookback_days,
        BACKLOG_DAYS,
        limit,
    )
    found = [t for r in rows if (t := await load(pool, r["kind"], r["ref"])) is not None]
    return sorted(found, key=lambda t: t.created_at, reverse=True)


async def missed_count(pool: asyncpg.Pool, lookback_days: int, min_severity: int) -> int:
    """How many open events are older than the scan's lookback (up to BACKLOG_DAYS)."""
    return int(
        await pool.fetchval(
            f"select count(*) from ({OPEN_SQL}) o"  # noqa: S608 (module constants)
            " where created_at <= now() - make_interval(days => $2)"
            " and created_at > now() - make_interval(days => $3)",
            min_severity,
            lookback_days,
            BACKLOG_DAYS,
        )
    )


async def load(pool: asyncpg.Pool, kind: str, ref: int) -> Trigger | None:
    loaders = {
        "escalation": _escalation,
        "alert": _alert,
        "disclosure": _disclosure,
        "breach": _breach,
        "verdict": _verdict,
    }
    loader = loaders.get(kind)
    return await loader(pool, ref) if loader else None


async def _breach(pool: asyncpg.Pool, ref: int) -> Trigger | None:
    row = await pool.fetchrow(
        "select id, key, kind, ticker, severity, detail, observed, limit_value, first_seen"
        " from public.portfolio_breach where id = $1",
        ref,
    )
    if row is None:
        return None
    what = {"hard_limit": "hard limit", "band": "target band"}.get(row["kind"], "strategy rule")
    if str(row["key"]).startswith("risk:"):
        what = "risk limit"
    figures = ""
    if row["observed"] is not None and row["limit_value"] is not None:
        figures = f" Observed {row['observed']} against a limit of {row['limit_value']}."
    return Trigger(
        "breach",
        ref,
        row["ticker"],
        row["severity"],
        f"{what.capitalize()} breached: {row['key']}"[:200],
        neutralise(f"Portfolio {what} breach ({row['key']}): {row['detail']}{figures}")[
            :MATERIAL_CHARS
        ],
        row["first_seen"],
    )


async def _verdict(pool: asyncpg.Pool, ref: int) -> Trigger | None:
    row = await pool.fetchrow(
        "select id, ticker, kpi_id, metric, kpi_period, fiscal_year, fiscal_period, observed_value,"  # noqa: S608
        " operator, threshold, threshold_unit, rationale, created_at"
        f" from public.verdict v where id = $1 and status = 'breach' and {is_current('v')}",
        ref,
    )
    if row is None:
        return None
    period = f"{row['fiscal_period'] or ''} {row['fiscal_year'] or ''}".strip() or row["kpi_period"]
    return Trigger(
        "verdict",
        ref,
        row["ticker"],
        None,
        f"KPI {row['kpi_id']} breached for {row['ticker']} ({period})"[:200],
        neutralise(
            f"Thesis KPI {row['kpi_id']} ({row['metric']}) for {row['ticker']}, {period}: "
            f"observed {row['observed_value']} against the rule {row['operator']} "
            f"{row['threshold']} {row['threshold_unit']}. {row['rationale']}"
        )[:MATERIAL_CHARS],
        row["created_at"],
    )


async def _disclosure(pool: asyncpg.Pool, ref: int) -> Trigger | None:
    row = await pool.fetchrow(
        "select id, kind, ticker, headline, detail, known_at from public.disclosure_alert"
        " where id = $1",
        ref,
    )
    if row is None:
        return None
    detail = json.loads(row["detail"]) if isinstance(row["detail"], str) else row["detail"]
    facts = "; ".join(
        f"{k}: {v}"
        for k, v in (detail or {}).items()
        if isinstance(v, str | int | float) and str(v)
    )
    return Trigger(
        "disclosure",
        ref,
        row["ticker"],
        None,
        str(row["headline"]).splitlines()[0][:200],
        neutralise(
            f"Disclosure alert ({row['kind']}) on {row['ticker']}: {row['headline']}\n{facts}"
        )[:MATERIAL_CHARS],
        row["known_at"],
    )


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
