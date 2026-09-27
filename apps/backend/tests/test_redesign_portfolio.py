"""Data for the UI redesign: OHLCV bars on the holding page, absolute day-snapshot figures
on the history endpoint, every holding's recent closes in one call, the security type on
holdings, the market strip, a one-day contribution window and the transactions subtype
filter. Every figure comes from stored rows; anything unknown is null or absent."""

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import ValidationError

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from steward.config import HoldingProfilesConfig
from steward.config.holding_profiles import HoldingProfile
from steward.config.jobs import JobsConfig, MarketStripItem, PriceSettings
from steward.config.store import save_config
from steward.jobs.runner import JobContext, run_job
from steward.portfolio.quotes import holdings_prices, market_strip
from steward.portfolio.read import contribution_to_return, portfolio_history
from steward.reference.prices import GAPS_KEY, PriceDeps, run_price_history

pytestmark_db = pytest.mark.db

OWNER = "00000000-0000-0000-0000-00000000000b"


def day(n: int) -> dt.date:
    return dt.date(2026, 9, 1) + dt.timedelta(days=n)


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _job(pool: asyncpg.Pool) -> int:
    job_id: int = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values"
        " ('portfolio_sync', 'schedule', 'succeeded') returning id"
    )
    return job_id


async def _account(
    pool: asyncpg.Pool,
    job_id: int,
    snapshot_date: dt.date,
    *,
    invested: Decimal = Decimal(100),
    cash: Decimal = Decimal(0),
    unrealized_ppl: Decimal = Decimal(0),
    fx_rate: Decimal = Decimal(1),
    account_currency: str = "EUR",
    base_currency: str = "EUR",
    fx_source: str = "ecb",
) -> None:
    await pool.execute(
        "insert into public.account_snapshot (snapshot_date, job_id, captured_at,"
        " t212_account_id, account_currency, total_value, cash_available, cash_in_pies,"
        " cash_reserved, invested_value, invested_cost, unrealized_ppl, realized_ppl,"
        " base_currency, fx_source, fx_rate, cash_base, invested_base, total_base,"
        " weight_basis) values ($1, $2, $3, 1, $4, 0, 0, 0, 0, 0, 0, $5, 0, $6, $7, $8, $9,"
        " $10, $9::numeric + $10::numeric, 'total')",
        snapshot_date,
        job_id,
        dt.datetime.combine(snapshot_date, dt.time(12), tzinfo=dt.UTC),
        account_currency,
        unrealized_ppl,
        base_currency,
        fx_source,
        fx_rate,
        cash,
        invested,
    )


async def _position(
    pool: asyncpg.Pool,
    job_id: int,
    snapshot_date: dt.date,
    ticker: str,
    *,
    value_base: Decimal = Decimal(100),
    quantity: Decimal = Decimal(10),
    avg_price: Decimal = Decimal(10),
    currency: str = "USD",
) -> None:
    await pool.execute(
        "insert into public.instrument (t212_ticker, currency) values ($1, $2)"
        " on conflict do nothing",
        ticker,
        currency,
    )
    await pool.execute(
        "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at,"
        " quantity, avg_price, current_price, instrument_currency, value, account_currency,"
        " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
        " values ($1, $2, $3, now(), $4, $5, $5, $6, $4::numeric * $5::numeric, 'EUR', $7,"
        " $7, 0, 'EUR', 'ecb', 1, $7)",
        snapshot_date,
        ticker,
        job_id,
        quantity,
        avg_price,
        currency,
        value_base,
    )


async def _bars(pool: asyncpg.Pool, symbol: str, closes: dict[dt.date, float]) -> None:
    await pool.executemany(
        "insert into public.price_bar (symbol, date, close, source) values ($1, $2, $3, 'stooq')",
        [(symbol, d, Decimal(str(c))) for d, c in closes.items()],
    )


async def _rate(
    pool: asyncpg.Pool, source: str, rate_date: dt.date, currency: str, rate: str
) -> None:
    await pool.execute(
        "insert into public.fx_rate (source, rate_date, currency, home_currency, rate,"
        " published_amount, published_rate) values ($1, $2, $3, $4, $5, 1, $5)",
        source,
        rate_date,
        currency,
        "EUR" if source == "ecb" else "CZK",
        Decimal(rate),
    )


# ---------------------------------------------------------------------------
# 1. OHLCV on the holding page's price history
# ---------------------------------------------------------------------------


@pytestmark_db
async def test_holding_prices_carry_open_high_low_volume_as_stored(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await backend_pool.execute(
        "insert into public.instrument (t212_ticker, name, price_symbol) values"
        " ('AAPL_US_EQ', 'Apple', 'aapl.us')"
    )
    await backend_pool.execute(
        "insert into public.price_bar (symbol, date, open, high, low, close, volume, source)"
        " values ('aapl.us', $1, 10, 12, 9, 11, 1500, 'stooq'),"
        " ('aapl.us', $2, null, null, null, 11.5, null, 'stooq')",
        day(0),
        day(1),
    )
    body = (await api.get("/holdings/AAPL_US_EQ/prices", params={"days": 30})).json()
    assert body["bars"] == [
        {"date": day(0).isoformat(), "close": 11.0, "open": 10.0, "high": 12.0, "low": 9.0,
         "volume": 1500.0},
        {"date": day(1).isoformat(), "close": 11.5, "open": None, "high": None, "low": None,
         "volume": None},
    ]  # fmt: skip


# ---------------------------------------------------------------------------
# 2. Absolute day-snapshot values on the history endpoint
# ---------------------------------------------------------------------------


@pytestmark_db
async def test_history_values_come_from_the_day_snapshots(backend_pool: asyncpg.Pool) -> None:
    job_id = await _job(backend_pool)
    # An older snapshot in another base currency: never mixed into the series.
    await _account(backend_pool, job_id, day(0), invested=Decimal(2000), base_currency="CZK")
    await _account(
        backend_pool, job_id, day(1), invested=Decimal(100), cash=Decimal(20),
        unrealized_ppl=Decimal(10), fx_rate=Decimal(1),
    )  # fmt: skip
    await _account(
        backend_pool, job_id, day(2), invested=Decimal(110), cash=Decimal(20),
        unrealized_ppl=Decimal(8), fx_rate=Decimal("1.5"), account_currency="USD",
    )  # fmt: skip

    result = await portfolio_history(backend_pool, 365)
    assert result.values_currency == "EUR"
    assert [v.model_dump() for v in result.values] == [
        {"date": day(1), "total": 120.0, "invested": 100.0, "cash": 20.0, "unrealised": 10.0},
        # Unrealised P/L is in the account currency: converted at the snapshot's own rate.
        {"date": day(2), "total": 130.0, "invested": 110.0, "cash": 20.0, "unrealised": 12.0},
    ]
    assert len(result.points) == 3  # the index itself is unchanged


@pytestmark_db
async def test_history_values_before_and_with_a_single_snapshot(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    body = (await api.get("/portfolio/history")).json()
    assert body["values"] == [] and body["values_currency"] is None

    job_id = await _job(backend_pool)
    await _account(backend_pool, job_id, day(0), invested=Decimal(50), cash=Decimal(5))
    body = (await api.get("/portfolio/history", params={"days": 30})).json()
    assert body["gap"] == "fewer than two snapshots in this window"
    assert body["points"] == []
    assert body["values"] == [
        {"date": day(0).isoformat(), "total": 55.0, "invested": 50.0, "cash": 5.0,
         "unrealised": 0.0},
    ]  # fmt: skip
    # The index's points stay {date, close}.
    await _account(backend_pool, job_id, day(1), invested=Decimal(60), cash=Decimal(5))
    body = (await api.get("/portfolio/history", params={"days": 30})).json()
    assert set(body["points"][0]) == {"date", "close"}


# ---------------------------------------------------------------------------
# 3. Every holding's recent closes in one call
# ---------------------------------------------------------------------------


@pytestmark_db
async def test_holdings_prices_in_one_call(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    job_id = await _job(backend_pool)
    await _account(backend_pool, job_id, day(40))
    await _position(backend_pool, job_id, day(40), "AAPL_US_EQ", value_base=Decimal(300))
    await _position(backend_pool, job_id, day(40), "SAPd_EQ", value_base=Decimal(200))
    await _position(backend_pool, job_id, day(40), "NONE_EQ", value_base=Decimal(100))
    await _position(backend_pool, job_id, day(40), "NODATA_EQ", value_base=Decimal(50))
    # Resolved from OpenFIGI's symbol, from a holding-profile override, none, and a symbol
    # with nothing stored.
    await backend_pool.execute(
        "update public.instrument set price_symbol = 'aapl.us' where t212_ticker = 'AAPL_US_EQ'"
    )
    await backend_pool.execute(
        "update public.instrument set price_symbol = 'wrong.de' where t212_ticker = 'SAPd_EQ'"
    )
    await backend_pool.execute(
        "update public.instrument set price_symbol = 'zzz.de' where t212_ticker = 'NODATA_EQ'"
    )
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "holding_profiles",
            HoldingProfilesConfig(
                profiles=[HoldingProfile(ticker="SAPd_EQ", price_symbol="sap.de")]
            ),
            created_by=None,
        )
    await _bars(backend_pool, "aapl.us", {day(0): 90, day(30): 100, day(38): 100, day(39): 102})
    # SAP: the previous close lies before the window; the day change still uses it.
    await _bars(backend_pool, "sap.de", {day(10): 50, day(39): 55})
    await _bars(backend_pool, "wrong.de", {day(39): 1})

    result = await holdings_prices(backend_pool, 7)
    assert result.days == 7
    by_ticker = {h.ticker: h for h in result.holdings}
    assert [h.ticker for h in result.holdings] == ["AAPL_US_EQ", "SAPd_EQ", "NONE_EQ", "NODATA_EQ"]

    aapl = by_ticker["AAPL_US_EQ"]
    assert aapl.symbol == "aapl.us" and aapl.as_of == day(39)
    assert [(b.date, b.close) for b in aapl.bars] == [(day(38), 100.0), (day(39), 102.0)]
    assert aapl.last_close == 102.0 and aapl.previous_close == 100.0
    assert aapl.day_change_pct == pytest.approx(0.02)

    sap = by_ticker["SAPd_EQ"]
    assert sap.symbol == "sap.de"
    assert [b.close for b in sap.bars] == [55.0]
    assert sap.previous_close == 50.0 and sap.day_change_pct == pytest.approx(0.1)

    for ticker, symbol in (("NONE_EQ", None), ("NODATA_EQ", "zzz.de")):
        empty = by_ticker[ticker]
        assert empty.symbol == symbol and empty.bars == [] and empty.as_of is None
        assert empty.last_close is None and empty.day_change_pct is None

    body = (await api.get("/portfolio/holdings/prices", params={"days": 30})).json()
    assert body["days"] == 30
    assert [b["close"] for b in body["holdings"][0]["bars"]] == [100.0, 100.0, 102.0]
    assert (await api.get("/portfolio/holdings/prices", params={"days": 6})).status_code == 422
    assert (await api.get("/portfolio/holdings/prices", params={"days": 401})).status_code == 422


@pytestmark_db
async def test_holdings_prices_before_the_first_sync(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/holdings/prices")).json()
    assert body == {"days": 30, "holdings": []}


# ---------------------------------------------------------------------------
# 4. Security type on holdings
# ---------------------------------------------------------------------------


@pytestmark_db
async def test_holdings_carry_the_security_type_when_known(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    job_id = await _job(backend_pool)
    await _account(backend_pool, job_id, day(0))
    await _position(backend_pool, job_id, day(0), "AAPL_US_EQ", value_base=Decimal(60))
    await _position(backend_pool, job_id, day(0), "NEW_US_EQ", value_base=Decimal(40))
    await backend_pool.execute(
        "update public.instrument set name = 'Apple', security_type = 'Common Stock'"
        " where t212_ticker = 'AAPL_US_EQ'"
    )
    body = (await api.get("/portfolio/holdings")).json()
    assert [(h["ticker"], h["name"], h["security_type"]) for h in body["holdings"]] == [
        ("AAPL_US_EQ", "Apple", "Common Stock"),
        ("NEW_US_EQ", None, None),
    ]


# ---------------------------------------------------------------------------
# 5. Market strip
# ---------------------------------------------------------------------------


def test_market_strip_config_defaults_and_validation() -> None:
    assert [(i.symbol, i.label) for i in JobsConfig().prices.market_strip] == [
        ("^spx", "S&P 500"),
        ("^stoxx", "STOXX Europe 600"),
        ("^px", "PX"),
    ]
    assert PriceSettings(market_strip=[]).market_strip == []
    with pytest.raises(ValidationError, match="duplicate market strip symbols"):
        PriceSettings(
            market_strip=[
                MarketStripItem(symbol="^spx", label="A"),
                MarketStripItem(symbol="^spx", label="B"),
            ]
        )
    with pytest.raises(ValidationError):
        PriceSettings.model_validate({"market_strip": [{"symbol": "S&P", "label": "x"}]})


@pytestmark_db
async def test_market_strip_before_the_first_sync(api: httpx.AsyncClient) -> None:
    body = (await api.get("/market/strip")).json()
    assert body["fx"] == []
    assert [i["symbol"] for i in body["indices"]] == ["^spx", "^stoxx", "^px"]
    assert all(
        i["last_close"] is None and i["as_of"] is None and i["change_pct"] is None
        for i in body["indices"]
    )


@pytestmark_db
async def test_market_strip_rates_and_index_quotes(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    job_id = await _job(backend_pool)
    await _account(backend_pool, job_id, day(5))
    await _position(backend_pool, job_id, day(5), "AAPL_US_EQ", currency="USD")
    await _position(backend_pool, job_id, day(5), "VOD_EQ", currency="GBX")
    await _position(backend_pool, job_id, day(5), "SAPd_EQ", currency="EUR")
    await _rate(backend_pool, "ecb", day(1), "USD", "0.90")
    await _rate(backend_pool, "ecb", day(2), "USD", "0.92")
    await _rate(backend_pool, "ecb", day(2), "GBP", "1.17")
    await _bars(backend_pool, "^spx", {day(3): 5000, day(4): 5100})
    await _bars(backend_pool, "^px", {day(4): 2000})

    body = (await api.get("/market/strip")).json()
    assert body["base_currency"] == "EUR"
    gbp, usd = body["fx"]  # GBX held: quoted as GBP; EUR is the base and not listed
    assert usd["currency"] == "USD" and usd["base_currency"] == "EUR"
    assert usd["rate"] == pytest.approx(0.92) and usd["rate_date"] == day(2).isoformat()
    assert usd["previous_rate"] == pytest.approx(0.90)
    assert usd["previous_rate_date"] == day(1).isoformat()
    assert usd["change_pct"] == pytest.approx(0.92 / 0.90 - 1)
    assert gbp["currency"] == "GBP" and gbp["rate"] == pytest.approx(1.17)
    assert gbp["previous_rate"] is None and gbp["change_pct"] is None

    spx, stoxx, px = body["indices"]
    assert spx == {
        "symbol": "^spx", "label": "S&P 500", "last_close": 5100.0,
        "as_of": day(4).isoformat(), "previous_close": 5000.0,
        "previous_date": day(3).isoformat(), "change_pct": pytest.approx(0.02),
    }  # fmt: skip
    assert stoxx["last_close"] is None and stoxx["change_pct"] is None
    assert px["last_close"] == 2000.0 and px["previous_close"] is None
    assert px["change_pct"] is None


@pytestmark_db
async def test_market_strip_cross_rates_through_the_home_currency(
    backend_pool: asyncpg.Pool,
) -> None:
    # Base USD on ECB rates (home EUR): EUR/USD is 1 / rate(USD); CZK/USD is
    # rate(CZK) / rate(USD) on dates when both were published.
    job_id = await _job(backend_pool)
    await _account(backend_pool, job_id, day(5), base_currency="USD", account_currency="EUR")
    await _position(backend_pool, job_id, day(5), "CEZ_EQ", currency="CZK")
    await _rate(backend_pool, "ecb", day(1), "USD", "0.90")
    await _rate(backend_pool, "ecb", day(1), "CZK", "0.040")
    await _rate(backend_pool, "ecb", day(2), "USD", "0.92")
    await _rate(backend_pool, "ecb", day(2), "CZK", "0.041")
    await _rate(backend_pool, "ecb", day(3), "CZK", "0.042")  # no USD rate that day
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "jobs",
            JobsConfig(prices=PriceSettings(market_strip=[])),
            created_by=None,
        )

    strip = await market_strip(backend_pool)
    assert strip.base_currency == "USD" and strip.indices == []
    czk, eur = strip.fx
    assert eur.currency == "EUR" and eur.rate == pytest.approx(1 / 0.92)
    assert eur.previous_rate == pytest.approx(1 / 0.90)
    assert czk.rate == pytest.approx(0.041 / 0.92) and czk.rate_date == day(2)
    assert czk.previous_rate == pytest.approx(0.040 / 0.90)


@pytestmark_db
async def test_price_history_collects_the_market_strip_symbols(
    backend_pool: asyncpg.Pool,
) -> None:
    csv = "Date,Open,High,Low,Close,Volume\n2026-09-24,1,1,1,2000,5\n2026-09-25,1,1,1,2010,5"
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "stooq.com":
            symbol = request.url.params.get("s", "")
            requested.append(symbol)
            return httpx.Response(200, text=csv if symbol in ("^spx", "^px") else "No data")
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

        async def job(ctx: JobContext) -> None:
            await run_price_history(ctx, PriceDeps(http=http, today=dt.date(2026, 9, 25)))

        assert await run_job(backend_pool, "price_history", job, trigger="manual") == "succeeded"

    assert sorted(requested) == ["^px", "^spx", "^stoxx"]  # ^spx once: benchmark and strip
    stored = await backend_pool.fetchval("select count(*) from price_bar where symbol = '^px'")
    assert stored == 2
    gaps = await backend_pool.fetchval("select value from sync_state where key = $1", GAPS_KEY)
    assert "^stoxx" in gaps

    strip = await market_strip(backend_pool)
    px = next(i for i in strip.indices if i.symbol == "^px")
    assert px.last_close == 2010.0 and px.change_pct == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# 6. One-day contribution
# ---------------------------------------------------------------------------


@pytestmark_db
async def test_one_day_contribution_uses_the_last_two_snapshots(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    job_id = await _job(backend_pool)
    for n, value in ((0, 100), (1, 150), (2, 140)):
        await _account(backend_pool, job_id, day(n), invested=Decimal(value))
        await _position(backend_pool, job_id, day(n), "AAPL_US_EQ", value_base=Decimal(value))

    result = await contribution_to_return(backend_pool, 1)
    assert result.period_start == day(1) and result.as_of == day(2)
    [holding] = result.holdings
    assert holding.contribution_base == pytest.approx(-10.0)
    assert holding.contribution_pct == pytest.approx(-10 / 150)

    body = (await api.get("/portfolio/contribution", params={"days": 1})).json()
    assert body["gap"] is None and body["holdings"][0]["contribution_base"] == pytest.approx(-10)
    assert (await api.get("/portfolio/contribution", params={"days": 0})).status_code == 422


# ---------------------------------------------------------------------------
# 7. Transactions subtype filter
# ---------------------------------------------------------------------------


async def _seed_transactions(pool: asyncpg.Pool) -> None:
    base = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    rows: list[tuple[Any, ...]] = []
    for i in range(30):
        kind, subtype = [("order", "BUY"), ("order", "SELL"), ("cash", "DEPOSIT")][i % 3]
        rows.append(
            (
                f"{kind}:{i}",
                kind,
                subtype,
                None if kind == "cash" else "AAPL_US_EQ",
                Decimal(i + 1),
                "EUR",
                base + dt.timedelta(days=i // 2),
            )
        )
    await pool.executemany(
        "insert into public.transaction (id, type, subtype, ticker, amount, currency,"
        " occurred_at, raw) values ($1, $2, $3, $4, $5, $6, $7, '{}')",
        rows,
    )


@pytestmark_db
async def test_transactions_subtype_filter_keeps_counts_totals_and_paging_consistent(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await _seed_transactions(backend_pool)
    expected = await backend_pool.fetchrow(
        "select count(*) as n, sum(amount) as s from transaction where subtype = 'SELL'"
    )
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"type": "order", "subtype": "SELL", "limit": 3}
        if cursor:
            params["cursor"] = cursor
        page = (await api.get("/portfolio/transactions", params=params)).json()
        assert page["total_count"] == expected["n"] == 10
        assert [(t["subtype"], t["count"]) for t in page["totals"]] == [("SELL", 10)]
        assert page["totals"][0]["amount"] == float(expected["s"])
        assert {t["subtype"] for t in page["items"]} == {"SELL"}
        seen += [t["id"] for t in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 10

    both = (
        await api.get(
            "/portfolio/transactions", params=[("subtype", "BUY"), ("subtype", "DEPOSIT")]
        )
    ).json()
    assert both["total_count"] == 20
    assert {(t["type"], t["subtype"]) for t in both["totals"]} == {
        ("order", "BUY"),
        ("cash", "DEPOSIT"),
    }
    everything = (await api.get("/portfolio/transactions")).json()
    assert everything["total_count"] == 30
    bad = await api.get("/portfolio/transactions", params={"subtype": "BUY;drop"})
    assert bad.status_code == 422
