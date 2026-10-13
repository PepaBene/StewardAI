"""Owner actions and visual answers: the same registry behind screens and chat.

The chat proposes an action (nothing changes); the owner confirms it from the card.
"""

import datetime as dt
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from api.auth import Owner
from api.db import IdPath, Pool
from steward.actions import registry
from steward.chat import visuals
from steward.engine import timeline

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
) -> list[timeline.Entry]:
    """Everything the engine or the owner changed, newest first, from every ledger."""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    try:
        return await timeline.recent(pool, timeline.Query(since, limit, source))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


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
