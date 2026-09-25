"""Read models for the news feed, alerts and reports."""

import datetime as dt
import json
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.news.digest import DigestBody
from steward.portfolio.read import JobSummary

Confidence = Literal["verified", "corroborated", "unconfirmed", "disputed"]
StoryStatus = Literal["pending", "confirmed", "unconfirmed", "disputed", "sentiment"]


class ItemLink(BaseModel):
    ticker: str
    via: str
    term: str


class StoryOut(BaseModel):
    id: int
    ticker: str | None
    event_type: str | None
    severity: int | None
    status: StoryStatus
    confidence: Confidence
    status_reason: str | None
    rumour: bool
    recycled: bool
    best_tier: int
    source_count: int
    first_seen: dt.datetime
    last_seen: dt.datetime
    corroborating_urls: list[str] = Field(default_factory=list)
    contradicting_urls: list[str] = Field(default_factory=list)


class NewsItemOut(BaseModel):
    id: int
    url: str
    headline: str
    summary: str | None
    outlet: str | None
    outlet_name: str | None
    tier: int
    source_id: str
    published_at: dt.datetime
    tickers: list[str]
    links: list[ItemLink]
    event_type: str | None
    affected_ticker: str | None
    rumour: bool | None
    relevant: bool | None
    story: StoryOut


class NewsFeed(BaseModel):
    items: list[NewsItemOut]
    next_before_id: int | None = Field(description="Pass as before_id for the next page.")
    last_ingest: JobSummary | None


class FeedFilters(BaseModel):
    ticker: str | None = None
    severities: list[int] = Field(default_factory=list)
    confidences: list[Confidence] = Field(default_factory=list)
    tiers: list[int] = Field(default_factory=list)
    story_id: int | None = None
    before_id: int | None = None
    limit: int = 50


def feed_where(filters: FeedFilters) -> tuple[str, list[Any]]:
    """The WHERE clause of the feed query and its arguments. Each filter narrows the set;
    an empty list means no filter on that field."""
    clauses: list[str] = []
    args: list[Any] = []

    def add(sql: str, value: Any) -> None:
        args.append(value)
        clauses.append(sql.replace("?", f"${len(args)}"))

    if filters.ticker:
        add("i.tickers @> array[?::text]", filters.ticker)
    if filters.severities:
        add("c.severity = any(?::smallint[])", filters.severities)
    if filters.confidences:
        add("c.confidence = any(?::text[])", list(filters.confidences))
    if filters.tiers:
        add("i.tier = any(?::smallint[])", filters.tiers)
    if filters.story_id is not None:
        add("i.cluster_id = ?", filters.story_id)
    if filters.before_id is not None:
        add("i.id < ?", filters.before_id)
    return (" where " + " and ".join(clauses)) if clauses else "", args


def _urls(corroboration: Any, key: str) -> list[str]:
    if not corroboration:
        return []
    doc = json.loads(corroboration) if isinstance(corroboration, str) else corroboration
    return [entry[0] for entry in doc.get(key, [])]


def _story(row: asyncpg.Record) -> StoryOut:
    return StoryOut(
        id=row["cluster_id"],
        ticker=row["c_ticker"],
        event_type=row["c_event_type"],
        severity=row["c_severity"],
        status=row["c_status"],
        confidence=row["c_confidence"],
        status_reason=row["c_status_reason"],
        rumour=row["c_rumour"],
        recycled=row["c_recycled"],
        best_tier=row["c_best_tier"],
        source_count=row["c_source_count"],
        first_seen=row["c_first_seen"],
        last_seen=row["c_last_seen"],
        corroborating_urls=_urls(row["c_corroboration"], "supporting"),
        contradicting_urls=_urls(row["c_corroboration"], "contradicting"),
    )


FEED_SELECT = (
    "select i.id, i.url, i.headline, i.summary, i.outlet, i.outlet_name, i.tier, i.source_id,"
    " i.published_at, i.tickers, i.links, i.event_type, i.affected_ticker, i.rumour,"
    " i.relevant, i.cluster_id, c.ticker as c_ticker, c.event_type as c_event_type,"
    " c.severity as c_severity, c.status as c_status, c.confidence as c_confidence,"
    " c.status_reason as c_status_reason, c.rumour as c_rumour, c.recycled as c_recycled,"
    " c.best_tier as c_best_tier, c.source_count as c_source_count,"
    " c.first_seen as c_first_seen, c.last_seen as c_last_seen,"
    " c.corroboration as c_corroboration"
    " from public.news_item i join public.story_cluster c on c.id = i.cluster_id"
)


async def news_feed(pool: asyncpg.Pool, filters: FeedFilters) -> NewsFeed:
    where, args = feed_where(filters)
    args.append(filters.limit + 1)
    rows = await pool.fetch(
        f"{FEED_SELECT}{where} order by i.id desc limit ${len(args)}",
        *args,
    )
    more = len(rows) > filters.limit
    rows = rows[: filters.limit]
    items = [
        NewsItemOut(
            id=r["id"],
            url=r["url"],
            headline=r["headline"],
            summary=r["summary"],
            outlet=r["outlet"],
            outlet_name=r["outlet_name"],
            tier=r["tier"],
            source_id=r["source_id"],
            published_at=r["published_at"],
            tickers=list(r["tickers"]),
            links=[ItemLink(**link) for link in json.loads(r["links"])],
            event_type=r["event_type"],
            affected_ticker=r["affected_ticker"],
            rumour=r["rumour"],
            relevant=r["relevant"],
            story=_story(r),
        )
        for r in rows
    ]
    job = await pool.fetchrow(
        "select id, job_type, trigger, status, error, created_at, started_at, finished_at"
        " from public.job where job_type = 'news_ingest' order by id desc limit 1"
    )
    return NewsFeed(
        items=items,
        next_before_id=items[-1].id if more and items else None,
        last_ingest=JobSummary(**dict(job)) if job else None,
    )


class AlertOut(BaseModel):
    id: int
    kind: Literal["news", "thesis_breach", "test"]
    severity: int
    ticker: str | None
    event_type: str | None
    cluster_id: int | None
    verdict_id: int | None
    channel: Literal["telegram", "email"]
    message: str
    status: Literal["pending", "sent", "suppressed", "failed"]
    send_after: dt.datetime
    sent_at: dt.datetime | None
    suppressed_reason: str | None
    error: str | None
    created_at: dt.datetime


async def recent_alerts(pool: asyncpg.Pool, limit: int) -> list[AlertOut]:
    rows = await pool.fetch(
        "select id, kind, severity, ticker, event_type, cluster_id, verdict_id, channel, message,"
        " status, send_after, sent_at, suppressed_reason, error, created_at"
        " from public.alert order by id desc limit $1",
        limit,
    )
    return [AlertOut(**dict(r)) for r in rows]


class ReportSummary(BaseModel):
    id: int
    type: Literal["digest", "research", "review", "escalation"]
    period: str | None
    title: str
    headline: str | None
    nothing_material: bool | None
    created_at: dt.datetime
    delivery: dict[str, dict[str, str]]


class ReportOut(BaseModel):
    id: int
    type: Literal["digest", "research", "review", "escalation"]
    period: str | None
    title: str
    created_at: dt.datetime
    period_start: dt.datetime | None
    period_end: dt.datetime | None
    body: DigestBody
    body_md: str
    delivery: dict[str, dict[str, str]]


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def list_reports(
    pool: asyncpg.Pool, report_type: str, period: str | None, limit: int
) -> list[ReportSummary]:
    rows = await pool.fetch(
        "select id, type, period, title, created_at, delivery, body ->> 'headline' as headline,"
        " (body ->> 'nothing_material')::boolean as nothing_material from public.report"
        " where type = $1 and ($2::text is null or period = $2) order by id desc limit $3",
        report_type,
        period,
        limit,
    )
    return [ReportSummary(**{**dict(r), "delivery": _json(r["delivery"])}) for r in rows]


async def get_report(pool: asyncpg.Pool, report_id: int) -> ReportOut | None:
    row = await pool.fetchrow(
        "select id, type, period, title, created_at, period_start, period_end, body, body_md,"
        " delivery from public.report where id = $1",
        report_id,
    )
    if row is None:
        return None
    return ReportOut(
        **{
            **dict(row),
            "body": DigestBody.model_validate(_json(row["body"])),
            "delivery": _json(row["delivery"]),
        }
    )
