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
