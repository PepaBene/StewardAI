"""Authenticated API routes for the news pipeline: the feed, alerts, digests, on-demand runs,
and the news_sources, event_rules and notifications config domains."""

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.routes import NoteParam, _as_dict, _enqueue, _not_found
from steward.config import EventRulesConfig, NewsSourcesConfig, NotificationsConfig
from steward.config.common import Ticker
from steward.config.event_rules import EVENT_DESCRIPTIONS, EVENT_TYPES, EventType
from steward.config.store import load_active, save_config
from steward.news.read import (
    AlertOut,
    Confidence,
    DisputedStory,
    EscalationOut,
    FeedFilters,
    NewsFeed,
    ReportOut,
    ReportSummary,
    disputed_stories,
    get_report,
    list_escalations,
    list_reports,
    news_feed,
    recent_alerts,
    resolve_story,
)
from steward.notify.alerts import create_alerts
from steward.notify.route import AlertRequest
from steward.portfolio.read import JobSummary

router = APIRouter()


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------


@router.get("/news", tags=["news"])
async def get_news(
    pool: Pool,
    _owner: Owner,
    ticker: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = None,
    severity: Annotated[list[int] | None, Query(description="Story severity 0-3.")] = None,
    confidence: Annotated[list[Confidence] | None, Query()] = None,
    tier: Annotated[list[int] | None, Query(description="Source tier 1-3.")] = None,
    story: Annotated[int | None, Query(description="Only this story's items.")] = None,
    before_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> NewsFeed:
    """News items, newest first, each with its story's severity and confidence. Filters
    combine (AND); a repeated parameter matches any of its values."""
    filters = FeedFilters(
        ticker=ticker,
        severities=severity or [],
        confidences=confidence or [],
        tiers=tier or [],
        story_id=story,
        before_id=before_id,
        limit=limit,
    )
    return await news_feed(pool, filters)


@router.get("/stories/disputed", tags=["news"])
async def get_disputed_stories(
    pool: Pool, _owner: Owner, include_resolved: Annotated[bool, Query()] = False
) -> list[DisputedStory]:
    """Stories whose sources contradict each other: the claim with its sources beside the
    contradicting ones, each with its tier. The holding's verdicts are frozen until the owner
    resolves the story."""
    return await disputed_stories(pool, include_resolved)


class StoryResolution(BaseModel):
    resolution: Literal["confirmed", "false"]
    note: str | None = Field(default=None, max_length=500)


@router.post("/stories/{story_id}/resolve", tags=["news"])
async def resolve_disputed_story(
    story_id: int, body: StoryResolution, pool: Pool, _owner: Owner
) -> list[DisputedStory]:
    """Resolve a disputed story; returns the updated list (resolved ones included)."""
    if not await resolve_story(pool, story_id, body.resolution, body.note):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No disputed story {story_id}")
    return [s for s in await disputed_stories(pool, True) if s.id == story_id]


class EventTypeInfo(BaseModel):
    id: EventType
    description: str


@router.get("/news/event-types", tags=["news"])
async def get_event_types(_owner: Owner) -> list[EventTypeInfo]:
    """The fixed event taxonomy, for labels and filters."""
    return [EventTypeInfo(id=t, description=EVENT_DESCRIPTIONS[t]) for t in EVENT_TYPES]


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@router.get("/alerts", tags=["alerts"])
async def get_alerts(
    pool: Pool, _owner: Owner, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[AlertOut]:
    """Recent alert decisions: sent, held for quiet hours, suppressed (with why) or failed."""
    return await recent_alerts(pool, limit)


class TestAlertRequest(BaseModel):
    severity: Literal[1, 2, 3] = 3
    event_type: EventType = "guidance_change"
    ticker: Ticker | None = None


class TestAlertResult(BaseModel):
    explanation: str = Field(description="Which rule matched and what happens.")
    channels: list[str] = Field(description="Channels the alert is queued for.")
    held_until: dt.datetime | None
    suppressed_reason: str | None
    alerts: list[AlertOut]


@router.post("/alerts/test", tags=["alerts"], status_code=status.HTTP_202_ACCEPTED)
async def send_test_alert(body: TestAlertRequest, pool: Pool, _owner: Owner) -> TestAlertResult:
    """Route a test alert through the active notification rules exactly as a real event
    would be (quiet hours included; the daily cap and repeat throttle do not apply). The
    worker sends it within seconds, or when quiet hours end."""
    stored = await load_active(pool, "notifications", NotificationsConfig)
    config = stored.body if stored else NotificationsConfig()
    now = dt.datetime.now(dt.UTC)
    message = (
        f"[Severity {body.severity}] Test alert: "
        f"{EVENT_DESCRIPTIONS[body.event_type].split(':')[0]}"
        f"{f' ({body.ticker})' if body.ticker else ''}\n"
        f"Sent from StewardAI at {now.strftime('%Y-%m-%d %H:%M UTC')} to check the "
        "notification rules. No action needed."
    )
    created = await create_alerts(
        pool,
        AlertRequest("test", body.severity, body.ticker, body.event_type),
        message,
        config,
        now=now,
        config_version_id=stored.id if stored else None,
    )
    alerts = [a for a in await recent_alerts(pool, 20) if a.id in created.ids]
    return TestAlertResult(
        explanation=created.explanation,
        channels=created.channels,
        held_until=created.held_until,
        suppressed_reason=created.suppressed_reason,
        alerts=alerts,
    )


# ---------------------------------------------------------------------------
# Reports (digests)
# ---------------------------------------------------------------------------


@router.get("/reports", tags=["reports"])
async def get_reports(
    pool: Pool,
    _owner: Owner,
    type: Annotated[Literal["digest", "research", "review", "escalation"], Query()] = "digest",
    period: Annotated[Literal["daily", "weekly"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
) -> list[ReportSummary]:
    return await list_reports(pool, type, period, limit)


@router.get("/reports/{report_id}", tags=["reports"])
async def get_report_by_id(report_id: int, pool: Pool, _owner: Owner) -> ReportOut:
    """A digest with every sentence's citations and the source list."""
    report = await get_report(pool, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No report {report_id}")
    return report


@router.get("/escalations", tags=["reports"])
async def get_escalations(
    pool: Pool,
    _owner: Owner,
    ticker: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[EscalationOut]:
    """Impact notes on confirmed severity-2+ stories and large price moves, newest first:
    each cites its sources and names the thesis KPIs or exit conditions it touches, or
    states that none applies."""
    return await list_escalations(pool, ticker, limit)


@router.get("/escalations/{report_id}", tags=["reports"])
async def get_escalation(report_id: int, pool: Pool, _owner: Owner) -> EscalationOut:
    found = [e for e in await list_escalations(pool, None, 500) if e.id == report_id]
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No escalation {report_id}")
    return found[0]


# ---------------------------------------------------------------------------
# On-demand runs
# ---------------------------------------------------------------------------


@router.post("/jobs/macro-collect", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_macro_collect(pool: Pool, owner: Owner) -> JobSummary:
    """Collect macro series now and raise any macro surprise."""
    return await _enqueue(pool, "macro_collect", owner.user_id)


@router.post("/jobs/news-ingest", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_news_ingest(pool: Pool, owner: Owner) -> JobSummary:
    """Run the news pipeline now: ingest, classify, corroborate, alert."""
    return await _enqueue(pool, "news_ingest", owner.user_id)


class DigestRequest(BaseModel):
    kind: Literal["daily", "weekly"] = "daily"


@router.post("/jobs/digest", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_digest_now(body: DigestRequest, pool: Pool, owner: Owner) -> JobSummary:
    """Write and send a digest now."""
    return await _enqueue(pool, f"{body.kind}_digest", owner.user_id)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class NewsSourcesConfigVersion(BaseModel):
    id: int
    created_at: dt.datetime
    note: str | None
    body: NewsSourcesConfig


class EventRulesConfigVersion(BaseModel):
    id: int
    created_at: dt.datetime
    note: str | None
    body: EventRulesConfig


class NotificationsConfigVersion(BaseModel):
    id: int
    created_at: dt.datetime
    note: str | None
    body: NotificationsConfig


@router.get("/config/news-sources", tags=["config"])
async def get_news_sources_config(pool: Pool, _owner: Owner) -> NewsSourcesConfigVersion:
    """The saved source registry. 404 means the defaults apply."""
    stored = await load_active(pool, "news_sources", NewsSourcesConfig)
    if stored is None:
        raise _not_found("news sources")
    return NewsSourcesConfigVersion(**_as_dict(stored))


@router.put("/config/news-sources", tags=["config"])
async def save_news_sources_config(
    body: NewsSourcesConfig, pool: Pool, owner: Owner, note: NoteParam = None
) -> NewsSourcesConfigVersion:
    """Save the source registry. The next ingest run reads it; no deploy or restart."""
    async with pool.acquire() as conn:
        stored = await save_config(conn, "news_sources", body, created_by=owner.user_id, note=note)
    return NewsSourcesConfigVersion(**_as_dict(stored))


@router.post("/config/news-sources/validate", tags=["config"])
async def validate_news_sources(body: NewsSourcesConfig, _owner: Owner) -> NewsSourcesConfig:
    return body


@router.get("/config/event-rules", tags=["config"])
async def get_event_rules_config(pool: Pool, _owner: Owner) -> EventRulesConfigVersion:
    """The saved event severities. 404 means the defaults apply."""
    stored = await load_active(pool, "event_rules", EventRulesConfig)
    if stored is None:
        raise _not_found("event rules")
    return EventRulesConfigVersion(**_as_dict(stored))


@router.put("/config/event-rules", tags=["config"])
async def save_event_rules_config(
    body: EventRulesConfig, pool: Pool, owner: Owner, note: NoteParam = None
) -> EventRulesConfigVersion:
    async with pool.acquire() as conn:
        stored = await save_config(conn, "event_rules", body, created_by=owner.user_id, note=note)
    return EventRulesConfigVersion(**_as_dict(stored))


@router.post("/config/event-rules/validate", tags=["config"])
async def validate_event_rules(body: EventRulesConfig, _owner: Owner) -> EventRulesConfig:
    return body


@router.get("/config/notifications", tags=["config"])
async def get_notifications_config(pool: Pool, _owner: Owner) -> NotificationsConfigVersion:
    """The saved notification rules. 404 means the defaults apply."""
    stored = await load_active(pool, "notifications", NotificationsConfig)
    if stored is None:
        raise _not_found("notifications")
    return NotificationsConfigVersion(**_as_dict(stored))


@router.put("/config/notifications", tags=["config"])
async def save_notifications_config(
    body: NotificationsConfig, pool: Pool, owner: Owner, note: NoteParam = None
) -> NotificationsConfigVersion:
    """Save notification rules. The next alert is routed with them; no restart."""
    async with pool.acquire() as conn:
        stored = await save_config(conn, "notifications", body, created_by=owner.user_id, note=note)
    return NotificationsConfigVersion(**_as_dict(stored))


@router.post("/config/notifications/validate", tags=["config"])
async def validate_notifications(body: NotificationsConfig, _owner: Owner) -> NotificationsConfig:
    return body
