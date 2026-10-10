"""N3 and the AI search lane against a real Postgres: duplicate groups across languages,
translation of wire items, and leads that never alert on their own."""

import datetime as dt
from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import Any

import asyncpg
import httpx
import pytest

from steward.jobs.runner import run_job
from steward.llm.types import Citation
from steward.news import ai_search
from steward.news.ai_search import Lead, SearchLeads, news_ai_search_handler
from steward.news.embeddings import normalise
from steward.news.translate import LlmTranslator, TranslatedItem
from tests.test_news_pipeline import (
    NOON,
    Clock,
    FakeChannel,
    FakeCorroborator,
    Feeds,
    first_run_feeds,
    ingest,
    make_deps,
    setup_config,
)

pytestmark = pytest.mark.db

JA_HEADLINE = "マイクロソフト、コンテソを50億ドルで買収へ"
JA_URL = "https://www.nikkei.example/msft-contoso-ja"


@pytest.fixture
async def feeds() -> Feeds:
    return Feeds()


@pytest.fixture
async def http(feeds: Feeds) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(feeds.handler)) as client:
        yield client


class TopicEmbedder:
    """One axis per topic keyword, so reports of one event share a vector whatever the
    language."""

    model = "fake-embedding"
    dimensions = 4

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            if "Contoso" in text or "コンテソ" in text:
                out.append([1.0, 0.0, 0.0, 0.0])
            elif "watch" in text:
                out.append([0.0, 1.0, 0.0, 0.0])
            else:
                out.append([0.0, 0.0, 1.0, 0.01 * len(text)])
        return [normalise(v) for v in out]


class JapaneseTranslator(LlmTranslator):
    def __init__(self) -> None:
        self._model = "fake-translator"
        self.calls = 0

    async def translate(self, items: Any) -> Any:  # type: ignore[override]
        from steward.ai import Usage
        from steward.news.translate import check_translation

        self.calls += 1
        out = {}
        for i in items:
            raw = TranslatedItem(
                index=i.index, headline_en="Microsoft to buy Contoso for 50 billion dollars",
                summary_en=None,
            )  # fmt: skip
            checked = check_translation(i, raw)
            if checked is not None:
                out[i.index] = checked
        return out, Usage(model="fake")


async def test_a_japanese_report_groups_with_the_english_one_and_is_translated(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any
) -> None:
    await setup_config(backend_pool)
    deps = make_deps(http, Clock(NOON), FakeCorroborator(), {"telegram": FakeChannel()})
    deps.embedder = TopicEmbedder()
    deps.translator = JapaneseTranslator()
    ja = (JA_HEADLINE, JA_URL, NOON - dt.timedelta(hours=1))
    first_run_feeds(feeds, NOON, extra=[ja])

    assert await ingest(backend_pool, deps) == "succeeded"

    rows = await backend_pool.fetch(
        "select url, language, group_id, group_method, title_en, translation_state, linked"
        " from news_article where url = any($1::text[]) order by published_at",
        [JA_URL, "https://www.reuters.com/tech/msft-contoso"],
    )
    english, japanese = rows[0], rows[1] if rows[1]["language"] == "ja" else rows[0]
    assert japanese["language"] == "ja"
    # The Japanese report is not linked (no holding name in it) yet sits in the same group as
    # the English reports; it did not become a story or an alert of its own.
    assert japanese["group_id"] is not None and japanese["group_id"] == english["group_id"]
    assert japanese["linked"] is False
    assert japanese["translation_state"] == "done"
    assert japanese["title_en"] == "Microsoft to buy Contoso for 50 billion dollars"
    assert await backend_pool.fetchval("select count(*) from news_embedding") >= 3
    # Different topics did not group together.
    watch = await backend_pool.fetchval(
        "select group_id from news_article where url = 'https://www.reuters.com/tech/apple-watch'"
    )
    assert watch is None


async def test_an_embedder_failure_degrades_instead_of_failing_the_run(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any
) -> None:
    from steward.news.embeddings import EmbeddingError

    class Broken(TopicEmbedder):
        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise EmbeddingError("HTTP 500")

    await setup_config(backend_pool)
    deps = make_deps(http, Clock(NOON), FakeCorroborator(), {"telegram": FakeChannel()})
    deps.embedder = Broken()
    first_run_feeds(feeds, NOON)

    assert await ingest(backend_pool, deps) == "succeeded"
    assert await backend_pool.fetchval("select count(*) from news_embedding") == 0
    assert await backend_pool.fetchval("select count(*) from news_article") >= 3


class FakeSearchLLM:
    def __init__(self, parsed: SearchLeads, citations: list[Citation]) -> None:
        self.parsed, self.citations = parsed, citations

    def configured(self, model: str) -> bool:
        return True

    async def complete(self, request: Any) -> SimpleNamespace:
        return SimpleNamespace(
            parsed=self.parsed,
            citations=self.citations,
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=500, output_tokens=100),
        )


async def test_an_ai_search_lead_is_stored_at_tier_three_and_never_alerts(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    await setup_config(
        backend_pool,
        extra_sources=[
            {"id": "ai-search", "name": "AI search", "tier": 3, "type": "ai_search"},
        ],
    )
    url = "https://www.somewire.example/apple-acquires-startup"
    llm = FakeSearchLLM(
        SearchLeads(
            leads=[
                Lead(url=url, note="Apple buys a startup."),
                Lead(url="https://invented.example/x", note="Not cited."),
            ]
        ),
        [Citation(url, "Apple acquires a startup for $2 billion")],
    )
    monkeypatch.setattr(ai_search, "create_llm", lambda settings, client: llm)

    status = await run_job(backend_pool, "news_ai_search", news_ai_search_handler, trigger="manual")

    assert status == "succeeded"
    row = await backend_pool.fetchrow(
        "select source_id, tier, headline, linked from news_article where url = $1", url
    )
    assert row is not None
    assert (row["source_id"], row["tier"]) == ("ai-search", 3)
    # The headline is the provider's citation title; the uncited URL was dropped.
    assert row["headline"] == "Apple acquires a startup for $2 billion"
    assert (
        await backend_pool.fetchval(
            "select count(*) from news_article where url = 'https://invented.example/x'"
        )
        == 0
    )
    # Tier-3 leads never reach a story, so there is nothing to alert on.
    assert await backend_pool.fetchval("select count(*) from alert") == 0
    cost = await backend_pool.fetchval(
        "select count(*) from job_step s join job j on j.id = s.job_id"
        " where j.job_type = 'news_ai_search' and s.name = 'search'"
    )
    assert cost == 1
