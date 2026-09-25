"""The pgmq `jobs` queue: the API enqueues on-demand runs, the worker consumes them."""

import json
from dataclasses import dataclass

import asyncpg

QUEUE = "jobs"


@dataclass(frozen=True)
class QueuedJob:
    msg_id: int
    read_count: int
    job_id: int
    job_type: str


async def enqueue(conn: asyncpg.Connection | asyncpg.Pool, job_id: int, job_type: str) -> int:
    msg_id: int = await conn.fetchval(
        "select * from pgmq.send($1, $2::jsonb)",
        QUEUE,
        json.dumps({"job_id": job_id, "job_type": job_type}),
    )
    return msg_id


async def read(
    conn: asyncpg.Connection | asyncpg.Pool, *, visibility_timeout: int, limit: int
) -> list[QueuedJob | int]:
    """Next messages, hidden from other readers for `visibility_timeout` seconds.
    Unreadable messages are returned as their bare msg_id so the caller can archive them."""
    rows = await conn.fetch(
        "select msg_id, read_ct, message from pgmq.read($1, $2, $3)",
        QUEUE,
        visibility_timeout,
        limit,
    )
    messages: list[QueuedJob | int] = []
    for row in rows:
        try:
            body = json.loads(row["message"])
            messages.append(
                QueuedJob(row["msg_id"], row["read_ct"], int(body["job_id"]), str(body["job_type"]))
            )
        except (KeyError, TypeError, ValueError):
            messages.append(row["msg_id"])
    return messages


async def archive(conn: asyncpg.Connection | asyncpg.Pool, msg_id: int) -> None:
    await conn.execute("select pgmq.archive($1, $2::bigint)", QUEUE, msg_id)
