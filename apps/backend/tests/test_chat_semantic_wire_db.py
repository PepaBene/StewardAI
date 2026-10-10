"""Chat `search_wire`: when the words match too few items, items close in meaning are added."""

import datetime as dt
from collections.abc import Sequence

import asyncpg
import pytest

from steward.chat.tools import ChatTools
from steward.research.sources import Registry

NOW = dt.datetime.now(dt.UTC)


class FakeEmbedder:
    model = "fake-embed"
    dimensions = 3

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


async def article(pool: asyncpg.Pool, n: int, headline: str, vector: list[float]) -> int:
    await pool.execute(
        "insert into public.source (id, name, tier, type) values ('s', 's', 2, 'rss')"
        " on conflict do nothing"
    )
    row = await pool.fetchrow(
        "insert into public.news_article (url, source_id, tier, published_at, headline, region,"
        " language) values ($1, 's', 2, $2, $3, 'JP', 'ja') returning id",
        f"https://example.com/{n}",
        NOW - dt.timedelta(hours=n),
        headline,
    )
    assert row is not None
    await pool.execute(
        "insert into public.news_embedding (article_id, model, dim, vector) values ($1, $2, 3, $3)",
        row["id"],
        "fake-embed",
        vector,
    )
    return int(row["id"])


@pytest.mark.db
async def test_search_wire_adds_items_close_in_meaning(backend_pool: asyncpg.Pool) -> None:
    close = await article(backend_pool, 1, "円相場が急落し当局が介入", [0.99, 0.1, 0.0])
    await article(backend_pool, 2, "Local bakery opens", [0.0, 1.0, 0.0])
    tools = ChatTools(backend_pool, Registry(), 0, None, FakeEmbedder())

    result = await tools.call("search_wire", {"query": "yen intervention"})

    ids = [item["headline"] for item in result["items"]]
    assert ids == ["円相場が急落し当局が介入"]
    assert "similar in meaning" in result["items"][0]["match"]
    assert close


@pytest.mark.db
async def test_search_wire_without_an_embedder_uses_words_only(
    backend_pool: asyncpg.Pool,
) -> None:
    await article(backend_pool, 1, "円相場が急落し当局が介入", [0.99, 0.1, 0.0])
    tools = ChatTools(backend_pool, Registry(), 0, None)
    assert (await tools.call("search_wire", {"query": "yen intervention"}))["items"] == []
