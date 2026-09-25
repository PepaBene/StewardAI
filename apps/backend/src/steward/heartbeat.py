"""Worker liveness: a heartbeat row the API's readiness check reads, and an optional ping
to an external dead-man's-switch monitor.

The uptime monitor watches `GET /health` (the API process) and `GET /health/ready` (the
database and a worker heartbeat younger than `STALE_AFTER`), so a stopped API, a lost
database or a stuck worker each raise an alert outside the host.
"""

import datetime as dt
import json
import os
from dataclasses import dataclass

import asyncpg

from steward import __version__

HEARTBEAT_KEY = "worker_heartbeat"
HEARTBEAT_SECONDS = 30
STALE_AFTER = dt.timedelta(minutes=3)


async def beat(pool: asyncpg.Pool, now: dt.datetime | None = None) -> None:
    at = now or dt.datetime.now(dt.UTC)
    await pool.execute(
        "insert into public.sync_state (key, value) values ($1, $2::jsonb)"
        " on conflict (key) do update set value = excluded.value",
        HEARTBEAT_KEY,
        json.dumps({"at": at.isoformat(), "version": __version__, "pid": os.getpid()}),
    )


@dataclass(frozen=True)
class WorkerStatus:
    last_beat: dt.datetime | None
    alive: bool


async def worker_status(
    conn: asyncpg.Pool | asyncpg.Connection, now: dt.datetime | None = None
) -> WorkerStatus:
    raw = await conn.fetchval("select value from public.sync_state where key = $1", HEARTBEAT_KEY)
    if raw is None:
        return WorkerStatus(None, False)
    last = dt.datetime.fromisoformat(json.loads(raw)["at"])
    moment = now or dt.datetime.now(dt.UTC)
    return WorkerStatus(last, moment - last <= STALE_AFTER)
