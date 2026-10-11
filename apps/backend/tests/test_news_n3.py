"""N3 and the AI search lane without a database: language detection, SimHash, translation
checks, embedding grouping, syndication independence and lead verification."""

import datetime as dt
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from steward.config.news_sources import NewsSourcesConfig
from steward.llm.types import Citation
from steward.news.ai_search import (
    AiSearcher,
    FoundLead,
    Lead,
    SearchLeads,
    raw_items,
    verified_leads,
)
from steward.news.embeddings import (
    EmbeddingError,
    GeminiEmbedder,
    Vec,
    assign_groups,
    cosine,
    estimate_tokens,
    normalise,
)
from steward.news.language import detect_language, resolve_language
from steward.news.simhash import SimhashIndex, distance, item_hash, simhash
from steward.news.translate import (
    LlmTranslator,
    TranslatedItem,
    TranslateItem,
    TranslationError,
    TranslationOutput,
    check_translation,
    numbers_in,
)

NOW = dt.datetime(2026, 10, 10, 12, tzinfo=dt.UTC)


# -- language ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("トヨタ自動車が通期の営業利益見通しを引き上げた", "ja"),
        ("腾讯控股公布第三季度业绩，收入同比增长", "zh"),
        ("삼성전자 3분기 영업이익 발표", "ko"),
        ("Die Bundesbank erwartet für das kommende Jahr eine schwächere Konjunktur und mehr", "de"),
        (
            "The company said that it will report its results for the third quarter of the year",
            "en",
        ),
    ],
)
def test_language_is_detected_from_script_and_stopwords(text: str, code: str) -> None:
    detected = detect_language(text)
    assert detected is not None and detected.language == code


def test_short_or_empty_text_has_no_language() -> None:
    assert detect_language("") is None
    assert detect_language(None) is None
    assert detect_language("Apple") is None


def test_detected_language_beats_a_wrong_feed_tag() -> None:
    assert resolve_language("en", "トヨタ自動車が通期の営業利益見通しを引き上げた", None) == "ja"
    assert resolve_language("de-DE", "Apple", None) == "de"
    assert resolve_language(None, "Apple", None) is None


# -- simhash ----------------------------------------------------------------------------------


def test_a_reworded_copy_is_near_and_a_different_story_is_far() -> None:
    base = item_hash(
        "Microsoft to acquire Contoso for $5 billion", "Deal expected to close in 2027"
    )
    copy = item_hash(
        "UPDATE 1-Microsoft to acquire Contoso for $5 billion - Reuters",
        "Deal expected to close in 2027",
    )
    other = item_hash("Apple unveils a thinner watch", "The new model ships in October")
    assert base is not None and copy is not None and other is not None
    assert distance(base, copy) <= 3
    assert distance(base, other) > 10


def test_simhash_of_nothing_is_none() -> None:
    assert simhash("") is None


def test_the_index_finds_every_hash_within_three_bits() -> None:
    index = SimhashIndex()
    value = 0x0123456789ABCDEF
    index.add(1, value)
    index.add(2, value ^ 0b101)  # 2 bits away, in one band
    index.add(3, value ^ (1 << 3) ^ (1 << 20) ^ (1 << 40))  # 3 bits, three bands
    index.add(4, value ^ 0xFF)  # 8 bits
    found = dict(index.matches(value))
    assert found == {1: 0, 2: 2, 3: 3}
    with pytest.raises(ValueError):
        SimhashIndex(bits=4)


# -- translation checks -----------------------------------------------------------------------


def source(headline: str, summary: str | None = None) -> TranslateItem:
    return TranslateItem(0, headline, summary, "de")


def test_a_translation_must_keep_every_number() -> None:
    src = source("Siemens erhöht Prognose auf 12,5 Milliarden Euro")
    assert check_translation(
        src, TranslatedItem(index=0, headline_en="x 12.5 billion", summary_en=None)
    )
    assert (
        check_translation(src, TranslatedItem(index=0, headline_en="x 12 billion", summary_en=None))
        is None
    )
    assert numbers_in("1.234,5 und ３００") == ["1234.5", "300"]
    # Digits after letters (Q3) are labels, not numbers.
    assert numbers_in("Q3 results") == []
    # A decimal separator is meaningful, a thousands group is not.
    assert numbers_in("1.5 and 15 and 1,5 and 1,500") == ["1.5", "15", "1.5", "1500"]
    assert numbers_in("0.500 and 2,50") == ["0.5", "2.5"]
    # Unit letters glued to the number do not hide it.
    assert numbers_in("$12bn, 3x EBITDA, 5m units") == ["12", "3", "5"]
    assert numbers_in("5億円") == []
    assert (
        check_translation(
            source("Umsatz 12bn"),
            TranslatedItem(index=0, headline_en="x 12 billion", summary_en=None),
        )
        is not None
    )
    assert (
        check_translation(
            source("Umsatz 1.5bn"),
            TranslatedItem(index=0, headline_en="x 15 billion", summary_en=None),
        )
        is None
    )


def test_empty_or_runaway_translations_are_rejected() -> None:
    src = source("Kurz")
    assert (
        check_translation(src, TranslatedItem(index=0, headline_en="  ", summary_en=None)) is None
    )
    long = TranslatedItem(index=0, headline_en="word " * 100, summary_en=None)
    assert check_translation(src, long) is None


class FakeLLM:
    def __init__(self, parsed: Any, citations: list[Citation] | None = None) -> None:
        self.parsed = parsed
        self.citations = citations or []
        self.requests: list[Any] = []

    def configured(self, model: str) -> bool:
        return True

    async def complete(self, request: Any) -> SimpleNamespace:
        self.requests.append(request)
        return SimpleNamespace(
            parsed=self.parsed,
            citations=self.citations,
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


async def test_translator_keeps_only_translations_that_pass_the_checks() -> None:
    parsed = TranslationOutput(
        items=[
            TranslatedItem(
                index=0, headline_en="Siemens raises outlook to 12.5 billion euros", summary_en=None
            ),
            TranslatedItem(index=1, headline_en="Order of 3 units", summary_en=None),  # lost 40
        ]
    )
    llm = FakeLLM(parsed)
    translator = LlmTranslator(llm, "gpt-6-luna")  # type: ignore[arg-type]
    items = [
        TranslateItem(0, "Siemens erhöht Prognose auf 12,5 Milliarden Euro", None, "de"),
        TranslateItem(1, "Auftrag über 40 Einheiten", None, "de"),
    ]
    done, usage = await translator.translate(items)
    assert list(done) == [0]
    assert usage.input_tokens == 100


async def test_translator_reports_an_unparseable_answer() -> None:
    translator = LlmTranslator(FakeLLM(None), "gpt-6-luna")  # type: ignore[arg-type]
    with pytest.raises(TranslationError):
        await translator.translate([source("Hallo Welt")])


# -- embeddings -------------------------------------------------------------------------------


def vec(i: int, values: list[float], hours: float, group: int | None = None) -> Vec:
    return Vec(i, tuple(normalise(values)), NOW.timestamp() + hours * 3600, group)


def test_similar_vectors_group_backwards_in_time_only() -> None:
    first = vec(1, [1, 0, 0], 0)
    second = vec(2, [0.99, 0.05, 0], 1)
    third = vec(3, [0, 1, 0], 2)
    groups = assign_groups([first, second, third], [], 0.9, 36)
    assert groups == {2: 1}


def test_a_chain_of_reports_forms_one_group_and_the_window_limits_it() -> None:
    a, b, c = vec(1, [1, 0], 0), vec(2, [1, 0.01], 20), vec(3, [1, 0.02], 40)
    assert assign_groups([a, b, c], [], 0.9, 36) == {2: 1, 3: 1}
    assert assign_groups([c], [a], 0.9, 36) == {}  # 40h apart
    # An existing member that already has a group passes its group on.
    old = vec(5, [1, 0], 0, group=4)
    assert assign_groups([vec(6, [1, 0], 1)], [old], 0.9, 36) == {6: 4}


def test_cosine_and_token_estimate() -> None:
    assert cosine(normalise([1, 1]), normalise([1, 1])) == pytest.approx(1.0)
    assert estimate_tokens(["a" * 40]) == 10
    assert estimate_tokens(["日本語" * 10]) > 10


async def test_gemini_embedder_normalises_and_checks_the_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["x-goog-api-key"] == "k"
        assert body["requests"][0]["outputDimensionality"] == 3
        return httpx.Response(200, json={"embeddings": [{"values": [3.0, 4.0, 0.0]}] * 2})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        embedder = GeminiEmbedder(http, "k", "gemini-embedding-2", 3)
        out = await embedder.embed(["a", "b"])
    assert out[0] == pytest.approx([0.6, 0.8, 0.0])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"embeddings": []}))
    ) as http:
        with pytest.raises(EmbeddingError):
            await GeminiEmbedder(http, "k", "m", 3).embed(["a"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(429))
    ) as http:
        with pytest.raises(EmbeddingError):
            await GeminiEmbedder(http, "k", "m", 3).embed(["a"])


# -- syndication ------------------------------------------------------------------------------


def test_outlets_republishing_one_wire_count_as_one_source() -> None:
    config = NewsSourcesConfig()
    assert config.independence_key("finance.yahoo.com") == config.independence_key("reuters.com")
    assert config.independence_key("marketwatch.com") == config.independence_key("wsj.com")
    assert config.independence_key("reuters.com") != config.independence_key("wsj.com")
    assert config.independence_key("ft.com") == "ft.com"
    assert config.independence_key(None) is None or isinstance(config.independence_key(None), str)


# -- AI search leads --------------------------------------------------------------------------


def done_with(*citations: Citation) -> Any:
    return SimpleNamespace(citations=list(citations))


def test_a_lead_needs_a_citation_and_takes_the_citation_title() -> None:
    cited = Citation("https://www.ft.com/content/abc?utm_source=x", "Apple suppliers cut output")
    done = done_with(cited, Citation("https://blog.example/untitled", ""))
    leads = [
        Lead(url="https://www.ft.com/content/abc", note="Suppliers cut output."),
        Lead(url="https://www.ft.com/content/abc", note="Duplicate."),
        Lead(url="https://invented.example/story", note="Not cited."),
        Lead(url="https://blog.example/untitled", note="No title."),
    ]
    found = verified_leads("AAPL_US_EQ", leads, done)
    assert [(f.title, f.host) for f in found] == [("Apple suppliers cut output", "ft.com")]


def test_leads_become_tier_free_raw_items_tied_to_the_holding() -> None:
    items = raw_items(
        [FoundLead("AAPL_US_EQ", "https://ft.com/a", "Title", "Note.", "ft.com")], NOW
    )
    assert items[0].source_id == "ai-search"
    assert items[0].tickers == ("AAPL_US_EQ",)
    assert items[0].headline == "Title" and items[0].published_at == NOW


async def test_the_searcher_asks_with_a_search_spec_and_costs_the_call() -> None:
    from steward.ai import Usage

    llm = FakeLLM(
        SearchLeads(leads=[Lead(url="https://ft.com/a", note="n")]),
        [Citation("https://ft.com/a", "Headline")],
    )
    searcher = AiSearcher(llm, "gpt-6-luna", max_searches=2)  # type: ignore[arg-type]
    usage = Usage(model="gpt-6-luna")
    leads = await searcher.search("Apple", "AAPL_US_EQ", NOW.date(), NOW.date(), usage)
    assert [lead.title for lead in leads] == ["Headline"]
    assert llm.requests[0].search.max_uses == 2
    assert usage.input_tokens == 100
