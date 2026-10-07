"""Storing the issuer's benchmark series against the migrated schema."""

# ruff: noqa: E501

import datetime as dt
from decimal import Decimal

from steward.fundresearch import issuer_sync as sync

TODAY = dt.date(2026, 10, 7)


def reading(levels, name="NASDAQ 100 Index", basis=None):
    series = sync.BenchmarkSeries(
        symbol="ishares-bm-253741",
        name=name,
        currency="USD",
        basis=basis,
        points=tuple(
            (dt.date(2026, 10, 1) + dt.timedelta(days=i), Decimal(v)) for i, v in enumerate(levels)
        ),
    )
    r = sync.IssuerReading(
        isin="IE00B53SZB19", source_url="https://www.ishares.com/uk/p/253741", as_of=TODAY
    )
    r.benchmark = series
    return r


async def test_series_is_stored_idempotently_and_only_changed_days_are_rewritten(backend_pool):
    async with backend_pool.acquire() as conn:
        assert await sync.store_benchmark(conn, reading(["10000", "10100", "10200"])) == 3
        assert await sync.store_benchmark(conn, reading(["10000", "10100", "10200"])) == 0
        assert await sync.store_benchmark(conn, reading(["10000", "10100", "10250", "10300"])) == 2
    rows = await backend_pool.fetch(
        "select date, close, source, currency from public.price_bar where symbol='ishares-bm-253741' order by date"
    )
    assert [(r["close"], r["source"], r["currency"]) for r in rows] == [
        (Decimal("10000"), "issuer", "USD"),
        (Decimal("10100"), "issuer", "USD"),
        (Decimal("10250"), "issuer", "USD"),
        (Decimal("10300"), "issuer", "USD"),
    ]


async def test_coverage_row_says_total_return_only_when_the_name_does(backend_pool):
    async with backend_pool.acquire() as conn:
        await sync.store_benchmark(conn, reading(["10000", "10100"]))
        first = await conn.fetchval(
            "select total_return_available from public.fund_index_coverage where subject_key='ishares-bm-253741'"
        )
        await sync.store_benchmark(
            conn, reading(["10000", "10100"], name="X Total Return", basis="total")
        )
        second = await conn.fetchval(
            "select total_return_available from public.fund_index_coverage where subject_key='ishares-bm-253741'"
        )
    assert first is None and second is True
