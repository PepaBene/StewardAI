"""Browser-pass item 2: the Finnhub cross-check stores eps_ttm as a bare number."""

import asyncpg
import pytest

from steward.valuation.collect import store_fundamentals


@pytest.mark.db
async def test_finnhub_bare_number_eps_is_stored_without_currency(
    backend_pool: asyncpg.Pool,
) -> None:
    body = {"source": "finnhub", "basis": None, "pe": 30.1, "pb": 45.0, "eps_ttm": 6.5}
    await store_fundamentals(backend_pool, "AAPL", "finnhub", body, {"metric": {}}, "AAPL", "u")
    row = await backend_pool.fetchrow(
        "select currency, period_end, figures from fundamentals where ticker='AAPL'"
    )
    # A bare number carries neither currency nor period: stored as unknown, never invented.
    assert row is not None and row["currency"] is None and row["period_end"] is None
