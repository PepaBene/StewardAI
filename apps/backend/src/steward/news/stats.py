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
    state: Literal["ok", "degraded", "down", "unknown", "disabled"] = Field(
        default="unknown",
        description="down: the circuit breaker is open after repeated failures (the outlet "
        "is unreachable, which is not the same as no news); degraded: the last read failed; "
        "unknown: never read successfully.",
    )
    region: str | None = Field(default=None, description="ISO country of the outlet.")
    language: str | None = None
    paywall: Literal["open", "metered", "hard"] = "open"
    state_media: bool = False
    needs_alt: bool = Field(default=False, description="Feed blocked or gone; kept off.")
    consecutive_failures: int = 0
    backoff_until: dt.datetime | None = Field(default=None, description="Breaker reopens then.")
    last_success_at: dt.datetime | None = None
    runs_7d: int = 0
    fetched_7d: int = 0
    stored_linked_7d: int = Field(default=0, description="Items that became story items.")
    stored_wire_7d: int = Field(default=0, description="Items kept on the wire only.")
    avg_latency_ms: int | None = Field(
        default=None, description="Average time the feed or API took to answer, last 7 days."
    )
    fulltext_by_rung_7d: dict[str, int] = Field(
        default_factory=dict,
        description="Article texts stored in the last 7 days per fetch method: '1' the feed's own"
        " text, '2' a polite page fetch with the extractor, '3' a browser render, '4' an"
        " official API.",
    )
    fulltext_truncated_7d: int = Field(
        default=0, description="Of those, texts that look cut off (paywall, teaser)."
    )
    fulltext_failed_7d: int = Field(
        default=0, description="Articles first seen in the last 7 days whose text fetch failed."
    )
    delay_p50_min: int | None = Field(
        default=None,
        description="Median minutes from an article's publication to the app first seeing it,"
        " last 7 days.",
    )
    delay_p95_min: int | None = Field(default=None, description="95th percentile of the same.")


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
            "select s.id, s.last_fetched_at, s.last_item_count, s.last_error,"
            " h.health, h.consecutive_failures, h.backoff_until, h.last_success_at, h.runs_7d,"
            " h.fetched_7d, h.stored_linked_7d, h.stored_wire_7d, h.avg_latency_ms"
            " from public.source s join public.news_source_health h on h.source_id = s.id"
        )
    }
    since = now - dt.timedelta(days=7)
    rungs: dict[str, dict[str, int]] = {}
    truncated: dict[str, int] = {}
    for r in await pool.fetch(
        "select coalesce(a.source_id, i.source_id) as source_id, f.rung,"
        " count(*) as n, count(*) filter (where f.truncated) as cut"
        " from public.news_fulltext f"
        " left join public.news_article a on a.id = f.article_id"
        " left join public.news_item i on i.id = f.news_item_id"
        " where f.fetched_at >= $1 group by 1, 2",
        since,
    ):
        if r["source_id"] is None:
            continue
        rungs.setdefault(r["source_id"], {})[str(r["rung"])] = r["n"]
        truncated[r["source_id"]] = truncated.get(r["source_id"], 0) + r["cut"]
    failed = {
        r["source_id"]: r["n"]
        for r in await pool.fetch(
            "select source_id, count(*) as n from public.news_article"
            " where first_seen_at >= $1 and fetch_state = 'failed' group by 1",
            since,
        )
    }
    delays = {
        r["source_id"]: r
        for r in await pool.fetch(
            "select source_id,"
            " percentile_cont(0.5) within group (order by extract(epoch from"
            "   (first_seen_at - published_at)) / 60) as p50,"
            " percentile_cont(0.95) within group (order by extract(epoch from"
            "   (first_seen_at - published_at)) / 60) as p95"
            " from public.news_article where first_seen_at >= $1 and published_at <= first_seen_at"
            " group by 1",
            since,
        )
    }
    out = []
    for source in config.sources:
        row = fetched.get(source.id)
        delay = delays.get(source.id)
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
                state=row["health"] if row else ("unknown" if source.enabled else "disabled"),
                region=source.region,
                language=source.language,
                paywall=source.paywall,
                state_media=source.state_media,
                needs_alt=source.needs_alt,
                consecutive_failures=row["consecutive_failures"] if row else 0,
                backoff_until=row["backoff_until"] if row else None,
                last_success_at=row["last_success_at"] if row else None,
                runs_7d=row["runs_7d"] if row else 0,
                fetched_7d=row["fetched_7d"] if row else 0,
                stored_linked_7d=row["stored_linked_7d"] if row else 0,
                stored_wire_7d=row["stored_wire_7d"] if row else 0,
                avg_latency_ms=int(row["avg_latency_ms"])
                if row and row["avg_latency_ms"] is not None
                else None,
                fulltext_by_rung_7d=rungs.get(source.id, {}),
                fulltext_truncated_7d=truncated.get(source.id, 0),
                fulltext_failed_7d=failed.get(source.id, 0),
                delay_p50_min=int(delay["p50"]) if delay and delay["p50"] is not None else None,
                delay_p95_min=int(delay["p95"]) if delay and delay["p95"] is not None else None,
            )
        )
    return SourcesHealth(window_hours=hours, sources=out)
