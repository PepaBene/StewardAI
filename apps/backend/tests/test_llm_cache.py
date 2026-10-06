import json
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from steward.llm.cache import ResultCache, request_key
from steward.llm.routing import RoutingClient


class Out(BaseModel):
    label: str


class FakePool:
    def __init__(self) -> None:
        self.rows: dict[str, str] = {}

    async def fetchval(self, _sql: str, key: str, _days: int) -> str | None:
        return self.rows.get(key)

    async def execute(self, _sql: str, key: str, _job: str, _model: str, result: str) -> None:
        self.rows[key] = result


class FakeLLM:
    """Stands in for `LLM`: counts the provider calls."""

    def __init__(self, citations: list[Any] | None = None) -> None:
        self.calls = 0
        self.citations = citations or []

    async def complete(self, _request: Any) -> Any:
        self.calls += 1
        return SimpleNamespace(
            text='{"label": "yes"}',
            parsed=Out(label="yes"),
            usage=None,
            stop_reason="end",
            citations=self.citations,
        )


def test_the_key_changes_with_prompt_model_and_effort() -> None:
    base = request_key("claude-sonnet-5", "sys", [{"role": "user", "content": "a"}], Out, None)
    assert base == request_key(
        "claude-sonnet-5", "sys", [{"role": "user", "content": "a"}], Out, None
    )
    assert base != request_key(
        "claude-sonnet-5", "sys2", [{"role": "user", "content": "a"}], Out, None
    )
    assert base != request_key(
        "claude-haiku-4-5", "sys", [{"role": "user", "content": "a"}], Out, None
    )
    assert base != request_key(
        "claude-sonnet-5", "sys", [{"role": "user", "content": "b"}], Out, None
    )
    assert base != request_key(
        "claude-sonnet-5", "sys", [{"role": "user", "content": "a"}], Out, "low"
    )


async def test_a_repeated_request_is_answered_from_the_cache() -> None:
    pool = FakePool()
    cache = ResultCache(pool, "ai_approvals")  # type: ignore[arg-type]
    fake = FakeLLM()
    client = RoutingClient(None, fake, cache)  # type: ignore[arg-type]
    args: dict[str, Any] = {
        "model": "gpt-6-luna",
        "system": "s",
        "messages": [{"role": "user", "content": "same"}],
        "output_format": Out,
    }
    first = await client.messages.parse(**args)
    second = await client.messages.parse(**args)
    assert first.parsed_output == second.parsed_output == Out(label="yes")
    assert fake.calls == 1 and (cache.hits, cache.misses) == (1, 1)
    assert json.loads(next(iter(pool.rows.values()))) == {"label": "yes"}
    await client.messages.parse(**{**args, "messages": [{"role": "user", "content": "other"}]})
    assert fake.calls == 2


async def test_a_call_with_tools_or_without_a_schema_is_never_cached() -> None:
    pool = FakePool()
    fake = FakeLLM()
    client = RoutingClient(None, fake, ResultCache(pool, "x"))  # type: ignore[arg-type]
    args: dict[str, Any] = {"model": "gpt-6-luna", "system": "s", "messages": []}
    await client.messages.parse(**args, tools=[{"type": "web_search_20260209"}], output_format=Out)
    await client.messages.parse(**args)
    assert fake.calls == 2 and not pool.rows


async def test_a_search_answer_is_cached_only_when_it_has_sources() -> None:
    from steward.llm.types import Citation

    args: dict[str, Any] = {
        "model": "gemini-3.8-flash",
        "system": "s",
        "messages": [{"role": "user", "content": "find the IR site"}],
        "tools": [{"type": "web_search_20260209"}],
        "output_format": Out,
    }
    pool = FakePool()
    unsourced = FakeLLM()  # answered from memory: no sources
    cache = ResultCache(pool, "x", search_ttl_days=3)  # type: ignore[arg-type]
    client = RoutingClient(None, unsourced, cache)  # type: ignore[arg-type]
    await client.messages.parse(**args)
    await client.messages.parse(**args)
    assert unsourced.calls == 2 and not pool.rows

    sourced = FakeLLM([Citation("https://ir.example.com/", "IR")])
    client = RoutingClient(None, sourced, cache)  # type: ignore[arg-type]
    await client.messages.parse(**args)
    await client.messages.parse(**args)
    assert sourced.calls == 1 and len(pool.rows) == 1
