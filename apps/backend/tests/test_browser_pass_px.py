"""Browser-pass item 11: PX has no free keyless history API; its closes are stored one per session.

Evidence (live, 2026-10-03): Stooq needs an apikey; Yahoo's `^PX` answers with an unrelated
CBOE series (WCB, USD, price 0.0) and `PX.PR`, `^PX50`, `PX.PS`, `^PXI` are 404; Yahoo search
finds no Prague index; PSE's detail page serves the current value (2616.27, 2026-10-02 16:35)
but loads its history table from /api/, which robots.txt disallows; PSE's anonymous FTP price
list (Results.ak) holds issues, not indices.  So the page is the source and each close is kept.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from steward.config.jobs import JobsConfig
from steward.reference.intraday import refresh_pse_px
from steward.reference.price_series import choose_one, index_symbols, series_mic

FIXTURE = Path(__file__).parent / "fixtures" / "prices" / "pse_px_detail.html"
ROOT = Path(__file__).resolve().parents[3] / "supabase"
UTC = dt.UTC


def _http(stamp: str | None = None) -> httpx.AsyncClient:
    page = FIXTURE.read_text(encoding="utf-8")
    if stamp:
        page = page.replace("25.09.2026 | 16:35:00", stamp)
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=page))
    )


@pytest.mark.db
async def test_a_pse_close_is_stored_as_the_days_bar_and_chosen_for_px(
    backend_pool: asyncpg.Pool,
) -> None:
    after_close = dt.datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
    detail: dict[str, Any] = {}
    async with _http() as http:
        assert await refresh_pse_px(backend_pool, http, after_close, detail)
    bar = await backend_pool.fetchrow(
        "select date, open, high, low, close, currency, mic, bar_type, access_state,"
        " source_timestamp from price_bar where symbol = '^px' and source = 'pse'"
    )
    assert bar is not None
    assert bar["date"] == dt.date(2026, 9, 25) and bar["close"] == Decimal("2721.27")
    assert (bar["open"], bar["high"], bar["low"]) == (
        Decimal("2724.53"), Decimal("2735.72"), Decimal("2720.86"),
    )  # fmt: skip
    assert (bar["currency"], bar["mic"]) == ("CZK", "XPRA")
    assert (bar["bar_type"], bar["access_state"]) == ("adjusted", "available")
    # Readers find it: with no Stooq or Yahoo bar the PX series is the PSE one.
    symbols = index_symbols(JobsConfig().prices, "^px")
    ref = await choose_one(backend_pool, symbols)
    assert ref is not None and (ref.symbol, ref.source, ref.name) == (
        "^px", "pse", "Prague Stock Exchange",
    )  # fmt: skip
    assert series_mic("^px", "pse") == "XPRA"
    # Another index never gets a PSE series.
    assert index_symbols(JobsConfig().prices, "^spx").pse is None


@pytest.mark.db
async def test_an_intraday_value_is_never_stored_as_a_close(backend_pool: asyncpg.Pool) -> None:
    during = dt.datetime(2026, 9, 25, 9, 30, tzinfo=UTC)
    detail: dict[str, Any] = {}
    async with _http("25.09.2026 | 11:30:00") as http:  # the page's stamp: mid-session
        await refresh_pse_px(backend_pool, http, during, detail)
    assert detail["pse_px"]["kind"] == "intraday"
    assert await backend_pool.fetchval("select count(*) from price_bar") == 0


@pytest.mark.db
async def test_the_migration_admits_pse_and_the_rollback_refuses_it_again(
    migrated_db: Any,
) -> None:
    name = "20261007171000_price_bar_pse_source"
    down = (ROOT / "rollback" / f"{name}.down.sql").read_text()
    up = (ROOT / "migrations" / f"{name}.sql").read_text()
    insert = (
        "insert into price_bar (symbol, date, close, source) values ('^px', '2026-09-25', 1, 'pse')"
    )
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        await conn.execute(insert)
        await conn.execute(down)
        assert await conn.fetchval("select count(*) from price_bar where source = 'pse'") == 0
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(insert)
        await conn.execute(up)
        await conn.execute(insert)
    finally:
        await conn.close()
