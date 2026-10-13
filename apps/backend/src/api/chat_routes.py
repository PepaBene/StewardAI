"""Authenticated API routes for the chat panel.

A message is answered by a `chat` job in the worker (the API only stores it and queues the
job); the panel polls the conversation until the answer arrives. Jobs the assistant proposes
start only when the owner confirms them here, as jobs with the `chat` trigger.
"""

import datetime as dt
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import IdPath, Pool
from steward.actions import registry
from steward.chat.assistant import ChatAnswer
from steward.chat.tools import CHANGE_KINDS
from steward.jobs import queue
from steward.jobs.runner import create_job

router = APIRouter(tags=["chat"])

ACTION_JOB_TYPES = {
    "earnings_check": "earnings_check",
    "research": "research",
    "portfolio_sync": "portfolio_sync",
}


class ConversationOut(BaseModel):
    id: int
    title: str
    created_at: dt.datetime
    updated_at: dt.datetime
    last_message: str | None = None


class MessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    answer: ChatAnswer | None = Field(description="Assistant messages: sentences and sources.")
    status: Literal["pending", "done", "failed"]
    job_id: int | None
    created_at: dt.datetime


class ActionOut(BaseModel):
    id: int
    message_id: int | None
    kind: str = Field(description="A job to start, or an owner action (dismiss_notice, ...).")
    params: dict[str, Any]
    reason: str | None
    status: Literal["proposed", "confirmed", "cancelled"]
    job_id: int | None
    created_at: dt.datetime
    decided_at: dt.datetime | None


class ConversationDetail(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]
    actions: list[ActionOut]


class NewConversation(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class NewMessage(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class PostedMessage(BaseModel):
    message: MessageOut
    job_id: int


def _message(row: Any) -> MessageOut:
    body = json.loads(row["body"]) if row["body"] else None
    return MessageOut(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        answer=ChatAnswer.model_validate(body) if body else None,
        status=row["status"],
        job_id=row["job_id"],
        created_at=row["created_at"],
    )


def _action(row: Any) -> ActionOut:
    return ActionOut(**{**dict(row), "params": json.loads(row["params"])})


@router.post("/chat/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(body: NewConversation, pool: Pool, owner: Owner) -> ConversationOut:
    row = await pool.fetchrow(
        "insert into public.chat_conversation (title, created_by) values"
        " (coalesce($1, 'New conversation'), $2::uuid) returning id, title, created_at, updated_at",
        body.title,
        owner.user_id,
    )
    return ConversationOut(**dict(row))


@router.get("/chat/conversations")
async def list_conversations(
    pool: Pool, _owner: Owner, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[ConversationOut]:
    rows = await pool.fetch(
        "select c.id, c.title, c.created_at, c.updated_at, (select content from"
        " public.chat_message m where m.conversation_id = c.id order by id desc limit 1)"
        " as last_message from public.chat_conversation c order by c.updated_at desc limit $1",
        limit,
    )
    return [ConversationOut(**dict(r)) for r in rows]


@router.get("/chat/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: IdPath, pool: Pool, _owner: Owner
) -> ConversationDetail:
    conv = await pool.fetchrow(
        "select id, title, created_at, updated_at from public.chat_conversation where id = $1",
        conversation_id,
    )
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No conversation {conversation_id}")
    messages = await pool.fetch(
        "select id, role, content, body, status, job_id, created_at from public.chat_message"
        " where conversation_id = $1 order by id",
        conversation_id,
    )
    actions = await pool.fetch(
        "select id, message_id, kind, params, reason, status, job_id, created_at, decided_at"
        " from public.chat_action where conversation_id = $1 order by id",
        conversation_id,
    )
    return ConversationDetail(
        conversation=ConversationOut(**dict(conv)),
        messages=[_message(m) for m in messages],
        actions=[_action(a) for a in actions],
    )


@router.post("/chat/conversations/{conversation_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def post_message(
    conversation_id: IdPath, body: NewMessage, pool: Pool, owner: Owner
) -> PostedMessage:
    """Store the owner's message and queue the answer; poll the conversation for it."""
    async with pool.acquire() as conn, conn.transaction():
        exists = await conn.fetchval(
            "select exists (select 1 from public.chat_conversation where id = $1)",
            conversation_id,
        )
        if not exists:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No conversation {conversation_id}")
        row = await conn.fetchrow(
            "insert into public.chat_message (conversation_id, role, content, status)"
            " values ($1, 'user', $2, 'pending')"
            " returning id, role, content, body, status, job_id, created_at",
            conversation_id,
            body.content.strip(),
        )
        job_id = await create_job(
            conn,
            "chat",
            "chat",
            requested_by=owner.user_id,
            params={"conversation_id": conversation_id, "message_id": row["id"]},
        )
        await conn.execute(
            "update public.chat_message set job_id = $2 where id = $1", row["id"], job_id
        )
        await conn.execute(
            "update public.chat_conversation set updated_at = now(), title = case when title ="
            " 'New conversation' then left($2, 60) else title end where id = $1",
            conversation_id,
            body.content.strip(),
        )
        await queue.enqueue(conn, job_id, "chat")
    return PostedMessage(message=_message({**dict(row), "job_id": job_id}), job_id=job_id)


@router.post("/chat/actions/{action_id}/confirm")
async def confirm_action(action_id: IdPath, pool: Pool, owner: Owner) -> ActionOut:
    """Start a job the assistant proposed. It appears in the job log with trigger `chat`."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "select * from public.chat_action where id = $1 for update", action_id
        )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No action {action_id}")
        if row["status"] != "proposed":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Action {action_id} is {row['status']}")
        params = json.loads(row["params"])
        job_id: int | None = None
        if row["kind"] in CHANGE_KINDS:
            # A change to what the engine did runs now, through the same registry the screens
            # use; the card's button is the owner's confirmation.
            try:
                await registry.confirm(pool, int(params["owner_action_id"]), second=True)
            except (registry.ActionError, KeyError) as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        else:
            job_type = ACTION_JOB_TYPES[row["kind"]]
            job_id = await create_job(
                conn, job_type, "chat", requested_by=owner.user_id, params=params
            )
            await queue.enqueue(conn, job_id, job_type)
        updated = await conn.fetchrow(
            "update public.chat_action set status = 'confirmed', job_id = $2, decided_at = now(),"
            " decided_by = $3::uuid where id = $1 returning id, message_id, kind, params, reason,"
            " status, job_id, created_at, decided_at",
            action_id,
            job_id,
            owner.user_id,
        )
    return _action(updated)


@router.post("/chat/actions/{action_id}/cancel")
async def cancel_action(action_id: IdPath, pool: Pool, owner: Owner) -> ActionOut:
    row = await pool.fetchrow(
        "update public.chat_action set status = 'cancelled', decided_at = now(), decided_by ="
        " $2::uuid where id = $1 and status = 'proposed' returning id, message_id, kind, params,"
        " reason, status, job_id, created_at, decided_at",
        action_id,
        owner.user_id,
    )
    if row is None:
        current = await pool.fetchval(
            "select status from public.chat_action where id = $1", action_id
        )
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No action {action_id}")
        raise HTTPException(status.HTTP_409_CONFLICT, f"Action {action_id} is {current}")
    if row["kind"] in CHANGE_KINDS:
        params = json.loads(row["params"])
        await registry.cancel(pool, int(params.get("owner_action_id", 0)))
    return _action(row)
