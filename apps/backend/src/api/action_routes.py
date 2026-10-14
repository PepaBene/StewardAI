"""Owner actions and visual answers: the same registry behind screens and chat.

The chat proposes an action (nothing changes); the owner confirms it from the card.
"""

import datetime as dt
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import IdPath, Pool
from steward.actions import registry
from steward.autopilot import reverify
from steward.chat import visuals
from steward.engine import overrides, timeline

router = APIRouter(tags=["actions"])


class ProposeIn(BaseModel):
    action: str
    args: dict[str, Any] = {}
    via: Literal["screen", "chat"] = "chat"
    reason: str | None = None


class ConfirmIn(BaseModel):
    second: bool = False


class Cancelled(BaseModel):
    action_id: int


class ActionKind(BaseModel):
    name: str
    title: str
    risk: registry.Risk
    undo: str


@router.get("/engine/ledger")
async def engine_ledger(
    pool: Pool,
    _owner: Owner,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    source: Annotated[str | None, Query(max_length=40)] = None,
    kind: Annotated[str | None, Query(max_length=80)] = None,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[timeline.Entry]:
    """Everything the engine or the owner changed, newest first, from every ledger. An entry
    with `undo_action` can be undone through `/actions/propose` with `undo_args`."""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    try:
        return await timeline.recent(pool, timeline.Query(since, limit, source, kind, offset))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


class OverrideIn(BaseModel):
    area: str = Field(min_length=1, max_length=80)
    subject_kind: str = Field(min_length=1, max_length=80)
    subject_ref: str = Field(min_length=1, max_length=200)
    action: overrides.Action
    reason_code: overrides.Reason | None = None
    note: str | None = Field(default=None, max_length=1000)
    via: overrides.Via = "screen"


class OverrideOut(BaseModel):
    id: int
    created_at: dt.datetime
    area: str
    subject_kind: str
    subject_ref: str
    action: str
    reason_code: str | None
    note: str | None
    via: str


class Recorded(BaseModel):
    recorded: bool


@router.post("/overrides", status_code=status.HTTP_201_CREATED)
async def record_override(body: OverrideIn, pool: Pool, _owner: Owner) -> Recorded:
    """Say that you overruled something the engine did, and why. Screens use this for areas
    that have no dedicated undo (a story, a graph edge, an identity); the engine does not redo
    an item you overruled and learns from the reasons."""
    await overrides.record(
        pool,
        area=body.area,
        subject_kind=body.subject_kind,
        subject_ref=body.subject_ref,
        action=body.action,
        via=body.via,
        reason_code=body.reason_code,
        note=body.note,
        decided_by="engine",
    )
    return Recorded(recorded=True)


@router.get("/overrides")
async def list_overrides(
    pool: Pool,
    _owner: Owner,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    area: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[OverrideOut]:
    """How you have overruled the engine, newest first."""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    rows = await pool.fetch(
        "select id, created_at, area, subject_kind, subject_ref, action, reason_code, note, via"
        " from public.owner_override where created_at >= $1 and ($2::text is null or area = $2)"
        " order by created_at desc limit $3",
        since,
        area,
        limit,
    )
    return [OverrideOut(**dict(r)) for r in rows]


class SweepOut(BaseModel):
    ran_at: dt.datetime
    checked: int
    flagged: int
    disagreement_rate: float | None


@router.get("/engine/sweeps")
async def engine_sweeps(pool: Pool, _owner: Owner) -> list[SweepOut]:
    """The weekly re-check of applied decisions: how many it looked at and how many no longer
    held, newest first. The share that no longer held is the disagreement rate."""
    return [
        SweepOut(
            ran_at=s.ran_at,
            checked=s.checked,
            flagged=s.flagged,
            disagreement_rate=s.disagreement_rate,
        )
        for s in await reverify.history(pool)
    ]


@router.get("/actions/kinds")
async def list_kinds(_owner: Owner) -> list[ActionKind]:
    """What the owner can change from a screen or from chat."""
    return [
        ActionKind(name=a.name, title=a.title, risk=a.risk, undo=a.undo)
        for a in registry.ACTIONS.values()
    ]


@router.get("/actions")
async def recent_actions(
    pool: Pool, _owner: Owner, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[registry.ActionRow]:
    return await registry.recent(pool, limit)


@router.post("/actions/propose", status_code=status.HTTP_201_CREATED)
async def propose(body: ProposeIn, pool: Pool, _owner: Owner) -> registry.ActionRow:
    """Check an action and say what it would do. Nothing changes until it is confirmed."""
    try:
        return await registry.propose(
            pool, body.action, body.args, via=body.via, reason=body.reason
        )
    except registry.ActionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/actions/{action_id}/confirm")
async def confirm(
    action_id: IdPath, body: ConfirmIn, pool: Pool, _owner: Owner
) -> registry.ActionRow:
    try:
        return await registry.confirm(pool, action_id, second=body.second)
    except registry.NeedsSecondConfirm as exc:
        raise HTTPException(status.HTTP_428_PRECONDITION_REQUIRED, str(exc)) from exc
    except registry.ActionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/actions/{action_id}/cancel")
async def cancel(action_id: IdPath, pool: Pool, _owner: Owner) -> Cancelled:
    if not await registry.cancel(pool, action_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No proposed action {action_id}")
    return Cancelled(action_id=action_id)


@router.get("/visuals/{kind}")
async def get_visual(kind: str, pool: Pool, _owner: Owner) -> visuals.Block:
    """A chart block built from stored records, with its source, date, summary and table."""
    try:
        return await visuals.build(pool, kind, dt.datetime.now(dt.UTC))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
