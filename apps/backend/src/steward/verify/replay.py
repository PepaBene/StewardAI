"""Replay preview: what the last 7 days would have alerted under a draft Event rules or
Notifications config, from stored results only (no model, search or network call).

Two runs of the same simulation, one with the active configs and one with the draft
swapped in, over the same stored events in time order:

- news stories: the stored classification (event type, holding, relevance, thesis link,
  rumour, recycled, duplicate) and corroboration status; the severity is recomputed from
  the Event rules, then routed like the pipeline routes a confirmed story;
- other alerts (KPI breaches, portfolio rule breaches, monitoring): their stored severity
  and event, routed again under the Notifications config.

Each run counts its own alerts for the daily cap and the repeat throttle, so a draft that
raises one story's severity can push a later one over the cap. The result lists the events
whose outcome changes; comparing two simulations (not the draft against what actually
happened) keeps differences caused by earlier config changes out of the diff.
"""

import datetime as dt
import time
from dataclasses import dataclass
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.event_rules import EventRulesConfig
from steward.config.notifications import NotificationsConfig
from steward.notify.route import AlertRequest, RouteContext, day_start, quiet_day_end, route

WINDOW = dt.timedelta(days=7)


@dataclass(frozen=True)
class ReplayEvent:
    key: str
    at: dt.datetime
    kind: str
    ticker: str | None
    event_type: str | None
    headline: str
    cluster_id: int | None
    # Stories: routed only when these hold; severity from the rules.
    story: bool
    alertable: bool
    relevant: bool
    thesis_linked: bool
    stored_severity: int
    # First-seen times of insider-transaction stories for the holding up to this one.
    insider_times: tuple[dt.datetime, ...] = ()


class ReplayOutcome(BaseModel):
    severity: int
    decision: str = Field(description="What routing decided, in one line.")
    channels: list[str] = Field(description="Channels an alert would be sent or held for.")
    held_until: dt.datetime | None = None
    suppressed: str | None = None


class ReplayChange(BaseModel):
    key: str = Field(description="story:<id> or alert:<kind>:<id>.")
    kind: str
    at: dt.datetime
    cluster_id: int | None
    ticker: str | None
    event_type: str | None
    headline: str
    before: ReplayOutcome
    after: ReplayOutcome


class ReplayPreview(BaseModel):
    domain: Literal["event_rules", "notifications"]
    window_start: dt.datetime
    window_end: dt.datetime
    events: int = Field(description="Stored stories and alerts replayed.")
    alerts_before: int = Field(description="Events that would alert under the active config.")
    alerts_after: int = Field(description="Events that would alert under the draft.")
    changes: list[ReplayChange]
    elapsed_ms: int


async def load_events(
    pool: asyncpg.Pool, start: dt.datetime, end: dt.datetime, insider_days: int
) -> list[ReplayEvent]:
    stories = await pool.fetch(
        "select c.id, c.ticker, c.event_type, c.severity, c.status, c.rumour, c.recycled,"
        " c.duplicate_of, c.kpi_id, c.headline, c.first_seen,"
        " coalesce(a.created_at, c.first_seen) as at, coalesce(lead.relevant, false) as relevant,"
        " array(select o.first_seen from public.story_cluster o where o.ticker = c.ticker"
        "  and o.event_type = 'insider_transaction' and o.first_seen <= c.first_seen"
        "  and o.first_seen >= c.first_seen - make_interval(days => $3)) as insiders"
        " from public.story_cluster c"
        " left join lateral (select min(created_at) as created_at from public.alert"
        "  where cluster_id = c.id and kind = 'news') a on true"
        " left join lateral (select relevant from public.news_item where cluster_id = c.id"
        "  and classified_at is not null order by tier, published_at, id limit 1) lead on true"
        " where c.first_seen >= $1 and c.first_seen < $2 and c.status <> 'pending'"
        " and c.event_type is not null",
        start,
        end,
        insider_days,
    )
    events = [
        ReplayEvent(
            key=f"story:{r['id']}",
            at=r["at"],
            kind="news",
            ticker=r["ticker"],
            event_type=r["event_type"],
            headline=r["headline"],
            cluster_id=r["id"],
            story=True,
            alertable=r["status"] == "confirmed"
            and not r["rumour"]
            and not r["recycled"]
            and r["duplicate_of"] is None,
            relevant=r["relevant"],
            thesis_linked=r["kpi_id"] is not None,
            stored_severity=r["severity"] or 0,
            insider_times=tuple(r["insiders"]),
        )
        for r in stories
    ]
    # Every non-news, non-test decision once (one row per channel is stored).
    others = await pool.fetch(
        "select distinct on (kind, coalesce(verdict_id::text, breach_id::text, dedupe_key,"
        " id::text)) id, kind, severity, ticker, event_type, message, created_at"
        " from public.alert where kind not in ('news', 'test') and created_at >= $1"
        " and created_at < $2"
        " order by kind, coalesce(verdict_id::text, breach_id::text, dedupe_key, id::text), id",
        start,
        end,
    )
    events += [
        ReplayEvent(
            key=f"alert:{r['kind']}:{r['id']}",
            at=r["created_at"],
            kind=r["kind"],
            ticker=r["ticker"],
            event_type=r["event_type"],
            headline=r["message"].split("\n", 1)[0][:200],
            cluster_id=None,
            story=False,
            alertable=True,
            relevant=True,
            thesis_linked=False,
            stored_severity=r["severity"],
        )
        for r in others
    ]
    return sorted(events, key=lambda e: (e.at, e.key))


def _severity(event: ReplayEvent, rules: EventRulesConfig) -> int:
    if not event.story:
        return event.stored_severity
    if not event.relevant or event.event_type is None:
        return 0
    since = event.at - dt.timedelta(days=rules.insider_cluster_days)
    insiders = sum(1 for t in event.insider_times if t >= since)
    return rules.severity(
        event.event_type,
        event.ticker,
        thesis_linked=event.thesis_linked,
        insider_cluster=event.event_type == "insider_transaction"
        and insiders >= rules.insider_cluster_count,
    )


@dataclass
class _Sent:
    at: dt.datetime
    ticker: str | None
    event_type: str | None
    kind: str
    alert_no: int


def simulate(
    events: list[ReplayEvent],
    rules: EventRulesConfig,
    notify: NotificationsConfig,
    holidays: set[dt.date],
) -> dict[str, ReplayOutcome]:
    sent: list[_Sent] = []
    out: dict[str, ReplayOutcome] = {}
    for event in events:
        severity = _severity(event, rules)
        if event.story and (not event.alertable or severity < 1):
            reason = "not confirmed or not alertable" if not event.alertable else "severity 0"
            out[event.key] = ReplayOutcome(
                severity=severity, decision=f"no alert ({reason})", channels=[]
            )
            continue
        today = day_start(notify, event.at)
        alerts_today = sum(1 for s in sent if s.at >= today and s.kind != "test")
        recent = None
        if event.ticker and event.event_type and notify.repeat_throttle_hours > 0:
            window = event.at - dt.timedelta(hours=notify.repeat_throttle_hours)
            recent = next(
                (
                    s.alert_no
                    for s in reversed(sent)
                    if s.kind == event.kind
                    and s.ticker == event.ticker
                    and s.event_type == event.event_type
                    and s.at >= window
                ),
                None,
            )
        rows, explanation = route(
            AlertRequest(event.kind, severity, event.ticker, event.event_type),
            notify,
            RouteContext(
                now=event.at,
                alerts_today=alerts_today,
                recent_alert_id=recent,
                quiet_until=quiet_day_end(notify, event.at, holidays),
            ),
        )
        pending = [r for r in rows if r.status == "pending"]
        suppressed = next((r.suppressed_reason for r in rows if r.status == "suppressed"), None)
        held = max((r.send_after for r in pending), default=None)
        out[event.key] = ReplayOutcome(
            severity=severity,
            decision=explanation,
            channels=sorted(r.channel for r in pending),
            held_until=held if held and held > event.at else None,
            suppressed=suppressed,
        )
        if pending:
            sent.append(_Sent(event.at, event.ticker, event.event_type, event.kind, len(sent) + 1))
    return out


def _same(a: ReplayOutcome, b: ReplayOutcome) -> bool:
    return (a.severity, a.channels, a.held_until, a.suppressed is None) == (
        b.severity,
        b.channels,
        b.held_until,
        b.suppressed is None,
    )


async def replay_preview(
    pool: asyncpg.Pool,
    domain: Literal["event_rules", "notifications"],
    active_rules: EventRulesConfig,
    active_notify: NotificationsConfig,
    draft_rules: EventRulesConfig | None,
    draft_notify: NotificationsConfig | None,
    now: dt.datetime,
) -> ReplayPreview:
    started = time.monotonic()
    start = now - WINDOW
    rules_after = draft_rules or active_rules
    notify_after = draft_notify or active_notify
    events = await load_events(
        pool,
        start,
        now,
        max(active_rules.insider_cluster_days, rules_after.insider_cluster_days),
    )
    countries = {active_notify.holiday_country, notify_after.holiday_country}
    holidays: dict[str, set[dt.date]] = {c: set() for c in countries}
    for r in await pool.fetch(
        "select calendar, date from public.holiday where calendar = any($1::text[])"
        " and scope = 'national' and date between $2 and $3",
        list(countries),
        start.date() - dt.timedelta(days=1),
        now.date() + dt.timedelta(days=14),
    ):
        holidays[r["calendar"]].add(r["date"])
    before = simulate(events, active_rules, active_notify, holidays[active_notify.holiday_country])
    after = simulate(events, rules_after, notify_after, holidays[notify_after.holiday_country])
    changes = [
        ReplayChange(
            key=e.key,
            kind=e.kind,
            at=e.at,
            cluster_id=e.cluster_id,
            ticker=e.ticker,
            event_type=e.event_type,
            headline=e.headline,
            before=before[e.key],
            after=after[e.key],
        )
        for e in events
        if not _same(before[e.key], after[e.key])
    ]
    return ReplayPreview(
        domain=domain,
        window_start=start,
        window_end=now,
        events=len(events),
        alerts_before=sum(1 for o in before.values() if o.channels),
        alerts_after=sum(1 for o in after.values() if o.channels),
        changes=changes,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
