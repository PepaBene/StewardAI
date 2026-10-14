"""Self-change control, phase 3: a proposed confidence floor is replayed against real decisions,
promoted or rejected by gates fixed in code, rolled back when it goes worse, and the engine can
never touch what is frozen. Plus the reliability of stated confidence per kind."""

import datetime as dt

import asyncpg
import pytest

from steward.autopilot import ledger, reliability
from steward.engine import challenger, change, override_watch

NOW = dt.datetime.now(dt.UTC)


# ------------------------------------------------------------------ pure rules


@pytest.mark.parametrize(
    "target",
    [
        "trading",
        "broker.orders",
        "order_limits",
        "budget",
        "autopilot:budget",
        "autopilot:trading_mode",
        "config/credentials",
        "gates",
        "engine_change.gates",
        "secret.key",
        "provenance",
        "freeze_list",
    ],
)
def test_nothing_frozen_can_be_proposed_in_any_part_of_the_target(target: str) -> None:
    with pytest.raises(change.FrozenTarget):
        change.ensure_changeable(target)


@pytest.mark.parametrize("target", ["autopilot:fact", "autopilot:listing", "news.source_weight"])
def test_ordinary_targets_are_changeable(target: str) -> None:
    change.ensure_changeable(target)


class Row(dict):  # type: ignore[type-arg]
    pass


def decision(confidence: float | None, review: str | None) -> Row:
    return Row(confidence=confidence, status="applied", review=review)


def test_replay_moves_the_decisions_below_the_floor_to_the_owner() -> None:
    rows = [
        decision(0.95, "ok"),
        decision(0.8, "wrong"),
        decision(None, "ok"),
        decision(0.85, None),
    ]
    base, cand = challenger.replay(rows, 0.9, escalated=2)  # type: ignore[arg-type]
    assert (base.n, base.reviewed, base.wrong, base.escalated) == (4, 3, 1, 2)
    # A rule (no confidence) stays; the two below 0.9 wait for the owner.
    assert (cand.n, cand.reviewed, cand.wrong, cand.escalated) == (2, 2, 0, 4)


def test_a_floor_must_improve_things_not_merely_not_hurt() -> None:
    gates = challenger.FLOOR_GATES
    same = change.Metrics(n=40, reviewed=40, wrong=4, escalated=0, verifier_failures=0, cost=0)
    verdict = change.decide(same, same, gates, golden_passed=None)
    assert not verdict.promote and "more wrong" in verdict.reasons[0]
    better = change.Metrics(n=40, reviewed=40, wrong=2, escalated=10, verifier_failures=0, cost=0)
    assert change.decide(same, better, gates, golden_passed=None).promote


def test_the_reliability_report_compares_confidence_with_being_right() -> None:
    points = [reliability.Point(0.95, True)] * 8 + [reliability.Point(0.95, False)] * 2
    bins = reliability.bins_of(points)
    assert len(bins) == 1 and bins[0].share_right == 0.8 and bins[0].high == 1.0
    assert reliability.ece(bins) == 0.15
    assert reliability.verdict(bins, 10).startswith("Too few")
    assert reliability.verdict(bins, 30).startswith("Overconfident")
    sure_and_right = reliability.bins_of(
        [reliability.Point(0.9, True)] * 9 + [reliability.Point(0.9, False)]
    )
    assert reliability.verdict(sure_and_right, 30).startswith("Well calibrated")
    assert reliability.ece([]) is None


# ------------------------------------------------------------------ database


async def add_decisions(
    pool: asyncpg.Pool, kind: str, count: int, confidence: float, wrong: int, *, days_ago: int = 1
) -> None:
    when = NOW - dt.timedelta(days=days_ago)
    for i in range(count):
        await pool.execute(
            "insert into public.auto_decision (kind, subject_ref, decision, confidence, decided_by,"
            " summary, evidence, status, review, decided_at) values ($1, $2, 'accept', $3,"
            " 'model:x', 's', '{}'::jsonb, 'applied', $4, $5)",
            kind,
            f"{kind}-{confidence}-{days_ago}-{i}",
            confidence,
            "wrong" if i < wrong else "ok",
            when,
        )


async def propose_floor(pool: asyncpg.Pool, before: float, after: float) -> change.Change:
    return await change.propose(
        pool,
        "autopilot:fact",
        "threshold",
        "A higher confidence floor for fact lowers how often you overrule it",
        "owner override rate",
        {"min_confidence": before},
        {"min_confidence": after},
        challenger.FLOOR_GATES,
    )


@pytest.mark.db
async def test_a_floor_that_would_have_avoided_the_wrong_decisions_is_promoted_and_applied(
    backend_pool: asyncpg.Pool,
) -> None:
    await add_decisions(backend_pool, "fact", 30, 0.95, wrong=0)
    await add_decisions(backend_pool, "fact", 20, 0.8, wrong=10)
    proposal = await propose_floor(backend_pool, 0.7, 0.9)
    assert await ledger.learned_thresholds(backend_pool) == {}  # nothing before the verdict

    done = await challenger.settle_floors(backend_pool, NOW)
    assert done == {"promoted": 1, "rejected": 0, "waiting_for_owner": 0}
    row = await backend_pool.fetchrow(
        "select * from public.engine_change where id = $1", proposal.id
    )
    assert row["status"] == "promoted" and row["baseline"] and row["candidate"]
    assert await ledger.learned_thresholds(backend_pool) == {"fact": 0.9}
    notice = await backend_pool.fetchrow("select title, status from public.ops_task")
    assert "90%" in notice["title"] and notice["status"] == "needs_owner"


@pytest.mark.db
async def test_a_floor_that_throws_away_right_decisions_is_rejected(
    backend_pool: asyncpg.Pool,
) -> None:
    await add_decisions(backend_pool, "fact", 40, 0.95, wrong=4)
    await add_decisions(backend_pool, "fact", 20, 0.8, wrong=0)
    await propose_floor(backend_pool, 0.7, 0.9)
    done = await challenger.settle_floors(backend_pool, NOW)
    assert done["rejected"] == 1 and done["promoted"] == 0
    assert await ledger.learned_thresholds(backend_pool) == {}
    verdict = await backend_pool.fetchval("select verdict from public.engine_change")
    assert "more wrong" in verdict


@pytest.mark.db
async def test_too_little_evidence_rejects_instead_of_promoting(
    backend_pool: asyncpg.Pool,
) -> None:
    await add_decisions(backend_pool, "fact", 5, 0.8, wrong=3)
    await propose_floor(backend_pool, 0.7, 0.9)
    await challenger.settle_floors(backend_pool, NOW)
    assert await backend_pool.fetchval("select status from public.engine_change") == "rejected"


@pytest.mark.db
async def test_the_engine_never_loosens_a_floor_by_itself(backend_pool: asyncpg.Pool) -> None:
    await add_decisions(backend_pool, "fact", 40, 0.95, wrong=0)
    await propose_floor(backend_pool, 0.9, 0.8)
    done = await challenger.settle_floors(backend_pool, NOW)
    assert done == {"promoted": 0, "rejected": 0, "waiting_for_owner": 1}
    assert await backend_pool.fetchval("select status from public.engine_change") == "shadow"


@pytest.mark.db
async def test_a_frozen_target_is_refused_even_if_it_is_already_stored(
    backend_pool: asyncpg.Pool,
) -> None:
    with pytest.raises(change.FrozenTarget):
        await change.propose(
            backend_pool, "autopilot:budget", "threshold", "h", "m", {"min_confidence": 0.5}, {}
        )
    await backend_pool.execute(
        "insert into public.engine_change (target, change_kind, hypothesis, metric, gates, before,"
        " after) values ('autopilot:budget', 'threshold', 'h', 'm', '{}'::jsonb,"
        " '{\"min_confidence\": 0.5}'::jsonb, '{\"min_confidence\": 0.9}'::jsonb)"
    )
    with pytest.raises(change.FrozenTarget):
        await challenger.settle_floors(backend_pool, NOW)


@pytest.mark.db
async def test_a_promoted_floor_that_goes_worse_is_rolled_back(
    backend_pool: asyncpg.Pool,
) -> None:
    await add_decisions(backend_pool, "fact", 30, 0.95, wrong=0, days_ago=40)
    await add_decisions(backend_pool, "fact", 20, 0.8, wrong=10, days_ago=40)
    proposal = await propose_floor(backend_pool, 0.7, 0.9)
    await challenger.settle_floors(backend_pool, NOW - dt.timedelta(days=21))
    await backend_pool.execute(
        "update public.engine_change set decided_at = $2 where id = $1",
        proposal.id,
        NOW - dt.timedelta(days=20),
    )
    assert await ledger.learned_thresholds(backend_pool) == {"fact": 0.9}

    # The weeks since went badly: a third of the reviewed decisions were wrong.
    await add_decisions(backend_pool, "fact", 15, 0.95, wrong=5, days_ago=5)
    done = await challenger.check_rollbacks(backend_pool, NOW)
    assert done == {"rolled_back": 1}
    assert await backend_pool.fetchval("select status from public.engine_change") == "rolled_back"
    assert await ledger.learned_thresholds(backend_pool) == {}


@pytest.mark.db
async def test_a_promoted_floor_that_holds_up_stays(backend_pool: asyncpg.Pool) -> None:
    await add_decisions(backend_pool, "fact", 30, 0.95, wrong=0, days_ago=40)
    await add_decisions(backend_pool, "fact", 20, 0.8, wrong=10, days_ago=40)
    proposal = await propose_floor(backend_pool, 0.7, 0.9)
    await challenger.settle_floors(backend_pool, NOW - dt.timedelta(days=21))
    await backend_pool.execute(
        "update public.engine_change set decided_at = $2 where id = $1",
        proposal.id,
        NOW - dt.timedelta(days=20),
    )
    await add_decisions(backend_pool, "fact", 15, 0.95, wrong=0, days_ago=5)
    assert await challenger.check_rollbacks(backend_pool, NOW) == {"rolled_back": 0}
    assert await ledger.learned_thresholds(backend_pool) == {"fact": 0.9}


@pytest.mark.db
async def test_a_floor_decided_recently_is_not_proposed_again(backend_pool: asyncpg.Pool) -> None:
    await propose_floor(backend_pool, 0.7, 0.9)
    await backend_pool.execute("update public.engine_change set status = 'rejected'")
    target, why = (
        "autopilot:fact",
        "A higher confidence floor for fact lowers how often you overrule it",
    )
    assert await override_watch._has_open_proposal(backend_pool, target, why)
    assert not await override_watch._has_open_proposal(backend_pool, target, "another idea")


@pytest.mark.db
async def test_the_reliability_report_reads_reviewed_decisions(backend_pool: asyncpg.Pool) -> None:
    assert await reliability.report(backend_pool, NOW) == []
    await add_decisions(backend_pool, "fact", 25, 0.95, wrong=10)
    (fact,) = await reliability.report(backend_pool, NOW)
    assert fact.kind == "fact" and fact.reviewed == 25
    assert fact.verdict.startswith("Overconfident") and fact.ece is not None and fact.ece > 0.3
