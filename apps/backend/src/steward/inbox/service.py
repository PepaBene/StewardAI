"""The owner's side of the inbox: read, dismiss, give feedback, confirm or decline an action,
and the working notes the agent reads.

Confirming is the only place an inbox item starts a job. It re-checks the stored action against
the same kinds chat may propose, inside one transaction with a row lock, so a double click
starts one job.
"""

import json
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.jobs import queue
from steward.jobs.runner import create_job

ACTION_JOB_TYPES = {
    "earnings_check": "earnings_check",
    "research": "research",
    "portfolio_sync": "portfolio_sync",
}
ItemStatus = Literal["new", "read", "actioned", "dismissed"]
COLUMNS = (
    "id, trigger_key, trigger_kind, trigger_ref, ticker, severity, headline, body, status,"
    " proposed_action, action_status, action_job_id, feedback, feedback_note, job_id,"
    " created_at, decided_at"
)


class InboxItem(BaseModel):
    id: int
    trigger_key: str
    trigger_kind: str
    trigger_ref: int
    ticker: str | None
    severity: int | None
    headline: str
    body: dict[str, Any]
    status: ItemStatus
    proposed_action: dict[str, Any] | None
    action_status: Literal["proposed", "confirmed", "declined"] | None
    action_job_id: int | None
    feedback: Literal["useful", "not_useful"] | None
    feedback_note: str | None
    job_id: int | None
    created_at: str
    decided_at: str | None


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def item_from(row: asyncpg.Record) -> InboxItem:
    return InboxItem(
        **{
            **dict(row),
            "body": _json(row["body"]),
            "proposed_action": _json(row["proposed_action"]),
            "created_at": row["created_at"].isoformat(),
            "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
        }
    )


async def list_items(
    pool: asyncpg.Pool, status: str | None, ticker: str | None, limit: int
) -> list[InboxItem]:
    rows = await pool.fetch(
        f"select {COLUMNS} from public.inbox_item"  # noqa: S608 - fixed column list
        " where ($1::text is null or status = $1) and ($2::text is null or ticker = $2)"
        " order by created_at desc, id desc limit $3",
        status,
        ticker,
        limit,
    )
    return [item_from(r) for r in rows]


async def get_item(pool: asyncpg.Pool, item_id: int) -> InboxItem | None:
    row = await pool.fetchrow(
        f"select {COLUMNS} from public.inbox_item where id = $1",  # noqa: S608
        item_id,
    )
    return item_from(row) if row else None


async def unread_count(pool: asyncpg.Pool) -> int:
    return int(await pool.fetchval("select count(*) from public.inbox_item where status = 'new'"))


async def set_status(pool: asyncpg.Pool, item_id: int, status: ItemStatus) -> InboxItem | None:
    """read or dismissed. Dismissing an item with an open action declines it too."""
    row = await pool.fetchrow(
        "update public.inbox_item set status = $2,"
        " action_status = case when $2 = 'dismissed' and action_status = 'proposed'"
        "   then 'declined' else action_status end,"
        " decided_at = case when $2 = 'dismissed' then now() else decided_at end"
        " where id = $1 and status <> 'actioned' returning id",
        item_id,
        status,
    )
    return await get_item(pool, item_id) if row else None


async def set_feedback(
    pool: asyncpg.Pool, item_id: int, feedback: str, note: str | None
) -> InboxItem | None:
    row = await pool.fetchval(
        "update public.inbox_item set feedback = $2, feedback_note = $3 where id = $1 returning id",
        item_id,
        feedback,
        (note or "").strip()[:1000] or None,
    )
    return await get_item(pool, item_id) if row else None


class ActionError(Exception):
    def __init__(self, message: str, conflict: bool = False) -> None:
        super().__init__(message)
        self.conflict = conflict


async def confirm_action(pool: asyncpg.Pool, item_id: int, owner_id: str | None) -> InboxItem:
    """Start the proposed job. With no `owner_id` (the autopilot confirming) the job is an
    event-triggered run with no requester."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "select id, proposed_action, action_status from public.inbox_item"
            " where id = $1 for update",
            item_id,
        )
        if row is None:
            raise ActionError(f"No inbox item {item_id}")
        if row["action_status"] != "proposed":
            raise ActionError(
                f"Item {item_id} has no open action ({row['action_status'] or 'none'})",
                conflict=True,
            )
        action = _json(row["proposed_action"])
        job_type = ACTION_JOB_TYPES.get(action.get("kind", ""))
        if job_type is None:
            raise ActionError(f"Unknown action kind {action.get('kind')!r}")
        # The job carries the item it came from, so the job log shows it was an inbox action.
        job_id = await create_job(
            conn,
            job_type,
            "manual" if owner_id else "event",
            requested_by=owner_id,
            params={**dict(action.get("params", {})), "inbox_item_id": item_id},
        )
        await queue.enqueue(conn, job_id, job_type)
        await conn.execute(
            "update public.inbox_item set action_status = 'confirmed', status = 'actioned',"
            " action_job_id = $2, decided_at = now() where id = $1",
            item_id,
            job_id,
        )
    item = await get_item(pool, item_id)
    assert item is not None
    return item


async def decline_action(pool: asyncpg.Pool, item_id: int) -> InboxItem:
    row = await pool.fetchval(
        "update public.inbox_item set action_status = 'declined', decided_at = now()"
        " where id = $1 and action_status = 'proposed' returning id",
        item_id,
    )
    if row is None:
        raise ActionError(f"Item {item_id} has no open action", conflict=True)
    item = await get_item(pool, item_id)
    assert item is not None
    return item


# --------------------------------------------------------------------------- agent notes


class AgentNote(BaseModel):
    id: int
    ticker: str | None
    text: str
    active: bool
    created_at: str
    updated_at: str


class NoteIn(BaseModel):
    ticker: str | None = Field(default=None, max_length=40)
    text: str = Field(min_length=1, max_length=2000)
    active: bool = True


class NotePatch(BaseModel):
    ticker: str | None = None
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    active: bool | None = None


def note_from(row: asyncpg.Record) -> AgentNote:
    return AgentNote(
        **{
            **dict(row),
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }
    )


NOTE_COLUMNS = "id, ticker, text, active, created_at, updated_at"


async def list_notes(pool: asyncpg.Pool) -> list[AgentNote]:
    rows = await pool.fetch(
        f"select {NOTE_COLUMNS} from public.agent_note order by ticker nulls first, id"  # noqa: S608
    )
    return [note_from(r) for r in rows]


class NoteVersion(BaseModel):
    id: int
    note_id: int
    ticker: str | None
    text: str
    active: bool
    change: Literal["created", "edited", "deleted"]
    changed_at: str


async def _version(
    conn: asyncpg.Connection | asyncpg.Pool,
    note_id: int,
    ticker: str | None,
    text: str,
    active: bool,
    change: str,
) -> None:
    await conn.execute(
        "insert into public.agent_note_version (note_id, ticker, text, active, change)"
        " values ($1, $2, $3, $4, $5)",
        note_id,
        ticker,
        text,
        active,
        change,
    )


async def note_history(pool: asyncpg.Pool, note_id: int) -> list[NoteVersion]:
    """Every state the note has had, newest first (kept after the note is deleted)."""
    rows = await pool.fetch(
        "select id, note_id, ticker, text, active, change, changed_at"
        " from public.agent_note_version where note_id = $1 order by id desc",
        note_id,
    )
    return [NoteVersion(**{**dict(r), "changed_at": r["changed_at"].isoformat()}) for r in rows]


async def add_note(pool: asyncpg.Pool, body: NoteIn) -> AgentNote:
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "insert into public.agent_note (ticker, text, active) values ($1, $2, $3)"
            " returning id, ticker, text, active, created_at, updated_at",
            body.ticker or None,
            body.text.strip(),
            body.active,
        )
        await _version(conn, row["id"], row["ticker"], row["text"], row["active"], "created")
    return note_from(row)


async def edit_note(pool: asyncpg.Pool, note_id: int, patch: NotePatch) -> AgentNote | None:
    fields = patch.model_dump(exclude_unset=True)
    if not fields:
        row = await pool.fetchrow(
            f"select {NOTE_COLUMNS} from public.agent_note where id = $1",  # noqa: S608
            note_id,
        )
        return note_from(row) if row else None
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "update public.agent_note set"  # noqa: S608
            " ticker = case when $2 then $3 else ticker end,"
            " text = case when $4 then $5 else text end,"
            " active = case when $6 then $7 else active end, updated_at = now()"
            f" where id = $1 returning {NOTE_COLUMNS}",
            note_id,
            "ticker" in fields,
            fields.get("ticker") or None,
            "text" in fields,
            (fields.get("text") or "").strip(),
            "active" in fields,
            fields.get("active", True),
        )
        if row:
            await _version(conn, row["id"], row["ticker"], row["text"], row["active"], "edited")
    return note_from(row) if row else None


async def delete_note(pool: asyncpg.Pool, note_id: int) -> bool:
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "delete from public.agent_note where id = $1 returning ticker, text, active", note_id
        )
        if row:
            await _version(conn, note_id, row["ticker"], row["text"], row["active"], "deleted")
    return row is not None


async def decide_suggested_note(
    pool: asyncpg.Pool, item_id: int, accept: bool
) -> tuple[InboxItem, AgentNote | None]:
    """The owner keeps or drops the note an agent run suggested. Keeping it creates an ordinary
    agent note for the item's holding; only this call ever creates one from a run."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "select ticker, body from public.inbox_item where id = $1 for update", item_id
        )
        if row is None:
            raise ActionError(f"No inbox item {item_id}")
        body = _json(row["body"])
        if body.get("note_status") != "suggested" or not body.get("suggested_note"):
            raise ActionError(f"Item {item_id} has no open note suggestion", conflict=True)
        created: AgentNote | None = None
        if accept:
            note_row = await conn.fetchrow(
                "insert into public.agent_note (ticker, text, active) values ($1, $2, true)"
                " returning id, ticker, text, active, created_at, updated_at",
                row["ticker"],
                str(body["suggested_note"])[:2000],
            )
            await _version(
                conn,
                note_row["id"],
                note_row["ticker"],
                note_row["text"],
                note_row["active"],
                "created",
            )
            created = note_from(note_row)
        body["note_status"] = "accepted" if accept else "declined"
        await conn.execute(
            "update public.inbox_item set body = $2::jsonb where id = $1",
            item_id,
            json.dumps(body, default=str),
        )
    item = await get_item(pool, item_id)
    assert item is not None
    return item, created
