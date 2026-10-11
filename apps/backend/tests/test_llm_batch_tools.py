"""Function tools and batch payloads in the provider wire code, `classify_many` without an
answer, and per-provider prompt overrides. No network: these are payload and parsing checks."""

import asyncio
import datetime as dt
import json
from typing import Any

from pydantic import BaseModel

from steward.config.prompts import PromptsConfig
from steward.llm import wire
from steward.llm.types import Message, Request, ToolCall, ToolSpec
from steward.news.classify import LLMClassifier


class Answer(BaseModel):
    value: int


TOOL = ToolSpec("holding", "One holding", {"type": "object", "properties": {"ticker": {}}})


def _req(model: str = "gpt-6-luna", **kw: Any) -> Request:
    return Request(model=model, system="s", messages=(Message("user", "q"),), schema=Answer, **kw)


# ------------------------------------------------------------------------------- tools


def test_openai_body_declares_function_tools() -> None:
    body = wire.openai_body(_req(tools=(TOOL,)))
    assert {"type": "function", "name": "holding"}.items() <= body["tools"][0].items()


def test_openai_function_call_becomes_a_tool_call_and_keeps_the_output_items() -> None:
    data = {
        "status": "completed",
        "output": [
            {"type": "reasoning", "id": "rs_1"},
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "holding",
                "arguments": '{"ticker": "AAPL"}',
            },
        ],
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }
    done = wire.openai_completion(_req(tools=(TOOL,)), data)
    assert done.stop_reason == "tool_use"
    assert done.tool_calls == [ToolCall("c1", "holding", {"ticker": "AAPL"})]
    assert done.raw == data["output"]


def test_openai_replays_assistant_items_and_tool_results() -> None:
    items = [{"type": "reasoning", "id": "rs_1"}]
    messages = (
        Message("user", "q"),
        Message("assistant", "", raw=items),
        Message("tool", "result", tool_call_id="c1", tool_name="holding"),
    )
    sent = wire._openai_input(messages)
    assert sent[0] == {"role": "user", "content": "q"}
    assert sent[1] == items[0]
    assert sent[2] == {"type": "function_call_output", "call_id": "c1", "output": "result"}


def test_google_body_declares_functions_and_groups_tool_results() -> None:
    body = wire.google_body(_req("gemini-3.8-flash", tools=(TOOL,)))
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "holding"
    contents = wire._google_contents(
        (
            Message("user", "q"),
            Message("tool", "a", tool_call_id="1", tool_name="holding"),
            Message("tool", "b", tool_call_id="2", tool_name="holding"),
        )
    )
    assert len(contents) == 2 and len(contents[1]["parts"]) == 2
    assert all("_tool" not in c for c in contents)


def test_google_function_call_is_a_tool_call() -> None:
    data = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": "holding", "args": {"ticker": "X"}}}],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1},
    }
    done = wire.google_completion(_req("gemini-3.8-flash", tools=(TOOL,)), data)
    assert done.stop_reason == "tool_use" and done.tool_calls[0].arguments == {"ticker": "X"}


# ------------------------------------------------------------------------------- batches


def _openai_row(index: int, value: int, status: int = 200) -> dict[str, Any]:
    body = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps({"value": value})}],
            }
        ],
        "usage": {"input_tokens": 4, "output_tokens": 1},
    }
    return {"custom_id": f"r{index}", "response": {"status_code": status, "body": body}}


def test_openai_batch_file_has_one_responses_request_per_line() -> None:
    lines = wire.openai_batch_lines([_req(), _req()]).splitlines()
    rows = [json.loads(line) for line in lines]
    assert [r["custom_id"] for r in rows] == ["r0", "r1"]
    assert {r["url"] for r in rows} == {"/v1/responses"}
    assert rows[0]["body"]["model"] == "gpt-6-luna"


def test_openai_batch_results_are_matched_by_id_and_failures_are_none() -> None:
    requests = [_req(), _req(), _req()]
    out_lines = [
        _openai_row(2, 22),  # out of order
        _openai_row(0, 10),
        {"custom_id": "r1", "error": {"message": "boom"}, "response": None},
    ]
    results = wire.openai_batch_results("\n".join(json.dumps(r) for r in out_lines), requests)
    assert results[0] is not None and results[0].parsed == Answer(value=10)
    assert results[1] is None
    assert results[2] is not None and results[2].parsed == Answer(value=22)


def test_google_batch_body_tags_each_request() -> None:
    body = wire.google_batch_body([_req("gemini-3.8-flash")] * 2, "steward")
    items = body["batch"]["input_config"]["requests"]["requests"]
    assert [i["metadata"]["key"] for i in items] == ["r0", "r1"]


def test_google_batch_state_and_results() -> None:
    assert wire.google_batch_state({"metadata": {"state": "BATCH_STATE_SUCCEEDED"}}) == "done"
    assert wire.google_batch_state({"metadata": {"state": "BATCH_STATE_FAILED"}}) == "failed"
    assert wire.google_batch_state({"metadata": {"state": "BATCH_STATE_RUNNING"}}) == "running"

    def candidate(value: int) -> dict[str, Any]:
        return {
            "candidates": [
                {
                    "content": {"parts": [{"text": json.dumps({"value": value})}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1},
        }

    data = {
        "response": {
            "inlinedResponses": {
                "inlinedResponses": [
                    {"metadata": {"key": "r1"}, "response": candidate(7)},
                    {"metadata": {"key": "r0"}, "error": {"message": "bad"}},
                ]
            }
        }
    }
    results = wire.google_batch_results(data, [_req("gemini-3.8-flash")] * 2)
    assert results[0] is None
    assert results[1] is not None and results[1].parsed == Answer(value=7)


# ------------------------------------------------------------------- classify_many


class _NoBatchLLM:
    def __init__(self) -> None:
        self.asked = 0

    async def complete_batch(self, requests: list[Request], **_: Any) -> list[None]:
        self.asked += len(requests)
        return [None] * len(requests)


def test_classify_many_returns_none_for_unanswered_groups() -> None:
    llm = _NoBatchLLM()
    classifier = LLMClassifier(llm, "gpt-6-luna")  # type: ignore[arg-type]
    today = dt.date(2026, 10, 6)
    assert asyncio.run(classifier.classify_many([], today)) == []
    out = asyncio.run(classifier.classify_many([((), ()), ((), ())], today))
    assert out == [None, None] and llm.asked == 2


# --------------------------------------------------------------- prompt overrides


def test_an_override_limited_to_some_providers_is_not_sent_to_others() -> None:
    prompts = PromptsConfig(
        overrides={"chat_assistant": "a custom prompt of enough length"},
        override_providers={"chat_assistant": ["google"]},
    )
    assert (
        prompts.text("chat_assistant", "default", "gemini-3.8-flash")
        == "a custom prompt of enough length"
    )
    assert prompts.text("chat_assistant", "default", "gpt-6-luna") == "default"
    assert (
        prompts.text("chat_assistant", "default") == "a custom prompt of enough length"
    )  # no model: unrestricted
    unlimited = PromptsConfig(overrides={"chat_assistant": "a custom prompt of enough length"})
    assert (
        unlimited.text("chat_assistant", "default", "gpt-6-luna")
        == "a custom prompt of enough length"
    )
