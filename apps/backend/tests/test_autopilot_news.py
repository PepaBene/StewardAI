"""The news kinds: what the judge is shown, the escalation rule, and how decisions are applied
and undone. A fake pool stands in for the database."""

import datetime as dt
from typing import Any

import pytest

from steward.autopilot.judge import ApplyError, Candidate
from steward.autopilot.kinds import news
from steward.news import read
from steward.news.read import ClaimSource, DisputedStory

NOW = dt.datetime(2026, 11, 9, 12, tzinfo=dt.UTC)


class FakePool:
    def __init__(self, row: Any = None, scalar: Any = None) -> None:
        self.row = row
        self.scalar = scalar
        self.queries: list[str] = []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.queries.append(query)
        return self.row

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.queries.append(query)
        return self.scalar


def story(**extra: Any) -> DisputedStory:
    base: dict[str, Any] = {
        "id": 5,
        "ticker": "TSM_US_EQ",
        "event_type": "guidance",
        "severity": 2,
        "headline": "TSMC cuts capex",
        "status_reason": "contradicted by the company",
        "first_seen": NOW,
        "last_seen": NOW,
        "claim": [ClaimSource(url="https://blog.example/a", outlet="blog.example", tier=4)],
        "contradicting": [ClaimSource(url="https://tsmc.com/ir", outlet="tsmc.com", tier=1)],
        "supporting": [],
        "note": "The company's release does not mention it.",
        "resolution": None,
        "resolution_note": None,
        "resolved_at": None,
        "verdicts_frozen": True,
    }
    return DisputedStory(**{**base, **extra})


def test_story_prompt_shows_every_source_with_its_tier() -> None:
    prompt = news.story_prompt(story())
    assert "tier 4 blog.example" in prompt and "tier 1 tsmc.com" in prompt
    assert "<supporting>\nnone" in prompt
    assert "TSMC cuts capex" in prompt


async def test_load_stories_makes_one_candidate_per_disputed_story(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def disputed(pool: Any, include_resolved: bool) -> list[DisputedStory]:
        assert include_resolved is False
        return [story(), story(id=6)]

    monkeypatch.setattr(news, "disputed_stories", disputed)
    cands = await news._load_stories(None, 1)
    assert [c.ref for c in cands] == ["news_story:5"]
    assert cands[0].evidence == {"story_id": 5}


async def test_resolving_a_story_keeps_what_is_needed_to_restore_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved: list[tuple[int, str, str | None]] = []

    async def resolve(pool: Any, story_id: int, resolution: str, note: str | None) -> bool:
        resolved.append((story_id, resolution, note))
        return True

    monkeypatch.setattr(news, "resolve_story", resolve)
    before = {
        "status": "disputed",
        "confidence": "uncorroborated",
        "status_reason": "contradicted",
        "last_seen": NOW,
    }
    cand = Candidate("news_story:5", None, "", {"story_id": 5})
    payload = await news._apply_story(FakePool(row=before), cand, "false", "Autopilot (x): y")
    assert resolved == [(5, "false", "Autopilot (x): y")]
    assert payload is not None and payload["status"] == "disputed"
    assert payload["last_seen"] == NOW.isoformat()


async def test_a_story_resolved_meanwhile_is_not_touched() -> None:
    cand = Candidate("news_story:5", None, "", {"story_id": 5})
    with pytest.raises(ApplyError, match="no longer disputed"):
        await news._apply_story(FakePool(row=None), cand, "false", "n")


async def test_reverting_refuses_when_someone_else_resolved_it_again() -> None:
    payload = {
        "story_id": 5,
        "status": "disputed",
        "confidence": "c",
        "status_reason": "r",
        "last_seen": NOW.isoformat(),
    }
    pool = FakePool(scalar=None)
    with pytest.raises(ApplyError, match="resolved again"):
        await news._revert_story(pool, payload)
    assert "autopilot|owner accepted" in pool.queries[0]


async def test_story_outcome_ignores_the_autopilots_own_resolution() -> None:
    mine = {"resolution": "false", "resolution_note": "Autopilot (model:x, 0.95): y"}
    yours = {"resolution": "confirmed", "resolution_note": "I checked"}
    assert await news._story_outcome(FakePool(row=mine), "news_story:5") is None
    assert await news._story_outcome(FakePool(row=yours), "news_story:5") == "confirmed"
    assert await news._story_outcome(FakePool(row=None), "news_story:5") is None


def test_escalation_is_acknowledged_by_rule_only_after_a_check_ran() -> None:
    ran = Candidate("escalation:1", "ASML", "", {"report_id": 1, "checks": 1})
    none = Candidate("escalation:2", "ASML", "", {"report_id": 2, "checks": 0})
    ruled = news._escalation_rule(ran)
    assert ruled is not None and ruled.decision == "acknowledge"
    assert news._escalation_rule(none) is None


async def test_a_thesis_written_after_the_note_counts_as_its_follow_up() -> None:
    # Production, 17 Oct 2026: 19 notes read "thesis impact cannot be assessed without a written
    # thesis" and waited on the owner although a thesis had since been written for each.
    seen: list[tuple[Any, ...]] = []

    class Pool:
        async def fetch(self, query: str, *args: Any) -> list[Any]:
            seen.append(args)
            return []

    await news._followup(Pool(), 3, "CIEN_US_EQ")  # type: ignore[arg-type]
    assert "thesis" in seen[0][1] and "earnings_check" in seen[0][1]


async def test_escalation_apply_moves_only_an_open_one_and_revert_checks_the_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moves: list[tuple[int, str]] = []

    async def set_status(pool: Any, report_id: int, status: str) -> bool:
        moves.append((report_id, status))
        return True

    monkeypatch.setattr(news, "set_escalation_status", set_status)
    cand = Candidate("escalation:9", None, "", {"report_id": 9})
    payload = await news._apply_escalation(FakePool(scalar="open"), cand, "resolve", "n")
    assert moves == [(9, "resolved")] and payload == {"report_id": 9, "status": "resolved"}
    with pytest.raises(ApplyError, match="already acknowledged"):
        await news._apply_escalation(FakePool(scalar="acknowledged"), cand, "resolve", "n")
    await news._revert_escalation(FakePool(scalar="resolved"), payload)
    assert moves[-1] == (9, "open")
    with pytest.raises(ApplyError, match="not resolved"):
        await news._revert_escalation(FakePool(scalar="open"), payload)


async def test_escalation_outcome_is_the_owners_status_unless_the_autopilot_set_it() -> None:
    class Pool(FakePool):
        async def fetchval(self, query: str, *args: Any) -> Any:
            return self.scalar if "auto_decision" in query else "acknowledged"

    assert await news._escalation_outcome(Pool(scalar=True), "escalation:3") is None
    assert await news._escalation_outcome(Pool(scalar=False), "escalation:3") == "acknowledge"


def test_kinds_are_registered_and_a_defer_is_never_actionable() -> None:
    assert {k.name for k in news.KINDS} == {"news_story", "escalation"}
    assert all("defer" not in k.actionable for k in news.KINDS)
    assert news.ESCALATION.expire_decision == "acknowledge"
    assert news.NEWS_STORY.expire_decision is None
    assert read.ClaimSource is ClaimSource
