"""The data tools against a migrated database: every query must plan (right tables and
columns) and the envelope must tell an empty table from a value."""

import datetime as dt

import asyncpg
import pytest

from steward.chat.tools import ChatTools
from steward.research.sources import Registry

TICKER = "ACME_US_EQ"
ISIN = "US0000000001"


def tools(pool: asyncpg.Pool) -> ChatTools:
    return ChatTools(pool, Registry(), 0, None)


async def seed_instrument(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "insert into public.instrument (t212_ticker, isin, name, currency, price_symbol)"
        " values ($1, $2, 'Acme', 'USD', 'acme.us')",
        TICKER,
        ISIN,
    )


EMPTY_CALLS: list[tuple[str, dict[str, object]]] = [
    ("fx_rates", {"currency": "USD"}),
    ("latest_quote", {"ticker": TICKER}),
    ("listing_liquidity", {"ticker": TICKER}),
    ("statement_lines", {"ticker": TICKER}),
    ("statement_lines", {"ticker": TICKER, "statement": "income", "concept": "Revenue"}),
    ("share_counts", {"ticker": TICKER}),
    ("segment_revenue", {"ticker": TICKER}),
    ("corporate_actions", {"ticker": TICKER}),
    ("peers", {"ticker": TICKER}),
    ("etf_breakdown", {"fund_isin": ISIN}),
    ("coverage_review", {}),
    ("fundamentals", {"ticker": TICKER, "periods": 4}),
    ("price_history", {"ticker": TICKER, "benchmark": TICKER}),
]


@pytest.mark.db
async def test_every_new_query_plans_and_empty_data_is_missing_not_an_error(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_instrument(backend_pool)
    t = tools(backend_pool)
    for name, args in EMPTY_CALLS:
        out = await t.call(name, args)
        assert out.get("tool") == name, (name, out)
        assert "error" not in out or "owner" in str(out["error"]), (name, out)


@pytest.mark.db
async def test_stored_rows_come_back_as_cited_figures(backend_pool: asyncpg.Pool) -> None:
    await seed_instrument(backend_pool)
    today = dt.datetime.now(dt.UTC).date()
    await backend_pool.execute(
        "insert into public.fx_rate (source, rate_date, currency, home_currency, rate,"
        " published_amount, published_rate) values ('ecb', $1, 'USD', 'EUR', 1.1, 1, 1.1)",
        today,
    )
    t = tools(backend_pool)
    fx = await t.call("fx_rates", {"currency": "usd"})
    assert fx["latest"]["value"] == pytest.approx(1.1)
    assert fx["latest"]["status"] == "ok"
    assert t.registry.get(fx["source_id"]) is not None
