"""Self-change control: fixed gates, a frozen list, rollback, and the reward-hacking audit."""

import datetime as dt

import asyncpg
import pytest

from steward.engine import audit
from steward.engine import change as ch
from steward.ops import tasks

NOW = dt.datetime.now(dt.UTC)


def m(n=50, reviewed=20, wrong=2, escalated=10, failures=1, cost=1.0) -> ch.Metrics:  # type: ignore[no-untyped-def]
    return ch.Metrics(n, reviewed, wrong, escalated, failures, cost)


def test_a_candidate_that_is_no_worse_and_passes_the_golden_set_is_promoted() -> None:
    verdict = ch.decide(m(), m(wrong=1), ch.Gates(), golden_passed=True)
    assert verdict.promote and verdict.reasons == ["all gates passed"]


def test_every_failed_gate_is_listed_and_blocks_the_change() -> None:
    gates = ch.Gates()
    assert not ch.decide(m(), m(wrong=6), gates, golden_passed=True).promote  # more wrong
    assert not ch.decide(m(), m(n=10), gates, golden_passed=True).promote  # too few
    assert not ch.decide(m(), m(reviewed=3, wrong=0), gates, golden_passed=True).promote
    assert not ch.decide(m(), m(cost=1.5), gates, golden_passed=True).promote  # costs more
    assert not ch.decide(m(), m(), gates, golden_passed=False).promote
    assert not ch.decide(m(), m(), gates, golden_passed=None).promote
    assert not ch.decide(m(), m(failures=5), gates, golden_passed=True).promote
    hollow = ch.decide(m(escalated=20), m(escalated=2), gates, golden_passed=True)
    assert not hollow.promote and "escalates much less" in hollow.reasons[0]


def test_a_promoted_change_is_rolled_back_when_the_next_window_is_worse() -> None:
    gates = ch.Gates()
    assert ch.rollback_due(m(), m(wrong=2), gates) == []
    assert "wrong decisions rose" in ch.rollback_due(m(), m(wrong=8), gates)[0]
    assert ch.rollback_due(m(), m(reviewed=3, wrong=3), gates) == []  # too little to judge


def test_what_the_engine_may_never_change_is_refused() -> None:
    for target in ("budget.monthly_cap", "trading.enabled", "gates.min_n", "credentials:openai"):
        with pytest.raises(ch.FrozenTarget):
            ch.ensure_changeable(target)
    ch.ensure_changeable("autopilot.fact.min_confidence")


@pytest.mark.db
async def test_a_change_is_proposed_judged_once_and_can_be_rolled_back(
    backend_pool: asyncpg.Pool,
) -> None:
    with pytest.raises(ch.FrozenTarget):
        await ch.propose(backend_pool, "budget.cap", "threshold", "h", "m", 1, 2)
    change = await ch.propose(
        backend_pool, "autopilot.fact.min_confidence", "threshold", "fewer wrong", "wrong_rate",
        0.9, 0.93,
    )  # fmt: skip
    assert change.status == "shadow" and change.gates["min_n"] == 30
    with pytest.raises(ValueError, match="not waiting"):
        await ch.settle(backend_pool, change.id, golden_passed=True)  # no results yet

    await ch.record_results(backend_pool, change.id, m(), m(wrong=1))
    done = await ch.settle(backend_pool, change.id, golden_passed=True)
    assert done.status == "promoted"
    with pytest.raises(ValueError, match="not waiting"):
        await ch.settle(backend_pool, change.id, golden_passed=True)  # judged once

    assert await ch.roll_back(backend_pool, change.id, ["wrong decisions rose"])
    assert not await ch.roll_back(backend_pool, change.id, ["again"])


def test_the_audit_flags_an_engine_that_escalates_less_and_is_wrong_more() -> None:
    prev = audit.Window(decided=40, escalated=40, reviewed=20, wrong=2)
    hollow = audit.Window(decided=70, escalated=10, reviewed=20, wrong=6)
    assert "escalates less" in audit.gaming(prev, hollow)[0]
    assert audit.gaming(prev, audit.Window(70, 10, 20, 2)) == []  # fewer escalations, still right
    unchecked = audit.Window(decided=80, escalated=40, reviewed=4, wrong=0)
    assert "only 5% is reviewed" in audit.gaming(prev, unchecked)[0]
    assert audit.gaming(audit.Window(5, 5, 1, 0), hollow) == []  # too little history


@pytest.mark.db
async def test_the_audit_opens_a_notice_and_clears_it(backend_pool: asyncpg.Pool) -> None:
    async def rows(when: dt.datetime, status: str, n: int, review: str | None) -> None:
        for i in range(n):
            await backend_pool.execute(
                "insert into public.auto_decision (kind, subject_ref, decision, decided_by,"
                " summary, evidence, status, review, decided_at)"
                " values ('fact', $1, 'accept', 'rule:x', 's', '{}'::jsonb, $2, $3, $4)",
                f"{status}-{when.date()}-{review}-{i}-{n}",
                status,
                review,
                when,
            )

    before, recent = NOW - dt.timedelta(days=45), NOW - dt.timedelta(days=5)
    await rows(before, "applied", 18, "ok")
    await rows(before, "applied", 2, "wrong")
    await rows(before, "escalated", 40, None)
    await rows(recent, "applied", 14, "ok")
    await rows(recent, "applied", 6, "wrong")
    await rows(recent, "applied", 30, None)
    await rows(recent, "escalated", 10, None)
    flagged = await audit.audit(backend_pool, ["fact"], NOW)
    assert "fact" in flagged
    assert (await tasks.needs_owner(backend_pool))[0].kind == audit.KIND

    await backend_pool.execute(
        "update public.auto_decision set review = 'ok' where review = 'wrong' or review is null"
    )
    assert await audit.audit(backend_pool, ["fact"], NOW) == {}
    assert await tasks.needs_owner(backend_pool) == []
