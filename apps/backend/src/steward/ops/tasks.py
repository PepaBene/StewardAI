"""The ops task ledger (`ops_task`): problems the watchdog is fixing or has handed to the owner.

A task is keyed by (kind, subject) and has at most one open row. The watchdog calls `see` for
every problem it finds in a run, `acted` when it tries a repair, and `settle` with the problems
it did not find, which resolves their tasks. `decide` is the retry policy, kept pure.
"""

import datetime as dt
from typing import Literal

import asyncpg
from pydantic import BaseModel

Status = Literal["fixing", "needs_owner", "resolved"]
Step = Literal["repair", "wait", "escalate", "hold"]

SEE = (
    "insert into public.ops_task (kind, subject, status, title, detail, href, weight)"
    " values ($1, $2, $3, $4, $5, $6, $7)"
    " on conflict (kind, subject) where status <> 'resolved' do update set"
    " title = excluded.title, detail = excluded.detail, href = excluded.href,"
    " weight = excluded.weight, last_seen = now()"
    " returning id, kind, subject, status, title, detail, href, weight, attempts,"
    " last_action, last_action_at, first_seen, last_seen"
)
NEEDS_OWNER = (
    "select id, kind, subject, status, title, detail, href, weight, attempts,"
    " last_action, last_action_at, first_seen, last_seen from public.ops_task"
    " where status = 'needs_owner' order by coalesce(weight, 0) desc, first_seen"
)


class OpsTask(BaseModel):
    id: int
    kind: str
    subject: str
    status: Status
    title: str
    detail: str
    href: str | None
    weight: float | None
    attempts: int
    last_action: str | None
    last_action_at: dt.datetime | None
    first_seen: dt.datetime
    last_seen: dt.datetime


def decide(
    task: OpsTask, now: dt.datetime, *, retry_hours: int, max_attempts: int, can_repair: bool
) -> Step:
    """What to do about a problem seen again.

    - `repair`: no attempt yet, or the last one is old enough and attempts remain.
    - `wait`: an attempt is recent; give it time.
    - `escalate`: attempts are used up and the last one has had time to work.
    - `hold`: the problem has no repair (only the owner can fix it) or it is already with the
      owner.
    """
    if task.status == "needs_owner" or not can_repair:
        return "hold"
    if task.attempts == 0:
        return "repair"
    since = now - task.last_action_at if task.last_action_at else dt.timedelta.max
    if since < dt.timedelta(hours=retry_hours):
        return "wait"
    return "escalate" if task.attempts >= max_attempts else "repair"


async def see(
    pool: asyncpg.Pool,
    kind: str,
    subject: str,
    *,
    title: str,
    detail: str,
    href: str | None = None,
    weight: float | None = None,
    status: Status = "fixing",
) -> OpsTask:
    """Record that the problem exists now; open its task or refresh the open one."""
    row = await pool.fetchrow(
        SEE,
        kind,
        subject,
        status,
        title,
        detail,
        href,
        weight,
    )
    return OpsTask.model_validate(dict(row))


async def acted(pool: asyncpg.Pool, task_id: int, action: str) -> None:
    await pool.execute(
        "update public.ops_task set attempts = attempts + 1, last_action = $2,"
        " last_action_at = now() where id = $1",
        task_id,
        action,
    )


async def hand_over(pool: asyncpg.Pool, task_id: int) -> None:
    await pool.execute(
        "update public.ops_task set status = 'needs_owner' where id = $1 and status = 'fixing'",
        task_id,
    )


async def dismiss(pool: asyncpg.Pool, task_id: int) -> bool:
    """The owner set a notice aside. A missing key stays dismissed; a problem that is still
    there on a later run opens a new notice."""
    result = await pool.execute(
        "update public.ops_task set status = 'resolved', resolved_at = now(),"
        " last_action = 'dismissed' where id = $1 and status = 'needs_owner'",
        task_id,
    )
    return bool(result.endswith(" 1"))


async def settle(pool: asyncpg.Pool, kind: str, present: set[str]) -> int:
    """Resolve the open tasks of `kind` whose subject was not seen this run."""
    result = await pool.execute(
        "update public.ops_task set status = 'resolved', resolved_at = now()"
        " where kind = $1 and status <> 'resolved' and not (subject = any($2::text[]))",
        kind,
        sorted(present),
    )
    return int(result.rsplit(" ", 1)[-1])


async def needs_owner(pool: asyncpg.Pool) -> list[OpsTask]:
    rows = await pool.fetch(NEEDS_OWNER)
    return [OpsTask.model_validate(dict(r)) for r in rows]


async def counts(pool: asyncpg.Pool, since: dt.datetime) -> dict[str, int]:
    """Open and resolved-since task counts, for the autopilot report."""
    row = await pool.fetchrow(
        "select count(*) filter (where status = 'fixing') as fixing,"
        " count(*) filter (where status = 'needs_owner') as needs_owner,"
        " count(*) filter (where status = 'resolved' and resolved_at >= $1) as resolved"
        " from public.ops_task",
        since,
    )
    return {k: int(v) for k, v in dict(row).items()}
