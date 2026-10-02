"""Prices from more than one source: the Yahoo Finance adapter (recorded chart responses),
the Stooq -> Yahoo daily fallback and how every reader picks one series, the intraday job
and its reads, the PX close from the Prague Stock Exchange's page, and the new Stooq
markets (Amsterdam, Copenhagen, Prague). No live network: every response is a fixture."""

import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from api.market_routes import yahoo_http
from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import CalendarSettings, ExchangeClosure, JobsConfig, PriceSettings
from steward.config.store import save_config
from steward.jobs.runner import JobContext, run_job
from steward.portfolio.quotes import holdings_prices, market_strip
from steward.reference import intraday
from steward.reference.intraday import (
    IntradayDeps,
    holding_intraday,
    market_open,
    portfolio_intraday,
    quote_kind,
    run_intraday,
)
from steward.reference.price_series import (
    PriceSymbols,
    SeriesRef,
    pick,
    yahoo_from_listing,
    yahoo_from_stooq,
    yahoo_mic,
)
from steward.reference.prices import SOURCES_KEY, PriceDeps, run_price_history
from steward.reference.read import price_history
from steward.sources import openfigi, yahoo
from steward.sources.market import stooq_currency
from steward.sources.pse import PX_ISIN, parse_detail

FIXTURES = Path(__file__).parent / "fixtures" / "prices"
OWNER = "00000000-0000-0000-0000-00000000000b"
UTC = dt.UTC

# The recorded 5-minute VWCE.DE session: Monday 28 Sep 2026, fetched 08:46 UTC.
VWCE_NOW = dt.datetime(2026, 9, 28, 9, 0, tzinfo=UTC)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def chart(name: str, symbol: str) -> yahoo.YahooChart:
    return yahoo.parse_chart(symbol, json.loads(fixture(name)), raw=fixture(name))


# ---------------------------------------------------------------------------
# The Yahoo adapter
# ---------------------------------------------------------------------------


def test_yahoo_intraday_chart_meta_bars_and_rounding() -> None:
    c = chart("yahoo_vwce_de_5m.json", "VWCE.DE")
    assert c.meta.symbol == "VWCE.DE" and c.meta.currency == "EUR"
    assert c.meta.exchange_tz == "Europe/Berlin" and c.meta.exchange_name == "XETRA"
    assert c.meta.price == Decimal("169.64")
    assert c.meta.price_time == dt.datetime(2026, 9, 28, 8, 46, 12, tzinfo=UTC)
    assert c.meta.previous_close == Decimal("169.74")
    assert c.meta.session_start == dt.datetime(2026, 9, 28, 7, 0, tzinfo=UTC)
    assert c.meta.session_end == dt.datetime(2026, 9, 28, 15, 30, tzinfo=UTC)
    assert c.granularity == "5m"
    # 32-bit float noise (169.75999450683594) is rounded to Yahoo's priceHint (2).
    assert c.bars[0].close == Decimal("169.76")
    assert c.bars[0].ts == dt.datetime(2026, 9, 28, 7, 5, tzinfo=UTC)
    # The trailing live tick (08:46:12) is a quote, not a 5-minute bar.
    assert c.bars[-1].ts.second == 12
    aligned = intraday._aligned(c.bars)
    assert len(aligned) == len(c.bars) - 1 and aligned[-1].ts.minute % 5 == 0
    assert c.body == fixture("yahoo_vwce_de_5m.json")  # the original payload, verbatim


def test_yahoo_unknown_symbol_is_no_data() -> None:
    with pytest.raises(yahoo.YahooNoData):
        yahoo.parse_chart("^SXXP", json.loads(fixture("yahoo_not_found.json")))


def test_yahoo_daily_bars_are_exchange_days_and_skip_a_session_in_progress() -> None:
    c = chart("yahoo_cez_pr_1d.json", "CEZ.PR")
    after = yahoo.daily_bars(c, dt.datetime(2026, 9, 28, 9, 0, tzinfo=UTC))
    assert [b.date for b in after] == [dt.date(2026, 9, d) for d in (21, 22, 23, 24, 25)]
    assert after[-1].close == Decimal("1349.00") and after[-1].volume == Decimal(69503)
    # Friday during its session: Friday's bar is not a close yet.
    during = yahoo.daily_bars(c, dt.datetime(2026, 9, 25, 10, 0, tzinfo=UTC))
    assert during[-1].date == dt.date(2026, 9, 24)
    assert yahoo.daily_range(735) == "2y" and yahoo.daily_range(20) == "1mo"


async def test_yahoo_waits_out_a_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(yahoo, "_sleep", fake_sleep)
    answers = [httpx.Response(429, text="Too Many Requests")]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["interval"] == "5m"
        if answers:
            return answers.pop()
        return httpx.Response(200, text=fixture("yahoo_vwce_de_5m.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = await yahoo.yahoo_intraday(http, "VWCE.DE")
        assert c.meta.price == Decimal("169.64") and waits == [yahoo.RETRY_WAITS[0]]

        # A page waiting on the answer does not wait: the 429 is reported at once.
        answers.append(httpx.Response(429, text="Too Many Requests"))
        with pytest.raises(yahoo.YahooError, match="429"):
            await yahoo.yahoo_intraday(http, "VWCE.DE", retry=False)


# ---------------------------------------------------------------------------
# Symbols, sessions, the PSE page
# ---------------------------------------------------------------------------


def test_yahoo_symbols_derive_from_the_listing() -> None:
    derived = {
        s: yahoo_from_stooq(s)
        for s in ("vwce.de", "cspx.uk", "asml.nl", "novo-b.dk", "sap.de", "cez.cz", "komb.cz",
                  "msft.us", "brk-b.us", "^spx")
    }  # fmt: skip
    assert derived == {
        "vwce.de": "VWCE.DE", "cspx.uk": "CSPX.L", "asml.nl": "ASML.AS",
        "novo-b.dk": "NOVO-B.CO", "sap.de": "SAP.DE", "cez.cz": "CEZ.PR", "komb.cz": "KOMB.PR",
        "msft.us": "MSFT", "brk-b.us": "BRK-B", "^spx": None,
    }  # fmt: skip
    assert yahoo_from_listing("KOMB", "XPRA") == "KOMB.PR"
    assert yahoo_from_listing("BRK/B", "XNYS") == "BRK-B"
    assert yahoo_from_listing("X", "XXXX") is None
    assert [yahoo_mic(s) for s in ("VWCE.DE", "CSPX.L", "NOVO-B.CO", "CEZ.PR", "MSFT")] == [
        "XETR", "XLON", "XCSE", "XPRA", "XNYS",
    ]  # fmt: skip
    assert yahoo_mic("^GSPC") == "XNYS" and yahoo_mic("^STOXX") == "XETR"
    assert yahoo_mic("^UNKNOWN") is None


def test_stooq_covers_amsterdam_copenhagen_and_prague() -> None:
    def listing(ticker: str, exch: str) -> openfigi.FigiListing:
        return openfigi.FigiListing(
            figi="F", composite_figi="F", share_class_figi=None, ticker=ticker, exch_code=exch,
            name=None, security_type=None, security_type2=None, market_sector=None,
            security_description=None, raw={},
        )  # fmt: skip

    assert openfigi.stooq_symbol(listing("ASML", "NA")) == "asml.nl"
    assert openfigi.stooq_symbol(listing("NOVO B", "DC")) == "novo-b.dk"
    assert openfigi.stooq_symbol(listing("CEZ", "CP")) == "cez.cz"
    assert [stooq_currency(s) for s in ("asml.nl", "novo-b.dk", "cez.cz")] == ["EUR", "DKK", "CZK"]


def test_markets_are_polled_only_in_their_session() -> None:
    calendar = CalendarSettings(
        exchange_closures=[
            ExchangeClosure(mic="XPRA", date=dt.date(2026, 9, 28), name="St Wenceslas")
        ]
    )
    monday = dt.datetime(2026, 9, 28, 9, 0, tzinfo=UTC)  # 11:00 Berlin, 05:00 New York
    assert market_open("XETR", monday, calendar)
    assert not market_open("XNYS", monday, calendar)
    assert not market_open("XPRA", monday, calendar)  # a confirmed closure
    assert not market_open("XETR", dt.datetime(2026, 9, 26, 9, 0, tzinfo=UTC), calendar)  # Sat
    # Delayed bars still arrive for a while after the close (Xetra 17:30 = 15:30 UTC).
    assert market_open("XETR", dt.datetime(2026, 9, 28, 15, 50, tzinfo=UTC), calendar)
    assert not market_open("XETR", dt.datetime(2026, 9, 28, 16, 10, tzinfo=UTC), calendar)
    assert market_open("XNYS", dt.datetime(2026, 9, 28, 14, 0, tzinfo=UTC), calendar)
    assert market_open(None, monday, calendar)  # unknown market: the job's hours bound it


def test_quote_kind_tells_a_close_from_an_intraday_price() -> None:
    start = dt.datetime(2026, 9, 28, 13, 30, tzinfo=UTC)
    end = dt.datetime(2026, 9, 28, 20, 0, tzinfo=UTC)

    def q(price_time: dt.datetime, fetched: dt.datetime) -> dict[str, Any]:
        return {
            "price_time": price_time,
            "session_start": start,
            "session_end": end,
            "fetched_at": fetched,
        }

    monday = dt.datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
    friday_close = dt.datetime(2026, 9, 25, 20, 0, 1, tzinfo=UTC)
    assert quote_kind(q(friday_close, monday), monday) == "close"
    during = dt.datetime(2026, 9, 28, 15, 0, tzinfo=UTC)
    fetched = during + dt.timedelta(minutes=5)
    assert (
        quote_kind(q(during, fetched), dt.datetime(2026, 9, 28, 15, 20, tzinfo=UTC)) == "intraday"
    )
    # Fetched mid-session and never refreshed: still an intraday price, not a close.
    assert quote_kind(q(during, fetched), dt.datetime(2026, 9, 28, 22, 0, tzinfo=UTC)) == "intraday"
    # Fetched after the end: the last trade is the close, even minutes before the end.
    last = dt.datetime(2026, 9, 28, 19, 44, tzinfo=UTC)
    after = dt.datetime(2026, 9, 28, 20, 15, tzinfo=UTC)
    assert quote_kind(q(last, after), dt.datetime(2026, 9, 28, 21, 0, tzinfo=UTC)) == "close"


def test_pse_px_detail_page() -> None:
    value = parse_detail(PX_ISIN, fixture("pse_px_detail.html"))
    assert value.value == Decimal("2721.27")
    assert value.at.isoformat() == "2026-09-25T16:35:00+02:00"
    assert value.change_abs == Decimal("-2.52")
    assert value.previous_close == Decimal("2723.79")
    assert (value.open, value.high, value.low) == (
        Decimal("2724.53"), Decimal("2735.72"), Decimal("2720.86"),
    )  # fmt: skip
    assert value.is_close(dt.datetime(2026, 9, 28, 9, 0, tzinfo=UTC))
    with pytest.raises(Exception, match="layout changed"):
        parse_detail(PX_ISIN, "<html><body>maintenance</body></html>")


def test_intraday_config_and_its_schedule() -> None:
    config = JobsConfig()
    assert config.prices.intraday_enabled is True
    assert config.prices.intraday_interval_minutes == 15
    schedule = config.interval_schedules()["intraday_prices"]
    assert schedule.enabled and schedule.cron == "*/15 6-21 * * 1-5" and schedule.timezone == "UTC"
    assert "intraday_prices" not in config.schedules()  # not a cron of its own
    off = JobsConfig(prices=PriceSettings(intraday_enabled=False, intraday_interval_minutes=60))
    hourly = off.interval_schedules()["intraday_prices"]
    assert not hourly.enabled and hourly.cron == "0 6-21 * * 1-5"
    with pytest.raises(ValueError):
        PriceSettings.model_validate({"intraday_interval_minutes": 7})
    strip = {i.symbol: i.yahoo() for i in config.prices.market_strip}
    assert strip == {"^spx": "^GSPC", "^stoxx": "^STOXX", "^px": None}
    assert PriceSettings(market_strip=[]).index_yahoo("^spx") == "^GSPC"
    with pytest.raises(ValueError):
        HoldingProfile.model_validate({"ticker": "X", "yahoo_symbol": "novo b.co"})


def test_one_series_per_holding_stooq_first() -> None:
    symbols = PriceSymbols(stooq="cez.cz", yahoo="CEZ.PR")
    stooq, yh = SeriesRef("cez.cz", "stooq"), SeriesRef("CEZ.PR", "yahoo")
    d = dt.date(2026, 9, 25)
    assert pick(symbols, {stooq: d, yh: d}) == stooq  # a tie goes to the source of record
    assert pick(symbols, {stooq: d - dt.timedelta(days=3), yh: d}) == yh  # Stooq stopped
    assert pick(symbols, {yh: d}) == yh
    assert pick(symbols, {}) is None
    assert pick(PriceSymbols(stooq=None, yahoo=None), {stooq: d}) is None


# ---------------------------------------------------------------------------
# Database: the fallback chain, the intraday job and the reads
# ---------------------------------------------------------------------------


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    async def no_http() -> AsyncIterator[httpx.AsyncClient]:
        def refuse(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="Too Many Requests")

        async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
            yield http

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    app.dependency_overrides[yahoo_http] = no_http
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _instrument(
    pool: asyncpg.Pool, ticker: str, currency: str, symbol: str | None, mic: str | None
) -> None:
    await pool.execute(
        "insert into public.instrument (t212_ticker, name, currency, price_symbol, home_mic)"
        " values ($1, $1, $2, $3, $4)",
        ticker,
        currency,
        symbol,
        mic,
    )


async def _snapshot(
    pool: asyncpg.Pool, day: dt.date, positions: list[tuple[str, str, str, str]]
) -> None:
    """positions: (ticker, quantity, currency, value_base); base CZK on ČNB rates."""
    job_id = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values ('portfolio_sync',"
        " 'schedule', 'succeeded') returning id"
    )
    await pool.execute(
        "insert into public.account_snapshot (snapshot_date, job_id, captured_at,"
        " t212_account_id, account_currency, total_value, cash_available, cash_in_pies,"
        " cash_reserved, invested_value, invested_cost, unrealized_ppl, realized_ppl,"
        " base_currency, fx_source, fx_rate, cash_base, invested_base, total_base,"
        " weight_basis) values ($1, $2, now(), 1, 'CZK', 0, 0, 0, 0, 0, 0, 0, 0, 'CZK', 'cnb',"
        " 1, 0, 0, 0, 'total')",
        day,
        job_id,
    )
    for ticker, quantity, currency, value_base in positions:
        await pool.execute(
            "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at,"
            " quantity, avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
            " values ($1, $2, $3, now(), $4, 1, 1, $5, $4, 'CZK', $6, $6, 0, 'CZK', 'cnb', 1, $6)",
            day,
            ticker,
            job_id,
            Decimal(quantity),
            currency,
            Decimal(value_base),
        )


async def _cnb(pool: asyncpg.Pool, day: dt.date, currency: str, rate: str) -> None:
    await pool.execute(
        "insert into public.fx_rate (source, rate_date, currency, home_currency, rate,"
        " published_amount, published_rate) values ('cnb', $1, $2, 'CZK', $3, 1, $3)",
        day,
        currency,
        Decimal(rate),
    )


STOOQ_SAP = (
    "Date,Open,High,Low,Close,Volume\n2026-09-24,180,182,179,181,100\n"
    "2026-09-25,181,187,180,186,120\n"
)


class Markets:
    """Recorded answers: Stooq refuses cez.cz (no API key) and knows sap.de; Yahoo answers
    from the fixtures; the PSE page is the recorded one."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.requests.append(f"{url.host}{url.path}?{url.query.decode()}")
        if url.host == "stooq.com":
            symbol = url.params["s"]
            if symbol == "sap.de":
                return httpx.Response(200, text=STOOQ_SAP)
            if symbol == "cez.cz":
                return httpx.Response(
                    200, text="Get your apikey: https://stooq.com/q/d/?s=x&get_apikey"
                )
            return httpx.Response(200, text="No data")
        if url.host == "query1.finance.yahoo.com":
            symbol = url.path.rsplit("/", 1)[-1]
            if symbol == "CEZ.PR" and url.params["interval"] == "1d":
                return httpx.Response(200, text=fixture("yahoo_cez_pr_1d.json"))
            if symbol == "VWCE.DE" and url.params["interval"] == "5m":
                return httpx.Response(200, text=fixture("yahoo_vwce_de_5m.json"))
            if symbol == "MSFT" and url.params["interval"] == "5m":
                return httpx.Response(200, text=fixture("yahoo_msft_5m.json"))
            return httpx.Response(404, text=fixture("yahoo_not_found.json"))
        if url.host == "www.pse.cz":
            return httpx.Response(200, text=fixture("pse_px_detail.html"))
        if url.host == "api.frankfurter.dev":
            return httpx.Response(200, json={"base": "EUR", "rates": {}})
        return httpx.Response(404)


@pytest.mark.db
async def test_stooq_refusal_falls_back_to_yahoo_daily_and_px_to_the_pse_page(
    backend_pool: asyncpg.Pool,
) -> None:
    day = dt.date(2026, 9, 25)
    await _instrument(backend_pool, "CEZ_EQ", "CZK", "cez.cz", "XPRA")
    await _instrument(backend_pool, "SAPd_EQ", "EUR", "sap.de", "XETR")
    await _snapshot(
        backend_pool, day, [("CEZ_EQ", "10", "CZK", "13490"), ("SAPd_EQ", "2", "EUR", "9000")]
    )
    markets = Markets()
    now = dt.datetime(2026, 9, 28, 21, 15, tzinfo=UTC)
    async with httpx.AsyncClient(transport=httpx.MockTransport(markets.handler)) as http:

        async def handler(ctx: JobContext) -> None:
            await run_price_history(ctx, PriceDeps(http=http, today=now.date(), now=now))

        assert (
            await run_job(backend_pool, "price_history", handler, trigger="manual") == "succeeded"
        )

    stored = {
        (r["symbol"], r["source"]): (r["n"], r["currency"])
        for r in await backend_pool.fetch(
            "select symbol, source, count(*) as n, max(currency) as currency"
            " from public.price_bar group by symbol, source"
        )
    }
    # Stooq's closes where it has them (its quote currency is derived from the market suffix at
    # write time, audit A16: it used to be NULL and made every return "missing currency");
    # Yahoo's (with its quote currency) where it refused.
    assert stored[("sap.de", "stooq")] == (2, "EUR")
    assert stored[("CEZ.PR", "yahoo")] == (5, "CZK")
    assert ("SAP.DE", "yahoo") not in stored  # Yahoo is only asked when Stooq fails
    payload = await backend_pool.fetchrow(
        "select url, http_status, body from public.market_payload where source = 'yahoo'"
        " and symbol = 'CEZ.PR' and kind = 'chart_1d'"
    )
    assert payload["http_status"] == 200 and payload["body"] == fixture("yahoo_cez_pr_1d.json")
    assert "range=2y" in payload["url"] and "interval=1d" in payload["url"]

    sources = json.loads(
        await backend_pool.fetchval("select value from sync_state where key = $1", SOURCES_KEY)
    )["sources"]
    assert sources["SAPd_EQ"] == {"source": "stooq", "symbol": "sap.de"}
    assert sources["CEZ_EQ"]["source"] == "yahoo" and sources["CEZ_EQ"]["symbol"] == "CEZ.PR"
    assert "refused" in sources["CEZ_EQ"]["stooq_gap"]
    assert sources["^px"]["source"] == "pse"

    cez = await price_history(backend_pool, "CEZ_EQ", 365)
    assert cez.source == "Yahoo" and cez.symbol == "CEZ.PR" and cez.gap is None
    assert cez.stooq_symbol == "cez.cz" and cez.yahoo_symbol == "CEZ.PR"
    assert cez.fallback_reason and "refused" in cez.fallback_reason
    assert cez.as_of == day and cez.bars[-1].close == 1349.0
    # Plausibility compares Yahoo's CZK close with Trading212's CZK price (1 in the fixture).
    assert cez.plausibility is not None and cez.plausibility.source == "Yahoo"
    sap = await price_history(backend_pool, "SAPd_EQ", 365)
    assert sap.source == "Stooq" and sap.symbol == "sap.de" and sap.fallback_reason is None

    prices = await holdings_prices(backend_pool, 30)
    by_ticker = {h.ticker: h for h in prices.holdings}
    assert by_ticker["CEZ_EQ"].source == "Yahoo" and by_ticker["CEZ_EQ"].last_close == 1349.0
    assert by_ticker["CEZ_EQ"].day_change_pct == pytest.approx(1349 / 1350 - 1)
    assert by_ticker["SAPd_EQ"].source == "Stooq"

    strip = await market_strip(backend_pool)
    px = next(i for i in strip.indices if i.symbol == "^px")
    assert px.source == "Prague Stock Exchange" and px.kind == "close"
    assert px.last_close == 2721.27 and px.previous_close == pytest.approx(2723.79)
    assert px.as_of == day and px.exchange_tz == "Europe/Prague"

    # Once a day: a second run within the day reads the stored PX value, not the page.
    before = len([r for r in markets.requests if r.startswith("www.pse.cz")])
    async with httpx.AsyncClient(transport=httpx.MockTransport(markets.handler)) as http:

        async def again(ctx: JobContext) -> None:
            later = now + dt.timedelta(hours=1)
            await run_price_history(ctx, PriceDeps(http=http, today=later.date(), now=later))

        assert await run_job(backend_pool, "price_history", again, trigger="manual") == "succeeded"
    assert len([r for r in markets.requests if r.startswith("www.pse.cz")]) == before


@pytest.mark.db
async def test_without_the_fallback_a_stooq_refusal_stays_a_gap(backend_pool: asyncpg.Pool) -> None:
    await _instrument(backend_pool, "CEZ_EQ", "CZK", "cez.cz", "XPRA")
    await _snapshot(backend_pool, dt.date(2026, 9, 25), [("CEZ_EQ", "10", "CZK", "13490")])
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "jobs",
            JobsConfig(prices=PriceSettings(yahoo_daily_fallback=False)),
            created_by=None,
        )
    markets = Markets()
    now = dt.datetime(2026, 9, 28, 21, 15, tzinfo=UTC)
    async with httpx.AsyncClient(transport=httpx.MockTransport(markets.handler)) as http:

        async def handler(ctx: JobContext) -> None:
            await run_price_history(ctx, PriceDeps(http=http, today=now.date(), now=now))

        assert (
            await run_job(backend_pool, "price_history", handler, trigger="manual") == "succeeded"
        )
    assert not any("yahoo" in r and "CEZ" in r for r in markets.requests)
    history = await price_history(backend_pool, "CEZ_EQ", 365)
    assert history.bars == [] and history.gap and "refused" in history.gap


@pytest.mark.db
async def test_intraday_job_polls_open_markets_and_prunes(backend_pool: asyncpg.Pool) -> None:
    await _instrument(backend_pool, "VWCEd_EQ", "EUR", "vwce.de", "XETR")
    await _instrument(backend_pool, "MSFT_US_EQ", "USD", "msft.us", "XNAS")
    await _snapshot(
        backend_pool,
        dt.date(2026, 9, 27),
        [("VWCEd_EQ", "100", "EUR", "417000"), ("MSFT_US_EQ", "10", "USD", "115000")],
    )
    await backend_pool.execute(
        "insert into public.intraday_bar (symbol, ts, close) values ('VWCE.DE', $1, 160)",
        VWCE_NOW - dt.timedelta(days=9),
    )
    markets = Markets()
    async with httpx.AsyncClient(transport=httpx.MockTransport(markets.handler)) as http:

        async def handler(ctx: JobContext) -> None:
            await run_intraday(ctx, IntradayDeps(http=http, now=lambda: VWCE_NOW))

        assert (
            await run_job(backend_pool, "intraday_prices", handler, trigger="manual") == "succeeded"
        )

    polled = {r.split("/")[-1].split("?")[0] for r in markets.requests}
    # 09:00 UTC: Xetra and the STOXX 600 are open; New York is not.
    assert polled == {"VWCE.DE", "^STOXX"}
    bars = await backend_pool.fetch(
        "select ts, close from public.intraday_bar where symbol = 'VWCE.DE' order by ts"
    )
    assert len(bars) == 21 and bars[0]["ts"] == dt.datetime(2026, 9, 28, 7, 5, tzinfo=UTC)
    assert bars[-1]["close"] == Decimal("169.64")
    quote = await backend_pool.fetchrow(
        "select * from public.market_quote where symbol = 'VWCE.DE' and source = 'yahoo'"
    )
    assert quote["price"] == Decimal("169.64") and quote["previous_close"] == Decimal("169.74")
    assert quote["exchange_tz"] == "Europe/Berlin" and quote["session_date"] == VWCE_NOW.date()
    body = await backend_pool.fetchval(
        "select body from public.market_payload where symbol = 'VWCE.DE' and kind = 'chart_5m'"
    )
    assert body == fixture("yahoo_vwce_de_5m.json")
    status = json.loads(
        await backend_pool.fetchval("select value from sync_state where key = 'intraday_status'")
    )
    assert status["VWCE.DE"]["ok"] is True
    assert status["^STOXX"]["ok"] is False and "No data" in status["^STOXX"]["error"]

    # Off: the job does nothing.
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "jobs",
            JobsConfig(prices=PriceSettings(intraday_enabled=False)),
            created_by=None,
        )
    markets.requests.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(markets.handler)) as http:

        async def off(ctx: JobContext) -> None:
            await run_intraday(ctx, IntradayDeps(http=http, now=lambda: VWCE_NOW))

        assert await run_job(backend_pool, "intraday_prices", off, trigger="manual") == "succeeded"
    assert markets.requests == []


@pytest.mark.db
async def test_holding_intraday_fetches_on_demand_and_degrades(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await _instrument(backend_pool, "MSFT_US_EQ", "USD", "msft.us", "XNAS")
    await backend_pool.execute(
        "insert into public.price_bar (symbol, date, close, source) values"
        " ('msft.us', '2026-09-24', 497.93, 'stooq'), ('msft.us', '2026-09-25', 516.17, 'stooq')"
    )
    # Nothing stored and Yahoo rate-limits (the API fixture's client): daily closes, a note.
    body = (await api.get("/holdings/MSFT_US_EQ/intraday")).json()
    assert body["available"] is False and body["symbol"] == "MSFT"
    assert "429" in body["note"] and body["daily"] == {
        "date": "2026-09-25", "close": 516.17, "source": "Stooq",
    }  # fmt: skip

    markets = Markets()
    monday = dt.datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
    async with httpx.AsyncClient(transport=httpx.MockTransport(markets.handler)) as http:
        first = await holding_intraday(backend_pool, "MSFT_US_EQ", now=monday, http=http)
        # Within the cache age nothing is fetched again.
        again = await holding_intraday(
            backend_pool, "MSFT_US_EQ", now=monday + dt.timedelta(seconds=30), http=http
        )
    assert len(markets.requests) == 1
    assert first.available and first.note is None and again.last_price == first.last_price
    # Friday's session, read on Monday morning: the last price is Friday's close.
    assert first.kind == "close" and first.session_date == dt.date(2026, 9, 25)
    assert first.last_price == 516.17 and first.previous_close == 497.93
    assert first.last_trade_time == "16:00" and first.exchange_tz == "America/New_York"
    assert first.change_pct == pytest.approx(516.17 / 497.93 - 1)
    assert first.delay_note and first.source == "Yahoo Finance (unofficial)"
    # 09:30 to the 16:00 closing print; the trailing live tick is not a bar.
    assert len(first.bars) == 79 and first.bars[0].time == "09:30"
    assert first.bars[-1].time == "16:00"

    # Yahoo fails later on: the stored session is still shown, with a note.
    body = (await api.get("/holdings/MSFT_US_EQ/intraday")).json()
    assert body["available"] is True and body["last_price"] == 516.17
    assert body["note"] is None or "did not answer" in body["note"]


@pytest.mark.db
async def test_portfolio_intraday_values_the_covered_holdings_in_the_base_currency(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await _instrument(backend_pool, "VWCEd_EQ", "EUR", "vwce.de", "XETR")
    await _instrument(backend_pool, "KOMB_EQ", "CZK", "komb.cz", "XPRA")
    await _snapshot(
        backend_pool,
        dt.date(2026, 9, 27),
        [("VWCEd_EQ", "100", "EUR", "417000"), ("KOMB_EQ", "10", "CZK", "10250")],
    )
    await _cnb(backend_pool, dt.date(2026, 9, 25), "EUR", "24.5")
    async with httpx.AsyncClient(transport=httpx.MockTransport(Markets().handler)) as http:
        await intraday.fetch_symbol(backend_pool, http, "VWCE.DE", VWCE_NOW)

    path = await portfolio_intraday(backend_pool, VWCE_NOW)
    assert path.day == VWCE_NOW.date() and path.base_currency == "CZK"
    assert path.fx_source == "cnb" and path.fx_rate_date == dt.date(2026, 9, 25)
    assert path.coverage.holdings == 2 and path.coverage.covered == 1
    assert path.coverage.missing == ["KOMB_EQ"]
    assert path.coverage.covered_weight == pytest.approx(417000 / 427250)
    assert len(path.points) == 21
    assert path.points[0].value == pytest.approx(100 * 169.76 * 24.5)
    assert path.previous_value == pytest.approx(100 * 169.74 * 24.5)
    assert path.last_value == pytest.approx(100 * 169.64 * 24.5)
    assert path.change_pct == pytest.approx(169.64 / 169.74 - 1)
    assert path.note == f"1 of 2 holdings have intraday prices for {VWCE_NOW.date()}."

    # Up to a time: only the bars before it.
    early = await portfolio_intraday(backend_pool, dt.datetime(2026, 9, 28, 7, 30, tzinfo=UTC))
    assert [p.ts.minute for p in early.points] == [5, 10, 15, 20, 25, 30]

    # A day with no bars (retention is a few days): no path, a note.
    body = (await api.get("/portfolio/intraday", params={"until": "2026-09-27T23:00:00Z"})).json()
    assert body["day"] is None and body["points"] == [] and "daily closes only" in body["note"]

    # The holdings list carries the latest intraday quote next to the daily close.
    prices = await holdings_prices(backend_pool, 30, now=VWCE_NOW)
    vwce = next(h for h in prices.holdings if h.ticker == "VWCEd_EQ")
    assert vwce.intraday is not None and vwce.intraday.kind == "intraday"
    assert vwce.intraday.time_local == "10:46" and vwce.intraday.session_date == VWCE_NOW.date()
    assert vwce.intraday.change_pct == pytest.approx(169.64 / 169.74 - 1)


@pytest.mark.db
async def test_the_yahoo_symbol_override_and_the_listing_fallback(
    backend_pool: asyncpg.Pool,
) -> None:
    from steward.reference.price_series import holding_symbols

    await _instrument(backend_pool, "NOVOb_EQ", "DKK", "novob.dk", "XCSE")
    await _instrument(backend_pool, "KOMB_EQ", "CZK", None, "XPRA")
    await backend_pool.execute(
        "insert into public.instrument_identifier (ticker, figi, composite_figi, listing_ticker,"
        " exch_code, raw) values ('KOMB_EQ', 'BBG1', 'BBG1', 'KOMB', 'CP', '{}')"
    )
    profiles = HoldingProfilesConfig(
        profiles=[HoldingProfile(ticker="NOVOb_EQ", yahoo_symbol="NOVO-B.CO")]
    )
    symbols = await holding_symbols(backend_pool, ["NOVOb_EQ", "KOMB_EQ"], profiles)
    assert symbols["NOVOb_EQ"] == PriceSymbols(stooq="novob.dk", yahoo="NOVO-B.CO")
    assert symbols["KOMB_EQ"] == PriceSymbols(stooq=None, yahoo="KOMB.PR")
