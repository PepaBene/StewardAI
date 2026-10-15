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
from steward.engine import change, config_change
from steward.engine.change import Gates, Metrics
from steward.ops import tasks

logger = logging.getLogger("steward.ops")

WINDOW_DAYS = 60
MIN_ALERTS = 10
MAX_ACK_RATE = 0.1
COOLDOWN_DAYS = 90
FLOOR = 2
KIND = "alert_tuning"
MISS_WEIGHT = 3
RULE_GATES = Gates(
    min_n=MIN_ALERTS,
    min_reviewed=MIN_ALERTS,
    max_wrong_rate_increase=0.0,
    max_cost_increase=1.0,
    max_escalation_drop=1.0,
    require_golden=False,
)


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


NOTICE_KIND = "notice_tuning"
NOTICE_MIN = 8
# Optional notice kinds and the Ops setting that produces them. Notices that guard money or
# data (rebalance, thesis break, missing key, release gate) are never switched off.
OPTIONAL_NOTICES: dict[str, str] = {
    "opportunity": "suggest_opportunities",
    "journal_gap": "journal_gaps",
}


def pick_notice(resolved: int, dismissed: int) -> bool:
    """Switch an optional notice off: enough were closed and every one was dismissed."""
    return resolved >= NOTICE_MIN and dismissed == resolved


async def tune_notices(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, Any]:
    """A kind of optional notice that was dismissed every time (and never resolved by acting
    on it) in the last 60 days is switched off in the Ops settings, with a notice saying so.
    It is the Jobs config, so rolling it back turns the notice on again."""
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    rows = await pool.fetch(
        "select kind, count(*) as resolved,"
        " count(*) filter (where last_action = 'dismissed') as dismissed"
        " from public.ops_task where kind = any($1::text[]) and status = 'resolved'"
        " and resolved_at >= $2 group by kind",
        sorted(OPTIONAL_NOTICES),
        now - dt.timedelta(days=WINDOW_DAYS),
    )
    for r in rows:
        flag = OPTIONAL_NOTICES[r["kind"]]
        if not getattr(config.ops_settings, flag) or not pick_notice(r["resolved"], r["dismissed"]):
            continue
        new = config.model_copy(
            update={"ops_settings": config.ops_settings.model_copy(update={flag: False})}
        )
        async with pool.acquire() as conn:
            await save_config(
                conn,
                "jobs",
                new,
                created_by=None,
                note=f"Automatic: all {r['resolved']} {r['kind']} notices were dismissed.",
                expected_active_id=stored.id if stored else None,
            )
        await tasks.see(
            pool,
            NOTICE_KIND,
            r["kind"],
            title=f"{r['kind'].replace('_', ' ').capitalize()} notices are switched off",
            detail=(
                f"You dismissed all {r['resolved']} of them in the last {WINDOW_DAYS} days. "
                f"Turn `{flag}` back on in Jobs config → ops settings if you want them."
            ),
            href="/config/jobs",
            weight=0.1,
            status="needs_owner",
        )
        return {"switched_off": flag}  # one change per run
    return {"switched_off": None}


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
    if chosen.event_type not in config.severities:
        return {"lowered": None}
    if await config_change.blocked(pool, f"config:event_rules:severities.{chosen.event_type}", now):
        return {"lowered": None, "blocked": chosen.event_type}
    # The change goes through the engine's pipeline: its numbers are stored, its gates are fixed
    # beforehand, and it shows in the engine ledger. An alert you needed and would have missed
    # counts as three ignored ones.
    kind_name = chosen.event_type.replace("_", " ")
    proposal = await config_change.propose(
        pool,
        "rule",
        config_change.Edit("event_rules", f"severities.{chosen.event_type}"),
        severities[chosen.event_type] - 1,
        f"{kind_name.capitalize()} stops being an instant alert: you acknowledged "
        f"{chosen.acknowledged} of {chosen.sent} in {WINDOW_DAYS} days",
        "alerts you acted on that would have waited for the digest",
        RULE_GATES,
    )
    ignored = chosen.sent - chosen.acknowledged
    await change.record_results(
        pool,
        proposal.id,
        Metrics(chosen.sent, chosen.sent, ignored, 0, 0, 0.0),
        Metrics(chosen.sent, chosen.sent, MISS_WEIGHT * chosen.acknowledged, 0, 0, 0.0),
    )
    settled = await config_change.promote(pool, proposal, golden_passed=None)
    if settled.status != "promoted":
        return {"lowered": None, "rejected": chosen.event_type}
    saved_id = await pool.fetchval(
        "select id from public.config_version where domain = 'event_rules' and active"
    )
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
    return {"lowered": chosen.event_type, "config_version": saved_id}
