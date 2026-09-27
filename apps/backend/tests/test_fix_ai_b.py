"""Group AI-B: owner prompt overrides reach the news and chat requests; the multi-step loops
ask for prompt caching; failed calls still report their usage."""

import datetime as dt
import hashlib
from types import SimpleNamespace
from typing import Any

import pytest
from anthropic.types import ToolUseBlock

from steward.chat.assistant import SYSTEM as CHAT_SYSTEM
from steward.chat.assistant import AnswerSentence, ChatAnswerDraft, ClaudeChatAssistant
from steward.config.event_rules import EVENT_TYPES
from steward.config.jobs import ChatSettings, DigestSettings, EscalationSettings
from steward.config.prompts import PromptsConfig, default_text
from steward.news.classify import (
    SYSTEM_PROMPT,
    Candidate,
    ClassificationError,
    ClassificationOutput,
    ClassifyItem,
    ClaudeClassifier,
    items_block,
    system_prompt,
)
from steward.news.corroborate import ClaudeCorroborator, CorroborationAnswer, SearchRequest
from steward.news.digest import ClaudeDigestWriter
from steward.news.escalation import ClaudeEscalator, EscalationInput, ImpactDraft

OVERRIDE = "Custom instructions from the owner, long enough to be valid."

# sha256 of the classification system prompt before prompts became configurable: the default
# must stay byte-identical to what the classification eval measured.
LEGACY_CLASSIFICATION_SHA = "0d36c9d4b5dffb4d4c2797de234348b4cc1cd4887749d66acf6e6a4099d9904c"


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
    assert all(f"- {name}:" in system_prompt() for name in EVENT_TYPES)


def test_an_override_gets_the_event_types_appended() -> None:
    built = system_prompt(OVERRIDE)
    assert built.startswith(OVERRIDE)
    assert all(f"- {name}:" in built for name in EVENT_TYPES)
    assert "Rules:" not in built


async def test_the_classification_override_reaches_the_request() -> None:
    parsed = ClassificationOutput(items=[])
    response = SimpleNamespace(usage=_usage(), stop_reason="end_turn", parsed_output=parsed)
    recorder = _Recorder([response, response])
    client = SimpleNamespace(messages=recorder)
    client.with_options = lambda **_kwargs: client
    await ClaudeClassifier(client, "claude-haiku-4-5").classify(  # type: ignore[arg-type]
        [_item()], [], dt.date(2026, 9, 1)
    )
    config = PromptsConfig(overrides={"news_classification": OVERRIDE})
    await ClaudeClassifier(
        client,  # type: ignore[arg-type]
        "claude-haiku-4-5",
        config.text("news_classification", SYSTEM_PROMPT),
    ).classify([_item()], [], dt.date(2026, 9, 1))
    default_system, override_system = (c["system"][0]["text"] for c in recorder.calls)
    assert default_system == system_prompt()
    assert override_system == system_prompt(OVERRIDE)
    # The sub-minimum prefix carries no cache marker (it would only pay the write premium).
    assert all("cache_control" not in block for c in recorder.calls for block in c["system"])


async def test_a_failed_classification_still_reports_its_usage() -> None:
    response = SimpleNamespace(usage=_usage(), stop_reason="max_tokens", parsed_output=None)
    client = SimpleNamespace(messages=_Recorder([response]))
    client.with_options = lambda **_kwargs: client
    with pytest.raises(ClassificationError) as caught:
        await ClaudeClassifier(client, "claude-haiku-4-5").classify(  # type: ignore[arg-type]
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


async def test_the_corroboration_override_and_cache_reach_the_request() -> None:
    answer = CorroborationAnswer(verdict="not_found", note="Nothing found.")
    paused = SimpleNamespace(usage=_usage(), content=[], stop_reason="pause_turn")
    done = SimpleNamespace(usage=_usage(), content=[], stop_reason="end_turn", parsed_output=answer)
    recorder = _Recorder([paused, done])
    corroborator = ClaudeCorroborator(
        SimpleNamespace(messages=recorder),  # type: ignore[arg-type]
        "claude-haiku-4-5",
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
    assert len(recorder.calls) == 2
    for call in recorder.calls:
        assert call["system"] == OVERRIDE
        assert call["extra_body"] == {"cache_control": {"type": "ephemeral"}}


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


async def test_the_escalation_override_and_cache_reach_the_request() -> None:
    draft = ImpactDraft(headline="h", sentences=[], none_applies=True, rationale="r")
    done = SimpleNamespace(usage=_usage(), content=[], stop_reason="end_turn", parsed_output=draft)
    recorder = _Recorder([done, done])
    writer = ClaudeEscalator(
        SimpleNamespace(messages=recorder),  # type: ignore[arg-type]
        EscalationSettings(),
        OVERRIDE,
    )
    await writer.write(_escalation_input(("reuters.com",)), dt.date(2026, 9, 1))
    await writer.write(_escalation_input(()), dt.date(2026, 9, 1))
    with_search, without = recorder.calls
    assert with_search["system"] == without["system"] == OVERRIDE
    assert with_search["extra_body"] == {"cache_control": {"type": "ephemeral"}}
    # A single call without tools has nothing to read back: no cache write premium.
    assert without["extra_body"] is None


# ---------------------------------------------------------------------------
# Chat: the tool loop and the conversation history
# ---------------------------------------------------------------------------


class _Tools:
    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}


async def test_the_chat_loop_caches_every_step_and_the_history() -> None:
    block = ToolUseBlock(id="t1", name="portfolio", input={}, type="tool_use")
    step = SimpleNamespace(usage=_usage(), content=[block], stop_reason="tool_use")
    draft = ChatAnswerDraft(sentences=[AnswerSentence(text="Done.")])
    done = SimpleNamespace(usage=_usage(), content=[], stop_reason="end_turn", parsed_output=draft)
    recorder = _Recorder([step, done])
    assistant = ClaudeChatAssistant(
        SimpleNamespace(messages=recorder),  # type: ignore[arg-type]
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
    assert len(recorder.calls) == 2
    for call in recorder.calls:
        assert call["system"] == OVERRIDE
        assert call["extra_body"] == {"cache_control": {"type": "ephemeral"}}
    first, second = (c["messages"] for c in recorder.calls)
    # The second step resends the first step's messages unchanged (a byte-stable prefix).
    assert second[: len(first)] == first
    # The last history message carries a breakpoint for the next question's request.
    assert first[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in first[0]["content"][0]


def test_the_default_chat_prompt_is_the_module_constant() -> None:
    assert default_text("chat_assistant") == CHAT_SYSTEM


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


async def test_the_digest_override_reaches_the_request() -> None:
    text = SimpleNamespace(type="text", text='{"overview": [], "sections": []}')
    response = SimpleNamespace(usage=_usage(), content=[text], stop_reason="end_turn")
    recorder = _Recorder([response])
    writer = ClaudeDigestWriter(
        SimpleNamespace(messages=recorder),  # type: ignore[arg-type]
        DigestSettings(use_batch=False),
        system=OVERRIDE,
    )
    await writer.write("material", dt.date(2026, 9, 1))
    assert recorder.calls[0]["system"] == OVERRIDE
