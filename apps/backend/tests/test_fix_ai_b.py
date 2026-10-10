"""Group AI-B: owner prompt overrides reach the news and chat requests; the multi-step loops
ask for prompt caching; failed calls still report their usage."""

import datetime as dt
import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from steward.chat.assistant import SYSTEM as CHAT_SYSTEM
from steward.chat.assistant import AnswerSentence, ChatAnswerDraft, LLMChatAssistant
from steward.config.event_rules import CLASSIFIABLE_EVENT_TYPES
from steward.config.jobs import ChatSettings, DigestSettings, EscalationSettings
from steward.config.prompts import PromptsConfig, default_text
from steward.llm.types import Completion, Request, StopReason, ToolCall, normalized_usage
from steward.news.classify import (
    SYSTEM_PROMPT,
    Candidate,
    ClassificationError,
    ClassificationOutput,
    ClassifyItem,
    LLMClassifier,
    items_block,
    system_prompt,
)
from steward.news.corroborate import CorroborationAnswer, LLMCorroborator, SearchRequest
from steward.news.digest import DigestDraft, LLMDigestWriter
from steward.news.escalation import EscalationInput, ImpactDraft, LLMEscalator

OVERRIDE = "Custom instructions from the owner, long enough to be valid."

# sha256 of the classification system prompt the classification eval last measured
# (evals/baseline.json, 97%): the default must stay byte-identical to it until re-measured.
LEGACY_CLASSIFICATION_SHA = "69750d37d6d646060c9371b97bdfd52b2f6dc9b13271a21ff3b7a362f6e4d85c"


def _usage(**extra: Any) -> SimpleNamespace:
    return SimpleNamespace(input_tokens=10, output_tokens=5, **extra)


class _Recorder:
    """messages.parse / create that records each request and replays canned responses."""

    def __init__(self, responses: list[Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = responses

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._responses[len(self.calls) - 1]

    async def create(self, **kwargs: Any) -> Any:
        return await self.parse(**kwargs)


def _completion(
    parsed: Any = None, stop_reason: StopReason = "end", tool_calls: list[ToolCall] | None = None
) -> Completion:
    return Completion(
        model="gpt-6-luna",
        provider="openai",
        text="",
        parsed=parsed,
        stop_reason=stop_reason,
        usage=normalized_usage(input_tokens=10, output_tokens=5),
        tool_calls=tool_calls or [],
    )


class _FakeLLM:
    """Stands in for `LLM`: records each request and replays canned completions."""

    def __init__(self, responses: list[Completion]) -> None:
        self.calls: list[Request] = []
        self._responses = responses

    async def complete(self, request: Request) -> Completion:
        self.calls.append(request)
        return self._responses[len(self.calls) - 1]


def _item(index: int = 0, summary: str | None = "Apple said it would buy back shares.") -> Any:
    return ClassifyItem(
        index=index,
        headline="Apple announces buyback",
        summary=summary,
        outlet="reuters.com",
        tier=2,
        published_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        candidates=(Candidate("AAPL_US_EQ", "name", "Apple"),),
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_the_default_classification_prompt_is_unchanged() -> None:
    assert hashlib.sha256(system_prompt().encode()).hexdigest() == LEGACY_CLASSIFICATION_SHA
    # The editable default is the instruction text; the event types are appended by code.
    assert default_text("news_classification") == SYSTEM_PROMPT
    assert "Event types:" not in SYSTEM_PROMPT
    assert all(f"- {name}:" in system_prompt() for name in CLASSIFIABLE_EVENT_TYPES)


def test_an_override_gets_the_event_types_appended() -> None:
    built = system_prompt(OVERRIDE)
    assert built.startswith(OVERRIDE)
    assert all(f"- {name}:" in built for name in CLASSIFIABLE_EVENT_TYPES)
    assert "Rules:" not in built


async def test_the_classification_override_reaches_the_request() -> None:
    parsed = ClassificationOutput(items=[])
    llm = _FakeLLM([_completion(parsed), _completion(parsed)])
    await LLMClassifier(llm, "gpt-6-luna").classify(  # type: ignore[arg-type]
        [_item()], [], dt.date(2026, 9, 1)
    )
    config = PromptsConfig(overrides={"news_classification": OVERRIDE})
    await LLMClassifier(
        llm,  # type: ignore[arg-type]
        "gpt-6-luna",
        config.text("news_classification", SYSTEM_PROMPT),
    ).classify([_item()], [], dt.date(2026, 9, 1))
    default, override = llm.calls
    # The holdings block follows the static instructions.
    assert default.system.startswith(system_prompt())
    assert override.system.startswith(system_prompt(OVERRIDE))
    assert OVERRIDE not in default.system
    # Related requests share one cache key so the provider lands them on the same prefix.
    assert default.cache_key == override.cache_key == "news_classification"


async def test_a_failed_classification_still_reports_its_usage() -> None:
    llm = _FakeLLM([_completion(None, "max_tokens")])
    with pytest.raises(ClassificationError) as caught:
        await LLMClassifier(llm, "gpt-6-luna").classify(  # type: ignore[arg-type]
            [_item()], [], dt.date(2026, 9, 1)
        )
    assert caught.value.usage is not None
    assert caught.value.usage.input_tokens == 10


def test_a_summary_that_repeats_the_headline_is_sent_once() -> None:
    block = items_block([_item(0, "Apple announces buyback"), _item(1)])
    assert block.count("Apple announces buyback") == 2  # the two headlines only
    assert "buy back shares" in block
    assert block.count("\n") == 3  # one item per line


# ---------------------------------------------------------------------------
# Corroboration and escalation: web-search loops
# ---------------------------------------------------------------------------


async def test_the_corroboration_override_and_search_reach_the_request() -> None:
    answer = CorroborationAnswer(verdict="not_found", note="Nothing found.")
    done = SimpleNamespace(usage=_usage(), content=[], stop_reason="end_turn", parsed_output=answer)
    recorder = _Recorder([done])
    corroborator = LLMCorroborator(
        SimpleNamespace(messages=recorder),  # type: ignore[arg-type]
        "gpt-6-luna",
        3,
        OVERRIDE,
    )
    request = SearchRequest(
        headline="Apple announces buyback",
        summary=None,
        company="Apple",
        published_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        own_outlets=frozenset({"reuters.com"}),
        domains=("bloomberg.com",),
    )
    await corroborator.search(request, lambda host: 2)
    [call] = recorder.calls
    assert call["system"] == OVERRIDE
    [tool] = call["tools"]
    assert tool["type"] == "web_search" and tool["allowed_domains"] == ["bloomberg.com"]


def _escalation_input(domains: tuple[str, ...]) -> EscalationInput:
    return EscalationInput(
        ticker="AAPL_US_EQ",
        name="Apple",
        trigger="story",
        cluster_id=1,
        event_type="guidance",
        severity=2,
        headline="Apple cuts guidance",
        sources=[],
        material="Event: ...",
        thesis=None,
        domains=domains,
    )


async def test_the_escalation_override_and_search_reach_the_request() -> None:
    draft = ImpactDraft(headline="h", sentences=[], none_applies=True, rationale="r")
    done = SimpleNamespace(usage=_usage(), content=[], stop_reason="end_turn", parsed_output=draft)
    recorder = _Recorder([done, done])
    writer = LLMEscalator(
        SimpleNamespace(messages=recorder),  # type: ignore[arg-type]
        EscalationSettings(),
        OVERRIDE,
    )
    await writer.write(_escalation_input(("reuters.com",)), dt.date(2026, 9, 1))
    await writer.write(_escalation_input(()), dt.date(2026, 9, 1))
    with_search, without = recorder.calls
    assert with_search["system"] == without["system"] == OVERRIDE
    assert with_search["tools"][0]["allowed_domains"] == ["reuters.com"]
    # Without source domains there is nothing to search.
    assert "tools" not in without


# ---------------------------------------------------------------------------
# Chat: the tool loop and the conversation history
# ---------------------------------------------------------------------------


class _Tools:
    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}


async def test_the_chat_loop_keeps_a_stable_prefix_and_one_cache_key() -> None:
    step = _completion(None, "tool_use", [ToolCall("t1", "portfolio", {})])
    draft = ChatAnswerDraft(sentences=[AnswerSentence(text="Done.")])
    llm = _FakeLLM([step, _completion(draft)])
    assistant = LLMChatAssistant(
        llm,  # type: ignore[arg-type]
        ChatSettings(),
        (),
        OVERRIDE,
    )
    history = [
        {"role": "user", "content": "How is Apple doing?"},
        {"role": "assistant", "content": "Fine."},
    ]
    result = await assistant.answer(history, "And MSFT?", _Tools(), dt.date(2026, 9, 1))  # type: ignore[arg-type]
    assert result.draft is not None
    assert len(llm.calls) == 2
    for call in llm.calls:
        assert call.system == OVERRIDE
        assert call.cache_key == "chat"
    first, second = (c.messages for c in llm.calls)
    # The second step resends the first step's messages unchanged (a byte-stable prefix),
    # then adds the assistant's tool call and the tool's result.
    assert second[: len(first)] == first
    assert [m.role for m in second[len(first) :]] == ["assistant", "tool"]
    # The history is sent as plain turns before the new question.
    assert [m.text for m in first[:2]] == ["How is Apple doing?", "Fine."]
    assert "And MSFT?" in first[2].text


def test_the_default_chat_prompt_is_the_module_constant() -> None:
    assert default_text("chat_assistant") == CHAT_SYSTEM


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


async def test_the_digest_override_reaches_the_request() -> None:
    llm = _FakeLLM([_completion(DigestDraft(overview=[], sections=[]))])
    writer = LLMDigestWriter(
        llm,  # type: ignore[arg-type]
        DigestSettings(use_batch=False),
        system=OVERRIDE,
    )
    await writer.write("material", dt.date(2026, 9, 1))
    assert llm.calls[0].system == OVERRIDE
