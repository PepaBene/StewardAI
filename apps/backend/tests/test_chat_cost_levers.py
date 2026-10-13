"""Cost levers H1-H3: cache retention, the API search tool with its cache, a byte-stable prefix."""

import datetime as dt
import json
from typing import Any, cast

from steward.chat import search_tool
from steward.chat.assistant import SYSTEM, LLMChatAssistant
from steward.chat.search_tool import ChatWebSearch
from steward.chat.tools import TOOL_DEFINITIONS
from steward.config.jobs import ChatSettings
from steward.llm import wire
from steward.llm.cache import ResultCache
from steward.llm.types import Completion, Message, Request, normalized_usage
from steward.search_api import ApiResult, SearchAPIError

TODAY = dt.date(2026, 10, 13)


def _req(**kw: Any) -> Request:
    return Request(model="gpt-6-luna", system="s", messages=(Message("user", "q"),), **kw)


def test_retention_is_sent_only_with_a_cache_key_and_when_asked() -> None:
    assert "prompt_cache_retention" not in wire.openai_body(_req(cache_key="chat"))
    body = wire.openai_body(_req(cache_key="chat", cache_retention="24h"))
    assert body["prompt_cache_retention"] == "24h"
    assert "prompt_cache_retention" not in wire.openai_body(_req(cache_retention="24h"))


def test_the_tool_definitions_do_not_change_between_calls() -> None:
    first = json.dumps(TOOL_DEFINITIONS, sort_keys=True)
    second = json.dumps(TOOL_DEFINITIONS, sort_keys=True)
    assert first == second
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert len(names) == len(set(names))
    assert "Today is" not in SYSTEM and "20" + "26" not in SYSTEM


class _Cache:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    async def get(self, key: str, ttl_days: int | None = None) -> dict[str, Any] | None:
        return self.store.get(key)

    async def put(self, key: str, model: str, result: dict[str, Any]) -> None:
        self.store[key] = result


class _Api:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def search(self, query: str, since: dt.date, until: dt.date) -> list[ApiResult]:
        self.calls += 1
        if self.fail:
            raise SearchAPIError("down")
        return [
            ApiResult("https://www.reuters.com/a", "A", "snip", "www.reuters.com"),
            ApiResult("https://blog.example.net/b", "B", "snip", "blog.example.net"),
        ]


def _web(api: _Api, cache: _Cache | None, max_searches: int = 2) -> ChatWebSearch:
    return ChatWebSearch(
        cast(Any, api), ("reuters.com",), cast(ResultCache, cache), 3, max_searches
    )


async def test_results_are_limited_to_the_allowed_outlets_and_titles_recorded() -> None:
    web = _web(_Api(), _Cache())
    out = await web.call({"query": "ASML orders"}, TODAY)
    assert [r["url"] for r in out["results"]] == ["https://www.reuters.com/a"]
    assert web.titles == {"https://www.reuters.com/a": "A"}


async def test_an_identical_search_is_answered_from_the_cache() -> None:
    api = _Api()
    web = _web(api, _Cache(), max_searches=1)
    await web.call({"query": "ASML orders"}, TODAY)
    again = await web.call({"query": "asml orders"}, TODAY)
    assert api.calls == 1 and again["cached"] is True
    assert web.left == 0


async def test_the_search_limit_and_errors_come_back_as_data() -> None:
    web = _web(_Api(), None, max_searches=1)
    await web.call({"query": "a"}, TODAY)
    assert "limit" in (await web.call({"query": "b"}, TODAY))["error"]
    assert "failed" in (await _web(_Api(fail=True), None).call({"query": "a"}, TODAY))["error"]
    assert "required" in (await _web(_Api(), None).call({"query": " "}, TODAY))["error"]


class _LLM:
    def __init__(self) -> None:
        self.requests: list[Request] = []

    async def complete(self, request: Request) -> Completion:
        self.requests.append(request)
        return Completion(
            model=request.model,
            provider="openai",
            text="",
            parsed=None,
            stop_reason="end",
            usage=normalized_usage(input_tokens=1, output_tokens=1),
        )


async def test_with_the_api_search_the_provider_search_is_off_and_the_tool_is_last() -> None:
    llm = _LLM()
    settings = ChatSettings(model="gpt-6-luna", cache_retention="24h")
    assistant = LLMChatAssistant(
        cast(Any, llm), settings, ("reuters.com",), SYSTEM, _web(_Api(), None)
    )
    await assistant.answer([], "hello", cast(Any, None), TODAY)
    sent = llm.requests[0]
    assert sent.search is None
    assert sent.tools[-1].name == search_tool.TOOL_NAME
    assert [t.name for t in sent.tools[:-1]] == [t["name"] for t in TOOL_DEFINITIONS]
    assert sent.cache_retention == "24h"
    assert "Today is" in sent.messages[-1].text


async def test_without_it_the_provider_search_stays_on() -> None:
    llm = _LLM()
    assistant = LLMChatAssistant(cast(Any, llm), ChatSettings(model="gpt-6-luna"), ("reuters.com",))
    await assistant.answer([], "hello", cast(Any, None), TODAY)
    assert llm.requests[0].search is not None
    assert llm.requests[0].cache_retention is None
