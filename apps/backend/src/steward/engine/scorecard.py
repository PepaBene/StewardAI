"""Is the autonomy working? The plan's success metrics, measured (autonomy plan, "Success metrics").

Read-only. Every figure comes from what the app already stores: the decision ledger, the notices
that need you, the liveness notices and the month's AI spend. Each metric carries its target and
whether it is met, so a number that drifts is visible without anyone doing arithmetic.
"""

import datetime as dt
from decimal import Decimal

import asyncpg
from pydantic import BaseModel

from steward.autopilot import ledger
from steward.budget_guard import month_of, month_spend
from steward.config.budget import BudgetConfig
from steward.config.store import load_active

WINDOW_DAYS = 28
ITEMS_PER_WEEK_TARGET = 5.0
AUTOMATIC_SHARE_TARGET = 0.9
PRECISION_TARGET = 0.95
MIN_REVIEWED = 20  # fewer reviewed decisions than this say nothing about precision
OLDEST_OPEN_DAYS_TARGET = 14.0


class KindPrecision(BaseModel):
    kind: str
    reviewed: int
    right: int
    precision: float | None
    enough_evidence: bool
    met: bool | None


class AutonomyScorecard(BaseModel):
    window_days: int
    items_per_week: float
    items_per_week_target: float
    items_open_now: int
    items_met: bool
    decisions_automatic: int
    decisions_shadow: int
    decisions_escalated: int
    automatic_share: float | None
    automatic_share_target: float
    automatic_met: bool | None
    precision: list[KindPrecision]
    precision_target: float
    oldest_open_days: float | None
    oldest_open_days_target: float
    oldest_met: bool | None
    late_jobs: int
    missing_keys: int
    spend_month: float
    spend_cap: float | None
    spend_met: bool | None


def share(automatic: int, shadow: int, escalated: int) -> float | None:
    """Of the decisions the app had to make, the part it made and applied by itself."""
    total = automatic + shadow + escalated
    return automatic / total if total else None


def kind_precision(kind: str, reviewed: int, right: int) -> KindPrecision:
    enough = reviewed >= MIN_REVIEWED
    value = right / reviewed if reviewed else None
    return KindPrecision(
        kind=kind,
        reviewed=reviewed,
        right=right,
        precision=value,
        enough_evidence=enough,
        met=(value >= PRECISION_TARGET) if enough and value is not None else None,
    )


async def build(pool: asyncpg.Pool, now: dt.datetime) -> AutonomyScorecard:
    since = now - dt.timedelta(days=WINDOW_DAYS)
    weeks = WINDOW_DAYS / 7
    new_notices = int(
        await pool.fetchval(
            "select count(*) from public.ops_task where status <> 'fixing' and first_seen >= $1"
            " and kind not in ('liveness', 'missing_key')",
            since,
        )
        or 0
    )
    stats = await ledger.stats(pool, since)
    escalated = sum(s.escalated for s in stats)
    items_per_week = (new_notices + escalated) / weeks
    open_notices = int(
        await pool.fetchval("select count(*) from public.ops_task where status = 'needs_owner'")
        or 0
    )
    open_escalations = len(await ledger.escalations(pool))
    automatic = sum(s.applied for s in stats)
    shadow = sum(s.shadow for s in stats)
    automatic_share = share(automatic, shadow, escalated)
    oldest = await pool.fetchval(
        "select min(t) from (select min(decided_at) as t from public.auto_decision"
        " where status = 'escalated' union all select min(first_seen) from public.ops_task"
        " where status = 'needs_owner') x"
    )
    oldest_days = (now - oldest).total_seconds() / 86400 if oldest else None
    late = int(
        await pool.fetchval(
            "select count(*) from public.ops_task where kind = 'liveness' and status <> 'resolved'"
        )
        or 0
    )
    keys = int(
        await pool.fetchval(
            "select count(*) from public.ops_task where kind = 'missing_key'"
            " and status <> 'resolved'"
        )
        or 0
    )
    budget = await load_active(pool, "budget", BudgetConfig)
    cap = budget.body.monthly_cap_usd if budget else None
    spent: Decimal = await month_spend(pool, month_of(now))
    return AutonomyScorecard(
        window_days=WINDOW_DAYS,
        items_per_week=round(items_per_week, 1),
        items_per_week_target=ITEMS_PER_WEEK_TARGET,
        items_open_now=open_notices + open_escalations,
        items_met=items_per_week < ITEMS_PER_WEEK_TARGET,
        decisions_automatic=automatic,
        decisions_shadow=shadow,
        decisions_escalated=escalated,
        automatic_share=None if automatic_share is None else round(automatic_share, 3),
        automatic_share_target=AUTOMATIC_SHARE_TARGET,
        automatic_met=None
        if automatic_share is None
        else automatic_share >= AUTOMATIC_SHARE_TARGET,
        precision=[kind_precision(s.kind, s.reviewed, s.right) for s in stats if s.reviewed],
        precision_target=PRECISION_TARGET,
        oldest_open_days=None if oldest_days is None else round(oldest_days, 1),
        oldest_open_days_target=OLDEST_OPEN_DAYS_TARGET,
        oldest_met=None if oldest_days is None else oldest_days <= OLDEST_OPEN_DAYS_TARGET,
        late_jobs=late,
        missing_keys=keys,
        spend_month=round(float(spent), 2),
        spend_cap=cap,
        spend_met=None if cap is None else float(spent) <= cap,
    )
