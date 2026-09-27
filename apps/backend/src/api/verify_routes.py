"""Authenticated API routes for Phase 4 verification: owner feedback, source trust, the
evaluation candidates added from feedback, and the replay preview of rule changes."""

import datetime as dt
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from api.auth import Owner
from api.db import DbId, Pool
from steward.activity import ActivityCoverage, coverage
from steward.config import EventRulesConfig, JobsConfig, NotificationsConfig, VerificationConfig
from steward.config.store import load_active
from steward.verify.feedback import (
    FeedbackError,
    FeedbackIn,
    FeedbackOut,
    SourceTrustOut,
    TargetKind,
    list_feedback,
    list_trust,
    record_feedback,
)
from steward.verify.replay import ReplayPreview, replay_preview

router = APIRouter()


async def _verification(pool: Pool) -> VerificationConfig:
    stored = await load_active(pool, "verification", VerificationConfig)
    return stored.body if stored else VerificationConfig()


@router.post("/feedback", tags=["feedback"], status_code=status.HTTP_201_CREATED)
async def post_feedback(body: FeedbackIn, pool: Pool, owner: Owner) -> FeedbackOut:
    """Mark a fact, verdict, alert, news item, story or report sentence wrong, irrelevant or
    useful. A fact marked wrong is quarantined and its holding's verdicts are re-judged by
    the worker; wrong or useful moves the source's trust score. `effects` says what
    happened."""
    try:
        return await record_feedback(pool, body, await _verification(pool), owner.user_id)
    except FeedbackError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.not_found else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(exc)) from exc


@router.get("/feedback", tags=["feedback"])
async def get_feedback(
    pool: Pool,
    _owner: Owner,
    target_kind: Annotated[TargetKind | None, Query()] = None,
    target_id: Annotated[list[DbId] | None, Query(description="Any of these ids.")] = None,
    since: Annotated[dt.datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[FeedbackOut]:
    """Feedback given, newest first: to show the owner's earlier marks next to each item."""
    return await list_feedback(pool, target_kind, target_id or None, since, limit)


@router.get("/sources/trust", tags=["feedback"])
async def get_source_trust(pool: Pool, _owner: Owner) -> list[SourceTrustOut]:
    """Every source whose trust feedback has moved, lowest first. Sources never judged are
    at 1 and not listed."""
    return await list_trust(pool, await _verification(pool))


async def _active_rules(pool: Pool) -> tuple[EventRulesConfig, NotificationsConfig]:
    rules = await load_active(pool, "event_rules", EventRulesConfig)
    notify = await load_active(pool, "notifications", NotificationsConfig)
    return (
        rules.body if rules else EventRulesConfig(),
        notify.body if notify else NotificationsConfig(),
    )


@router.post("/config/event-rules/preview", tags=["config"])
async def preview_event_rules(body: EventRulesConfig, pool: Pool, _owner: Owner) -> ReplayPreview:
    """Replay the last 7 days of stored stories and alerts under this draft (with the active
    notification rules) and list every event whose alert outcome would change. Uses stored
    classifications only: no model or network call, nothing saved."""
    rules, notify = await _active_rules(pool)
    return await replay_preview(
        pool, "event_rules", rules, notify, body, None, dt.datetime.now(dt.UTC)
    )


@router.post("/config/notifications/preview", tags=["config"])
async def preview_notifications(
    body: NotificationsConfig, pool: Pool, _owner: Owner
) -> ReplayPreview:
    """Replay the last 7 days under this draft notifications config (with the active event
    rules): which alerts would have been sent, held or suppressed differently."""
    rules, notify = await _active_rules(pool)
    return await replay_preview(
        pool, "notifications", rules, notify, None, body, dt.datetime.now(dt.UTC)
    )


class EvalCandidateOut(BaseModel):
    id: int
    kind: Literal["earnings_figure", "news_label"]
    fact_id: int | None
    news_item_id: int | None
    corrected: dict[str, Any]
    note: str | None
    created_at: dt.datetime
    exported_at: dt.datetime | None
    exported_to: str | None


@router.get("/eval-candidates", tags=["feedback"])
async def get_eval_candidates(pool: Pool, _owner: Owner) -> list[EvalCandidateOut]:
    """Items marked wrong and added to an evaluation set. `exported_at` is set once
    `python -m steward.verify.export_evals` has written them into the repository."""
    rows = await pool.fetch(
        "select id, kind, fact_id, news_item_id, corrected, note, created_at, exported_at,"
        " exported_to from public.eval_candidate order by id desc limit 500"
    )
    return [EvalCandidateOut(**{**dict(r), "corrected": json.loads(r["corrected"])}) for r in rows]


@router.get("/activity/coverage", tags=["jobs"])
async def get_activity_coverage(pool: Pool, _owner: Owner) -> ActivityCoverage:
    """Per open holding: when news, earnings checks and prices last covered it, and whether
    that is a gap (no news run within the monitoring window, or stale price bars)."""
    stored = await load_active(pool, "jobs", JobsConfig)
    return await coverage(pool, stored.body if stored else JobsConfig(), dt.datetime.now(dt.UTC))
