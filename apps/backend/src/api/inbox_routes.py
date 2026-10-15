"""Authenticated API routes for the agent inbox, the agent's working notes and the cost and
latency of each kind of work (agentic workflows and RAG plan, AG4 and AG5)."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import IdPath, Pool
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.inbox import run, service, triggers
from steward.inbox.route_stats import RouteStat, route_stats
from steward.inbox.service import AgentNote, InboxItem, NoteIn, NotePatch, NoteVersion
from steward.training import labels

router = APIRouter(tags=["inbox"])

NOT_FOUND = "No inbox item {}"


class InboxList(BaseModel):
    items: list[InboxItem]
    unread: int
    missed: int = 0


class InboxFeedbackIn(BaseModel):
    feedback: Literal["useful", "not_useful"]
    note: str | None = None


class StatusIn(BaseModel):
    status: Literal["read", "dismissed"]


def _missing(item: InboxItem | None, item_id: int) -> InboxItem:
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND.format(item_id))
    return item


@router.get("/inbox")
async def list_inbox(
    pool: Pool,
    _owner: Owner,
    item_status: Annotated[
        Literal["new", "read", "actioned", "dismissed"] | None, Query(alias="status")
    ] = None,
    ticker: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> InboxList:
    """Items the event agents left, newest first. `unread` counts items still new; `missed`
    counts older events (up to 60 days) the scan never ran on."""
    stored = await load_active(pool, "jobs", JobsConfig)
    cfg = (stored.body if stored else JobsConfig()).inbox_settings
    return InboxList(
        items=await service.list_items(pool, item_status, ticker, limit),
        unread=await service.unread_count(pool),
        missed=await triggers.missed_count(pool, cfg.lookback_days, cfg.min_severity),
    )


class MissedEvent(BaseModel):
    key: str
    kind: str
    ref: int
    ticker: str | None
    severity: int | None
    headline: str
    created_at: str


class MissedRunIn(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=50)


class MissedRunOut(BaseModel):
    queued: list[str]
    skipped: list[str]


class NoteDecision(BaseModel):
    item: InboxItem
    note: AgentNote | None = None


@router.get("/inbox/missed")
async def list_missed(
    pool: Pool,
    _owner: Owner,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[MissedEvent]:
    """Open events older than the scan window (up to 60 days) that no run has looked into."""
    stored = await load_active(pool, "jobs", JobsConfig)
    cfg = (stored.body if stored else JobsConfig()).inbox_settings
    found = await triggers.missed(pool, cfg.lookback_days, cfg.min_severity, limit)
    return [
        MissedEvent(
            key=t.key,
            kind=t.kind,
            ref=t.ref,
            ticker=t.ticker,
            severity=t.severity,
            headline=t.headline,
            created_at=t.created_at.isoformat(),
        )
        for t in found
    ]


@router.post("/inbox/missed/run", status_code=status.HTTP_202_ACCEPTED)
async def run_missed(body: MissedRunIn, pool: Pool, owner: Owner) -> MissedRunOut:
    """Queue an agent run for each chosen missed event. Only events still open are queued."""
    stored = await load_active(pool, "jobs", JobsConfig)
    cfg = (stored.body if stored else JobsConfig()).inbox_settings
    open_now = {
        t.key: t
        for t in await triggers.missed(pool, cfg.lookback_days, cfg.min_severity, limit=100)
    }
    chosen = [open_now[k] for k in dict.fromkeys(body.keys) if k in open_now]
    queued = await run.queue_runs(pool, chosen, "manual", requested_by=owner.user_id)
    return MissedRunOut(queued=queued, skipped=[k for k in body.keys if k not in queued])


@router.get("/inbox/{item_id}")
async def get_inbox_item(item_id: IdPath, pool: Pool, _owner: Owner) -> InboxItem:
    return _missing(await service.get_item(pool, item_id), item_id)


@router.patch("/inbox/{item_id}")
async def set_inbox_status(item_id: IdPath, body: StatusIn, pool: Pool, _owner: Owner) -> InboxItem:
    """Mark an item read or dismiss it. Dismissing declines an open proposed action."""
    done = await service.set_status(pool, item_id, body.status)
    if done is None:
        existing = await service.get_item(pool, item_id)
        if existing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND.format(item_id))
        raise HTTPException(status.HTTP_409_CONFLICT, f"Item {item_id} is already actioned")
    return done


@router.post("/inbox/{item_id}/feedback")
async def give_feedback(
    item_id: IdPath, body: InboxFeedbackIn, pool: Pool, _owner: Owner
) -> InboxItem:
    """Say whether the item was useful. Feeds the candidate evaluation cases."""
    item = _missing(await service.set_feedback(pool, item_id, body.feedback, body.note), item_id)
    await _label(pool, item_id, "owner_review", "right" if body.feedback == "useful" else "wrong")
    return item


@router.post("/inbox/{item_id}/confirm")
async def confirm_inbox_action(item_id: IdPath, pool: Pool, owner: Owner) -> InboxItem:
    """Start the job the agent proposed. Only this call starts anything."""
    try:
        item = await service.confirm_action(pool, item_id, owner.user_id)
    except service.ActionError as exc:
        code = status.HTTP_409_CONFLICT if exc.conflict else status.HTTP_404_NOT_FOUND
        raise HTTPException(code, str(exc)) from exc
    await _label(pool, item_id, "owner_accept", "right")
    return item


@router.post("/inbox/{item_id}/decline")
async def decline_inbox_action(item_id: IdPath, pool: Pool, _owner: Owner) -> InboxItem:
    try:
        item = await service.decline_action(pool, item_id)
    except service.ActionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await _label(pool, item_id, "owner_review", "wrong")
    return item


async def _label(pool: Pool, item_id: int, source: labels.Source, label: labels.Label) -> None:
    """What the owner said about an agent's inbox item is a label on that agent's work."""
    await labels.record(
        pool,
        source=source,
        subject_kind="inbox_item",
        subject_ref=str(item_id),
        label=label,
        by_owner=True,
    )


@router.post("/inbox/{item_id}/note/accept")
async def accept_suggested_note(item_id: IdPath, pool: Pool, _owner: Owner) -> NoteDecision:
    """Keep the working note an agent run suggested; creates an ordinary agent note."""
    return await _decide_note(pool, item_id, True)


@router.post("/inbox/{item_id}/note/decline")
async def decline_suggested_note(item_id: IdPath, pool: Pool, _owner: Owner) -> NoteDecision:
    return await _decide_note(pool, item_id, False)


async def _decide_note(pool: Pool, item_id: int, accept: bool) -> NoteDecision:
    try:
        item, note = await service.decide_suggested_note(pool, item_id, accept)
    except service.ActionError as exc:
        code = status.HTTP_409_CONFLICT if exc.conflict else status.HTTP_404_NOT_FOUND
        raise HTTPException(code, str(exc)) from exc
    return NoteDecision(item=item, note=note)


@router.get("/agent-notes/{note_id}/history")
async def agent_note_history(note_id: IdPath, pool: Pool, _owner: Owner) -> list[NoteVersion]:
    """Every state a note has had, newest first; kept after the note is deleted."""
    return await service.note_history(pool, note_id)


@router.get("/agent-notes")
async def list_agent_notes(pool: Pool, _owner: Owner) -> list[AgentNote]:
    """The owner's working notes the agent reads. The agent never writes them."""
    return await service.list_notes(pool)


@router.post("/agent-notes", status_code=status.HTTP_201_CREATED)
async def add_agent_note(body: NoteIn, pool: Pool, _owner: Owner) -> AgentNote:
    return await service.add_note(pool, body)


@router.patch("/agent-notes/{note_id}")
async def edit_agent_note(note_id: IdPath, body: NotePatch, pool: Pool, _owner: Owner) -> AgentNote:
    note = await service.edit_note(pool, note_id, body)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No note {note_id}")
    return note


@router.delete("/agent-notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_note(note_id: IdPath, pool: Pool, _owner: Owner) -> Response:
    if not await service.delete_note(pool, note_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No note {note_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/ops/routes")
async def get_route_stats(
    pool: Pool,
    _owner: Owner,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    ai_only: Annotated[bool, Query(description="Only runs that called a model.")] = True,
) -> list[RouteStat]:
    """Runs, failures, cost and latency (median and 95th percentile) per kind of work."""
    return await route_stats(pool, days, ai_only)
