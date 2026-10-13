"""Calibration: how a kind of automatic decision earns, and loses, the right to act alone.

A decision is reviewed three ways: the owner overrode or agreed with it (the item's own state,
`kind.outcome`), the owner spot-checked it, or a stronger model re-judged the same evidence
independently (an audit). A kind moves from shadow to auto when enough reviewed decisions are
right; an auto kind that falls below the floor goes back to shadow.
"""

import datetime as dt
from typing import Any

import asyncpg

from steward.ai import Usage
from steward.autopilot import ledger
from steward.autopilot.judge import Candidate, JudgeKind, Settled, build_call, read_response
from steward.config.jobs import AutopilotSettings
from steward.llm.routing import RoutingClient, parse_many

WINDOW_DAYS = 90
MIN_REVIEWED_TO_DEMOTE = 10


def calibration_move(
    mode: str, reviewed: int, right: int, settings: AutopilotSettings
) -> tuple[str, str] | None:
    """(new mode, reason) when a kind should move, else None. Promotion needs
    `promote_min_reviewed` reviews at `promote_precision`; demotion needs ten reviews under
    `demote_precision`. Between the two bars a kind stays where it is."""
    if reviewed == 0:
        return None
    precision = right / reviewed
    if mode == "shadow" and reviewed >= settings.promote_min_reviewed:
        if precision >= settings.promote_precision:
            return "auto", f"{right}/{reviewed} reviewed decisions right ({precision:.0%})"
    if mode == "auto" and reviewed >= MIN_REVIEWED_TO_DEMOTE:
        if precision < settings.demote_precision:
            return "shadow", f"only {right}/{reviewed} reviewed decisions right ({precision:.0%})"
    return None


def owner_review(decision: str, outcome: str) -> str:
    """'ok' when the owner's own call matches the autopilot's decision, else 'wrong'."""
    return "ok" if decision == outcome else "wrong"


async def review_from_outcomes(
    pool: asyncpg.Pool, kinds: dict[str, JudgeKind], now: dt.datetime
) -> int:
    """Review decisions the owner has since decided by hand. Returns how many were reviewed."""
    done = 0
    since = now - dt.timedelta(days=WINDOW_DAYS)
    for row in await ledger.unreviewed(pool, since, 1000):
        kind = kinds.get(row.kind)
        if kind is None or kind.outcome is None:
            continue
        outcome = await kind.outcome(pool, row.subject_ref)
        if outcome is None:
            continue
        verdict = owner_review(row.decision, outcome)
        if await ledger.set_review(pool, row.id, verdict, "owner"):  # type: ignore[arg-type]
            done += 1
    return done


async def audit(
    pool: asyncpg.Pool,
    client: RoutingClient,
    kinds: dict[str, JudgeKind],
    settings: AutopilotSettings,
    now: dt.datetime,
) -> dict[str, Any]:
    """Have the stronger model re-judge up to `audit_per_run` unreviewed decisions from their
    stored evidence; agreement is a correct decision, disagreement a wrong one."""
    model = settings.escalate_model or settings.model
    out: dict[str, Any] = {"audited": 0, "agreed": 0}
    usage = Usage(model=model)
    out["usage"] = usage
    if settings.audit_per_run == 0:
        return out
    rows = [
        r
        for r in await ledger.unreviewed(pool, now - dt.timedelta(days=WINDOW_DAYS), 400)
        if r.kind in kinds and r.evidence.get("prompt") and r.decided_by != "ttl"
    ]
    # Spread the audit across kinds: the oldest unreviewed decision of each, in turn.
    by_kind: dict[str, list[ledger.AutoDecision]] = {}
    for row in rows:
        by_kind.setdefault(row.kind, []).append(row)
    picked: list[ledger.AutoDecision] = []
    while len(picked) < settings.audit_per_run and any(by_kind.values()):
        for queue in by_kind.values():
            if queue and len(picked) < settings.audit_per_run:
                picked.append(queue.pop(0))
    if not picked:
        return out
    calls = [
        build_call(
            kinds[r.kind],
            Candidate(r.subject_ref, r.ticker, str(r.evidence["prompt"]), r.evidence),
            model,
            settings,
        )
        for r in picked
    ]
    answers = await parse_many(client, calls)
    for row, answer in zip(picked, answers, strict=True):
        second: Settled | None = read_response(answer, model, usage)
        if second is None:
            continue
        agreed = second.decision == row.decision
        if await ledger.set_review(pool, row.id, "ok" if agreed else "wrong", f"audit:{model}"):
            out["audited"] += 1
            out["agreed"] += int(agreed)
    return out


async def calibrate(
    pool: asyncpg.Pool,
    kinds: dict[str, JudgeKind],
    settings: AutopilotSettings,
    now: dt.datetime,
) -> list[dict[str, Any]]:
    """Move each kind between shadow and auto by its reviewed precision. Returns the moves."""
    moves: list[dict[str, Any]] = []
    states = await ledger.mode_states(pool)
    for name in kinds:
        policy = settings.policy(name)
        if policy.mode == "off":
            continue
        state = states.get(name)
        mode = state[0] if state else policy.mode
        # A decision that cannot be undone is never handed the keys by a statistic: the owner
        # must set such a kind to auto in the Jobs config. Demotion still applies.
        if (kinds[name].revert is None or not kinds[name].promotable) and mode == "shadow":
            continue
        # After a move only decisions made since count, so a promotion is judged on the
        # decisions it let through, not on the shadow record that earned it.
        since = now - dt.timedelta(days=WINDOW_DAYS)
        if mode == "auto" and state is not None:
            since = max(since, state[1])
        reviewed, right = await ledger.review_tally(pool, name, since)
        move = calibration_move(mode, reviewed, right, settings)
        if move is None:
            continue
        await ledger.set_mode(pool, name, move[0], move[1])
        moves.append({"kind": name, "from": mode, "to": move[0], "reason": move[1]})
    return moves
