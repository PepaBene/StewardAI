"""Cost lever C1: an identical lead search is answered from the cache, and a plain search API
can find the leads instead of a model's built-in web search."""

import datetime as dt
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from steward.ai import Usage
from steward.llm.types import Citation, LLMError
from steward.news.ai_search import (
    AiSearcher,
    ApiSearcher,
    Lead,
    Picks,
    SearchLeadError,
    SearchLeads,
)
from steward.search_api import BraveNewsSearch, SearchAPIError

DAY = dt.date(2026, 10, 11)


class MemoryCache:
    """The ResultCache surface the searcher uses, in memory."""

    search_ttl_days = 1

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def get(self, key: str, ttl_days: int | None = None) -> dict[str, Any] | None:
        return self.rows.get(key)

    async def put(self, key: str, model: str, result: dict[str, Any]) -> None:
        self.rows[key] = result


class FakeLLM:
    def __init__(self, parsed: Any, citations: list[Citation] | None = None) -> None:
        self.parsed, self.citations, self.requests = parsed, citations or [], []

    async def complete(self, request: Any) -> SimpleNamespace:
        self.requests.append(request)
        if isinstance(self.parsed, Exception):
            raise self.parsed
        return SimpleNamespace(
            parsed=self.parsed,
            citations=self.citations,
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


async def test_a_repeated_lead_search_is_answered_from_the_cache() -> None:
    llm = FakeLLM(
        SearchLeads(leads=[Lead(url="https://ft.com/a", note="n")]),
        [Citation("https://ft.com/a", "Headline")],
    )
    cache = MemoryCache()
    searcher = AiSearcher(llm, "gpt-6-luna", cache=cache)  # type: ignore[arg-type]
    usage = Usage(model="gpt-6-luna")
    first = await searcher.search("Apple", "AAPL_US_EQ", DAY, DAY, usage)
    again = await searcher.search("Apple", "AAPL_US_EQ", DAY, DAY, usage)
    assert first == again and len(llm.requests) == 1
    await searcher.search("Apple", "AAPL_US_EQ", DAY - dt.timedelta(days=1), DAY, usage)
    assert len(llm.requests) == 2  # another window is another question


async def test_a_search_that_found_nothing_is_not_cached() -> None:
    llm = FakeLLM(SearchLeads(leads=[]))
    searcher = AiSearcher(llm, "gpt-6-luna", cache=MemoryCache())  # type: ignore[arg-type]
    usage = Usage(model="gpt-6-luna")
    await searcher.search("Apple", "AAPL_US_EQ", DAY, DAY, usage)
    await searcher.search("Apple", "AAPL_US_EQ", DAY, DAY, usage)
    assert len(llm.requests) == 2


BODY = {
    "results": [
        {
            "title": "Apple suppliers cut output",
            "url": "https://www.ft.com/content/abc",
            "description": "Suppliers cut output. More text follows.",
            "meta_url": {"hostname": "www.ft.com"},
        },
        {
            "title": "Top 5 stocks to buy now",
            "url": "https://blog.example/top5",
            "description": "Apple is one of five.",
        },
        {"title": "Duplicate", "url": "https://www.ft.com/content/abc"},
        {"title": "", "url": "https://x.example/untitled"},
        {"url": "https://x.example/no-title"},
    ]
}


def brave(seen: list[httpx.Request], status: int = 200) -> BraveNewsSearch:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=BODY)

    return BraveNewsSearch(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "secret-key")


async def test_the_api_sends_the_key_as_a_header_and_never_in_the_url() -> None:
    seen: list[httpx.Request] = []
    results = await brave(seen).search("Apple", DAY, DAY)
    assert seen[0].headers["X-Subscription-Token"] == "secret-key"
    assert "secret-key" not in str(seen[0].url)
    assert [r.url for r in results] == [
        "https://www.ft.com/content/abc",
        "https://blog.example/top5",
        "https://www.ft.com/content/abc",
    ]  # untitled rows are dropped; de-duplication is the searcher's job


async def test_an_api_failure_names_no_key_or_url() -> None:
    with pytest.raises(SearchAPIError) as caught:
        await brave([], status=500).search("Apple", DAY, DAY)
    assert "secret-key" not in str(caught.value) and "brave" not in str(caught.value).lower()


async def test_the_api_searcher_keeps_the_urls_and_titles_the_index_returned() -> None:
    seen: list[httpx.Request] = []
    llm = FakeLLM(Picks(keep=[1, 7, 0]))  # 7 and 0 do not exist: ignored
    searcher = ApiSearcher(brave(seen), llm, "gpt-6-luna")  # type: ignore[arg-type]
    usage = Usage(model="gpt-6-luna")
    leads = await searcher.search("Apple", "AAPL_US_EQ", DAY, DAY, usage)
    assert [(lead.url, lead.title, lead.host) for lead in leads] == [
        ("https://www.ft.com/content/abc", "Apple suppliers cut output", "www.ft.com")
    ]
    assert leads[0].note == "Suppliers cut output"
    assert usage.input_tokens == 100
    assert llm.requests[0].search is None  # the model runs no search of its own
    assert "Duplicate" not in llm.requests[0].messages[0].text  # de-duplicated before screening


async def test_if_the_screening_call_fails_every_result_is_kept() -> None:
    searcher = ApiSearcher(brave([]), FakeLLM(LLMError("down")), "gpt-6-luna")  # type: ignore[arg-type]
    leads = await searcher.search("Apple", "AAPL_US_EQ", DAY, DAY, Usage(model="gpt-6-luna"))
    assert [lead.url for lead in leads] == [
        "https://www.ft.com/content/abc",
        "https://blog.example/top5",
    ]


async def test_a_failed_search_request_is_a_lead_error_for_that_holding() -> None:
    searcher = ApiSearcher(brave([], status=429), FakeLLM(Picks()), "gpt-6-luna")  # type: ignore[arg-type]
    with pytest.raises(SearchLeadError):
        await searcher.search("Apple", "AAPL_US_EQ", DAY, DAY, Usage(model="gpt-6-luna"))
