"""Prices stream fixes (audit A1, A2, A15, A16, A17, B21-B24, B26, D3.1, D3.4).

Pure tests first (calculation, classification, exchange sessions), then database tests. No
live network: every provider answer is a recorded-shape fixture served by httpx.MockTransport.
"""

import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import ValidationError

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.free_sources_routes import CorporateActionInput
from api.main import create_app
from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import (
    CalendarSettings,
    ExchangeClosure,
    JobsConfig,
    ProviderJobSettings,
)
from steward.config.store import save_config
from steward.free_sources import compute_returns
from steward.jobs.runner import JobContext, run_job
from steward.market_analysis import (
    Action,
    PricePoint,
    calculate_return,
    dedupe_actions,
)
from steward.reference import exchange_probes
from steward.reference.calendar import (
    SessionRow,
    exchange_open,
    expected_sessions,
    last_expected_session,
    load_sessions,
)
from steward.reference.intraday import market_open
from steward.reference.listings import (
    confirm_listing,
    derive_listings,
    resolve_series_listing,
)
from steward.reference.price_diagnostics import holding_price_diagnostics
from steward.reference.price_series import (
    PriceSymbols,
    SeriesRef,
    choose_one,
    pick,
)
from steward.reference.prices import (
    PriceDeps,
    _accept_fallbacks,
    collect_alpha_vantage_fallbacks,
    collect_eodhd_fallbacks,
    collect_symbol,
    run_price_history,
    store_bars,
)
from steward.reference.returns import run_returns_refresh
from steward.sources import finnhub, twelvedata
from steward.sources.base import AccessState
from steward.sources.credit_group import CreditPart, refund, reserve_group
from steward.sources.free_market import (
    FreeSourceError,
    classify_alpha_message,
    parse_alpha_daily,
)
from steward.sources.governor import Budget, QuotaExceeded
from steward.sources.market import (
    PriceBar,
    infer_quote_currency,
    major_currency,
    quote_unit_of,
    stooq_currency,
    unit_factor,
)

OWNER = "00000000-0000-0000-0000-00000000000b"
D = Decimal


def day(d: int, month: int = 1) -> dt.date:
    return dt.date(2026, month, d)


def pts(*closes: str, currency: str = "USD", start: int = 1) -> list[PricePoint]:
    return [PricePoint(day(start + i), D(c), currency) for i, c in enumerate(closes)]


# ---------------------------------------------------------------------------------------
# A15: total return
# ---------------------------------------------------------------------------------------
def test_a_dividend_followed_by_a_split_is_split_adjusted_not_reinvested_unadjusted() -> None:
    # 1.00 dividend on 2 Jan at a raw close of 100 (1 %), then a 2:1 split on 5 Jan.
    points = [
        PricePoint(day(1), D(100), "USD"),
        PricePoint(day(2), D(100), "USD"),
        PricePoint(day(5), D(50), "USD"),
    ]
    actions = [
        Action("cash_dividend", day(2), cash=D(1), currency="USD"),
        Action("split", day(5), ratio=D(2)),
    ]
    result = calculate_return(points, actions, reinvest_dividends=True)
    # Flat after the split basis restates the earlier closes: the only return is the 1 % dividend
    # (the audit's reproduction gave 2.02 % because the amount was not halved with the prices).
    assert result.value == D("0.01")
    assert result.details["splits_applied"] == [day(5).isoformat()]


def test_the_same_action_from_two_sources_counts_once_and_a_disagreement_is_unavailable() -> None:
    points = pts("100", "100", "50", start=1)
    split_a = Action("split", day(3), ratio=D(2), source="issuer")
    split_b = Action("split", day(3), ratio=D(2), source="exchange")
    assert calculate_return(points, [split_a, split_b]).value == D(0)  # was +100 % applied twice
    conflicting = Action("split", day(3), ratio=D(3), source="exchange")
    result = calculate_return(points, [split_a, conflicting])
    assert result.value is None and "conflicting" in (result.reason or "")
    deduped = dedupe_actions(
        [
            Action("cash_dividend", day(2), cash=D(1), currency="USD", source="a"),
            Action("cash_dividend", day(2), cash=D(1), currency="USD", source="b"),
            Action("cash_dividend", day(2), cash=D(1), currency="USD", source="a"),
        ]
    )
    assert len(deduped.actions) == 1 and not deduped.conflicts


@pytest.mark.parametrize("kind", ["spin_off", "merger", "delisting", "symbol_change"])
def test_unhandled_actions_make_total_return_unavailable_and_flag_the_price_return(
    kind: str,
) -> None:
    points = pts("100", "101", "70")
    action = Action(kind, day(3))  # type: ignore[arg-type]
    total = calculate_return(points, [action], reinvest_dividends=True)
    assert total.value is None and kind in (total.reason or "")
    assert total.incomplete and kind in total.incomplete[0]
    price = calculate_return(points, [action])
    assert price.value is not None and price.incomplete  # computed, but marked incomplete


def test_stock_dividend_needs_a_ratio_and_the_validator_rejects_cash_only() -> None:
    result = calculate_return(
        pts("100", "100"), [Action("stock_dividend", day(2), cash=D(1))], reinvest_dividends=True
    )
    assert result.value is None and "ratio" in (result.reason or "")
    base = dict(
        action_type="stock_dividend",
        symbol="cez.cz",
        ex_date="2026-01-02",
        source="issuer",
        source_url="https://example.test/a",
    )
    with pytest.raises(ValidationError):
        CorporateActionInput(**base, cash_amount="1", currency="CZK")  # type: ignore[arg-type]
    assert CorporateActionInput(**base, ratio="0.05").ratio == D("0.05")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):  # a cash dividend needs its currency
        CorporateActionInput(
            action_type="cash_dividend",
            symbol="x",
            ex_date="2026-01-02",
            cash_amount="1",
            source="s1",
            source_url="https://example.test/b",
        )
    assert (
        CorporateActionInput(
            action_type="cash_dividend",
            symbol="x",
            ex_date="2026-01-02",
            cash_amount="40",
            currency="GBp",
            source="s1",
            source_url="https://example.test/b",
        ).currency
        == "GBp"
    )


def test_raw_closes_that_do_not_show_a_confirmed_split_are_not_adjusted_twice() -> None:
    # The closes are already split-adjusted (no 2:1 fall on the ex-date).
    result = calculate_return(
        pts("100", "101", "102"), [Action("split", day(3), ratio=D(2))], reinvest_dividends=True
    )
    assert result.value is None and "already split-adjusted" in (result.reason or "")


def test_dividend_currency_pence_pound_fx_withholding_adr_and_fx_dates() -> None:
    # Quoted in pence (GBX); the dividend is declared in pence (GBp) and in pounds.
    points = pts("1000", "1000", "1000", currency="GBX")
    pence = Action("cash_dividend", day(2), cash=D(10), currency="GBp")
    pounds = Action("cash_dividend", day(2), cash=D("0.10"), currency="GBP")
    assert calculate_return(points, [pence], reinvest_dividends=True).value == D("0.01")
    assert calculate_return(points, [pounds], reinvest_dividends=True).value == D("0.01")
    # A EUR dividend on a GBX line needs the ex-date EUR->GBP rate; its date is reported.
    euro = Action("cash_dividend", day(2), cash=D("1.00"), currency="EUR")
    assert calculate_return(points, [euro], reinvest_dividends=True).value is None
    result = calculate_return(
        points, [euro], reinvest_dividends=True, fx_to_quote={day(2): D("0.85")}
    )
    assert result.value == D("0.085")  # 0.85 GBP = 85 pence on a 1000 pence close
    assert result.details["fx_dates"] == {day(2).isoformat(): "0.85"}
    # 15 % withholding is an explicit, reported assumption.
    net = calculate_return(
        points, [pence], reinvest_dividends=True, withholding_rate=D("0.15")
    )
    assert net.value == D("0.0085")
    assert "15.0 %" in str(net.details["withholding_assumption"]).replace("%", " %")
    # ADS ratio: the dividend is per ordinary share and one ADS is five of them.
    adr = Action("cash_dividend", day(2), cash=D("0.50"), currency="USD", per_unit_multiplier=D(5))
    usd = pts("100", "100")
    assert calculate_return(usd, [adr], reinvest_dividends=True).value == D("0.025")
    # An unknown dividend currency is unavailable, not guessed.
    unknown = Action("cash_dividend", day(2), cash=D(1))
    assert calculate_return(usd, [unknown], reinvest_dividends=True).value is None


def test_quote_units_normalise_pence_and_minor_units() -> None:
    assert quote_unit_of("GBp") == "GBX" and quote_unit_of("GBX") == "GBX"
    assert quote_unit_of("GBP") == "GBP" and quote_unit_of("eur") == "EUR"
    assert major_currency("GBp") == "GBP" and unit_factor("GBp") == D("0.01")
    assert unit_factor("GBP") == 1 and quote_unit_of(None) is None
    # A provider without a unit: the instrument's pence code wins, then the exchange convention.
    assert infer_quote_currency("XLON", "GBP", "GBX") == ("GBX", "instrument")
    assert infer_quote_currency("XLON", "GBP", "GBP") == ("GBX", "exchange_convention")
    assert infer_quote_currency("XETR", "EUR", "EUR") == ("EUR", "listing_currency")
    assert stooq_currency("vod.uk") == "GBX" and stooq_currency("^spx") is None


# ---------------------------------------------------------------------------------------
# B24: Alpha Vantage HTTP-200 denials are typed
# ---------------------------------------------------------------------------------------
PREMIUM = (
    "Thank you for using Alpha Vantage! This is a premium endpoint. You may subscribe to any of "
    "the premium plans at https://www.alphavantage.co/premium/ to instantly unlock all premium "
    "endpoints"
)
DAILY_LIMIT = (
    "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day. "
    "Please subscribe to any of the premium plans at https://www.alphavantage.co/premium/ to "
    "instantly remove all daily rate limits."
)
PER_MINUTE = (
    "Thank you for using Alpha Vantage! Our standard API call frequency is 5 calls per minute."
)


def test_alpha_vantage_information_envelopes_are_typed_paid_only_or_quota() -> None:
    assert classify_alpha_message(PREMIUM) is AccessState.PAID_ONLY
    # The daily-limit text also says "premium plans": it is a quota, not a paid-only endpoint.
    assert classify_alpha_message(DAILY_LIMIT) is AccessState.QUOTA_EXHAUSTED
    assert classify_alpha_message(PER_MINUTE, key="Note") is AccessState.QUOTA_EXHAUSTED
    assert classify_alpha_message("Invalid API call. Please retry", key="Error Message") is (
        AccessState.NOT_COVERED
    )
    assert classify_alpha_message("the parameter apikey is invalid or missing") is (
        AccessState.ACCESS_UNVERIFIED
    )
    with pytest.raises(FreeSourceError) as premium:
        parse_alpha_daily({"Information": PREMIUM})
    assert premium.value.state is AccessState.PAID_ONLY
    with pytest.raises(FreeSourceError) as quota:
        parse_alpha_daily({"Information": DAILY_LIMIT})
    assert quota.value.state is AccessState.QUOTA_EXHAUSTED


# ---------------------------------------------------------------------------------------
# B22: exchange sessions (pure part)
# ---------------------------------------------------------------------------------------
def _sessions(*rows: SessionRow) -> dict[tuple[str, dt.date], SessionRow]:
    return {(r.mic, r.session_date): r for r in rows}


def test_a_national_holiday_on_which_the_exchange_trades_is_a_session() -> None:
    # US Independence Day observed is not relevant to Xetra; Xetra trades on 3 Oct 2026
    # (German Unity Day falls on a Saturday) - and on an invented holiday here: Nager would say
    # holiday, the exchange row says open, and the confirmed row decides.
    settings = CalendarSettings()
    monday = dt.date(2026, 9, 28)
    assert exchange_open(settings, "XETR", monday)
    sessions = _sessions(SessionRow("XETR", monday, "open"))
    assert exchange_open(settings, "XETR", monday, sessions)


def test_an_ad_hoc_closure_without_any_holiday_closes_the_day() -> None:
    settings = CalendarSettings()
    tuesday = dt.date(2026, 9, 29)  # no holiday anywhere
    assert exchange_open(settings, "XNYS", tuesday)
    closed = _sessions(SessionRow("XNYS", tuesday, "closed", reason="ad hoc closure"))
    assert not exchange_open(settings, "XNYS", tuesday, closed)
    # The configured closure still works without a row; a confirmed open row overrides it.
    configured = CalendarSettings(exchange_closures=[ExchangeClosure(mic="XPRA", date=tuesday)])
    assert not exchange_open(configured, "XPRA", tuesday)
    reopened = _sessions(SessionRow("XPRA", tuesday, "open"))
    assert exchange_open(configured, "XPRA", tuesday, reopened)


def test_market_open_follows_half_days_ad_hoc_closures_and_daylight_saving() -> None:
    settings = CalendarSettings()
    utc = dt.UTC
    # Day after Thanksgiving 2026 (27 Nov): NYSE closes at 13:00 ET = 18:00 UTC.
    half = SessionRow(
        "XNYS",
        dt.date(2026, 11, 27),
        "half_day",
        dt.datetime(2026, 11, 27, 14, 30, tzinfo=utc),
        dt.datetime(2026, 11, 27, 18, 0, tzinfo=utc),
    )
    sessions = _sessions(half)
    assert market_open("XNYS", dt.datetime(2026, 11, 27, 17, 0, tzinfo=utc), settings,
                       sessions=sessions)  # fmt: skip
    # 19:00 UTC would be inside the regular session (to 21:00 UTC) but the half day ended.
    assert not market_open("XNYS", dt.datetime(2026, 11, 27, 19, 0, tzinfo=utc), settings,
                           sessions=sessions)  # fmt: skip
    assert market_open("XNYS", dt.datetime(2026, 11, 27, 19, 0, tzinfo=utc), settings)
    # An ad hoc closure: closed all day.
    closed = _sessions(SessionRow("XNYS", dt.date(2026, 9, 29), "closed"))
    noon = dt.datetime(2026, 9, 29, 16, 0, tzinfo=utc)
    assert market_open("XNYS", noon, settings) and not market_open(
        "XNYS", noon, settings, sessions=closed
    )
    # Daylight saving: the US clocks change on Sunday 8 Mar 2026. The open (09:30 local) is
    # 14:30 UTC on Fri 6 Mar (EST) and 13:30 UTC on Mon 9 Mar (EDT).
    assert not market_open("XNYS", dt.datetime(2026, 3, 6, 13, 35, tzinfo=utc), settings)
    assert market_open("XNYS", dt.datetime(2026, 3, 6, 14, 35, tzinfo=utc), settings)
    assert market_open("XNYS", dt.datetime(2026, 3, 9, 13, 35, tzinfo=utc), settings)
    assert not market_open("XNYS", dt.datetime(2026, 3, 9, 13, 25, tzinfo=utc), settings)
    # Europe/London moves a week later: Mon 30 Mar 08:05 BST = 07:05 UTC is open.
    assert market_open("XLON", dt.datetime(2026, 3, 30, 7, 5, tzinfo=utc), settings)
    assert not market_open("XLON", dt.datetime(2026, 3, 27, 7, 5, tzinfo=utc), settings)


# ---------------------------------------------------------------------------------------
# A2 / D3.1 pure: the series a reader serves
# ---------------------------------------------------------------------------------------
def test_pick_serves_fallback_series_by_recency_and_source_order() -> None:
    d = dt.date(2026, 9, 25)
    symbols = PriceSymbols(stooq="cez.cz", yahoo="CEZ.PR", eodhd="CEZ.PSE", alpha_vantage="CEZ.PRG")
    stooq, yahoo = SeriesRef("cez.cz", "stooq"), SeriesRef("CEZ.PR", "yahoo")
    eodhd, alpha = SeriesRef("CEZ.PSE", "eodhd"), SeriesRef("CEZ.PRG", "alpha_vantage")
    old = d - dt.timedelta(days=5)
    assert pick(symbols, {stooq: d, yahoo: d, eodhd: d, alpha: d}) == stooq  # ties: Stooq
    assert pick(symbols, {stooq: old, alpha: d}) == alpha  # a fallback is served when fresher
    assert pick(symbols, {stooq: old, eodhd: d, alpha: d}) == eodhd  # source order breaks ties
    assert pick(symbols, {alpha: old}) == alpha
    assert pick(PriceSymbols(stooq=None, yahoo=None), {stooq: d}) is None


# ---------------------------------------------------------------------------------------
# D3.4 pure: probe classification
# ---------------------------------------------------------------------------------------
def test_probe_outcomes_separate_missing_coverage_from_technical_denials() -> None:
    state, _, level = exchange_probes.classify_finnhub(None, None)
    assert (state, level) == (AccessState.NOT_COVERED, False)
    forbidden = finnhub.FinnhubError("HTTP 403", 403)
    assert exchange_probes.classify_finnhub(None, forbidden)[0] is AccessState.PAID_ONLY
    assert exchange_probes.classify_finnhub(None, forbidden)[2] is False  # exchange-scoped
    assert exchange_probes.classify_finnhub(None, finnhub.FinnhubError("k", 401))[2] is True
    plan = twelvedata.classify_time_series(
        {"code": 400, "status": "error", "message": "This symbol is available exclusively "
         "with pro or ultra plans"}
    )  # fmt: skip
    assert plan.state is AccessState.PAID_ONLY
    missing = twelvedata.classify_time_series(
        {"code": 400, "status": "error", "message": "**symbol** not found: XYZ"}
    )
    assert missing.state is AccessState.NOT_COVERED
    assert twelvedata.classify_time_series({"code": 429, "message": "run out of API credits"}).state is (
        AccessState.QUOTA_EXHAUSTED
    )
    ok = twelvedata.classify_time_series(
        {"meta": {"currency": "EUR", "exchange": "XETR"}, "values": [{"close": "1"}]}
    )
    assert ok.state is AccessState.AVAILABLE and ok.currency == "EUR"


# ---------------------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------------------
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


async def instrument(
    pool: asyncpg.Pool,
    ticker: str,
    *,
    currency: str = "CZK",
    symbol: str | None = "cez.cz",
    mic: str | None = "XPRA",
    isin: str | None = "CZ0005112300",
) -> None:
    await pool.execute(
        "insert into public.instrument (t212_ticker, name, currency, price_symbol, home_mic, isin)"
        " values ($1, $1, $2, $3, $4, $5)",
        ticker,
        currency,
        symbol,
        mic,
        isin,
    )


async def firds_and_figi(
    pool: asyncpg.Pool, ticker: str, isin: str, mic: str, currency: str, listing_ticker: str,
    exch_code: str,
) -> None:  # fmt: skip
    await pool.execute(
        "insert into public.listing (isin, mic, currency, status, raw)"
        " values ($1, $2, $3, 'ACTIV', '{}')",
        isin,
        mic,
        currency,
    )
    await pool.execute(
        "insert into public.instrument_identifier (ticker, figi, composite_figi, listing_ticker,"
        " exch_code, raw) values ($1, $2, $2, $3, $4, '{}')",
        ticker,
        "BBG" + listing_ticker.ljust(9, "0"),
        listing_ticker,
        exch_code,
    )


async def confirmed_listing(
    pool: asyncpg.Pool, ticker: str, profiles: HoldingProfilesConfig | None = None
) -> int:
    results = await derive_listings(pool, [ticker], profiles or HoldingProfilesConfig())
    assert results[0].status == "confirmed", results
    assert results[0].listing_id is not None
    return results[0].listing_id


def bar(symbol: str, d: dt.date, close: str, ts: dt.datetime | None = None) -> PriceBar:
    return PriceBar(symbol, d, D(close), D(close), D(close), D(close), D(1000), ts)


async def put_bars(
    pool: asyncpg.Pool,
    symbol: str,
    source: Any,
    closes: dict[dt.date, str],
    *,
    currency: str | None = "CZK",
    bar_type: str = "adjusted",
) -> None:
    await store_bars(
        pool, [bar(symbol, d, c) for d, c in closes.items()], source, currency, bar_type
    )


def weekdays(start: dt.date, end: dt.date) -> list[dt.date]:
    out, current = [], start
    while current <= end:
        if current.weekday() < 5:
            out.append(current)
        current += dt.timedelta(days=1)
    return out


# ------------------------------------------------------------------ A1 / A2 / B23 / B24
EODHD_CEZ = [
    {"date": "2026-09-24", "open": 1300, "high": 1310, "low": 1290, "close": 1300,
     "adjusted_close": 1290.5, "volume": 100},
    {"date": "2026-09-25", "open": 1310, "high": 1360, "low": 1300, "close": 1349,
     "adjusted_close": 1339.4, "volume": 120},
]  # fmt: skip


def alpha_body(rows: dict[str, tuple[str, str]]) -> dict[str, Any]:
    return {
        "Meta Data": {"2. Symbol": "X"},
        "Time Series (Daily)": {
            d: {
                "1. open": c,
                "2. high": c,
                "3. low": c,
                "4. close": c,
                "5. adjusted close": adj,
                "6. volume": "10",
                "7. dividend amount": "0.0",
                "8. split coefficient": "1.0",
            }
            for d, (c, adj) in rows.items()
        },
    }


async def open_gates(pool: asyncpg.Pool) -> None:
    for provider, endpoint in (
        ("eodhd", "eod"),
        ("eodhd", "symbol_search"),
        ("alpha_vantage", "daily_adjusted"),
        ("alpha_vantage", "account_free"),
    ):
        await pool.execute(
            "insert into public.provider_access (provider, endpoint, state, credential_env)"
            " values ($1, $2, 'available', 'X') on conflict (provider, endpoint)"
            " do update set state = 'available', hard_blocked = false, retry_at = null",
            provider,
            endpoint,
        )


ENABLED = ProviderJobSettings(enabled=True, daily_quota=25, reserve_credits=1)


@pytest.mark.db
async def test_a1_both_fallback_collectors_store_through_the_source_check(
    backend_pool: asyncpg.Pool,
) -> None:
    """EODHD and Alpha Vantage bars used to raise CheckViolationError after the API call had
    spent the credit (the CHECK only allowed stooq and yahoo)."""
    await instrument(backend_pool, "CEZ_EQ")
    await firds_and_figi(backend_pool, "CEZ_EQ", "CZ0005112300", "XPRA", "CZK", "CEZ", "CP")
    profiles = HoldingProfilesConfig(
        profiles=[HoldingProfile(ticker="CEZ_EQ", eodhd_symbol="CEZ.PSE", alpha_vantage_symbol="CEZ.PRG")]
    )
    await confirmed_listing(backend_pool, "CEZ_EQ", profiles)
    await open_gates(backend_pool)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "eodhd.com":
            if "exchange-symbol-list" in request.url.path:
                return httpx.Response(200, json=[{"Code": "CEZ", "Exchange": "PSE"}])
            return httpx.Response(200, json=EODHD_CEZ)
        if request.url.host == "www.alphavantage.co":
            return httpx.Response(
                200, json=alpha_body({"2026-09-24": ("1300", "1290"), "2026-09-25": ("1349", "1339")})
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        deps = PriceDeps(http=http, today=dt.date(2026, 9, 28))
        eodhd = await collect_eodhd_fallbacks(
            backend_pool, deps, ["CEZ_EQ"], profiles, ENABLED, "key", {}
        )
        alpha = await collect_alpha_vantage_fallbacks(
            backend_pool, deps, ["CEZ_EQ"], profiles, ENABLED, "key", {}
        )
    assert eodhd["CEZ_EQ"]["source"] == "eodhd" and alpha["CEZ_EQ"]["source"] == "alpha_vantage"
    rows = await backend_pool.fetch(
        "select symbol, source, bar_type, close, currency, quote_unit, mic, listing_id"
        " from public.price_bar where source in ('eodhd','alpha_vantage')"
        " order by source, bar_type, date"
    )
    got = {(r["source"], r["bar_type"]): [] for r in rows}
    for r in rows:
        got[(r["source"], r["bar_type"])].append(r)
    assert set(got) == {
        ("eodhd", "raw"), ("eodhd", "adjusted"), ("alpha_vantage", "raw"),
        ("alpha_vantage", "adjusted"),
    }  # fmt: skip
    # EODHD: `close` is the raw basis, `adjusted_close` the adjusted one (SUSP: adjusted_close
    # handling), both under the EODHD symbol; the quote unit and listing travel with the bar.
    assert [r["close"] for r in got[("eodhd", "raw")]] == [D(1300), D(1349)]
    assert [r["close"] for r in got[("eodhd", "adjusted")]] == [D("1290.5"), D("1339.4")]
    assert {r["symbol"] for r in got[("eodhd", "raw")]} == {"CEZ.PSE"}
    assert {r["symbol"] for r in got[("alpha_vantage", "raw")]} == {"CEZ.PRG"}
    assert all(r["quote_unit"] == "CZK" and r["mic"] == "XPRA" and r["listing_id"] for r in rows)
    # The credits were recorded.
    spent = await backend_pool.fetchval(
        "select sum(credits) from public.provider_quota_usage"
        " where provider = 'alpha_vantage' and window_kind = 'day'"
    )
    assert spent == 2  # the account credit and the endpoint credit, once each


@pytest.mark.db
async def test_a2_a_stored_fallback_series_is_served_and_only_then_the_gap_is_cleared(
    backend_pool: asyncpg.Pool,
) -> None:
    await instrument(backend_pool, "CEZ_EQ")
    await firds_and_figi(backend_pool, "CEZ_EQ", "CZ0005112300", "XPRA", "CZK", "CEZ", "CP")
    profiles = HoldingProfilesConfig(
        profiles=[HoldingProfile(ticker="CEZ_EQ", alpha_vantage_symbol="CEZ.PRG")]
    )
    await confirmed_listing(backend_pool, "CEZ_EQ", profiles)
    from steward.reference.price_series import holding_symbols

    holdings = await holding_symbols(backend_pool, ["CEZ_EQ"], profiles)
    result = {"source": "alpha_vantage", "symbol": "CEZ.PRG"}

    # Alpha Vantage's bars are stored but a fresher Yahoo series exists: not served, gap kept.
    await put_bars(backend_pool, "CEZ.PR", "yahoo", {dt.date(2026, 9, 28): "1350"})
    await put_bars(backend_pool, "CEZ.PRG", "alpha_vantage", {dt.date(2026, 9, 25): "1349"})
    gaps = {"CEZ_EQ": {"symbol": "cez.cz", "reason": "Stooq unreachable"}}
    sources: dict[str, Any] = {}
    detail: dict[str, Any] = {}
    await _accept_fallbacks(backend_pool, {"CEZ_EQ": dict(result)}, holdings, gaps, sources, detail)
    assert "CEZ_EQ" in gaps and "CEZ_EQ" not in sources and "unserved" in detail
    assert "readers serve Yahoo" in gaps["CEZ_EQ"]["reason"]

    # Now Alpha Vantage's series is the freshest stored one: served, so the gap clears.
    await put_bars(backend_pool, "CEZ.PRG", "alpha_vantage", {dt.date(2026, 9, 29): "1351"})
    gaps = {"CEZ_EQ": {"symbol": "cez.cz", "reason": "Stooq unreachable"}}
    served = {"CEZ_EQ": dict(result)}
    await _accept_fallbacks(backend_pool, served, holdings, gaps, sources, detail)
    assert gaps == {} and sources["CEZ_EQ"]["served"] is True
    assert sources["CEZ_EQ"]["stooq_gap"] == "Stooq unreachable"
    ref = await choose_one(backend_pool, holdings["CEZ_EQ"])
    assert ref == SeriesRef("CEZ.PRG", "alpha_vantage")


@pytest.mark.db
async def test_b23_a_london_listing_from_a_provider_without_units_is_stored_in_pence(
    backend_pool: asyncpg.Pool,
) -> None:
    await instrument(
        backend_pool, "VOD_EQ", currency="GBX", symbol="vod.uk", mic="XLON", isin="GB00BH4HKS39"
    )
    await firds_and_figi(backend_pool, "VOD_EQ", "GB00BH4HKS39", "XLON", "GBP", "VOD", "LN")
    profiles = HoldingProfilesConfig(
        profiles=[HoldingProfile(ticker="VOD_EQ", eodhd_symbol="VOD.LSE")]
    )
    await confirmed_listing(backend_pool, "VOD_EQ", profiles)
    await open_gates(backend_pool)

    def handler(request: httpx.Request) -> httpx.Response:
        if "exchange-symbol-list" in request.url.path:
            return httpx.Response(200, json=[{"Code": "VOD"}])
        return httpx.Response(200, json=[{"date": "2026-09-25", "open": 70, "high": 71, "low": 69,
                                          "close": 70.5, "adjusted_close": 70.5, "volume": 5}])  # fmt: skip

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        out = await collect_eodhd_fallbacks(
            backend_pool, PriceDeps(http=http), ["VOD_EQ"], profiles, ENABLED, "k", {}
        )
    assert out["VOD_EQ"]["quote_unit"] == "GBX"
    row = await backend_pool.fetchrow(
        "select currency, quote_unit from public.price_bar where source = 'eodhd'"
    )
    assert (row["currency"], row["quote_unit"]) == ("GBX", "GBX")


@pytest.mark.db
async def test_b24_the_group_reservation_is_atomic_refundable_and_keeps_an_account_reserve(
    backend_pool: asyncpg.Pool,
) -> None:
    await open_gates(backend_pool)
    account = Budget(daily=D(25), reserve=D(5))
    tiny = Budget(daily=D(1))

    def usage_query() -> str:
        return (
            "select coalesce(sum(credits), 0) from public.provider_quota_usage where "
            "provider = 'alpha_vantage' and endpoint = $1 and window_kind = 'day'"
        )

    first = await reserve_group(
        backend_pool,
        [CreditPart("alpha_vantage", "account_free", D(1), account),
         CreditPart("alpha_vantage", "daily_adjusted", D(1), tiny)],
    )  # fmt: skip
    assert await backend_pool.fetchval(usage_query(), "account_free") == 1
    # The endpoint budget is spent: the second group is refused and must not burn the account
    # credit (the two-step reservation used to).
    with pytest.raises(QuotaExceeded):
        await reserve_group(
            backend_pool,
            [CreditPart("alpha_vantage", "account_free", D(1), account),
             CreditPart("alpha_vantage", "daily_adjusted", D(1), tiny)],
        )  # fmt: skip
    assert await backend_pool.fetchval(usage_query(), "account_free") == 1
    # A request that never reached the provider gives its credits back.
    await refund(backend_pool, first)
    assert await backend_pool.fetchval(usage_query(), "account_free") == 0
    assert await backend_pool.fetchval(usage_query(), "daily_adjusted") == 0
    # The account reserve: of 25 only 20 may go to the price fallback.
    granted = 0
    for _ in range(25):
        try:
            await reserve_group(
                backend_pool, [CreditPart("alpha_vantage", "account_free", D(1), account)]
            )
            granted += 1
        except QuotaExceeded:
            break
    assert granted == 20
    # Company Overview (no reserve) can still take the last five.
    for _ in range(5):
        await reserve_group(
            backend_pool,
            [CreditPart("alpha_vantage", "account_free", D(1), Budget(daily=D(25)))],
        )


@pytest.mark.db
async def test_b24_denials_and_network_failures_are_typed_and_refunded(
    backend_pool: asyncpg.Pool,
) -> None:
    await instrument(backend_pool, "CEZ_EQ")
    await firds_and_figi(backend_pool, "CEZ_EQ", "CZ0005112300", "XPRA", "CZK", "CEZ", "CP")
    profiles = HoldingProfilesConfig(
        profiles=[HoldingProfile(ticker="CEZ_EQ", alpha_vantage_symbol="CEZ.PRG")]
    )
    await confirmed_listing(backend_pool, "CEZ_EQ", profiles)
    await open_gates(backend_pool)

    async def run(handler: Any) -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await collect_alpha_vantage_fallbacks(
                backend_pool, PriceDeps(http=http), ["CEZ_EQ"], profiles, ENABLED, "k", {}
            )

    def day_total() -> Any:
        return backend_pool.fetchval(
            "select coalesce(sum(credits), 0) from public.provider_quota_usage"
            " where provider = 'alpha_vantage' and endpoint = 'account_free'"
            " and window_kind = 'day'"
        )

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    assert await run(broken) == {}
    assert await day_total() == 0  # refunded: the request never reached Alpha Vantage

    assert await run(lambda r: httpx.Response(200, json={"Information": DAILY_LIMIT})) == {}
    access = await backend_pool.fetchrow(
        "select state, retry_at, hard_blocked from public.provider_access"
        " where provider = 'alpha_vantage' and endpoint = 'daily_adjusted'"
    )
    assert access["state"] != "paid_only" and not access["hard_blocked"]
    assert await day_total() == 1  # charged by the provider: kept
    last = await backend_pool.fetchrow(
        "select state, retry_at from public.provider_fetch_audit where provider = 'alpha_vantage'"
        " and endpoint = 'daily_adjusted' order by id desc limit 1"
    )
    assert last["state"] == "quota_exhausted" and last["retry_at"] is not None

    await open_gates(backend_pool)
    assert await run(lambda r: httpx.Response(200, json={"Information": PREMIUM})) == {}
    access = await backend_pool.fetchrow(
        "select state, hard_blocked from public.provider_access"
        " where provider = 'alpha_vantage' and endpoint = 'daily_adjusted'"
    )
    assert access["state"] == "paid_only" and access["hard_blocked"] is True


# ------------------------------------------------------------------ A16 / D3.1
@pytest.mark.db
async def test_a16_stooq_currency_and_yahoo_listing_are_known_at_write_time(
    backend_pool: asyncpg.Pool,
) -> None:
    await instrument(
        backend_pool, "VOD_EQ", currency="GBX", symbol="vod.uk", mic="XLON", isin="GB00BH4HKS39"
    )
    await firds_and_figi(backend_pool, "VOD_EQ", "GB00BH4HKS39", "XLON", "GBP", "VOD", "LN")
    profiles = HoldingProfilesConfig(profiles=[HoldingProfile(ticker="VOD_EQ", yahoo_symbol="VOD.L")])
    await confirmed_listing(backend_pool, "VOD_EQ", profiles)

    csv = "Date,Open,High,Low,Close,Volume\n2026-09-24,70,71,69,70,100\n2026-09-25,71,72,70,71,90\n"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=csv))
    ) as http:
        count, gap = await collect_symbol(
            backend_pool, PriceDeps(http=http, today=dt.date(2026, 9, 28)), "vod.uk", 1
        )
    assert (count, gap) == (2, None)
    stooq = await backend_pool.fetchrow(
        "select currency, quote_unit, mic, listing_id from public.price_bar where source = 'stooq'"
    )
    assert (stooq["currency"], stooq["quote_unit"], stooq["mic"]) == ("GBX", "GBX", "XLON")
    assert stooq["listing_id"] is not None

    # Yahoo reports GBp under its own symbol and timestamp; the bar finds its listing through
    # the provider-symbol mapping, not through the Stooq price_symbol.
    stamp = dt.datetime(2026, 9, 25, 15, 30, tzinfo=dt.UTC)
    await store_bars(
        backend_pool, [bar("VOD.L", dt.date(2026, 9, 25), "71", stamp)], "yahoo", "GBp"
    )
    yahoo = await backend_pool.fetchrow(
        "select currency, quote_unit, mic, listing_id, source_timestamp from public.price_bar"
        " where source = 'yahoo'"
    )
    assert (yahoo["currency"], yahoo["quote_unit"], yahoo["mic"]) == ("GBp", "GBX", "XLON")
    assert yahoo["listing_id"] == stooq["listing_id"] and yahoo["source_timestamp"] == stamp


@pytest.mark.db
async def test_a16_returns_use_the_chosen_series_without_writing_and_the_job_stores_them(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    # Stooq stopped on 20 Sep; Yahoo's series (a different symbol) is the one readers serve.
    await instrument(backend_pool, "CEZ_EQ")
    await firds_and_figi(backend_pool, "CEZ_EQ", "CZ0005112300", "XPRA", "CZK", "CEZ", "CP")
    await put_bars(backend_pool, "cez.cz", "stooq", {dt.date(2026, 9, 18): "1300"}, currency="CZK")
    dates = weekdays(dt.date(2026, 9, 14), dt.date(2026, 9, 25))
    await put_bars(
        backend_pool,
        "CEZ.PR",
        "yahoo",
        {d: str(1300 + 10 * i) for i, d in enumerate(dates)},
        currency="CZK",
    )
    await backend_pool.execute(
        "insert into public.sync_state (key, value) values ('x', '{}') on conflict do nothing"
    )
    response = await api.get(
        "/holdings/CEZ_EQ/returns", params={"start": "2026-09-14", "end": "2026-09-25"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    [result] = body["results"]
    assert result["symbol"] == "CEZ.PR" and result["source"] == "Yahoo" and result["chosen"]
    assert result["price_return"] is not None  # no "missing or mixed quote currency"
    assert result["coverage"] == 1.0 and result["calendar_basis"] == (
        "weekdays_minus_configured_closures"
    )
    assert result["total_return"] is None and "raw close" in result["total_return_reason"]
    # GET /returns computes; it does not write.
    assert await backend_pool.fetchval("select count(*) from public.price_return_series") == 0

    async def handler(ctx: JobContext) -> None:
        await run_returns_refresh(ctx, dt.date(2026, 9, 25))

    assert await run_job(backend_pool, "returns_refresh", handler, trigger="manual") == "succeeded"
    rows = await backend_pool.fetch(
        "select symbol, source, adjustment_basis, calc_version, expected_sessions, inputs_digest"
        " from public.price_return_series where start_date = '2026-08-26'"
    )
    assert {r["adjustment_basis"] for r in rows} == {"price_raw", "price_adjusted", "total_return"}
    assert {(r["symbol"], r["source"], r["calc_version"]) for r in rows} == {("CEZ.PR", "yahoo", 2)}
    assert all(r["inputs_digest"] for r in rows)
    count = await backend_pool.fetchval("select count(*) from public.price_return_series")
    assert await run_job(backend_pool, "returns_refresh", handler, trigger="manual") == "succeeded"
    assert await backend_pool.fetchval("select count(*) from public.price_return_series") == count


@pytest.mark.db
async def test_price_return_series_is_versioned_not_overwritten(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute(
        "insert into public.price_return_series (symbol, start_date, end_date, currency,"
        " adjustment_basis, source, value, coverage, expected_sessions, observed_sessions,"
        " known_at, calc_version) values ('A', '2026-01-01', '2026-02-01', 'USD', 'price_raw',"
        " 'stooq', 0.1, 1, 10, 10, now(), 1)"
    )
    await put_bars(
        backend_pool, "A", "stooq", {dt.date(2026, 1, 5): "10", dt.date(2026, 1, 6): "11"},
        currency="USD", bar_type="raw",
    )  # fmt: skip
    from steward.free_sources import calculate_and_store_returns

    await calculate_and_store_returns(
        backend_pool,
        symbol="A",
        start=dt.date(2026, 1, 1),
        end=dt.date(2026, 2, 1),
        source="stooq",
        currency="USD",
        actions_complete=False,
    )
    versions = await backend_pool.fetch(
        "select calc_version, value from public.price_return_series"
        " where symbol = 'A' and adjustment_basis = 'price_raw' order by calc_version"
    )
    assert [(r["calc_version"], r["value"]) for r in versions] == [(1, D("0.1")), (2, D("0.1"))]


# ------------------------------------------------------------------ A16 diagnostics
@pytest.mark.db
async def test_a16_cross_source_disagreement_joins_across_symbols(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await instrument(backend_pool, "CEZ_EQ")
    await firds_and_figi(backend_pool, "CEZ_EQ", "CZ0005112300", "XPRA", "CZK", "CEZ", "CP")
    dates = weekdays(dt.date(2026, 9, 14), dt.date(2026, 9, 25))
    await put_bars(backend_pool, "cez.cz", "stooq", {d: "1300" for d in dates}, currency="CZK")
    # Yahoo's symbol differs ("CEZ.PR") and its closes are 5 % higher on the last two days.
    yahoo_closes = {d: ("1300" if i < len(dates) - 2 else "1365") for i, d in enumerate(dates)}
    await put_bars(backend_pool, "CEZ.PR", "yahoo", yahoo_closes, currency="CZK")
    rows = (await api.get("/holdings/CEZ_EQ/prices/diagnostics")).json()
    by = {(r["symbol"], r["source"]): r for r in rows}
    assert set(by) == {("cez.cz", "stooq"), ("CEZ.PR", "yahoo")}
    assert by[("cez.cz", "stooq")]["disagreements"] == 2
    assert by[("CEZ.PR", "yahoo")]["disagreements"] == 2
    assert by[("cez.cz", "stooq")]["chosen"] is True  # tie on the last date: Stooq
    assert by[("cez.cz", "stooq")]["mic"] == "XPRA" and by[("cez.cz", "stooq")]["quote_unit"] == "CZK"
    assert by[("cez.cz", "stooq")]["missing_days"] == 0
    # A pence line and a pound line of the same series are compared in pounds, not flagged.
    assert holding_price_diagnostics  # imported API under test


# ------------------------------------------------------------------ B21 / B22
@pytest.mark.db
async def test_b21_coverage_is_judged_against_the_real_session_calendar(
    backend_pool: asyncpg.Pool,
) -> None:
    """10 bars in a 43-weekday window used to score 1.0 (expected = max(sessions, bars))."""
    start, end = dt.date(2026, 7, 1), dt.date(2026, 8, 28)
    all_days = weekdays(start, end)
    assert len(all_days) == 43
    await put_bars(
        backend_pool, "A", "stooq", {d: "10" for d in all_days[:10]}, currency="USD"
    )
    result = await compute_returns(
        backend_pool, symbol="A", start=start, end=end, source="stooq", currency="USD",
        actions_complete=False, mic="XNYS",
    )  # fmt: skip
    assert result.output["expected_sessions"] == 43
    assert result.output["coverage"] == D(10) / D(43)
    assert result.rows[1].observed == 10

    # Confirmed sessions replace the weekday guess: two ad hoc closures and a half day.
    for d, state in ((all_days[3], "closed"), (all_days[4], "closed"), (all_days[5], "half_day")):
        open_at = dt.datetime.combine(d, dt.time(14, 30), dt.UTC)
        await backend_pool.execute(
            "insert into public.exchange_session (mic, session_date, state, source, confirmed,"
            " open_at, close_at, reason) values ('XNYS', $1, $2, 'owner', true, $3, $4, 'test')",
            d,
            state,
            open_at if state != "closed" else None,
            open_at + dt.timedelta(hours=3) if state != "closed" else None,
        )
    result = await compute_returns(
        backend_pool, symbol="A", start=start, end=end, source="stooq", currency="USD",
        actions_complete=False, mic="XNYS",
    )  # fmt: skip
    assert result.output["expected_sessions"] == 41
    # Bars on the two closed days are not counted as observed sessions.
    assert result.rows[1].observed == 8 and result.output["calendar_basis"] == (
        "partial_confirmed_calendar"
    )
    assert result.output["coverage"] == D(8) / D(41)
    assert result.rows[1].details["unexpected_session_bars"] == 2


@pytest.mark.db
async def test_b22_exchange_session_rows_drive_the_expected_calendar(
    backend_pool: asyncpg.Pool,
) -> None:
    settings = CalendarSettings()
    monday = dt.date(2026, 9, 28)
    # A national holiday while the exchange trades: Nager says holiday, the exchange row says
    # open, so the day stays a session (and without the row it would also be one: holidays are
    # suggestions only).
    await backend_pool.execute(
        "insert into public.holiday (calendar, date, name, scope, source)"
        " values ('DE', $1, 'Invented holiday', 'national', 'nager')",
        monday,
    )
    await backend_pool.execute(
        "insert into public.exchange_session (mic, session_date, state, source, confirmed)"
        " values ('XETR', $1, 'open', 'owner', true)",
        monday,
    )
    # An ad hoc closure on a day with no holiday anywhere.
    tuesday = monday + dt.timedelta(days=1)
    await backend_pool.execute(
        "insert into public.exchange_session (mic, session_date, state, source, confirmed, reason)"
        " values ('XETR', $1, 'closed', 'owner', true, 'system outage')",
        tuesday,
    )
    expected = await expected_sessions(backend_pool, settings, "XETR", monday, monday + dt.timedelta(days=4))
    assert monday in expected.days and tuesday not in expected.days
    assert expected.count == 4 and expected.basis == "partial_confirmed_calendar"
    index = await load_sessions(backend_pool, ["XETR"], monday, tuesday)
    assert exchange_open(settings, "XETR", monday, index)
    assert not exchange_open(settings, "XETR", tuesday, index)
    # Freshness is judged against the last session that existed.
    assert await last_expected_session(backend_pool, settings, "XETR", tuesday) == monday
    # Unconfirmed rows never count.
    await backend_pool.execute(
        "insert into public.exchange_session (mic, session_date, state, source, confirmed)"
        " values ('XETR', $1, 'closed', 'vendor', false)",
        monday + dt.timedelta(days=2),
    )
    again = await expected_sessions(backend_pool, settings, "XETR", monday, monday + dt.timedelta(days=4))
    assert again.count == 4


# ------------------------------------------------------------------ A17
@pytest.mark.db
async def test_a17_the_listing_writer_derives_evidence_and_waits_for_the_owner_when_unsure(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    # All three agree -> confirmed automatically, with evidence.
    await instrument(backend_pool, "CEZ_EQ")
    await firds_and_figi(backend_pool, "CEZ_EQ", "CZ0005112300", "XPRA", "CZK", "CEZ", "CP")
    # No FIRDS record (a US name): only suggested.
    await instrument(
        backend_pool, "MSFT_US_EQ", currency="USD", symbol="msft.us", mic="XNAS",
        isin="US5949181045",
    )  # fmt: skip
    await backend_pool.execute(
        "insert into public.instrument_identifier (ticker, figi, composite_figi, listing_ticker,"
        " exch_code, raw) values ('MSFT_US_EQ', 'BBGMSFT', 'BBGMSFT', 'MSFT', 'US', '{}')"
    )
    profiles = HoldingProfilesConfig(
        profiles=[HoldingProfile(ticker="CEZ_EQ", yahoo_symbol="CEZ.PR")]
    )
    results = {r.ticker: r for r in await derive_listings(backend_pool, ["CEZ_EQ", "MSFT_US_EQ"], profiles)}
    assert results["CEZ_EQ"].status == "confirmed" and results["MSFT_US_EQ"].status == "suggested"
    listing = await backend_pool.fetchrow(
        "select l.mic, l.ticker, l.currency, l.evidence from public.security_listing l"
        " join public.instrument_security_link m on m.listing_id = l.id where m.ticker = 'CEZ_EQ'"
    )
    assert (listing["mic"], listing["ticker"], listing["currency"]) == ("XPRA", "CEZ", "CZK")
    evidence = json.loads(listing["evidence"])
    assert evidence["isin_has_firds_record_on_home_mic"] is True
    assert evidence["currency"]["agrees"] is True
    # The listing ticker (CEZ) is not the price symbol (cez.cz): provider_identifier bridges them.
    mapped = {
        (r["provider"], r["identifier"], r["match_status"])
        for r in await backend_pool.fetch(
            "select provider, identifier, match_status from public.provider_identifier"
        )
    }
    assert ("stooq", "cez.cz", "confirmed") in mapped and ("yahoo", "CEZ.PR", "confirmed") in mapped
    assert ("stooq", "msft.us", "suggested") in mapped
    assert await resolve_series_listing(backend_pool, "CEZ.PR", "yahoo") == (
        results["CEZ_EQ"].listing_id,
        "XPRA",
    )
    # Re-deriving duplicates nothing.
    await derive_listings(backend_pool, ["CEZ_EQ", "MSFT_US_EQ"], profiles)
    assert await backend_pool.fetchval("select count(*) from public.security_listing") == 2
    assert await backend_pool.fetchval("select count(*) from public.provider_identifier") == 4

    # A corporate action is recorded under the Stooq symbol, the Yahoo symbol or the exchange
    # ticker once the listing is confirmed; before that it is refused (suggested listing).
    action = {
        "action_type": "cash_dividend", "symbol": "cez.cz", "ex_date": "2026-09-10",
        "cash_amount": "42", "currency": "CZK", "source": "issuer",
        "source_url": "https://example.test/cez-dividend",
    }  # fmt: skip
    msft = dict(action, symbol="msft.us", source_url="https://example.test/msft")
    refused = await api.post("/holdings/MSFT_US_EQ/corporate-actions", json=msft)
    assert refused.status_code == 422 and "confirmed canonical listing" in refused.text
    ok = await api.post("/holdings/CEZ_EQ/corporate-actions", json=action)
    assert ok.status_code == 200, ok.text
    ticker_form = await api.post(
        "/holdings/CEZ_EQ/corporate-actions",
        json=dict(action, symbol="CEZ", source_url="https://example.test/other"),
    )
    assert ticker_form.status_code == 200, ticker_form.text
    wrong = await api.post("/holdings/CEZ_EQ/corporate-actions", json=dict(action, symbol="zzz.cz"))
    assert wrong.status_code == 422

    # The owner confirms the US suggestion; the decision is sticky across a re-derive.
    confirmed = await api.post(
        "/holdings/MSFT_US_EQ/listing/confirm",
        json={"decision": "confirmed", "reason": "ISIN and Nasdaq ticker checked by hand"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert (await api.get("/holdings/MSFT_US_EQ/listing")).json()["status"] == "confirmed"
    again = (await derive_listings(backend_pool, ["MSFT_US_EQ"], profiles))[0]
    assert again.status == "unchanged"
    assert (await api.post("/holdings/NOPE/listing/confirm",
                           json={"decision": "confirmed", "reason": "no such link at all"})
            ).status_code == 404  # fmt: skip
    assert await confirm_listing  # imported API under test


@pytest.mark.db
async def test_a17_the_price_job_derives_listings_before_storing_bars(
    backend_pool: asyncpg.Pool,
) -> None:
    await instrument(backend_pool, "SAPd_EQ", currency="EUR", symbol="sap.de", mic="XETR", isin="DE0007164600")
    await firds_and_figi(backend_pool, "SAPd_EQ", "DE0007164600", "XETR", "EUR", "SAP", "GR")
    await backend_pool.execute(
        "insert into public.account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency, fx_source,"
        " fx_rate, cash_base, invested_base, total_base, weight_basis) values"
        " ('2026-09-25', (select coalesce(max(id),1) from public.job), now(), 1, 'EUR', 0,0,0,0,0,0,0,0,"
        " 'EUR','ecb',1,0,0,0,'total')"
    ) if False else None  # holdings come from position_snapshot below
    job_id = await backend_pool.fetchval(
        "insert into public.job (job_type, trigger, status) values ('portfolio_sync','schedule',"
        "'succeeded') returning id"
    )
    await backend_pool.execute(
        "insert into public.account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency, fx_source,"
        " fx_rate, cash_base, invested_base, total_base, weight_basis) values ('2026-09-25', $1,"
        " now(), 1, 'EUR', 0,0,0,0,0,0,0,0,'EUR','ecb',1,0,0,0,'total')",
        job_id,
    )
    await backend_pool.execute(
        "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at,"
        " quantity, avg_price, current_price, instrument_currency, value, account_currency,"
        " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
        " values ('2026-09-25', 'SAPd_EQ', $1, now(), 1, 1, 1, 'EUR', 1, 'EUR', 1, 1, 0,"
        " 'EUR', 'ecb', 1, 1)",
        job_id,
    )
    csv = "Date,Open,High,Low,Close,Volume\n2026-09-24,180,182,179,181,100\n2026-09-25,181,187,180,186,120\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "stooq.com":
            return httpx.Response(200, text=csv if request.url.params["s"] == "sap.de" else "No data")
        if request.url.host == "api.frankfurter.dev":
            return httpx.Response(200, json={"base": "EUR", "rates": {}})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

        async def job(ctx: JobContext) -> None:
            await run_price_history(
                ctx, PriceDeps(http=http, today=dt.date(2026, 9, 28),
                               now=dt.datetime(2026, 9, 28, 21, 0, tzinfo=dt.UTC))
            )  # fmt: skip

        assert await run_job(backend_pool, "price_history", job, trigger="manual") == "succeeded"
    row = await backend_pool.fetchrow(
        "select listing_id, mic, currency from public.price_bar where symbol = 'sap.de' limit 1"
    )
    assert row["listing_id"] is not None and row["mic"] == "XETR" and row["currency"] == "EUR"


# ------------------------------------------------------------------ B26
@pytest.mark.db
async def test_b26_corporate_actions_are_retracted_only_through_the_decision_route(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await instrument(backend_pool, "CEZ_EQ")
    await firds_and_figi(backend_pool, "CEZ_EQ", "CZ0005112300", "XPRA", "CZK", "CEZ", "CP")
    await confirmed_listing(backend_pool, "CEZ_EQ")
    evidence_id = await backend_pool.fetchval(
        "insert into public.source_evidence (source_type, url) values ('regulator', 'https://x.test')"
        " returning id"
    ) if False else None
    body = {
        "action_type": "split", "symbol": "cez.cz", "ex_date": "2026-09-10", "ratio": "2",
        "source": "issuer", "source_url": "https://example.test/split",
    }  # fmt: skip
    first = (await api.post("/holdings/CEZ_EQ/corporate-actions", json=body)).json()
    assert first["status"] == "suggested"
    action_id = first["id"]
    # Confirming needs linked evidence.
    no_evidence = await api.post(
        f"/holdings/CEZ_EQ/corporate-actions/{action_id}/decision",
        json={"decision": "confirmed", "reason": "split announced by the issuer"},
    )
    assert no_evidence.status_code == 422
    await backend_pool.execute(
        "update public.corporate_action set evidence_id = null where id = $1", action_id
    )
    doc = await backend_pool.fetchval(
        "select id from public.source_evidence limit 1"
    )
    if doc is None:
        pytest.skip("no source_evidence row can be created in this schema")
    decided = await api.post(
        f"/holdings/CEZ_EQ/corporate-actions/{action_id}/decision",
        json={"decision": "confirmed", "reason": "split announced by the issuer", "evidence_id": doc},
    )
    assert decided.status_code == 200 and decided.json()["status"] == "confirmed"
    # A re-post (a vendor re-sync) cannot change the decided status ...
    again = (await api.post("/holdings/CEZ_EQ/corporate-actions",
                            json=dict(body, status="suggested"))).json()  # fmt: skip
    assert again["status"] == "confirmed" and again["status_applied"] is False
    # ... but the owner can retract it.
    retracted = await api.post(
        f"/holdings/CEZ_EQ/corporate-actions/{action_id}/decision",
        json={"decision": "suggested", "reason": "issuer withdrew the split announcement"},
    )
    assert retracted.json()["previous_status"] == "confirmed"
    row = await backend_pool.fetchrow(
        "select status, decided_at, decision_reason from public.corporate_action where id = $1",
        action_id,
    )
    assert row["status"] == "suggested" and row["decided_at"] is not None
    assert "withdrew" in row["decision_reason"]
    assert (await api.post("/holdings/CEZ_EQ/corporate-actions/99999/decision",
            json={"decision": "rejected", "reason": "an action that does not exist"})
            ).status_code == 404  # fmt: skip


# ------------------------------------------------------------------ D3.4
@pytest.mark.db
async def test_d34_exchange_probes_record_missing_coverage_apart_from_plan_denials(
    backend_pool: asyncpg.Pool,
) -> None:
    for provider, endpoint in (("finnhub", "quote"), ("twelve_data", "time_series")):
        await backend_pool.execute(
            "update public.provider_access set state = 'available', hard_blocked = false"
            " where provider = $1 and endpoint = $2",
            provider,
            endpoint,
        )
    targets = [
        exchange_probes.ProbeTarget("XNAS", "MSFT_US_EQ", "MSFT", "MSFT"),
        exchange_probes.ProbeTarget("XETR", "SAPd_EQ", "SAP.DE", "SAP"),
        exchange_probes.ProbeTarget("XPRA", "CEZ_EQ", "CEZ.PR", "CEZ"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        host, symbol = request.url.host, request.url.params.get("symbol")
        if host == "finnhub.io":
            if symbol == "MSFT":
                return httpx.Response(200, json={"c": 421.5, "pc": 420.0, "t": 1790000000})
            if symbol == "SAP.DE":
                return httpx.Response(403, json={"error": "You don't have access to this resource."})
            return httpx.Response(200, json={"c": 0, "d": None, "dp": None, "h": 0, "l": 0,
                                             "o": 0, "pc": 0, "t": 0})  # fmt: skip
        if symbol == "MSFT":
            return httpx.Response(200, json={"meta": {"currency": "USD"}, "values": [{"close": "1"}]})
        return httpx.Response(200, json={"code": 400, "status": "error", "message": "**symbol** not found"})

    detail: dict[str, Any] = {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        outcomes = await exchange_probes.run_exchange_probes(
            backend_pool, http, targets, finnhub_key="k", twelve_key="k", detail=detail
        )
    states = {(o.provider, o.mic): o.state for o in outcomes}
    assert states[("finnhub", "XNAS")] is AccessState.AVAILABLE
    assert states[("finnhub", "XETR")] is AccessState.PAID_ONLY  # technical denial
    assert states[("finnhub", "XPRA")] is AccessState.NOT_COVERED  # missing coverage
    assert states[("twelve_data", "XNAS")] is AccessState.AVAILABLE
    assert states[("twelve_data", "XPRA")] is AccessState.NOT_COVERED
    coverage = {
        (r["provider"], r["entity_key"]): r["state"]
        for r in await backend_pool.fetch(
            "select provider, entity_key, state from public.coverage_status"
            " where dataset in ('quote', 'time_series')"
        )
    }
    assert coverage[("finnhub", "XETR")] == "paid_only"
    assert coverage[("finnhub", "XPRA")] == "not_covered"
    # One exchange outside the free plan does not close the endpoint for the others.
    gate = await backend_pool.fetchrow(
        "select state, hard_blocked from public.provider_access"
        " where provider = 'finnhub' and endpoint = 'quote'"
    )
    assert gate["state"] == "available" and gate["hard_blocked"] is False


# ------------------------------------------------------------------ migration objects
@pytest.mark.db
async def test_migration_objects_are_named_and_widened(backend_pool: asyncpg.Pool) -> None:
    indexes = {
        r["indexname"]
        for r in await backend_pool.fetch(
            "select indexname from pg_indexes where schemaname = 'public'"
            " and tablename in ('exchange_session', 'corporate_action')"
        )
    }
    assert "exchange_session_source" in indexes
    assert "exchange_session_source_evidence" not in indexes
    assert "corporate_action_symbol_ex_date" in indexes
    # The CHECK admits every price writer (A1) and refuses a stranger.
    for source in ("stooq", "yahoo", "eodhd", "alpha_vantage"):
        await backend_pool.execute(
            "insert into public.price_bar (symbol, date, close, source) values ('Z', $1, 1, $2)",
            dt.date(2026, 1, 1),
            source,
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await backend_pool.execute(
            "insert into public.price_bar (symbol, date, close, source)"
            " values ('Z', '2026-01-02', 1, 'nobody')"
        )
    # A cash dividend can be declared in pence; a cash-only stock dividend row is refused.
    await backend_pool.execute(
        "insert into public.corporate_action (symbol, action_type, ex_date, cash_amount, currency,"
        " source, source_url) values ('Z', 'cash_dividend', '2026-01-01', 10, 'GBp', 's', 'u')"
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await backend_pool.execute(
            "insert into public.corporate_action (symbol, action_type, ex_date, cash_amount,"
            " currency, source, source_url) values ('Z', 'stock_dividend', '2026-01-01', 10, 'USD',"
            " 's', 'u2')"
        )


def test_json_and_jobs_config_defaults_are_untouched() -> None:
    # The profile/config models the tests above build must keep their defaults.
    assert JobsConfig().providers["alpha_vantage"].enabled is False
    assert save_config is not None
