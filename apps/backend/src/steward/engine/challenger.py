"""Runs the self-change pipeline (autonomous engine plan, 3.1 and 3.2).

`change` holds the rules (gates, verdict, freeze list); this module feeds them real numbers.
Only the change the engine can test honestly from its own ledger is run here: a stricter
confidence floor for an autopilot kind, replayed over the decisions already made.

- `settle_floors`: for each shadow floor change, replay the last `REPLAY_DAYS` of that kind's
  decisions with and without the floor, store both sets of numbers, apply the gates. A pass
  promotes it: `ledger.learned_thresholds` then includes the floor. Only a floor that makes the
  autopilot ask for more confidence is ever applied by itself; anything looser waits for the owner.
- `check_rollbacks`: a promoted change is compared with its baseline once it has run for
  `ROLLBACK_AFTER_DAYS`; a regression rolls it back. Either way the owner gets a notice.

The gates come from `engine.gates` (the owner edits them; the engine cannot), and the proposal
stores a copy so a
later edit of this file cannot bend the verdict on an old proposal.
"""

import datetime as dt
from typing import Any

import asyncpg

from steward.engine import change
from steward.engine.change import Change, Gates, Metrics
from steward.ops import tasks

KIND = "engine_change"
REPLAY_DAYS = 90
ROLLBACK_AFTER_DAYS = 14
ROLLBACK_UNTIL_DAYS = 28
DECIDED = ("applied", "shadow", "reverted")

# A stricter floor must show fewer wrong decisions (by a point), not only no more of them. A
# threshold has no golden set to run, so that gate does not apply to it.
FLOOR_GATES = Gates(min_n=30, min_reviewed=10, max_wrong_rate_increase=-0.01, require_golden=False)


def _metrics(rows: list[asyncpg.Record], escalated: int) -> Metrics:
    reviewed = [r for r in rows if r["review"] is not None]
    return Metrics(
        n=len(rows),
        reviewed=len(reviewed),
        wrong=sum(1 for r in reviewed if r["review"] == "wrong"),
        escalated=escalated,
        verifier_failures=0,
        cost=0.0,
    )


def replay(rows: list[asyncpg.Record], floor: float, escalated: int) -> tuple[Metrics, Metrics]:
    """(as it was, with `floor` applied). A decision below the floor would have waited for the
    owner instead; one with no confidence (a rule) is kept."""
    kept = [r for r in rows if r["confidence"] is None or r["confidence"] >= floor]
    return _metrics(rows, escalated), _metrics(kept, escalated + len(rows) - len(kept))


OWNER_ACCEPTED = "owner-accepted"


async def _window(
    pool: asyncpg.Pool, kind: str, start: dt.datetime, end: dt.datetime
) -> tuple[list[asyncpg.Record], int]:
    """(the engine's own decisions in the window, how many items it escalated). A decision the
    owner made by accepting an escalated item is not the engine's: a floor would not have
    changed it, so it is left out of the replay; the escalated item it came from counts as an
    escalation (its row is `closed`)."""
    rows = await pool.fetch(
        "select confidence, status, review, decided_by from public.auto_decision"
        " where kind = $1 and decided_at >= $2 and decided_at < $3",
        kind,
        start,
        end,
    )
    return (
        [r for r in rows if r["status"] in DECIDED and r["decided_by"] != OWNER_ACCEPTED],
        sum(1 for r in rows if r["status"] in ("escalated", "closed")),
    )


async def owner_acceptance(
    pool: asyncpg.Pool, kind: str, start: dt.datetime, end: dt.datetime
) -> tuple[int, int] | None:
    """(items you accepted, items that reached you) among the escalations of the window, or None
    when none was settled. It tells whether the work a stricter floor would add is work you
    usually just approve."""
    row = await pool.fetchrow(
        "select count(*) filter (where status = 'closed') as accepted,"
        " count(*) as total from public.auto_decision where kind = $1 and decided_at >= $2"
        " and decided_at < $3 and status in ('escalated', 'closed')",
        kind,
        start,
        end,
    )
    if row is None or row["total"] == 0:
        return None
    return int(row["accepted"]), int(row["total"])


def _floor(value: Any) -> float | None:
    raw = value.get("min_confidence") if isinstance(value, dict) else None
    return float(raw) if isinstance(raw, int | float) else None


def _is_floor_change(c: Change) -> bool:
    return (
        c.change_kind == "threshold"
        and c.target.startswith("autopilot:")
        and _floor(c.before) is not None
        and _floor(c.after) is not None
    )


async def _tell(pool: asyncpg.Pool, c: Change, title: str, detail: str) -> None:
    await tasks.see(
        pool,
        KIND,
        f"{c.id}:{c.status}",
        title=title,
        detail=detail,
        href="/autopilot",
        weight=0.4,
        status="needs_owner",
    )


async def settle_floors(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, int]:
    """Give each waiting floor change its replay and its verdict."""
    rows = await pool.fetch(
        "select " + change.COLUMNS + " from public.engine_change"  # noqa: S608
        " where status = 'shadow' and change_kind = 'threshold' order by id"
    )
    promoted = rejected = waiting = 0
    for rec in rows:
        c = change.from_row(rec)
        if not _is_floor_change(c):
            continue
        change.ensure_changeable(c.target)
        before, after = _floor(c.before), _floor(c.after)
        assert before is not None and after is not None
        if after <= before:
            waiting += 1  # a looser floor is never applied by the engine itself
            continue
        kind = c.target.removeprefix("autopilot:")
        decided, escalated = await _window(pool, kind, now - dt.timedelta(days=REPLAY_DAYS), now)
        baseline, candidate = replay(decided, after, escalated)
        await change.record_results(pool, c.id, baseline, candidate)
        settled = await change.settle(pool, c.id, golden_passed=None)
        if settled.status == "promoted":
            promoted += 1
            usual = await owner_acceptance(pool, kind, now - dt.timedelta(days=REPLAY_DAYS), now)
            habit = (
                f" Of the {usual[1]} items it escalated in that time you accepted {usual[0]}."
                if usual
                else ""
            )
            await _tell(
                pool,
                settled,
                f"The {kind} autopilot now asks for {after:.0%} confidence",
                f"{settled.hypothesis}. In the last {REPLAY_DAYS} days this would have left "
                f"{candidate.escalated - baseline.escalated} more decisions for you and "
                f"cut wrong ones from {baseline.wrong_rate:.0%} to {candidate.wrong_rate:.0%}."
                f"{habit} If it is too strict, undo the change from the engine ledger.",
            )
        else:
            rejected += 1
    return {"promoted": promoted, "rejected": rejected, "waiting_for_owner": waiting}


async def check_rollbacks(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, int]:
    """Roll back a promoted floor whose first weeks went worse than the baseline it was
    promoted on."""
    rows = await pool.fetch(
        "select " + change.COLUMNS + ", decided_at from public.engine_change"  # noqa: S608
        " where status = 'promoted' and change_kind = 'threshold'"
        " and decided_at <= $1 and decided_at > $2",
        now - dt.timedelta(days=ROLLBACK_AFTER_DAYS),
        now - dt.timedelta(days=ROLLBACK_UNTIL_DAYS),
    )
    rolled = 0
    for rec in rows:
        c = change.from_row(rec)
        if not _is_floor_change(c) or c.baseline is None:
            continue
        decided, escalated = await _window(
            pool, c.target.removeprefix("autopilot:"), rec["decided_at"], now
        )
        after = _metrics(decided, escalated)
        # Promotion asked for an improvement; undoing it only needs a worsening.
        gates = Gates(**{**c.gates, "max_wrong_rate_increase": 0.0})
        reasons = change.rollback_due(Metrics(**c.baseline), after, gates)
        if not reasons:
            continue
        if await change.roll_back(pool, c.id, reasons):
            rolled += 1
            await _tell(
                pool,
                c.model_copy(update={"status": "rolled_back"}),
                f"The {c.target.removeprefix('autopilot:')} confidence floor was put back",
                "It went worse after the change: " + "; ".join(reasons) + ".",
            )
    return {"rolled_back": rolled, "unchecked": await _tell_unchecked(pool, now)}


async def _tell_unchecked(pool: asyncpg.Pool, now: dt.datetime) -> int:
    """A promoted floor leaves its watch window on some day. If too few of the decisions made
    since were reviewed to say whether it helped, the owner is told once, so a quiet kind is not
    silently left unchecked."""
    rows = await pool.fetch(
        "select " + change.COLUMNS + ", decided_at from public.engine_change"  # noqa: S608
        " where status = 'promoted' and change_kind = 'threshold' and decided_at <= $1"
        " and decided_at > $2",
        now - dt.timedelta(days=ROLLBACK_UNTIL_DAYS),
        now - dt.timedelta(days=ROLLBACK_UNTIL_DAYS + 1),
    )
    told = 0
    for rec in rows:
        c = change.from_row(rec)
        if not _is_floor_change(c):
            continue
        kind = c.target.removeprefix("autopilot:")
        decided, escalated = await _window(pool, kind, rec["decided_at"], now)
        seen = _metrics(decided, escalated)
        if seen.reviewed >= c.gates.get("min_reviewed", FLOOR_GATES.min_reviewed):
            continue
        told += 1
        await _tell(
            pool,
            c,
            f"The {kind} confidence floor could not be checked",
            f"Only {seen.reviewed} decisions since it began were reviewed, too few to say whether "
            "it helped. Review some decisions of this kind, or undo the floor from the ledger.",
        )
    return told
