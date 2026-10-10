"""N8 operations report: what the news jobs cost, what was useful, and which severe stories
nobody was told about.

Everything is read from rows that already exist (job steps, alerts, the owner's marks), so
the report needs no model and costs nothing to run.
"""

import datetime as dt
import json
import math
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.news.evidence import indirect_only


class JobCost(BaseModel):
    job_type: str
    runs: int
    cost_usd: float


class Incident(BaseModel):
    """A story the owner may need to look at: a severe one nobody was alerted to, or an alert
    the owner marked as not useful."""

    kind: str = Field(description="`unalerted_severe` or `alert_not_useful`")
    story_id: int
    headline: str
    ticker: str | None
    severity: int | None
    first_seen: dt.datetime
    reason: str
    status: Literal["open", "acknowledged", "resolved"] = "open"
    note: str | None = None


class AlertLatency(BaseModel):
    alerts: int
    p50_minutes: float | None = Field(
        description="Median minutes from the first item's publication to the alert being sent."
    )
    p95_minutes: float | None


class AlertVolume(BaseModel):
    total: int
    direct: int
    indirect: int = Field(
        description="Alerts for stories that reached the holding only through an indirect link."
    )
    per_day: float
    indirect_per_day: float


class AiSearchCost(BaseModel):
    """What the AI search lane spent against what it brought in, from the job steps."""

    runs: int
    searches: int
    cost_usd: float
    leads: int = Field(description="Verified leads the model returned.")
    new_items: int = Field(description="Leads stored as new wire items (the rest were known).")
    cost_per_search: float | None
    cost_per_new_item: float | None = Field(
        description="Null while the lane has stored no new item."
    )


class OpsReport(BaseModel):
    days: int
    latency: AlertLatency
    volume: AlertVolume
    cost_usd: float
    by_job: list[JobCost]
    useful_events: int
    cost_per_useful_event: float | None = Field(
        description="Null while no story has been marked useful or alerted and left unmarked."
    )
    marks: dict[str, int]
    incidents: list[Incident]
    ai_search: AiSearchCost


async def job_costs(pool: asyncpg.Pool, since: dt.datetime) -> list[JobCost]:
    rows = await pool.fetch(
        "select j.job_type, count(distinct j.id) as runs, coalesce(sum(s.cost_usd), 0) as cost"
        " from public.job j left join public.job_step s on s.job_id = j.id"
        " where j.job_type like 'news\\_%' and j.created_at >= $1"
        " group by j.job_type order by cost desc, j.job_type",
        since,
    )
    return [
        JobCost(job_type=r["job_type"], runs=r["runs"], cost_usd=float(r["cost"])) for r in rows
    ]


async def useful_event_count(pool: asyncpg.Pool, since: dt.datetime) -> int:
    """Stories the owner marked useful, plus stories that alerted and were not marked
    not-useful: an alert nobody objected to counts as a useful event."""
    return int(
        await pool.fetchval(
            "select count(*) from ("
            " select target_id::bigint as id from public.news_feedback"
            "  where target = 'story' and kind = 'useful' and created_at >= $1"
            "  and target_id ~ '^[0-9]+$'"
            " union"
            " select a.cluster_id from public.alert a"
            "  where a.kind = 'news' and a.status = 'sent' and a.created_at >= $1"
            "  and a.cluster_id is not null"
            "  and not exists (select 1 from public.news_feedback f"
            "   where f.target = 'story' and f.kind = 'not_useful'"
            "   and f.target_id = a.cluster_id::text)"
            ") u",
            since,
        )
        or 0
    )


async def incidents(pool: asyncpg.Pool, since: dt.datetime, limit: int = 50) -> list[Incident]:
    rows = await pool.fetch(
        "select u.*, rv.status, rv.note from ("
        "select 'unalerted_severe' as kind, c.id, c.headline, c.ticker, c.severity, c.first_seen,"
        "  'Severity 3 story with no alert sent or queued' as reason"
        " from public.story_cluster c"
        " where c.severity = 3 and c.first_seen >= $1 and not c.recycled"
        "  and c.duplicate_of is null and c.status in ('confirmed', 'unconfirmed', 'disputed')"
        "  and not exists (select 1 from public.alert a where a.cluster_id = c.id"
        "   and a.status in ('sent', 'pending'))"
        " union all"
        " select 'alert_not_useful', c.id, c.headline, c.ticker, c.severity, c.first_seen,"
        "  'Alert sent, then marked not useful by the owner'"
        " from public.story_cluster c"
        " where c.first_seen >= $1 and exists (select 1 from public.alert a"
        "   where a.cluster_id = c.id and a.status = 'sent')"
        "  and exists (select 1 from public.news_feedback f where f.target = 'story'"
        "   and f.target_id = c.id::text and f.kind = 'not_useful')"
        ") u left join public.news_incident_review rv on rv.kind = u.kind and rv.story_id = u.id"
        " where coalesce(rv.status, 'open') <> 'resolved'"
        " order by u.first_seen desc limit $2",
        since,
        limit,
    )
    return [
        Incident(
            kind=r["kind"],
            story_id=r["id"],
            headline=r["headline"],
            ticker=r["ticker"],
            severity=r["severity"],
            first_seen=r["first_seen"],
            reason=r["reason"],
            status=r["status"] or "open",
            note=r["note"],
        )
        for r in rows
    ]


IncidentStatus = Literal["open", "acknowledged", "resolved"]


async def set_incident_status(
    pool: asyncpg.Pool,
    kind: str,
    story_id: int,
    status: IncidentStatus,
    note: str | None,
) -> bool:
    """Record the owner's handling of one incident. False when the story does not exist."""
    if not await pool.fetchval(
        "select exists (select 1 from public.story_cluster where id = $1)", story_id
    ):
        return False
    await pool.execute(
        "insert into public.news_incident_review (kind, story_id, status, note, updated_at)"
        " values ($1, $2, $3, $4, now())"
        " on conflict (kind, story_id) do update set status = excluded.status,"
        " note = excluded.note, updated_at = now()",
        kind,
        story_id,
        status,
        note,
    )
    return True


def _detail(raw: object) -> dict[str, object]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw if isinstance(raw, dict) else {}


async def ai_search_cost(pool: asyncpg.Pool, since: dt.datetime) -> AiSearchCost:
    rows = await pool.fetch(
        "select j.id as job_id, s.name, coalesce(s.searches, 0) as searches,"
        " coalesce(s.cost_usd, 0) as cost, s.detail"
        " from public.job j join public.job_step s on s.job_id = j.id"
        " where j.job_type = 'news_ai_search' and j.created_at >= $1 and s.status = 'succeeded'",
        since,
    )
    searches = leads = new_items = 0
    cost = 0.0
    for r in rows:
        searches += r["searches"]
        cost += float(r["cost"])
        detail = _detail(r["detail"])
        if r["name"] == "search":
            leads += int(detail.get("leads", 0) or 0)  # type: ignore[call-overload]
        elif r["name"] == "store":
            new_items += int(detail.get("stored", 0) or 0)  # type: ignore[call-overload]
    return AiSearchCost(
        runs=len({r["job_id"] for r in rows}),
        searches=searches,
        cost_usd=round(cost, 6),
        leads=leads,
        new_items=new_items,
        cost_per_search=round(cost / searches, 6) if searches else None,
        cost_per_new_item=round(cost / new_items, 6) if new_items else None,
    )


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (`p` in 0..100); None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


async def alert_latency(pool: asyncpg.Pool, since: dt.datetime) -> AlertLatency:
    """Minutes from the earliest item's publication to the news alert being sent."""
    rows = await pool.fetch(
        "select extract(epoch from (a.sent_at - min(i.published_at))) / 60 as minutes"
        " from public.alert a join public.news_item i on i.cluster_id = a.cluster_id"
        " where a.kind = 'news' and a.status = 'sent' and a.sent_at >= $1"
        " group by a.id, a.sent_at",
        since,
    )
    minutes = [max(0.0, float(r["minutes"])) for r in rows if r["minutes"] is not None]
    p50, p95 = percentile(minutes, 50), percentile(minutes, 95)
    return AlertLatency(
        alerts=len(minutes),
        p50_minutes=round(p50, 1) if p50 is not None else None,
        p95_minutes=round(p95, 1) if p95 is not None else None,
    )


async def alert_volume(pool: asyncpg.Pool, since: dt.datetime, days: int) -> AlertVolume:
    """Sent news alerts per day, split by whether the story reached its holding only through
    an indirect link (competitor, supplier, customer, watch topic) or directly. This is the
    number to read when judging `indirect_alert_min_severity`."""
    rows = await pool.fetch(
        "select a.id, a.ticker, i.links from public.alert a"
        " join public.news_item i on i.cluster_id = a.cluster_id"
        " where a.kind = 'news' and a.status = 'sent' and a.sent_at >= $1",
        since,
    )
    vias: dict[int, list[str]] = {}
    for r in rows:
        links = json.loads(r["links"]) if isinstance(r["links"], str) else (r["links"] or [])
        vias.setdefault(r["id"], []).extend(
            link["via"] for link in links if link.get("ticker") == r["ticker"]
        )
    indirect = sum(1 for v in vias.values() if indirect_only(v))
    total = len(vias)
    return AlertVolume(
        total=total,
        indirect=indirect,
        direct=total - indirect,
        per_day=round(total / max(days, 1), 2),
        indirect_per_day=round(indirect / max(days, 1), 2),
    )


async def mark_counts(pool: asyncpg.Pool, since: dt.datetime) -> dict[str, int]:
    rows = await pool.fetch(
        "select kind, count(*) as n from public.news_feedback where created_at >= $1"
        " group by kind order by kind",
        since,
    )
    return {r["kind"]: r["n"] for r in rows}


async def ops_report(
    pool: asyncpg.Pool, days: int = 30, now: dt.datetime | None = None
) -> OpsReport:
    since = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=days)
    by_job = await job_costs(pool, since)
    total = round(sum(j.cost_usd for j in by_job), 6)
    useful = await useful_event_count(pool, since)
    return OpsReport(
        days=days,
        latency=await alert_latency(pool, since),
        volume=await alert_volume(pool, since, days),
        cost_usd=total,
        by_job=by_job,
        useful_events=useful,
        cost_per_useful_event=round(total / useful, 6) if useful else None,
        marks=await mark_counts(pool, since),
        incidents=await incidents(pool, since),
        ai_search=await ai_search_cost(pool, since),
    )


def weekly_text(report: OpsReport) -> str:
    """The scheduled report as the plain text of one alert."""
    open_ = [i for i in report.incidents if i.status == "open"]
    lines = [
        f"News operations, last {report.days} days: ${report.cost_usd:.2f} spent, "
        f"{report.volume.total} alerts ({report.volume.indirect} indirect), "
        f"{report.useful_events} useful events.",
    ]
    if report.latency.p50_minutes is not None:
        lines.append(
            f"Alert latency: median {report.latency.p50_minutes:.0f} min, "
            f"95th percentile {report.latency.p95_minutes or 0:.0f} min."
        )
    search = report.ai_search
    if search.runs:
        per = f", ${search.cost_per_new_item:.3f} per new item" if search.cost_per_new_item else ""
        lines.append(
            f"AI search: {search.searches} searches, ${search.cost_usd:.2f}, "
            f"{search.new_items} new items{per}."
        )
    lines.append(
        f"{len(open_)} incident(s) open: review them on the News Review page."
        if open_
        else "No open incidents."
    )
    return "\n".join(lines)


async def send_weekly_report(
    pool: asyncpg.Pool, now: dt.datetime, job_id: int | None = None, days: int = 7
) -> str:
    """Send the ops report once per ISO week through the alert routing (the owner's
    notification rules decide channel, quiet hours and cap). Returns what happened."""
    from steward.config.notifications import NotificationsConfig
    from steward.config.store import load_active
    from steward.notify.alerts import create_alerts
    from steward.notify.route import AlertRequest

    year, week, _ = now.isocalendar()
    key = f"news-ops:{year}-W{week:02d}"
    if await pool.fetchval("select exists (select 1 from public.alert where dedupe_key = $1)", key):
        return "already sent this week"
    report = await ops_report(pool, days, now)
    stored = await load_active(pool, "notifications", NotificationsConfig)
    created = await create_alerts(
        pool,
        AlertRequest(
            "system",
            3 if any(i.status == "open" for i in report.incidents) else 1,
            None,
            "news_ops",
        ),
        weekly_text(report),
        stored.body if stored else NotificationsConfig(),
        now=now,
        job_id=job_id,
        dedupe_key=key,
    )
    return created.explanation
