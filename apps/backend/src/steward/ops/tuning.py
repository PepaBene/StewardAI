"""Alert tuning (E2): an instant alert the owner never acknowledges drops to a digest highlight.

The one engagement signal the app stores is whether an escalation was acknowledged. For each
event type, over the last `WINDOW_DAYS`: if at least `MIN_ALERTS` instant alerts were sent and
under `MAX_ACK_RATE` of them were acknowledged, that type's severity goes from 3 (instant alert)
to 2 (digest highlight tied to the thesis). It is not silenced: the floor is 2, so the story
still reaches the digest.

Bounds that keep it safe:
- one step, one type per run, and never below 2;
- the change is a new `event_rules` config version, so it shows in config history and one
  click on "roll back" undoes it;
- a type changed in the last `COOLDOWN_DAYS` is not changed again, so an owner who raises it
  back is not overruled;
- a notice in "Needs you" says what moved and why.
"""

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

import asyncpg

from steward.config.event_rules import DEFAULT_SEVERITIES, EventRulesConfig
from steward.config.jobs import JobsConfig
from steward.config.store import load_active, save_config
from steward.ops import tasks

logger = logging.getLogger("steward.ops")

WINDOW_DAYS = 60
MIN_ALERTS = 10
MAX_ACK_RATE = 0.1
COOLDOWN_DAYS = 90
FLOOR = 2
KIND = "alert_tuning"


@dataclass(frozen=True)
class Engagement:
    event_type: str
    sent: int
    acknowledged: int

    @property
    def rate(self) -> float:
        return self.acknowledged / self.sent if self.sent else 1.0


def pick(
    rows: list[Engagement], severities: dict[str, int], recently_tuned: set[str]
) -> Engagement | None:
    """The one type to lower: the most ignored instant alert, or None."""
    candidates = [
        r
        for r in rows
        if r.sent >= MIN_ALERTS
        and r.rate < MAX_ACK_RATE
        and severities.get(r.event_type, 0) > FLOOR
        and r.event_type not in recently_tuned
    ]
    return min(candidates, key=lambda r: (r.rate, -r.sent), default=None)


async def engagement(pool: asyncpg.Pool, now: dt.datetime) -> list[Engagement]:
    rows = await pool.fetch(
        "select body->>'event_type' as event_type, count(*) as sent,"
        " count(acknowledged_at) as acknowledged from public.report"
        " where type = 'escalation' and created_at >= $1 and body->>'event_type' is not null"
        " group by 1",
        now - dt.timedelta(days=WINDOW_DAYS),
    )
    return [Engagement(r["event_type"], r["sent"], r["acknowledged"]) for r in rows]


INBOX_KIND = "inbox_tuning"
INBOX_MIN_DECIDED = 10
INBOX_MAX_TOP = 3


def pick_inbox(decided: int, engaged: int, min_severity: int, recently: bool) -> bool:
    """Raise the inbox's minimum severity one step: enough items were decided, almost none were
    acted on or marked useful, and the minimum is not already at the top or just changed."""
    return (
        not recently
        and min_severity < INBOX_MAX_TOP
        and decided >= INBOX_MIN_DECIDED
        and engaged / decided < MAX_ACK_RATE
    )


async def tune_inbox(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, Any]:
    """The inbox starts an agent run for every alert at or above `min_severity`. When the items
    at that level are dismissed nearly every time, the level goes up by one."""
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    level = config.inbox_settings.min_severity
    row = await pool.fetchrow(
        "select count(*) filter (where status in ('actioned', 'dismissed')) as decided,"
        " count(*) filter (where status = 'actioned' or feedback = 'useful') as engaged"
        " from public.inbox_item where severity = $1 and created_at >= $2",
        level,
        now - dt.timedelta(days=WINDOW_DAYS),
    )
    recently = bool(
        await pool.fetchval(
            "select exists (select 1 from public.ops_task where kind = $1 and first_seen >= $2)",
            INBOX_KIND,
            now - dt.timedelta(days=COOLDOWN_DAYS),
        )
    )
    decided, engaged = (row["decided"], row["engaged"]) if row else (0, 0)
    if not pick_inbox(decided, engaged, level, recently):
        return {"raised": None}
    new = config.model_copy(
        update={
            "inbox_settings": config.inbox_settings.model_copy(update={"min_severity": level + 1})
        }
    )
    async with pool.acquire() as conn:
        saved = await save_config(
            conn,
            "jobs",
            new,
            created_by=None,
            note=f"Automatic: {engaged} of {decided} severity-{level} inbox items were used.",
            expected_active_id=stored.id if stored else None,
        )
    await tasks.see(
        pool,
        INBOX_KIND,
        f"severity-{level}",
        title=f"Severity {level} alerts no longer start an inbox run",
        detail=(
            f"You used {engaged} of {decided} inbox items at severity {level} in the last "
            f"{WINDOW_DAYS} days, so the inbox now starts at severity {level + 1}. Roll back "
            "the jobs config to restore it."
        ),
        href="/config/jobs",
        weight=0.2,
        status="needs_owner",
    )
    return {"raised": level + 1, "config_version": saved.id}


async def tune_alerts(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, Any]:
    stored = await load_active(pool, "event_rules", EventRulesConfig)
    config = stored.body if stored else EventRulesConfig()
    severities: dict[str, int] = {**DEFAULT_SEVERITIES}
    for event_type, level in config.severities.items():
        severities[str(event_type)] = level
    recently = {
        r["subject"]
        for r in await pool.fetch(
            "select subject from public.ops_task where kind = $1 and first_seen >= $2",
            KIND,
            now - dt.timedelta(days=COOLDOWN_DAYS),
        )
    }
    chosen = pick(await engagement(pool, now), severities, recently)
    if chosen is None:
        return {"lowered": None}
    new = config.model_copy(
        update={
            "severities": {
                **config.severities,
                chosen.event_type: severities[chosen.event_type] - 1,
            }
        }
    )
    async with pool.acquire() as conn:
        saved = await save_config(
            conn,
            "event_rules",
            new,
            created_by=None,
            note=f"Automatic: {chosen.event_type} alerts were acknowledged "
            f"{chosen.acknowledged} of {chosen.sent} times.",
            expected_active_id=stored.id if stored else None,
        )
    kind_name = chosen.event_type.replace("_", " ")
    await tasks.see(
        pool,
        KIND,
        chosen.event_type,
        title=f"{kind_name.capitalize()} is no longer an instant alert",
        detail=(
            f"You acknowledged {chosen.acknowledged} of {chosen.sent} instant alerts about "
            f"{kind_name} in the last {WINDOW_DAYS} days, so it now goes to the digest as a "
            "highlight. Roll back the event rules config to restore it."
        ),
        href="/config/event-rules",
        weight=0.2,
        status="needs_owner",
    )
    return {"lowered": chosen.event_type, "config_version": saved.id}
