from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from steward.config.budget import BudgetConfig
from steward.config.jobs import NewsSettings
from steward.costs import PriceTable
from steward.llm import wire
from steward.llm.client import LLM
from steward.llm.types import LLMError, Message, Request, SearchSpec, provider_of


class Answer(BaseModel):
    label: str
    score: float | None = None


def request(**kw: Any) -> Request:
    base: dict[str, Any] = {
        "model": "gpt-6-luna",
        "system": "Be brief.",
        "messages": (Message("user", "Label this."),),
        "schema": Answer,
    }
    return Request(**{**base, **kw})


def test_provider_follows_the_model_prefix() -> None:
    assert provider_of("claude-haiku-4-5") == "anthropic"
    assert provider_of("gpt-6.1-sol") == "openai"
    assert provider_of("gemini-3.8-flash") == "google"
    with pytest.raises(ValueError):
        provider_of("llama-4")


def test_other_providers_model_ids_are_valid_config() -> None:
    news = NewsSettings(classification_model="gpt-6-luna", corroboration_model="gemini-3.8-flash")
    assert news.classification_model == "gpt-6-luna"
    with pytest.raises(ValueError):
        NewsSettings(classification_model="llama-4")


def test_strict_schema_requires_every_property_and_keeps_fields_named_title() -> None:
    class Inner(BaseModel):
        title: str
        note: str | None = None

    class Outer(BaseModel):
        items: list[Inner]
        default: int = 3

    schema = wire.strict_schema(Outer.model_json_schema())
    assert schema["required"] == ["items", "default"] and schema["additionalProperties"] is False
    inner = schema["$defs"]["Inner"]
    assert inner["required"] == ["title", "note"] and "title" in inner["properties"]
    assert "default" in schema["properties"] and "default" not in schema["properties"]["default"]


def test_openai_body_has_schema_effort_cache_key_and_domain_filtered_search() -> None:
    body = wire.openai_body(
        request(
            effort="low",
            cache_key="news",
            search=SearchSpec(max_uses=3, allowed_domains=("sec.gov",)),
        )
    )
    assert body["reasoning"] == {"effort": "low"} and body["prompt_cache_key"] == "news"
    assert body["text"]["format"]["strict"] is True
    assert body["tools"] == [{"type": "web_search", "filters": {"allowed_domains": ["sec.gov"]}}]
    assert body["max_tool_calls"] == 3 and body["store"] is False


OPENAI_ANSWER = {
    "status": "completed",
    "output": [
        {"type": "web_search_call", "status": "completed"},
        {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": '{"label": "earnings", "score": 0.9}',
                    "annotations": [
                        {"type": "url_citation", "url": "https://sec.gov/a", "title": "A"},
                        {"type": "url_citation", "url": "https://sec.gov/a", "title": "A"},
                    ],
                }
            ],
        },
    ],
    "usage": {
        "input_tokens": 1000,
        "input_tokens_details": {"cached_tokens": 800},
        "output_tokens": 50,
    },
}


def test_openai_cached_tokens_are_billed_once() -> None:
    done = wire.openai_completion(request(), OPENAI_ANSWER)
    assert done.parsed == Answer(label="earnings", score=0.9) and len(done.citations) == 1
    assert done.usage.input_tokens == 200 and done.usage.cache_read_input_tokens == 800
    assert done.usage.server_tool_use.web_search_requests == 1
    prices = PriceTable(BudgetConfig())
    cost = prices.cost(
        "gpt-6-luna",
        input_tokens=200,
        output_tokens=50,
        cache_read_tokens=800,
        searches=1,
    )
    # 200 in + 800 at 10 % = 280 token-equivalents at $0.10/M, 50 out at $0.50/M, one $10/1k search
    assert cost == Decimal("0.010053")


def test_openai_truncated_and_refused_answers_are_not_parsed() -> None:
    cut = {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [],
    }
    assert wire.openai_completion(request(), cut).stop_reason == "max_tokens"
    refusal = {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}],
    }
    done = wire.openai_completion(request(), refusal)
    assert done.stop_reason == "refusal" and done.parsed is None


def test_an_answer_that_breaks_the_schema_is_an_llm_error() -> None:
    bad = {
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"nope": 1}'}]}
        ],
    }
    with pytest.raises(LLMError):
        wire.openai_completion(request(), bad)


GOOGLE_ANSWER = {
    "candidates": [
        {
            "finishReason": "STOP",
            "content": {
                "parts": [{"text": "thinking", "thought": True}, {"text": '{"label": "x"}'}]
            },
            "groundingMetadata": {
                "webSearchQueries": ["q1", "q2"],
                "groundingChunks": [
                    {"web": {"uri": "https://www.sec.gov/a", "title": "A"}},
                    {"web": {"uri": "https://blog.example.com/b", "title": "B"}},
                ],
            },
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 500,
        "cachedContentTokenCount": 400,
        "candidatesTokenCount": 20,
        "thoughtsTokenCount": 30,
    },
}


def test_google_usage_search_count_and_off_list_citations() -> None:
    req = request(
        model="gemini-3.8-flash", search=SearchSpec(allowed_domains=("sec.gov",)), effort="medium"
    )
    body = wire.google_body(req)
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "medium"}
    assert body["tools"] == [{"googleSearch": {}}]
    done = wire.google_completion(req, GOOGLE_ANSWER)
    assert done.parsed == Answer(label="x")
    assert [c.url for c in done.citations] == ["https://www.sec.gov/a"]
    assert [c.url for c in done.off_list_citations] == ["https://blog.example.com/b"]
    assert done.usage.input_tokens == 100 and done.usage.cache_read_input_tokens == 400
    assert done.usage.output_tokens == 50 and done.usage.server_tool_use.web_search_requests == 2


def test_google_blocked_prompt_is_an_error() -> None:
    with pytest.raises(LLMError):
        wire.google_completion(request(), {"promptFeedback": {"blockReason": "SAFETY"}})


def test_per_model_prices_override_the_global_cache_and_search_prices() -> None:
    prices = PriceTable(BudgetConfig())
    sol = prices.prices_for("gpt-6.1-sol")
    flash = prices.prices_for("gemini-3.8-flash")
    claude = prices.prices_for("claude-sonnet-5")
    assert sol and flash and claude
    assert (sol["cache_read_multiplier"], sol["cache_write_multiplier"]) == (0.05, 1)
    assert flash["web_search_per_1000"] == 14 and claude["cache_write_multiplier"] == 1.25


async def test_complete_posts_to_the_provider_and_retries_a_rate_limit() -> None:
    calls: list[httpx.Request] = []

    def handler(request_: httpx.Request) -> httpx.Response:
        calls.append(request_)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json=OPENAI_ANSWER)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = LLM(openai_key="k", http=http, retries=1)
    done = await llm.complete(request(timeout=5))
    assert done.parsed == Answer(label="earnings", score=0.9) and len(calls) == 2
    assert calls[0].headers["authorization"] == "Bearer k"


async def test_complete_fails_on_a_client_error_and_a_missing_key() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "bad"}))
    )
    with pytest.raises(LLMError) as err:
        await LLM(openai_key="k", http=http).complete(request(timeout=5))
    assert err.value.status == 400
    with pytest.raises(LLMError):
        await LLM(http=http).complete(request(model="gemini-3.8-flash", timeout=5))
    assert LLM(openai_key="k").configured("gpt-6-luna")
    assert not LLM(openai_key="k").configured("gemini-3.8-flash")
