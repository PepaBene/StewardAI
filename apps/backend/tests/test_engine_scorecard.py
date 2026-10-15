"""The success metrics: the arithmetic, and a read of a database with a little in it."""

import datetime as dt

import asyncpg
import pytest

from steward.autopilot import ledger
from steward.engine import scorecard
from steward.ops import tasks

NOW = dt.datetime.now(dt.UTC)


def test_the_automatic_share_counts_applied_against_everything_the_app_had_to_decide() -> None:
    assert scorecard.share(0, 0, 0) is None
    assert scorecard.share(9, 0, 1) == 0.9
    assert scorecard.share(1, 2, 1) == 0.25


def test_precision_is_judged_only_with_enough_reviewed_decisions() -> None:
    few = scorecard.kind_precision("fact", 5, 5)
    assert few.met is None and not few.enough_evidence
    good = scorecard.kind_precision("fact", 40, 39)
    assert good.met is True
    bad = scorecard.kind_precision("fact", 40, 30)
    assert bad.met is False and bad.precision == 0.75


@pytest.mark.db
async def test_an_empty_database_reads_as_nothing_to_judge_yet(backend_pool: asyncpg.Pool) -> None:
    card = await scorecard.build(backend_pool, NOW)
    assert card.items_per_week == 0 and card.items_met
    assert card.automatic_share is None and card.automatic_met is None
    assert card.oldest_open_days is None and card.precision == []


@pytest.mark.db
async def test_open_items_and_escalations_are_counted(backend_pool: asyncpg.Pool) -> None:
    await tasks.see(
        backend_pool, "opportunity", "screen:1", title="t", detail="d", status="needs_owner"
    )
    await ledger.record(
        backend_pool,
        kind="fact",
        subject_ref="fact:1",
        ticker=None,
        evidence={},
        decision="escalate",
        confidence=0.4,
        decided_by="rule:x",
        summary="s",
        status="escalated",
    )
    card = await scorecard.build(backend_pool, NOW)
    assert card.items_open_now == 2
    assert card.items_per_week == 0.5  # two items over four weeks
    assert card.decisions_escalated == 1
    assert card.oldest_open_days is not None and card.oldest_open_days < 1
