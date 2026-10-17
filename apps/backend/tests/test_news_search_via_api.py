"""Cost lever: story corroboration and event escalation search through the search API.

Switch off: the model searches with the provider's tool, as before. Switch on with a key: the
code searches, the model reads titles and snippets of allowed outlets only and is sent no
search tool. Switch on without a key: the provider's search, as before.
"""

import datetime as dt
from types import SimpleNamespace
from typing import Any, cast

from steward.config.jobs import EscalationSettings, NewsSettings
from steward.llm.cache import ResultCache
from steward.news.api_search import ApiSearcher, api_searcher
from steward.news.corroborate import CorroborationAnswer, LLMCorroborator, SearchRequest
from steward.news.escalation import EscalationInput, ImpactDraft, LLMEscalator
from steward.search_api import ApiResult, BraveNewsSearch, SearchAPIError

TODAY = dt.date(2026, 10, 13)
GOOD = "https://bloomberg.com/story"
OFF_LIST = "https://blog.example.net/post"


class _Cache:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    async def get(self, key: str, ttl_days: int | None = None) -> dict[str, Any] | None:
        return self.store.get(key)

    async def put(self, key: str, model: str, result: dict[str, Any]) -> None:
        self.store[key] = result


class _Api:
    def __init__(self, rows: list[ApiResult] | None = None, fail: bool = False) -> None:
        self.queries: list[str] = []
        self._fail = fail
        self._rows = (
            rows
            if rows is not None
            else [
                ApiResult(GOOD, "Apple cuts guidance", "Bloomberg says...", "bloomberg.com"),
                ApiResult(OFF_LIST, "Hot take", "A blog", "blog.example.net"),
            ]
        )

    async def search(self, query: str, since: dt.date, until: dt.date) -> list[ApiResult]:
        self.queries.append(query)
        if self._fail:
            raise SearchAPIError("down")
        return self._rows


def _searcher(api: _Api) -> ApiSearcher:
    return ApiSearcher(cast(BraveNewsSearch, api), cast(ResultCache, _Cache()))


class _Recorder:
    def __init__(self, parsed: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._parsed = parsed

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            content=[],
            stop_reason="end_turn",
            parsed_output=self._parsed,
        )


def _request() -> SearchRequest:
    return SearchRequest(
        headline="Apple cuts guidance",
        summary=None,
        company="Apple",
        published_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
        own_outlets=frozenset({"apnews.com"}),
        domains=("bloomberg.com", "reuters.com"),
    )


def _corroborator(rec: _Recorder, searcher: ApiSearcher | None) -> LLMCorroborator:
    return LLMCorroborator(
        cast(Any, SimpleNamespace(messages=rec)), "gpt-6-luna", 2, searcher=searcher
    )


def _answer(*urls: str) -> CorroborationAnswer:
    return CorroborationAnswer(verdict="supported", supporting_urls=list(urls), note="n")


def test_the_switches_default_to_the_provider_search() -> None:
    assert NewsSettings().corroboration_search_via_api is False
    assert EscalationSettings().search_via_api is False


# ---------------------------------------------------------------------------
# Corroboration
# ---------------------------------------------------------------------------


async def test_corroboration_with_the_switch_off_sends_the_provider_tool() -> None:
    rec = _Recorder(_answer())
    outcome, _ = await _corroborator(rec, None).search(_request(), lambda _: 2)
    [call] = rec.calls
    assert call["tools"][0]["type"] == "web_search"
    assert "<results>" not in call["messages"][0]["content"]
    assert outcome.api_searches == 0


async def test_corroboration_through_the_api_sends_results_and_no_search_tool() -> None:
    api = _Api()
    rec = _Recorder(_answer(GOOD, OFF_LIST, "https://bloomberg.com/invented"))
    outcome, usage = await _corroborator(rec, _searcher(api)).search(_request(), lambda _: 2)
    [call] = rec.calls
    assert "tools" not in call
    prompt = call["messages"][0]["content"]
    assert GOOD in prompt and "Apple cuts guidance" in prompt
    # Outlets outside the allowed tiers never reach the model.
    assert OFF_LIST not in prompt
    # Only a URL the search returned counts; one the model wrote is rejected as before.
    assert outcome.finding.supporting == ((GOOD, 2),)
    assert any("invented" in r and "not among the search results" in r for r in outcome.rejected)
    assert any(OFF_LIST in r for r in outcome.rejected)
    assert outcome.api_searches == 2 == len(api.queries)
    # Brave calls are not provider searches: the usage carries none.
    assert usage.searches == 0


async def test_corroboration_skips_the_model_when_the_api_finds_nothing() -> None:
    rec = _Recorder(_answer())
    outcome, _ = await _corroborator(rec, _searcher(_Api(fail=True))).search(
        _request(), lambda _: 2
    )
    assert rec.calls == []
    assert outcome.finding.note.startswith("search failed")
    assert not outcome.finding.supporting


async def test_corroboration_without_a_key_falls_back_to_the_provider_search() -> None:
    no_key = SimpleNamespace(brave_api_key=None)
    searcher = api_searcher(cast(Any, no_key), cast(Any, None), None)
    assert searcher is None
    rec = _Recorder(_answer())
    await _corroborator(rec, searcher).search(_request(), lambda _: 2)
    [call] = rec.calls
    assert call["tools"][0]["allowed_domains"] == ["bloomberg.com", "reuters.com"]


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------


def _escalation_input() -> EscalationInput:
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
        domains=("bloomberg.com",),
    )


def _draft() -> ImpactDraft:
    return ImpactDraft(headline="h", sentences=[], none_applies=True, rationale="r")


async def test_escalation_with_the_switch_off_sends_the_provider_tool() -> None:
    rec = _Recorder(_draft())
    writer = LLMEscalator(cast(Any, SimpleNamespace(messages=rec)), EscalationSettings())
    result = await writer.write(_escalation_input(), TODAY)
    [call] = rec.calls
    assert call["tools"][0]["allowed_domains"] == ["bloomberg.com"]
    assert result.api_searches == 0


async def test_escalation_through_the_api_reads_results_and_sends_no_tool() -> None:
    api = _Api()
    rec = _Recorder(_draft())
    writer = LLMEscalator(
        cast(Any, SimpleNamespace(messages=rec)),
        EscalationSettings(),
        searcher=_searcher(api),
    )
    result = await writer.write(_escalation_input(), TODAY)
    [call] = rec.calls
    assert "tools" not in call
    prompt = call["messages"][0]["content"]
    assert GOOD in prompt and OFF_LIST not in prompt
    assert result.seen_urls == {GOOD}
    assert result.titles == {GOOD: "Apple cuts guidance"}
    assert result.api_searches == len(api.queries) == 2


async def test_escalation_without_a_key_falls_back_to_the_provider_search() -> None:
    searcher = api_searcher(cast(Any, SimpleNamespace(brave_api_key=None)), cast(Any, None), None)
    rec = _Recorder(_draft())
    writer = LLMEscalator(
        cast(Any, SimpleNamespace(messages=rec)), EscalationSettings(), searcher=searcher
    )
    await writer.write(_escalation_input(), TODAY)
    [call] = rec.calls
    assert call["tools"][0]["type"] == "web_search"
