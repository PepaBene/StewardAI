"""The `job_queue` table: the API enqueues on-demand runs, the worker consumes them.

A message is read by hiding it for a visibility timeout (`vt`); it comes back if it is not
archived (deleted) in time, so a worker that dies mid-run does not lose the job. Several workers
can read at once: `for update skip locked` gives each a different message.
"""

import json
from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class QueuedJob:
    msg_id: int
    read_count: int
    job_id: int
    job_type: str


async def enqueue(
    conn: asyncpg.Connection | asyncpg.Pool, job_id: int, job_type: str, *, delay: int = 0
) -> int:
    """Add a job to the queue, readable after `delay` seconds."""
    msg_id: int = await conn.fetchval(
        "insert into public.job_queue (message, vt)"
        " values ($1::jsonb, now() + make_interval(secs => $2::integer)) returning msg_id",
        json.dumps({"job_id": job_id, "job_type": job_type}),
        delay,
    )
    return msg_id


async def read(
    conn: asyncpg.Connection | asyncpg.Pool, *, visibility_timeout: int, limit: int
) -> list[QueuedJob | int]:
    """Next messages, hidden from other readers for `visibility_timeout` seconds.
    Unreadable messages are returned as their bare msg_id so the caller can archive them."""
    rows = await conn.fetch(
        "with next as ("
        "  select msg_id from public.job_queue where vt <= now()"
        "  order by msg_id limit $2 for update skip locked)"
        " update public.job_queue q"
        " set vt = now() + make_interval(secs => $1::integer), read_ct = q.read_ct + 1"
        " from next where q.msg_id = next.msg_id"
        " returning q.msg_id, q.read_ct, q.message",
        visibility_timeout,
        limit,
    )
    messages: list[QueuedJob | int] = []
    for row in sorted(rows, key=lambda r: r["msg_id"]):
        try:
            body = json.loads(row["message"])
            messages.append(
                QueuedJob(row["msg_id"], row["read_ct"], int(body["job_id"]), str(body["job_type"]))
            )
        except (KeyError, TypeError, ValueError):
            messages.append(row["msg_id"])
    return messages


async def archive(conn: asyncpg.Connection | asyncpg.Pool, msg_id: int) -> None:
    await conn.execute("delete from public.job_queue where msg_id = $1", msg_id)


async def set_visibility(
    conn: asyncpg.Connection | asyncpg.Pool, msg_id: int, seconds: int
) -> None:
    """Hide message `msg_id` from readers for `seconds` from now (0: visible again now)."""
    await conn.execute(
        "update public.job_queue set vt = now() + make_interval(secs => $2::integer)"
        " where msg_id = $1",
        msg_id,
        seconds,
    )
