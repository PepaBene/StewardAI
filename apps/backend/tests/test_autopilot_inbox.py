"""The inbox kinds: which proposals a rule settles, what the judge is shown, and how a decision
is applied and undone. A fake pool stands in for the database; the real SQL is covered by the
`backend_pool` tests at the bottom, which skip without a database."""

import json
from typing import Any

import pytest

from steward.autopilot.judge import ApplyError, Candidate
from steward.autopilot.kinds import inbox
from steward.inbox import service


class FakePool:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self.rows = rows or []
        self.scalar = scalar
        self.queries: list[str] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.queries.append(query)
        if "auto_decision" in query:  # the calibration record: none yet
            return []
        return self.rows

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.queries.append(query)
        return self.scalar


def action_row(kind: str = "earnings_check", **extra: Any) -> dict[str, Any]:
    return {
        "id": 7,
        "ticker": "NVDA_US_EQ",
        "severity": 3,
        "headline": "NVDA cut guidance",
        "body": json.dumps({"assessment": "Guidance fell", "statements": [{"text": "Cut by 8%"}]}),
        "proposed_action": json.dumps({"kind": kind, "params": {"ticker": "NVDA"}, "reason": "r"}),
        **extra,
    }


async def test_load_actions_builds_a_judge_prompt_from_the_evidence() -> None:
    cands = await inbox._load_actions(FakePool([action_row()]), 10)
    assert [c.ref for c in cands] == ["inbox_action:7"]
    prompt = cands[0].prompt
    assert (
        "NVDA cut guidance" in prompt and "Cut by 8%" in prompt and "Job: earnings_check" in prompt
    )
    assert cands[0].evidence == {"item_id": 7, "job": "earnings_check", "earned_rule": False}


async def test_load_actions_skips_a_job_kind_the_service_would_refuse() -> None:
    cands = await inbox._load_actions(FakePool([action_row("delete_everything")]), 10)
    assert cands == []


def test_only_the_idempotent_sync_is_settled_by_a_rule() -> None:
    sync = Candidate("inbox_action:1", None, "", {"item_id": 1, "job": "portfolio_sync"})
    check = Candidate("inbox_action:2", None, "", {"item_id": 2, "job": "earnings_check"})
    ruled = inbox._action_rule(sync)
    assert ruled is not None and ruled.decision == "confirm"
    assert inbox._action_rule(check) is None


def test_a_data_refresh_gets_a_rule_only_after_the_models_calls_on_it_held_up() -> None:
    assert not inbox.calibrated(9, 9)  # too few reviewed
    assert not inbox.calibrated(20, 18)  # 90% is not enough
    assert inbox.calibrated(20, 19)
    unproven = Candidate("inbox_action:3", None, "", {"job": "price_history", "earned_rule": False})
    proven = Candidate("inbox_action:4", None, "", {"job": "price_history", "earned_rule": True})
    assert inbox._action_rule(unproven) is None
    ruled = inbox._action_rule(proven)
    assert ruled is not None and ruled.decision == "confirm"
    # A job that costs model money never gets the rule, whatever its record.
    costly = Candidate("inbox_action:5", None, "", {"job": "research", "earned_rule": True})
    assert inbox._action_rule(costly) is None


async def test_confirm_starts_the_job_with_no_requester_and_cannot_be_undone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, Any]] = []

    async def confirm(pool: Any, item_id: int, owner_id: Any) -> Any:
        calls.append((item_id, owner_id))
        return type("Item", (), {"action_job_id": 41})()

    monkeypatch.setattr(service, "confirm_action", confirm)
    cand = Candidate("inbox_action:7", None, "", {"item_id": 7})
    payload = await inbox._apply_action(None, cand, "confirm", "note")
    assert calls == [(7, None)]
    assert payload == {"item_id": 7, "job_id": 41, "undoable": False}
    with pytest.raises(ApplyError, match="already started"):
        await inbox._revert_action(None, payload)


async def test_decline_can_be_reopened(monkeypatch: pytest.MonkeyPatch) -> None:
    async def decline(pool: Any, item_id: int) -> Any:
        return None

    monkeypatch.setattr(service, "decline_action", decline)
    cand = Candidate("inbox_action:7", None, "", {"item_id": 7})
    payload = await inbox._apply_action(None, cand, "decline", "note")
    assert payload == {"item_id": 7, "undoable": True}
    pool = FakePool(scalar=7)
    await inbox._revert_action(pool, payload)
    assert "action_status = 'proposed'" in pool.queries[0]
    with pytest.raises(ApplyError, match="no longer declined"):
        await inbox._revert_action(FakePool(scalar=None), payload)


async def test_a_refused_action_becomes_an_apply_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def confirm(pool: Any, item_id: int, owner_id: Any) -> Any:
        raise service.ActionError("has no open action", conflict=True)

    monkeypatch.setattr(service, "confirm_action", confirm)
    with pytest.raises(ApplyError, match="no open action"):
        await inbox._apply_action(None, Candidate("x:1", None, "", {"item_id": 1}), "confirm", "n")


async def test_load_notes_shows_the_existing_notes_and_skips_empty_suggestions() -> None:
    rows: list[dict[str, Any]] = [
        {
            "id": 3,
            "ticker": "ASML_US_EQ",
            "headline": "Order slump",
            "body": json.dumps({"suggested_note": "Watch China export share."}),
        },
        {"id": 4, "ticker": None, "headline": "x", "body": json.dumps({"suggested_note": " "})},
    ]

    class NotePool(FakePool):
        async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
            return self.rows if "inbox_item" in query else [{"text": "Mind FX."}]

    cands = await inbox._load_notes(NotePool(rows), 10)
    assert [c.ref for c in cands] == ["agent_note:3"]
    assert "Watch China export share." in cands[0].prompt and "- Mind FX." in cands[0].prompt


async def test_the_owners_call_counts_as_an_outcome_but_the_autopilots_does_not() -> None:
    # decided by the autopilot already: no outcome
    assert await inbox._action_outcome(FakePool(scalar=True), "inbox_action:7") is None

    class OwnerPool(FakePool):
        async def fetchval(self, query: str, *args: Any) -> Any:
            return False if "auto_decision" in query else "confirmed"

    assert await inbox._action_outcome(OwnerPool(), "inbox_action:7") == "confirm"


def test_both_kinds_expire_to_a_decline_and_are_registered() -> None:
    assert {k.name for k in inbox.KINDS} == {"inbox_action", "agent_note"}
    assert all(k.expire_decision == "decline" for k in inbox.KINDS)
    assert all("defer" not in k.actionable for k in inbox.KINDS)
