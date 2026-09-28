"""News counts and source health for the news dashboard. Counts of stored rows only.

Stories: a story is one `story_cluster`. It is dated by its earliest item's `published_at`
(the first time any outlet published it) and counted on that UTC day. Stories marked as a
repeat of an earlier story (`duplicate_of`) are left out: they are the same event seen again
and never alert. Stories not yet classified have no severity or event type; they are counted
in `total` and `unclassified`, never given a severity.

Source health (per configured news source): healthy when the source is enabled, its last
fetch is within the monitoring window (the Jobs config's `monitoring.news_coverage_hours`,
the same window the coverage alert uses), and that fetch recorded no error or note.
"""

import datetime as dt
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.jobs import JobsConfig
from steward.config.news_sources import NewsSourcesConfig, SourceType
from steward.config.store import load_active
from steward.daily import window_bounds, window_days
from steward.news.normalize import host_of

SEVERITIES = ("0", "1", "2", "3")


class SeverityDay(BaseModel):
    date: dt.date
    by_severity: dict[str, int] = Field(
        description="Stories first published that UTC day, per severity; keys '0'-'3', "
        "always all four."
    )
    unclassified: int = Field(description="Stories that day with no severity yet.")


class EventTypeCount(BaseModel):
    event_type: str
    count: int


class NewsStats(BaseModel):
    days: int
    total: int = Field(description="Stories first published in the window (repeats excluded).")
    unclassified: int = Field(description="Of those, stories not yet classified.")
    per_day: list[SeverityDay] = Field(description="Every UTC day of the window, oldest first.")
    by_event_type: list[EventTypeCount] = Field(
        description="Classified stories per event type, most first."
    )
    disputed_count: int = Field(
        description="Stories in the window whose sources contradict each other (status "
        "disputed, including ones the owner has since marked false)."
    )


async def news_stats(pool: asyncpg.Pool, days: int, now: dt.datetime) -> NewsStats:
    window = window_days(now, days)
    start, end = window_bounds(window)
    rows = await pool.fetch(
        "select c.severity, c.event_type, c.status,"
        " (p.published_at at time zone 'UTC')::date as day"
        " from public.story_cluster c"
        " join lateral (select min(i.published_at) as published_at from public.news_item i"
        "   where i.cluster_id = c.id) p on true"
        " where c.duplicate_of is null and p.published_at >= $1 and p.published_at < $2",
        start,
        end,
    )
    per: dict[dt.date, SeverityDay] = {
        d: SeverityDay(date=d, by_severity=dict.fromkeys(SEVERITIES, 0), unclassified=0)
        for d in window
    }
    by_type: dict[str, int] = {}
    for r in rows:
        day = per[r["day"]]
        if r["severity"] is None:
            day.unclassified += 1
        else:
            day.by_severity[str(r["severity"])] += 1
        if r["event_type"] is not None:
            by_type[r["event_type"]] = by_type.get(r["event_type"], 0) + 1
    return NewsStats(
        days=days,
        total=len(rows),
        unclassified=sum(d.unclassified for d in per.values()),
        per_day=list(per.values()),
        by_event_type=[
            EventTypeCount(event_type=t, count=n)
            for t, n in sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        disputed_count=sum(r["status"] == "disputed" for r in rows),
    )


HEALTH_RULE = (
    "healthy = enabled, last fetched within the Jobs config's monitoring.news_coverage_hours, "
    "and that fetch recorded no error or note"
)


class SourceHealth(BaseModel):
    id: str
    name: str
    type: SourceType
    host: str | None = Field(
        description="The feed's host; null for per-holding sources (EDGAR, Finnhub, ...)."
    )
    tier: Literal[1, 2, 3]
    enabled: bool
    last_fetched_at: dt.datetime | None = Field(description="Null: never fetched.")
    last_item_count: int | None = Field(description="Items the last fetch returned.")
    last_error: str | None = Field(description="The last fetch's error, or its note when empty.")
    healthy: bool = Field(description=HEALTH_RULE)


class SourcesHealth(BaseModel):
    rule: str = Field(default=HEALTH_RULE, description="How `healthy` is decided.")
    window_hours: int
    sources: list[SourceHealth] = Field(description="Every configured source, in config order.")


async def sources_health(pool: asyncpg.Pool, now: dt.datetime) -> SourcesHealth:
    stored = await load_active(pool, "news_sources", NewsSourcesConfig)
    config = stored.body if stored else NewsSourcesConfig()
    jobs = await load_active(pool, "jobs", JobsConfig)
    hours = (jobs.body if jobs else JobsConfig()).monitoring.news_coverage_hours
    fetched = {
        r["id"]: r
        for r in await pool.fetch(
            "select id, last_fetched_at, last_item_count, last_error from public.source"
        )
    }
    out = []
    for source in config.sources:
        row = fetched.get(source.id)
        last = row["last_fetched_at"] if row else None
        error = row["last_error"] if row else None
        out.append(
            SourceHealth(
                id=source.id,
                name=source.name,
                type=source.type,
                host=host_of(str(source.url)) if source.url else None,
                tier=source.tier,
                enabled=source.enabled,
                last_fetched_at=last,
                last_item_count=row["last_item_count"] if row else None,
                last_error=error,
                healthy=source.enabled
                and last is not None
                and last >= now - dt.timedelta(hours=hours)
                and error is None,
            )
        )
    return SourcesHealth(window_hours=hours, sources=out)
