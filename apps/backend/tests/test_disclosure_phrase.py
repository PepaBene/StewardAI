"""A model may only reword the templated answer; anything ungrounded keeps the template."""

from types import SimpleNamespace
from typing import Any

from steward.disclosure import answer
from steward.llm.types import LLMError

COMPOSED = {
    "text": "For ACME as of 2026-07-01: revenue reported as 1,250.5, from sec, known 2026-05-01."
}
EVIDENCE = [
    {"value": "1,250.5", "quote": None, "counterparty": None, "label": "revenue", "source": "sec"}
]


class FakeLLM:
    def __init__(self, text: str = "", configured: bool = True, fail: bool = False) -> None:
        self._text, self._configured, self._fail = text, configured, fail
        self.calls = 0

    def configured(self, model: str) -> bool:
        return self._configured

    async def complete(self, request: Any) -> Any:
        self.calls += 1
        if self._fail:
            raise LLMError("down")
        return SimpleNamespace(text=self._text)


async def test_a_grounded_rewrite_is_used() -> None:
    llm = FakeLLM("ACME reported revenue of 1,250.5 per sec, known 2026-05-01.")
    assert await answer.phrase(llm, "gpt-x", COMPOSED, EVIDENCE) == llm._text


async def test_an_invented_number_keeps_the_template() -> None:
    llm = FakeLLM("ACME reported revenue of 1,300 per sec.")
    assert await answer.phrase(llm, "gpt-x", COMPOSED, EVIDENCE) is None


async def test_an_unconfigured_model_is_never_called() -> None:
    llm = FakeLLM("anything", configured=False)
    assert await answer.phrase(llm, "gpt-x", COMPOSED, EVIDENCE) is None and llm.calls == 0


async def test_a_failed_call_keeps_the_template() -> None:
    assert await answer.phrase(FakeLLM(fail=True), "gpt-x", COMPOSED, EVIDENCE) is None


async def test_no_evidence_means_no_call() -> None:
    llm = FakeLLM("x")
    assert await answer.phrase(llm, "gpt-x", COMPOSED, []) is None and llm.calls == 0


async def test_the_route_uses_the_rewrite_only_when_asked_and_grounded(monkeypatch: Any) -> None:
    import datetime as dt

    from api import disclosure_replay_routes as routes

    when = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
    evidence = [
        {
            "kind": "observation",
            "id": 1,
            "label": "revenue",
            "counterparty": None,
            "value": "1,250.5",
            "period_end": None,
            "quote": None,
            "section": None,
            "known_at": when,
            "source": "sec",
            "document_url": None,
            "anonymous": False,
        }
    ]

    async def fake_answer(*_a: Any) -> dict[str, Any]:
        return {
            "evidence": evidence,
            **COMPOSED,
            "grounded": True,
            "limits": [],
            "retrieved": 1,
            "note": None,
        }

    async def nothing(*_a: Any) -> None:
        return None

    llm = FakeLLM("ACME reported revenue of 1,250.5 per sec, known 2026-05-01.")
    monkeypatch.setattr(routes.answer_mod, "answer", fake_answer)
    monkeypatch.setattr(routes.estimate_read, "issuer_for_ticker", nothing)
    monkeypatch.setattr(routes, "load_active", nothing)
    monkeypatch.setattr(routes, "create_llm", lambda *_a: llm)
    pool: Any = None
    owner: Any = None

    plain = routes.AnswerRequest(ticker="acme", question="what was revenue", as_of=when)
    out = await routes.disclosure_answer(plain, pool, owner)
    assert out.phrased_by == "template" and out.text == COMPOSED["text"] and llm.calls == 0

    asked = routes.AnswerRequest(
        ticker="acme", question="what was revenue", as_of=when, phrase=True
    )
    out = await routes.disclosure_answer(asked, pool, owner)
    assert out.phrased_by != "template" and out.text == llm._text and llm.calls == 1

    llm._text = "ACME reported revenue of 9,999 per sec."  # an invented number
    out = await routes.disclosure_answer(asked, pool, owner)
    assert out.phrased_by == "template" and out.text == COMPOSED["text"]
