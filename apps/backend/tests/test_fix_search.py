"""Chat search_news: full-text search over news items (stemming, several terms, ranking)."""

import datetime as dt

import asyncpg

from steward.chat.tools import ChatTools
from steward.research.sources import Registry

NOW = dt.datetime.now(dt.UTC)

ITEMS = [
    # (ticker, days ago, headline, summary)
    ("SAP_DE_EQ", 1, "SAP raises cloud guidance", "SAP lifted its cloud revenue outlook."),
    ("NOVO_DK_EQ", 2, "Novo Nordisk cuts Wegovy price", "The drugmaker cut list prices in the US."),
    ("NOVO_DK_EQ", 3, "Central bank signals rate cuts", "Markets price two cuts this year."),
    ("SAP_DE_EQ", 4, "Analysts expect the ECB to cut rates", None),
    ("SAP_DE_EQ", 5, "Quarterly dividend declared", "A routine note that mentions a rate once."),
    ("NOVO_DK_EQ", 6, "IT spending slows", "Corporate budgets for software tighten."),
    ("SAP_DE_EQ", 400, "SAP cuts jobs", "Too old for the default window."),
]


async def seed(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "insert into source (id, name, tier, type) values ('wire', 'Wire', 2, 'rss')"
        " on conflict do nothing"
    )
    for n, (ticker, days, headline, summary) in enumerate(ITEMS):
        await pool.execute(
            "insert into news_item (url, source_id, outlet, tier, published_at, headline,"
            " summary, tickers) values ($1, 'wire', 'wire.test', 2, $2, $3, $4, $5)",
            f"https://wire.test/{n}",
            NOW - dt.timedelta(days=days),
            headline,
            summary,
            [ticker],
        )


async def search(pool: asyncpg.Pool, **args: object) -> list[dict[str, object]]:
    tools = ChatTools(pool, Registry(), conversation_id=0, message_id=None)
    result = await tools.call("search_news", args)
    assert "error" not in result, result
    items: list[dict[str, object]] = result["items"]
    return items


def headlines(items: list[dict[str, object]]) -> list[object]:
    return [i["headline"] for i in items]


async def test_word_forms_match(backend_pool: asyncpg.Pool) -> None:
    await seed(backend_pool)
    # "cuts" finds "cut" and "cuts"; "rates" finds "rate"; both words must appear.
    expected = {"Central bank signals rate cuts", "Analysts expect the ECB to cut rates"}
    assert set(headlines(await search(backend_pool, query="rate cuts"))) == expected
    # Word order and form do not matter.
    assert set(headlines(await search(backend_pool, query="cutting rate"))) == expected
    # Names and tickers are kept as words.
    assert headlines(await search(backend_pool, query="Novo Nordisk")) == [
        "Novo Nordisk cuts Wegovy price"
    ]
    assert headlines(await search(backend_pool, query="sap")) == ["SAP raises cloud guidance"]


async def test_phrase_or_and_exclusion(backend_pool: asyncpg.Pool) -> None:
    await seed(backend_pool)
    found = headlines(await search(backend_pool, query='"Novo Nordisk" OR SAP'))
    assert set(found) == {"SAP raises cloud guidance", "Novo Nordisk cuts Wegovy price"}
    # A quoted phrase needs the words adjacent.
    assert headlines(await search(backend_pool, query='"Nordisk Novo"')) == []
    assert headlines(await search(backend_pool, query="cuts -wegovy -rates")) == []
    assert headlines(await search(backend_pool, query="cuts -wegovy -bank")) == [
        "Analysts expect the ECB to cut rates"
    ]


async def test_ranking_prefers_headline_matches_and_carries_an_excerpt(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed(backend_pool)
    items = await search(backend_pool, query="rate")
    # A headline match (weight A) outranks the newer item that only mentions it in passing.
    assert headlines(items)[:2] == [
        "Central bank signals rate cuts",
        "Analysts expect the ECB to cut rates",
    ]
    assert headlines(items)[-1] == "Quarterly dividend declared"
    assert "**rate**" in str(items[-1]["excerpt"])
    # Without a query: newest first and no excerpt, as before.
    plain = await search(backend_pool)
    assert headlines(plain)[0] == "SAP raises cloud guidance"
    assert all("excerpt" not in i for i in plain)


async def test_stop_word_query_falls_back_to_substring(backend_pool: asyncpg.Pool) -> None:
    await seed(backend_pool)
    # "IT" is an English stop word: the tsquery is empty, so the old substring match runs
    # (case-insensitive, newest first; "its" matches too).
    items = await search(backend_pool, query="IT")
    assert headlines(items) == ["SAP raises cloud guidance", "IT spending slows"]
    assert all("excerpt" not in i for i in items)


async def test_ticker_and_days_filters(backend_pool: asyncpg.Pool) -> None:
    await seed(backend_pool)
    assert headlines(await search(backend_pool, query="cuts", ticker="SAP_DE_EQ")) == [
        "Analysts expect the ECB to cut rates"
    ]
    # The window still applies: only the 2-day-old item is within 3 days.
    assert headlines(await search(backend_pool, query="cuts", days=3)) == [
        "Novo Nordisk cuts Wegovy price"
    ]
    # The 400-day-old item is past the 365-day maximum.
    assert "SAP cuts jobs" not in headlines(await search(backend_pool, query="SAP", days=365))
