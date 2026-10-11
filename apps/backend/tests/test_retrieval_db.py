"""Indexing, embedding and hybrid search over a migrated database, with a fake embedder.

The fake embedder gives synonyms the same direction, so "automobile" lands next to "vehicle"
with no word in common: the case keyword search cannot serve and the vector side must. The
same tests run on a server with pgvector (the index scan) and without it (the Python scan).
"""

import datetime as dt
import math
import re
from collections.abc import Sequence

import asyncpg
import pytest

from steward.chat.tools import ChatTools
from steward.research.sources import Registry
from steward.retrieval import index, search
from steward.retrieval.index import SourceDoc

DIM = 768
SYNONYMS = {
    "automobile": "vehicle",
    "car": "vehicle",
    "vehicles": "vehicle",
    "vendor": "supplier",
    "vendors": "supplier",
    "suppliers": "supplier",
    "profit": "margin",
    "margins": "margin",
}


class FakeEmbedder:
    model = "fake-embed"
    dimensions = DIM

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        out = []
        for text in texts:
            vec = [0.0] * DIM
            for word in re.findall(r"[a-z]+", text.lower()):
                word = SYNONYMS.get(word, word)
                vec[sum(ord(c) * (i + 7) for i, c in enumerate(word)) % DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


FILING = SourceDoc(
    kind="filing",
    ref="a" * 64,
    title="Acme annual report",
    text=(
        "ITEM 1. BUSINESS\nAcme builds electric vehicles and sells them to fleets.\n\n"
        "ITEM 1A. RISK FACTORS\nWe depend on one supplier for battery cells. If that supplier "
        "stops delivery our production halts.\n\nITEM 7. RESULTS\nSegment | 2025 | 2024\n"
        "Vehicles | 900 | 700\nEnergy | 100 | 90\n"
    ),
    tickers=("ACME_US_EQ",),
    as_of=dt.date(2026, 2, 1),
    source_url="https://example.com/acme-10k",
    tier=1,
)
REPORT = SourceDoc(
    kind="report",
    ref="7",
    title="Acme research",
    text="Pricing power is weak. Margin pressure comes from battery costs.",
    tickers=("ACME_US_EQ",),
    as_of=dt.date(2026, 6, 1),
    app_path="/research/7",
    tier=1,
)
NEWS = SourceDoc(
    kind="news",
    ref="31",
    title="Globex recalls cars",
    text="Globex recalled 40,000 cars over a brake defect.",
    tickers=("GLBX_US_EQ",),
    as_of=dt.date(2026, 9, 1),
    source_url="https://example.com/globex",
    tier=2,
    structured=False,
)


async def count(pool: asyncpg.Pool, where: str = "true") -> int:
    sql = f"select count(*) from public.document_chunk where {where}"  # noqa: S608
    return int(await pool.fetchval(sql))


# Short enough that a one-word query can be close to it in the fake embedding space.
NOTE = SourceDoc(
    kind="note",
    ref="1",
    title="Vehicles",
    text="Vehicles.",
    tickers=("ACME_US_EQ",),
    as_of=dt.date(2026, 7, 1),
    app_path="/companies/ACME_US_EQ",
    tier=1,
    structured=False,
)


@pytest.fixture
async def indexed(backend_pool: asyncpg.Pool) -> asyncpg.Pool:
    await index.index_documents(backend_pool, [FILING, REPORT, NEWS, NOTE])
    await index.embed_pending(backend_pool, FakeEmbedder(), 1000)
    return backend_pool


@pytest.mark.db
async def test_unchanged_documents_are_skipped_and_changed_ones_replace_their_chunks(
    backend_pool: asyncpg.Pool,
) -> None:
    first = await index.index_documents(backend_pool, [FILING, NEWS])
    assert first.documents == 2 and first.skipped == 0 and first.children >= 4
    before = await count(backend_pool)
    again = await index.index_documents(backend_pool, [FILING, NEWS])
    assert again.documents == 0 and again.skipped == 2 and await count(backend_pool) == before
    edited = SourceDoc(**{**FILING.__dict__, "text": "ITEM 1. BUSINESS\nAcme now makes boats."})
    await index.index_documents(backend_pool, [edited])
    versions = await backend_pool.fetchval(
        "select count(distinct version) from public.document_chunk where kind='filing'"
    )
    assert versions == 1  # the old version is gone, not kept beside the new one
    assert not await backend_pool.fetchval(
        "select exists (select 1 from public.document_chunk where text like '%battery%')"
    )


@pytest.mark.db
async def test_prune_removes_records_that_left_the_source(backend_pool: asyncpg.Pool) -> None:
    await index.index_documents(
        backend_pool, [REPORT, SourceDoc(**{**REPORT.__dict__, "ref": "8"})]
    )
    assert await index.prune(backend_pool, "report", ["7"]) > 0
    assert (
        await count(backend_pool, "ref = '8'") == 0 and await count(backend_pool, "ref = '7'") > 0
    )


@pytest.mark.db
async def test_only_missing_or_foreign_vectors_are_embedded(backend_pool: asyncpg.Pool) -> None:
    await index.index_documents(backend_pool, [FILING, REPORT])
    embedder = FakeEmbedder()
    total = await count(backend_pool, "not is_parent")
    done = await index.embed_pending(backend_pool, embedder, 1000)
    assert done.embedded == total and done.tokens_estimated > 0
    assert (await index.embed_pending(backend_pool, embedder, 1000)).embedded == 0
    # A model swap makes every passage pending again.
    swapped = FakeEmbedder()
    swapped.model = "other-model"
    assert (await index.embed_pending(backend_pool, swapped, 1000)).embedded == total
    # The cap is honoured, newest document first.
    await backend_pool.execute(
        "update public.document_chunk set embedding = null,"
        " embedding_model = null, embedding_dim = null"
    )
    assert (await index.embed_pending(backend_pool, embedder, 2)).embedded == 2


@pytest.mark.db
async def test_keyword_search_finds_the_passage_with_its_section(indexed: asyncpg.Pool) -> None:
    result = await search.search(indexed, None, "battery cells supplier", mode="keyword")
    top = result.hits[0]
    assert top.kind == "filing" and "RISK FACTORS" in (top.section_path or "").upper()
    assert top.context and "ITEM 1A" not in top.text  # the parent is the whole section
    assert top.keyword_rank == 1 and top.vector_rank is None


@pytest.mark.db
async def test_meaning_finds_what_keywords_cannot(indexed: asyncpg.Pool) -> None:
    keyword = await search.search(indexed, None, "automobile", mode="keyword")
    assert keyword.hits == []
    hybrid = await search.search(indexed, FakeEmbedder(), "automobile")
    assert hybrid.vector_backend in ("pgvector", "python")
    # "Vehicles." shares no word with "automobile"; only the embedding puts them together.
    assert [h.kind for h in hybrid.hits] == ["note"]
    assert all(h.vector_rank is not None and h.keyword_rank is None for h in hybrid.hits)


@pytest.mark.db
async def test_the_backend_matches_the_server(indexed: asyncpg.Pool) -> None:
    has_extension = await indexed.fetchval(
        "select exists (select 1 from pg_extension where extname = 'vector')"
    )
    result = await search.search(indexed, FakeEmbedder(), "vehicles", mode="vector")
    assert result.vector_backend == ("pgvector" if has_extension else "python")
    assert result.hits and result.hits[0].cosine is not None and result.hits[0].cosine > 0.5


@pytest.mark.db
async def test_a_passage_both_sides_find_outranks_one_side_only(indexed: asyncpg.Pool) -> None:
    # The short note matches "vehicles" by word and by meaning; the filing only by word.
    result = await search.search(indexed, FakeEmbedder(), "vehicles")
    top = result.hits[0]
    assert top.kind == "note"
    assert top.keyword_rank is not None and top.vector_rank is not None
    assert any(h.kind == "filing" and h.vector_rank is None for h in result.hits[1:])


@pytest.mark.db
async def test_filters_narrow_by_ticker_kind_and_date(indexed: asyncpg.Pool) -> None:
    embedder = FakeEmbedder()
    only_news = await search.search(
        indexed, embedder, "recalled", filters=search.Filters(kinds=("news",))
    )
    assert {h.kind for h in only_news.hits} == {"news"}
    acme = await search.search(
        indexed, embedder, "margin", filters=search.Filters(tickers=("ACME_US_EQ",))
    )
    assert acme.hits and all("ACME_US_EQ" in h.tickers for h in acme.hits)
    late = await search.search(
        indexed, embedder, "margin vehicles", filters=search.Filters(since=dt.date(2026, 5, 1))
    )
    assert late.hits and all(h.as_of and h.as_of >= dt.date(2026, 5, 1) for h in late.hits)


@pytest.mark.db
async def test_an_embedding_failure_falls_back_to_keywords_and_says_so(
    indexed: asyncpg.Pool,
) -> None:
    class Broken(FakeEmbedder):
        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise RuntimeError("down")

    result = await search.search(indexed, Broken(), "supplier")
    assert result.hits and any("keyword matches only" in n for n in result.notes)


@pytest.mark.db
async def test_one_document_shows_at_most_two_passages(backend_pool: asyncpg.Pool) -> None:
    text = "\n\n".join(f"Paragraph {i} discusses margins in detail. " * 12 for i in range(8))
    await index.index_documents(backend_pool, [SourceDoc(**{**REPORT.__dict__, "text": text})])
    result = await search.search(backend_pool, None, "margins", mode="keyword", limit=10)
    assert len(result.hits) == search.PER_DOCUMENT


@pytest.mark.db
async def test_the_chat_tool_returns_citable_passages(indexed: asyncpg.Pool) -> None:
    registry = Registry()
    tools = ChatTools(indexed, registry, None, None, doc_embedder=FakeEmbedder())
    out = await tools.call("search_documents", {"query": "supplier battery cells", "limit": 3})
    assert out["passages"] and out["tool"] == "search_documents"
    first = out["passages"][0]
    source = registry.get(first["source_id"])
    assert source is not None and source.snippet == first["passage"][:1500]
    assert source.url == "https://example.com/acme-10k" and source.tier == 1
    assert "section_context" in first  # the top hits carry their section
    bad = await tools.call("search_documents", {"query": "x", "kinds": ["tweets"]})
    assert "error" in bad
    none = await tools.call("search_documents", {"query": "zzzzqqqq"})
    assert none["passages"] == [] and "not be indexed" in none["note"]
