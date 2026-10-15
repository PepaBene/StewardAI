"""From owner overrides to a visible change (autonomous engine plan, 2.3).

A source (an autopilot kind decided by one rule or model) that the owner keeps overruling gets a
notice naming the main reason, and a proposed stricter confidence floor recorded as a shadow
`engine_change`. The proposal does not apply itself: it waits for the challenger gates, and the
notice tells the owner what is happening. One notice and one open proposal per source.
"""

import datetime as dt

import asyncpg

from steward.engine import change, gates, overrides
from steward.ops import tasks

KIND = "override_pattern"
FLOOR_STEP = 0.03
FLOOR_MAX = 0.99

REASON_TEXT = {
    "wrong_data": "the data it used was wrong",
    "wrong_source": "it trusted the wrong source",
    "right_but_unimportant": "it was right but not worth acting on",
    "too_early": "it acted too early",
    "too_late": "it acted too late",
    "prefer_my_judgement": "you prefer to decide these yourself",
    "other": "other reasons",
}


def describe(p: overrides.Pattern) -> str:
    reason = REASON_TEXT.get(p.top_reason or "", "no reason was given")
    return (
        f"You overruled {p.overridden} of its {p.decided} decisions in the last "
        f"{overrides.WINDOW_DAYS} days ({p.share:.0%}). Most common reason: {reason}."
    )


def _title(p: overrides.Pattern) -> str:
    if p.kind.startswith("config:"):
        return f"You keep changing what the engine writes into {p.kind.removeprefix('config:')}"
    return f"You keep overruling {p.decided_by} on {p.kind}"


def proposed_floor(current: float) -> float:
    return round(min(FLOOR_MAX, current + FLOOR_STEP), 4)


RETRY_AFTER_DAYS = 30


async def _has_open_proposal(pool: asyncpg.Pool, target: str, hypothesis: str) -> bool:
    """A proposal is waiting, or the same one was decided recently: a rejected floor is not
    proposed again every run."""
    return bool(
        await pool.fetchval(
            "select exists (select 1 from public.engine_change where target = $1"
            " and hypothesis = $2 and (status = 'shadow'"
            " or created_at >= now() - make_interval(days => $3)))",
            target,
            hypothesis,
            RETRY_AFTER_DAYS,
        )
    )


async def act(pool: asyncpg.Pool, floors: dict[str, float], now: dt.datetime) -> dict[str, int]:
    """`floors` is each kind's current confidence floor. Returns how many notices and proposals
    this run opened."""
    notices = proposals = 0
    for p in await overrides.patterns(pool, now):
        await tasks.see(
            pool,
            KIND,
            f"{p.kind}:{p.decided_by}",
            title=_title(p),
            detail=describe(p),
            href="/autopilot",
            weight=0.6,
            status="needs_owner",
        )
        notices += 1
        current = floors.get(p.kind)
        if current is None:
            continue
        target = f"autopilot:{p.kind}"
        hypothesis = f"A higher confidence floor for {p.kind} lowers how often you overrule it"
        if await _has_open_proposal(pool, target, hypothesis):
            continue
        await change.propose(
            pool,
            target,
            "threshold",
            hypothesis,
            "owner override rate",
            before={"min_confidence": current},
            after={"min_confidence": proposed_floor(current)},
            gates=await gates.floor_gates(pool),
        )
        proposals += 1
    return {"notices": notices, "proposals": proposals}
