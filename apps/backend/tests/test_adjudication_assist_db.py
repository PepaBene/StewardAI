"""Hybrid gate: model suggestions are stored apart and never reach the gate until a person saves."""

from types import SimpleNamespace

import asyncpg
import pytest

from steward.disclosure import adjudication, adjudication_assist
from steward.disclosure.adjudication import SampleItem

pytestmark = pytest.mark.db

LABELS = ("same_company", "different_company", "cannot_tell")


class FakeLLM:
    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers

    def configured(self, model: str) -> bool:
        return True

    async def complete(self, req: object) -> SimpleNamespace:
        text = req.messages[0].text  # type: ignore[attr-defined]
        for needle, label in self.answers.items():
            if needle in text:
                return SimpleNamespace(
                    parsed=adjudication_assist.Suggestion(label=label, reason="r")
                )
        return SimpleNamespace(parsed=None)


def test_review_rules() -> None:
    seed = "k"
    assert adjudication_assist.review_requirement(
        "planted:1", True, None, "a", False, seed
    ).must_review
    r = adjudication_assist.review_requirement("x", False, "a", "b", False, seed)
    assert "model disagrees with the extractor" in r.reasons
    assert adjudication_assist.review_requirement("x", False, "a", "a", True, seed).must_review
    flagged = sum(
        adjudication_assist.review_requirement(f"i{n}", False, "a", "a", False, seed).must_review
        for n in range(1000)
    )
    assert 100 < flagged < 300  # about the random share


async def test_suggestions_do_not_count_until_a_person_accepts(
    backend_pool: asyncpg.Pool,
) -> None:
    items = [SampleItem(f"m:{n}", f"name {n}", "same_company", ()) for n in range(40)]
    llm = FakeLLM({f"name {n}": "same_company" for n in range(40)})
    key = "ownership_match:t:40"
    out = await adjudication_assist.prelabel(
        backend_pool, llm, "gpt-test", "ownership_match", key, items, LABELS
    )
    assert out == {"suggested": 40, "left_to_person": 0}
    gate = await adjudication.measure(backend_pool, "ownership_match", key)
    assert gate.labelled == 0  # nothing counts yet
    n = await adjudication_assist.accept_unflagged(backend_pool, "ownership_match", key, "owner:1")
    flagged = await backend_pool.fetchval(
        "select count(*) from public.disclosure_adjudication_suggestion where must_review"
    )
    assert n == 40 - flagged and 0 < flagged < 40
    gate = await adjudication.measure(backend_pool, "ownership_match", key)
    assert gate.labelled == n
    assert (
        await adjudication_assist.accept_unflagged(backend_pool, "ownership_match", key, "owner:1")
        == 0
    )


async def test_an_off_list_or_missing_answer_is_left_to_the_person(
    backend_pool: asyncpg.Pool,
) -> None:
    items = [SampleItem("m:1", "name 1", "same_company", ())]
    llm = FakeLLM({"name 1": "banana"})
    out = await adjudication_assist.prelabel(
        backend_pool, llm, "gpt-test", "ownership_match", "k2", items, LABELS
    )
    assert out == {"suggested": 0, "left_to_person": 1}
