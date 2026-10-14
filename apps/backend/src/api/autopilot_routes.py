"""Authenticated API routes for the autopilot: what it decided, what it needs from the owner,
and the owner's levers (revert a decision, accept a recommendation, mark a spot check)."""

import datetime as dt
from typing import Annotated, Literal

import asyncpg
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import IdPath, Pool
from steward.autopilot import ledger, reliability
from steward.autopilot.judge import ApplyError, accept, effective_mode, revert
from steward.autopilot.kinds import all_kinds
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.engine import overrides
from steward.ops import engagement
from steward.ops import tasks as ops_tasks

router = APIRouter(tags=["autopilot"])

STATS_DAYS = 30


class KindSummary(BaseModel):
    kind: str
    label: str
    href: str
    configured_mode: Literal["off", "shadow", "auto"]
    mode: Literal["off", "shadow", "auto"]
    decided: int
    applied: int
    shadow: int
    escalated: int
    reverted: int
    reviewed: int
    right: int
    precision: float | None
    waiting: int


class Summary(BaseModel):
    days: int
    kinds: list[KindSummary]
    waiting: int


class NeedsYouItem(BaseModel):
    id: int
    kind: str
    label: str
    href: str
    ticker: str | None
    # A notice is a problem the ops watchdog could not fix itself (a missing key, a failing
    # collector); its `id` is an ops task id, not an autopilot decision id.
    kind_of_item: Literal["escalation", "spot_check", "notice"]
    recommendation: str
    confidence: float | None
    reasons: str
    age_days: int
    can_accept: bool


class NeedsYou(BaseModel):
    count: int
    items: list[NeedsYouItem]


class WhyIn(BaseModel):
    reason_code: overrides.Reason | None = None
    note: str | None = Field(default=None, max_length=1000)


class ReviewIn(WhyIn):
    verdict: Literal["ok", "wrong"]


async def _record_override(
    pool: asyncpg.Pool,
    row: ledger.AutoDecision,
    action: Literal["reverted", "marked_wrong"],
    why: WhyIn | None,
) -> None:
    await overrides.record(
        pool,
        area="autopilot",
        subject_kind=row.kind,
        subject_ref=row.subject_ref,
        action=action,
        via="screen",
        reason_code=why.reason_code if why else None,
        note=why.note if why else None,
        decided_by=row.decided_by,
        decision_id=row.id,
        before={"decision": row.decision},
    )


def _decision_or_404(row: ledger.AutoDecision | None, decision_id: int) -> ledger.AutoDecision:
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No autopilot decision {decision_id}")
    return row


@router.get("/autopilot/summary")
async def get_summary(pool: Pool, _owner: Owner) -> Summary:
    """Per kind: its mode, what it decided in the last 30 days and how often reviewed
    decisions were right."""
    stored = await load_active(pool, "jobs", JobsConfig)
    cfg = (stored.body if stored else JobsConfig()).autopilot_settings
    overrides = await ledger.mode_overrides(pool)
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=STATS_DAYS)
    stats = {s.kind: s for s in await ledger.stats(pool, since)}
    kinds = all_kinds()
    waiting: dict[str, int] = {}
    for item in await ledger.escalations(pool):
        waiting[item.kind] = waiting.get(item.kind, 0) + 1
    out: list[KindSummary] = []
    for name, kind in kinds.items():
        s = stats.get(name) or ledger.KindStats(
            kind=name, decided=0, applied=0, shadow=0, escalated=0, reverted=0, reviewed=0, right=0
        )
        configured = cfg.policy(name).mode
        out.append(
            KindSummary(
                label=kind.label,
                href=kind.href,
                configured_mode=configured,
                mode=effective_mode(configured, overrides.get(name)),
                precision=s.right / s.reviewed if s.reviewed else None,
                waiting=waiting.get(name, 0),
                **s.model_dump(),
            )
        )
    return Summary(days=STATS_DAYS, kinds=out, waiting=sum(waiting.values()))


@router.get("/autopilot/calibration")
async def get_calibration(pool: Pool, _owner: Owner) -> list[reliability.KindCalibration]:
    """Per kind, how sure the autopilot was against how often it was right, over reviewed
    decisions of the last 180 days."""
    return await reliability.report(pool, dt.datetime.now(dt.UTC))


@router.get("/autopilot/decisions")
async def list_decisions(
    pool: Pool,
    _owner: Owner,
    kind: Annotated[str | None, Query(max_length=60)] = None,
    decision_status: Annotated[
        Literal["shadow", "applied", "escalated", "reverted", "closed"] | None,
        Query(alias="status"),
    ] = None,
    ticker: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ledger.AutoDecision]:
    return await ledger.list_decisions(
        pool, kind=kind, status=decision_status, ticker=ticker, limit=limit
    )


@router.get("/autopilot/needs-you")
async def needs_you(pool: Pool, _owner: Owner) -> NeedsYou:
    """What the autopilot could not settle with confidence, plus a few applied decisions kept
    for a spot check. Items already settled elsewhere are dropped (and closed) here."""
    kinds = all_kinds()
    now = dt.datetime.now(dt.UTC)
    rows = await ledger.escalations(pool)
    open_refs: dict[str, set[str]] = {}
    for name, kind in kinds.items():
        refs = [r.subject_ref for r in rows if r.kind == name]
        open_refs[name] = await kind.is_open(pool, refs) if refs else set()
    items: list[NeedsYouItem] = []
    for row in rows:
        judge = kinds.get(row.kind)
        if judge is None:
            continue
        if row.subject_ref not in open_refs[row.kind]:
            await ledger.close_escalation(pool, row.kind, row.subject_ref)
            continue
        items.append(
            NeedsYouItem(
                id=row.id,
                kind=row.kind,
                label=judge.label,
                href=judge.href,
                ticker=row.ticker,
                kind_of_item="escalation",
                recommendation=row.decision,
                confidence=row.confidence,
                reasons=row.summary,
                age_days=(now - row.decided_at).days,
                can_accept=row.decision in judge.actionable,
            )
        )
    weights = await ledger.holding_weights(pool)
    # Biggest holdings first, then the oldest; items without a ticker or not held come last.
    items.sort(key=lambda i: (-weights.get(i.ticker or "", 0.0), -i.age_days))
    notices = [
        NeedsYouItem(
            id=task.id,
            kind=task.kind,
            label=task.title,
            href=task.href or "/status",
            ticker=None,
            kind_of_item="notice",
            recommendation="",
            confidence=None,
            reasons=task.detail,
            age_days=(now - task.first_seen).days,
            can_accept=False,
        )
        for task in await ops_tasks.needs_owner(pool)
    ]
    items = [*notices, *items]
    for row in await ledger.spot_checks(pool):
        judge = kinds.get(row.kind)
        if judge is None:
            continue
        items.append(
            NeedsYouItem(
                id=row.id,
                kind=row.kind,
                label=judge.label,
                href=judge.href,
                ticker=row.ticker,
                kind_of_item="spot_check",
                recommendation=row.decision,
                confidence=row.confidence,
                reasons=row.summary,
                age_days=(now - row.decided_at).days,
                can_accept=False,
            )
        )
    return NeedsYou(count=len(items), items=items)


class Dismissed(BaseModel):
    task_id: int


class EngagementIn(BaseModel):
    kind: engagement.Kind
    ref: Annotated[str, Field(min_length=1, max_length=120)]
    ticker: Annotated[str | None, Field(max_length=40)] = None


class Recorded(BaseModel):
    ok: bool = True


@router.post("/engagement", status_code=status.HTTP_201_CREATED)
async def record_engagement(body: EngagementIn, pool: Pool, _owner: Owner) -> Recorded:
    """Note that the owner opened something. The app uses it to send less of what is never
    read; it stores the kind and id only."""
    await engagement.record(pool, body.kind, body.ref, body.ticker)
    return Recorded()


@router.post("/autopilot/notices/{task_id}/dismiss")
async def dismiss_notice(task_id: IdPath, pool: Pool, _owner: Owner) -> Dismissed:
    """Set aside a watchdog notice (for example a key you do not want). A problem that is
    still there on a later run opens a new notice; a missing key stays dismissed."""
    if not await ops_tasks.dismiss(pool, task_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No open notice {task_id}")
    return Dismissed(task_id=task_id)


class SnoozeIn(BaseModel):
    days: int = Field(default=7, ge=1, le=ops_tasks.SNOOZE_MAX_DAYS)


@router.post("/autopilot/notices/{task_id}/snooze")
async def snooze_notice(task_id: IdPath, pool: Pool, _owner: Owner, body: SnoozeIn) -> Dismissed:
    """Set a notice aside for some days. It does not come back before then, and unlike a
    dismissal it does not count against the notice."""
    if not await ops_tasks.snooze(pool, task_id, body.days):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No open notice {task_id}")
    return Dismissed(task_id=task_id)


@router.post("/autopilot/decisions/{decision_id}/revert")
async def revert_decision(
    decision_id: IdPath, pool: Pool, _owner: Owner, body: WhyIn | None = None
) -> ledger.AutoDecision:
    """Undo an applied decision. It counts as a wrong decision in calibration. The body, with
    a reason chip and a note, is optional."""
    row = _decision_or_404(await ledger.get(pool, decision_id), decision_id)
    kind = all_kinds().get(row.kind)
    if kind is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Unknown autopilot kind {row.kind}")
    try:
        await revert(pool, kind, row)
    except ApplyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await _record_override(pool, row, "reverted", body)
    return _decision_or_404(await ledger.get(pool, decision_id), decision_id)


@router.post("/autopilot/decisions/{decision_id}/accept")
async def accept_recommendation(
    decision_id: IdPath, pool: Pool, _owner: Owner
) -> ledger.AutoDecision:
    """Apply the autopilot's recommendation on an item it escalated."""
    row = _decision_or_404(await ledger.get(pool, decision_id), decision_id)
    kind = all_kinds().get(row.kind)
    if kind is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Unknown autopilot kind {row.kind}")
    try:
        await accept(pool, kind, row)
    except ApplyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _decision_or_404(await ledger.get(pool, decision_id), decision_id)


@router.post("/autopilot/decisions/{decision_id}/review")
async def review_decision(
    decision_id: IdPath, body: ReviewIn, pool: Pool, _owner: Owner
) -> ledger.AutoDecision:
    """The owner's verdict on a spot-checked decision. Calibration counts it."""
    row = _decision_or_404(await ledger.get(pool, decision_id), decision_id)
    await ledger.set_review(pool, row.id, body.verdict, "owner")
    if body.verdict == "wrong":
        await _record_override(pool, row, "marked_wrong", body)
    return _decision_or_404(await ledger.get(pool, decision_id), decision_id)
