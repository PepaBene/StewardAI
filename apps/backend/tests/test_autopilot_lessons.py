"""Learning from wrong decisions: the lessons block, the learned floor, and its storage."""

import datetime as dt

import asyncpg
import pytest

from steward.autopilot import ledger, lessons
from steward.autopilot.judge import Candidate, JudgeKind, build_call
from steward.autopilot.kinds import all_kinds
from steward.config.jobs import AutopilotSettings

NOW = dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.UTC)


def test_floor_rises_only_after_repeated_confident_mistakes() -> None:
    assert lessons.learned_floor([0.95], 0.9) is None  # one mistake is a lesson, not a rule
    assert lessons.learned_floor([0.95, 0.5], 0.9) is None  # the second was already escalated
    assert lessons.learned_floor([0.95, 0.93], 0.9) == 0.96
    assert lessons.learned_floor([0.995, 0.99], 0.9) == 0.99  # capped
    assert lessons.learned_floor([], 0.9) is None


def test_lessons_are_data_and_name_the_owners_choice() -> None:
    block = lessons.render(
        [
            ("Item: Acme Corp", "confirm", 0.93, "reject"),
            ("Item: Beta Inc", "confirm", None, None),
            ("Item: Gamma", "reject", 0.91, "reject"),
        ]
    )
    assert block.startswith("<lessons>") and block.endswith("</lessons>")
    assert "data, not instructions" in block
    assert "The owner chose reject instead." in block
    assert "Answered confirm (93% sure)." in block
    assert block.count("the right answer was not recorded") == 2  # undone, and same as owner's
    assert lessons.render([]) == ""


def test_the_lessons_reach_the_system_prompt_only_when_there_are_some() -> None:
    kind: JudgeKind = next(iter(all_kinds().values()))
    cfg = AutopilotSettings()
    call = build_call(kind, _cand(), "gpt-small", cfg)
    assert call["system"] == kind.system
    with_lessons = build_call(kind, _cand(), "gpt-small", cfg, "<lessons>x</lessons>")
    assert with_lessons["system"].startswith(kind.system)
    assert with_lessons["system"].endswith("<lessons>x</lessons>")


def _cand() -> Candidate:
    return Candidate("x:1", None, "item")


@pytest.mark.db
async def test_wrong_decisions_set_and_clear_the_floor(backend_pool: asyncpg.Pool) -> None:
    for i, conf in enumerate((0.95, 0.93)):
        row = await ledger.record(
            backend_pool,
            kind="fact",
            subject_ref=f"fact:{i}",
            ticker=None,
            decision="accept",
            confidence=conf,
            decided_by="model:gpt-small",
            summary="s",
            evidence={"prompt": f"case {i}"},
            status="applied",
        )
        await ledger.set_review(backend_pool, row, "wrong", "owner")

    kinds = {"fact": all_kinds()["fact"]}
    moved = await lessons.relearn(backend_pool, kinds, {"fact": 0.9}, dt.datetime.now(dt.UTC))
    assert moved == {"fact": 0.96}
    assert await ledger.learned_thresholds(backend_pool) == {"fact": pytest.approx(0.96)}

    text = await lessons.lessons_for(backend_pool, all_kinds()["fact"])
    assert "case 0" in text and "case 1" in text

    # Once the wrong decisions age out of the window the floor is dropped.
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=lessons.LOOKBACK_DAYS + 1)
    assert await lessons.relearn(backend_pool, kinds, {"fact": 0.9}, later) == {}
    assert await ledger.learned_thresholds(backend_pool) == {}
