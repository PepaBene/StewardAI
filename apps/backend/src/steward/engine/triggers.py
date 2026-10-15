"""Proactive triggers (autonomous engine plan, L4): work the engine starts on its own.

Every proactive start goes through `fire` (it does something that costs: queues a job, changes a
mode) or `log` (it only tells the owner something). Both write a row to `engine_trigger` with the
reason it started, so "why did it do that" always has an answer. `fire` also holds the start back
when:

- the same subject fired in the last `COOLDOWN_DAYS`;
- the kind already fired `DAILY_PER_KIND` times today, or all kinds `DAILY_TOTAL` times;
- today's cost of engine-started jobs passed `DAILY_USD`, or the month's AI spend is at
  `BUDGET_STOP` of the monthly cap;
- the owner dismissed the last `SUPPRESS_AFTER` notices of this kind and acted on none (the notice
  tuner, generalised): the kind stops for `SUPPRESS_DAYS` and one notice says so.

A held-back start is recorded once a day per subject and reason. An `essential` start (the check
after a company reports) ignores the cooldown and the caps, because skipping it would hide a
result; it is still recorded.
"""

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import asyncpg

from steward.budget_guard import month_of, month_spend
from steward.config.budget import BudgetConfig
from steward.config.store import load_active
from steward.ops import tasks

Kind = Literal["staleness", "event", "coverage", "anticipation", "learning"]

COOLDOWN_DAYS = 7
DAILY_PER_KIND = 3
DAILY_TOTAL = 6
DAILY_USD = 3.0
BUDGET_STOP = 0.9
SUPPRESS_AFTER = 3
SUPPRESS_DAYS = 30
STOPPED_NOTICE = "trigger_stopped"

# The notices each kind of trigger opens; what the owner does with them says if the trigger helps.
NOTICE_KINDS: dict[str, tuple[str, ...]] = {
    "coverage": ("thesis_draft",),
    "anticipation": ("pre_earnings",),
    "learning": ("learning_trigger",),
    "event": ("watch_over_cap", "watch_insider", "watch_guidance", "pre_review"),
}

KIND_NAMES = {
    "staleness": "stale research",
    "event": "event",
    "coverage": "coverage",
    "anticipation": "heads-up",
    "learning": "learning",
}


@dataclass(frozen=True)
class Outcome:
    fired: bool
    skipped: str | None = None
    job_id: int | None = None


@dataclass(frozen=True)
class Load:
    """What has already been started, for the rules in `hold_back`."""

    kind_today: int
    total_today: int
    usd_today: float
    budget_used: float | None  # share of the monthly cap spent; None when there is no cap
    dismissed: int  # of the last notices of this kind, how many were dismissed
    closed: int  # and how many were closed at all


def is_suppressed(closed: int, dismissed: int) -> bool:
    """Every recent notice of the kind was dismissed, and enough of them to mean something."""
    return dismissed >= SUPPRESS_AFTER and dismissed == closed


def hold_back(load: Load) -> str | None:
    """The reason a start is held back, or None when it may go."""
    if is_suppressed(load.closed, load.dismissed):
        return f"you dismissed the last {load.dismissed} of its notices without acting on any"
    if load.kind_today >= DAILY_PER_KIND:
        return f"{DAILY_PER_KIND} of this kind already started today"
    if load.total_today >= DAILY_TOTAL:
        return f"{DAILY_TOTAL} runs already started today"
    if load.usd_today >= DAILY_USD:
        return f"today's cost of engine-started runs is at its ${DAILY_USD:.2f} cap"
    if load.budget_used is not None and load.budget_used >= BUDGET_STOP:
        return f"this month's AI budget is {load.budget_used:.0%} used"
    return None


def _day_start(now: dt.datetime) -> dt.datetime:
    return now.astimezone(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def load(pool: asyncpg.Pool, kind: str, now: dt.datetime) -> Load:
    day = _day_start(now)
    counts = await pool.fetchrow(
        "select count(*) filter (where kind = $1) as kind_today,"
        " count(*) filter (where job_id is not null) as total_today"
        " from public.engine_trigger where status = 'fired' and created_at >= $2",
        kind,
        day,
    )
    # Only jobs this table started: other event-triggered jobs (news escalation, watchdog retries,
    # fact refreshes) have their own budgets and must not use up the engine's day.
    usd = await pool.fetchval(
        "select coalesce(sum(s.cost_usd), 0) from public.job_step s"
        " where s.cost_usd is not null and s.started_at >= $1 and s.job_id in ("
        "  select t.job_id from public.engine_trigger t"
        "  where t.status = 'fired' and t.job_id is not null)",
        day,
    )
    stored = await load_active(pool, "budget", BudgetConfig)
    cap = (stored.body if stored else BudgetConfig()).monthly_cap_usd
    used = None
    if cap:
        used = float(await month_spend(pool, month_of(now))) / cap
    notices = NOTICE_KINDS.get(kind, ())
    row = (
        await pool.fetchrow(
            "select count(*) filter (where last_action = 'dismissed') as dismissed,"
            " count(*) as closed from public.ops_task where kind = any($1::text[])"
            " and status = 'resolved' and resolved_at >= $2",
            list(notices),
            now - dt.timedelta(days=SUPPRESS_DAYS),
        )
        if notices
        else None
    )
    return Load(
        kind_today=counts["kind_today"],
        total_today=counts["total_today"],
        usd_today=float(usd),
        budget_used=used,
        dismissed=row["dismissed"] if row else 0,
        closed=row["closed"] if row else 0,
    )


async def suppressed(pool: asyncpg.Pool, kind: Kind, now: dt.datetime | None = None) -> bool:
    """The owner has been dismissing this kind of notice without acting; callers that only open
    notices (no `fire`) ask this before they open one."""
    loaded = await load(pool, kind, now or dt.datetime.now(dt.UTC))
    return is_suppressed(loaded.closed, loaded.dismissed)


async def _record(
    pool: asyncpg.Pool,
    kind: str,
    subject: str,
    reason: str,
    action: str,
    status: str,
    skip_reason: str | None,
    job_id: int | None,
) -> None:
    await pool.execute(
        "insert into public.engine_trigger (kind, subject, reason, action, status, skip_reason,"
        " job_id) values ($1, $2, $3, $4, $5, $6, $7)",
        kind,
        subject[:200],
        reason[:1000],
        action[:200],
        status,
        skip_reason,
        job_id,
    )


async def _recent(
    pool: asyncpg.Pool, kind: str, subject: str, since: dt.datetime, status: str
) -> bool:
    return bool(
        await pool.fetchval(
            "select exists (select 1 from public.engine_trigger where kind = $1 and subject = $2"
            " and status = $3 and created_at >= $4)",
            kind,
            subject,
            status,
            since,
        )
    )


async def fire(
    pool: asyncpg.Pool,
    kind: Kind,
    subject: str,
    reason: str,
    action: str,
    act: Callable[[], Awaitable[int | None]],
    *,
    now: dt.datetime | None = None,
    essential: bool = False,
) -> Outcome:
    """Start `act` (it returns the id of the job it queued, if any) unless something holds it
    back. Always leaves a row saying why it started, or why it did not."""
    now = now or dt.datetime.now(dt.UTC)
    if not essential:
        if await _recent(pool, kind, subject, now - dt.timedelta(days=COOLDOWN_DAYS), "fired"):
            return Outcome(False, "cooldown")
        held = hold_back(await load(pool, kind, now))
        if held is not None:
            if not await _recent(pool, kind, subject, _day_start(now), "skipped"):
                await _record(pool, kind, subject, reason, action, "skipped", held, None)
            if held.startswith("you dismissed"):
                await tasks.see(
                    pool,
                    STOPPED_NOTICE,
                    kind,
                    title=f"Proactive {KIND_NAMES[kind]} runs are paused",
                    detail=f"The engine stopped starting them for {SUPPRESS_DAYS} days: {held}.",
                    href="/engine-ledger",
                    weight=0.1,
                    status="needs_owner",
                )
            return Outcome(False, held)
    job_id = await act()
    await _record(pool, kind, subject, reason, action, "fired", None, job_id)
    return Outcome(True, None, job_id)


async def log(
    pool: asyncpg.Pool,
    kind: Kind,
    subject: str,
    reason: str,
    action: str,
    *,
    now: dt.datetime | None = None,
    cooldown_days: int = COOLDOWN_DAYS,
) -> bool:
    """Record a start that costs nothing (a notice). False when the same subject was logged
    inside the cooldown, so the caller opens the notice only once."""
    now = now or dt.datetime.now(dt.UTC)
    if await _recent(pool, kind, subject, now - dt.timedelta(days=cooldown_days), "fired"):
        return False
    await _record(pool, kind, subject, reason, action, "fired", None, None)
    return True


async def recent(pool: asyncpg.Pool, limit: int = 100) -> list[asyncpg.Record]:
    return list(
        await pool.fetch(
            "select id, created_at, kind, subject, reason, action, status, skip_reason, job_id"
            " from public.engine_trigger order by created_at desc, id desc limit $1",
            limit,
        )
    )
