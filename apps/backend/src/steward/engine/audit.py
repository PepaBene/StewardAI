"""Reward-hacking audit (autonomous engine plan, L5/section 6).

The numbers the engine can improve by itself (fewer escalations, fewer reviews needed) are not
the numbers that say it is right. This audit compares two 30-day windows per autopilot kind and
raises a notice when the easy numbers got better while the owner-judged ones did not:

- escalations fell sharply while the share of reviewed decisions judged wrong rose; or
- decisions rose while the share of them reviewed collapsed (the engine is being checked less).

It only reads the ledger and posts a notice; it changes nothing.
"""

import datetime as dt
from dataclasses import dataclass

import asyncpg

from steward.ops import tasks

KIND = "engine_audit"
WINDOW_DAYS = 30
MIN_DECIDED = 20
ESCALATION_DROP = 0.15
WRONG_RISE = 0.05


@dataclass(frozen=True)
class Window:
    decided: int  # applied + shadow + reverted
    escalated: int
    reviewed: int
    wrong: int

    @property
    def escalation_rate(self) -> float:
        total = self.decided + self.escalated
        return self.escalated / total if total else 0.0

    @property
    def wrong_rate(self) -> float | None:
        return self.wrong / self.reviewed if self.reviewed else None

    @property
    def review_share(self) -> float:
        return self.reviewed / self.decided if self.decided else 0.0


def gaming(prev: Window, now: Window) -> list[str]:
    """Reasons the engine's improvement looks hollow; empty when nothing stands out."""
    if prev.decided < MIN_DECIDED or now.decided < MIN_DECIDED:
        return []
    reasons: list[str] = []
    pw, nw = prev.wrong_rate, now.wrong_rate
    if (
        prev.escalation_rate - now.escalation_rate >= ESCALATION_DROP
        and pw is not None
        and nw is not None
        and nw - pw >= WRONG_RISE
    ):
        reasons.append(
            f"it escalates less ({prev.escalation_rate:.0%} to {now.escalation_rate:.0%}) while "
            f"more of what it decides is judged wrong ({pw:.0%} to {nw:.0%})"
        )
    if now.decided > prev.decided and now.review_share < prev.review_share / 2:
        reasons.append(
            f"it decides more but only {now.review_share:.0%} is reviewed "
            f"(was {prev.review_share:.0%})"
        )
    return reasons


async def window(pool: asyncpg.Pool, kind: str, start: dt.datetime, end: dt.datetime) -> Window:
    row = await pool.fetchrow(
        "select count(*) filter (where status in ('applied', 'shadow', 'reverted')) as decided,"
        " count(*) filter (where status in ('escalated', 'closed')) as escalated,"
        " count(*) filter (where status in ('applied', 'shadow', 'reverted')"
        "   and review is not null) as reviewed,"
        " count(*) filter (where status in ('applied', 'shadow', 'reverted')"
        "   and review = 'wrong') as wrong"
        " from public.auto_decision where kind = $1 and decided_at >= $2 and decided_at < $3",
        kind,
        start,
        end,
    )
    return Window(row["decided"], row["escalated"], row["reviewed"], row["wrong"])


async def audit(pool: asyncpg.Pool, kinds: list[str], now: dt.datetime) -> dict[str, list[str]]:
    """Check each kind; open a notice for those that look hollow and resolve the rest."""
    span = dt.timedelta(days=WINDOW_DAYS)
    flagged: dict[str, list[str]] = {}
    for kind in kinds:
        reasons = gaming(
            await window(pool, kind, now - 2 * span, now - span),
            await window(pool, kind, now - span, now),
        )
        if not reasons:
            await pool.execute(
                "update public.ops_task set status = 'resolved', resolved_at = now()"
                " where kind = $1 and subject = $2 and status <> 'resolved'",
                KIND,
                kind,
            )
            continue
        flagged[kind] = reasons
        await tasks.see(
            pool,
            KIND,
            kind,
            title=f"The {kind} autopilot may be looking better than it is",
            detail="; ".join(reasons).capitalize() + ". Review its recent decisions.",
            href="/autopilot",
            weight=0.5,
            status="needs_owner",
        )
    return flagged
