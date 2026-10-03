"""Measured release-target figures: value-weighted price coverage, global reach, provenance
completeness and identity review share. All data is seeded; nothing is fetched."""

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.coverage_scorecard_routes import router as scorecard_router
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.reference.coverage_scorecard import (
    data_reach,
    identity_review,
    price_coverage,
    provenance_completeness,
)

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
TODAY = dt.date(2026, 10, 2)  # a Friday
SNAP = dt.date(2026, 10, 1)


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.include_router(scorecard_router)

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def anonymous_api() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.include_router(scorecard_router)

    async def no_pool() -> None:
        return None

    app.dependency_overrides[get_pool] = no_pool
    app.dependency_overrides[get_pool_or_error] = no_pool
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.parametrize(
    "path",
    [
        "/portfolio/price-coverage",
        "/portfolio/identity-review",
        "/data/reach",
        "/data/provenance-completeness",
    ],
)
async def test_routes_require_the_owner(anonymous_api: httpx.AsyncClient, path: str) -> None:
    assert (await anonymous_api.get(path)).status_code == 401


async def bars(
    p: asyncpg.Pool,
    symbol: str,
    days: list[dt.date],
    *,
    currency: str | None = "USD",
    mic: str | None = None,
    bar_type: str = "adjusted",
    state: str = "available",
    source: str = "stooq",
) -> None:
    for day in days:
        await p.execute(
            "insert into public.price_bar (symbol, date, close, source, bar_type, currency, mic,"
            " access_state, quote_unit) values ($1, $2, 10, $3, $4, $5, $6, $7, $8)",
            symbol,
            day,
            source,
            bar_type,
            currency,
            mic,
            state,
            "pence" if currency == "GBX" else None,
        )


async def instrument(
    p: asyncpg.Pool,
    ticker: str,
    *,
    symbol: str | None,
    sec_type: str | None = "Common Stock",
    mic: str | None = "XNYS",
    country: str | None = "US",
) -> None:
    await p.execute(
        "insert into public.instrument (t212_ticker, name, price_symbol, security_type, home_mic,"
        " home_country) values ($1, $2, $3, $4, $5, $6)",
        ticker,
        f"{ticker} name",
        symbol,
        sec_type,
        mic,
        country,
    )


async def seed_positions(p: asyncpg.Pool, values: dict[str, int]) -> None:
    job = await p.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'manual',"
        " 'succeeded') returning id"
    )
    await p.execute(
        "insert into account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency,"
        " fx_source, fx_rate, cash_base, invested_base, total_base, weight_basis) values"
        " ($1, $2, now(), 1, 'USD', 1000, 0, 0, 0, 1000, 1000, 0, 0, 'USD', 'ecb',"
        " 1, 0, 1000, 1000, 'total')",
        SNAP,
        job,
    )
    for ticker, value in values.items():
        await p.execute(
            "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
            " avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base,"
            " weight) values ($1, $2, $3, now(), 1, 1, 1, 'USD', $4, 'USD', $4, $4, 0, 'USD',"
            " 'ecb', 1, $4, $5)",
            SNAP,
            ticker,
            job,
            Decimal(value),
            Decimal(value) / 1000,
        )


async def seed_world(p: asyncpg.Pool) -> None:
    """AAA fresh USD, BBB stale (Aug), CCC no price at all, VOD a fresh GBX (pence) line,
    DDD a close with no recorded currency."""
    await instrument(p, "AAA_US_EQ", symbol="aaa.us")
    await instrument(p, "BBB_US_EQ", symbol="bbb.us")
    await instrument(p, "CCC_US_EQ", symbol="ccc.us")
    await instrument(p, "VOD_EQ", symbol="vod.uk", mic="XLON", country="GB")
    await instrument(p, "DDD_US_EQ", symbol="ddd.us")
    await seed_positions(
        p, {"AAA_US_EQ": 600, "BBB_US_EQ": 100, "CCC_US_EQ": 50, "VOD_EQ": 200, "DDD_US_EQ": 50}
    )
    await bars(p, "aaa.us", [dt.date(2026, 9, 30), dt.date(2026, 10, 1)], mic="XNYS")
    await bars(p, "bbb.us", [dt.date(2026, 8, 3)], mic="XNYS")
    await bars(p, "vod.uk", [dt.date(2026, 10, 1)], currency="GBX", mic="XLON")
    await bars(p, "ddd.us", [dt.date(2026, 10, 1)], currency=None, mic="XNYS")
    # A raw-only series is not a usable adjusted close, and a refused bar is not a price.
    await bars(p, "ccc.us", [dt.date(2026, 10, 1)], bar_type="raw")


@pytest.mark.db
async def test_price_coverage_is_value_weighted_with_an_unknown_bucket(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_world(backend_pool)
    out = await price_coverage(backend_pool, TODAY)
    by = {h.ticker: h for h in out.holdings}
    assert by["AAA_US_EQ"].state == "usable"
    assert by["BBB_US_EQ"].state == "stale"  # a stale holding is not blended in
    assert by["BBB_US_EQ"].stale_sessions and by["BBB_US_EQ"].stale_sessions > 3
    assert by["CCC_US_EQ"].state == "no_price"  # a holding with no usable price
    assert by["CCC_US_EQ"].last_close_date is None
    assert by["DDD_US_EQ"].state == "basis_unknown"
    # A GBX listing keeps its quote code and states the major currency and the unit factor.
    vod = by["VOD_EQ"]
    assert (vod.state, vod.currency, vod.currency_major, vod.unit_factor) == (
        "usable",
        "GBX",
        "GBP",
        0.01,
    )
    assert vod.adjustment_basis == "adjusted"
    assert out.denominator_base == 1000
    assert out.usable_value_base == 800  # 600 + 200
    assert out.unknown_value_base == 200  # 100 + 50 + 50
    assert out.usable_share == pytest.approx(0.8)
    assert out.unknown_share == pytest.approx(0.2)
    assert out.target == 0.95
    assert out.meets_target is False
    assert (out.holdings_usable, out.holdings_stale, out.holdings_no_price) == (2, 1, 1)
    assert out.holdings_basis_unknown == 1
    assert by["AAA_US_EQ"].share_of_held_value == pytest.approx(0.6)


@pytest.mark.db
async def test_price_coverage_meets_the_target_and_honours_the_staleness_limit(
    backend_pool: asyncpg.Pool,
) -> None:
    await instrument(backend_pool, "AAA_US_EQ", symbol="aaa.us")
    await instrument(backend_pool, "BBB_US_EQ", symbol="bbb.us")
    await seed_positions(backend_pool, {"AAA_US_EQ": 960, "BBB_US_EQ": 40})
    await bars(backend_pool, "aaa.us", [dt.date(2026, 10, 1)], mic="XNYS")
    await bars(backend_pool, "bbb.us", [dt.date(2026, 9, 24)], mic="XNYS")
    strict = await price_coverage(backend_pool, TODAY, max_stale_sessions=3)
    assert strict.usable_share == pytest.approx(0.96)
    assert strict.meets_target is True
    assert {h.ticker: h.state for h in strict.holdings}["BBB_US_EQ"] == "stale"
    loose = await price_coverage(backend_pool, TODAY, max_stale_sessions=30)
    assert loose.usable_share == pytest.approx(1.0)


@pytest.mark.db
async def test_price_coverage_without_a_snapshot_is_empty_not_met(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    out = await price_coverage(backend_pool, TODAY)
    assert out.snapshot_date is None and out.usable_share is None
    assert out.meets_target is False and out.holdings == []
    response = await api.get("/portfolio/price-coverage")
    assert response.status_code == 200 and response.json()["meets_target"] is False


@pytest.mark.db
async def test_price_coverage_route_shape(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed_world(backend_pool)
    body = (await api.get("/portfolio/price-coverage", params={"max_stale_sessions": 5})).json()
    assert body["max_stale_sessions"] == 5 and body["target"] == 0.95
    assert body["denominator_base"] == 1000
    assert {h["ticker"] for h in body["holdings"]} >= {"AAA_US_EQ", "VOD_EQ"}
    assert (
        await api.get("/portfolio/price-coverage", params={"max_stale_sessions": 99})
    ).status_code == 422


async def seed_reach(p: asyncpg.Pool) -> None:
    await instrument(p, "AAA_US_EQ", symbol="aaa.us")
    await instrument(p, "VOD_EQ", symbol="vod.uk", mic="XLON", country="GB")
    await instrument(p, "SPY_US_EQ", symbol="spy.us", sec_type="ETP", mic="XNYS")
    await bars(
        p, "aaa.us", [dt.date(2026, 9, 29), dt.date(2026, 9, 30), dt.date(2026, 10, 1)], mic="XNYS"
    )
    await bars(p, "vod.uk", [dt.date(2026, 10, 1)], currency="GBX", mic="XLON")
    await bars(p, "spy.us", [dt.date(2026, 9, 30)], state="paid_only", mic="XNYS")
    await bars(p, "^GSPC", [dt.date(2026, 10, 1)], mic=None)
    await p.execute(
        "insert into public.fundamentals (ticker, source, document_url, figures, raw, period_end)"
        " values ('AAA_US_EQ', 'sec', 'https://sec.example/x', '{\"revenue\": 1}', '{}',"
        " '2026-06-30')"
    )
    await p.execute(
        "insert into public.coverage_status (provider, dataset, entity_key, state,"
        " available_periods, earliest_period, latest_period) values"
        " ('stooq', 'daily_price', 'aaa.us', 'available', 3, '2026-09-29', '2026-10-01'),"
        " ('eodhd', 'daily_price', 'vod.uk', 'not_covered', 0, null, null)"
    )


@pytest.mark.db
async def test_reach_is_derived_from_stored_rows_by_mic_country_class_and_metric(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed_reach(backend_pool)
    out = await data_reach(backend_pool)
    assert "held, watchlist and tested" in out.universe_note
    cells = {(c.exchange_mic, c.country, c.asset_class, c.metric): c for c in out.cells}
    aaa = cells[("XNYS", "US", "security", "price")]
    assert (aaa.tested_symbols, aaa.valid_records, aaa.history_count) == (1, 3, 3)
    assert (aaa.history_first, aaa.history_last) == (dt.date(2026, 9, 29), dt.date(2026, 10, 1))
    assert aaa.freshest_known_at is not None and aaa.coverage_states == {"available": 1}
    # A paid-only series is tested but has no valid record.
    etf = cells[("XNYS", "US", "etf", "price")]
    assert (etf.tested_symbols, etf.valid_records, etf.coverage_states) == (
        1,
        0,
        {"paid_only": 1},
    )
    assert etf.history_first is None
    # GBX line attributed to London / GB from the stored mic and the listing.
    assert cells[("XLON", "GB", "security", "price")].valid_records == 1
    # An index symbol is its own class; its exchange comes from the known index->MIC table.
    assert cells[("XNYS", "US", "index", "price")].tested_symbols == 1
    assert cells[("XNYS", "US", "security", "fundamentals")].valid_records == 1
    ledger = cells[("XNYS", "US", "security", "coverage:daily_price")]
    assert ledger.history_count == 3
    gb = cells[("XLON", "GB", "security", "coverage:daily_price")]
    assert gb.coverage_states == {"not_covered": 1} and gb.valid_records == 0
    by_mic = {(r.exchange_mic, r.metric): r for r in out.by_exchange}
    assert by_mic[("XNYS", "price")].tested_symbols == 3  # AAA, paid-only ETF, ^GSPC
    assert by_mic[("XNYS", "price")].valid_records == 4
    by_class = {(r.asset_class, r.metric): r for r in out.by_asset_class}
    assert by_class[("index", "price")].tested_symbols == 1
    assert out.tested_symbols == 4
    assert (await api.get("/data/reach")).json()["universe_note"] == out.universe_note


@pytest.mark.db
async def test_reach_on_an_empty_database_claims_nothing(backend_pool: asyncpg.Pool) -> None:
    out = await data_reach(backend_pool)
    assert out.cells == [] and out.tested_symbols == 0 and out.universe_note


@pytest.mark.db
async def test_provenance_completeness_counts_null_columns_as_missing(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await bars(backend_pool, "aaa.us", [dt.date(2026, 10, 1), dt.date(2026, 9, 30)])
    # Two forward rows, one with no provider (a null source column) and no vintage.
    for provider, vintage in (("finnhub", "2026-09-01"), (None, None)):
        await backend_pool.execute(
            "insert into public.forward_observation (ticker, kind, provider, metric,"
            " fiscal_period, value, vintage, known_at, source_url)"
            " values ('AAA', 'published_analyst_consensus', $1, 'eps', 'FY2026', 1, $2,"
            " now(), 'https://example.test/c')",
            provider,
            vintage,
        )
    out = await provenance_completeness(backend_pool)
    tables = {t.table: t for t in out.tables}
    fwd = tables["forward_observation"]
    dims = {d.dimension: d for d in fwd.dimensions}
    assert fwd.rows == 2
    assert (dims["source"].present, dims["source"].missing, dims["source"].share) == (1, 1, 0.5)
    assert dims["method"].share == 0.5
    assert dims["known_at"].share == 1.0
    assert dims["access_state"].column is None and dims["access_state"].share == 0.0
    assert fwd.meets_target is False
    assert fwd.complete_rows == 1  # carries every dimension that has a column
    price = tables["price_bar"]
    assert price.rows == 2 and price.complete_rows == 2 and price.meets_target is True
    assert tables["source_observation"].rows == 0
    assert tables["source_observation"].meets_target is False  # empty: nothing proven
    assert "source_observation" in out.empty_tables
    assert out.target == 1.0 and out.meets_target is False
    body = (await api.get("/data/provenance-completeness")).json()
    assert body["tables_total"] == len(tables) and body["meets_target"] is False


@pytest.mark.db
async def test_provenance_blank_text_counts_as_missing(backend_pool: asyncpg.Pool) -> None:
    await backend_pool.execute(
        "insert into public.forward_observation (ticker, kind, provider, metric, fiscal_period,"
        " value, vintage, known_at, source_url) values ('AAA', 'owner_scenario', '   ', 'eps',"
        " 'FY2026', 1, 'v1', now(), 'https://example.test/c')"
    )
    out = await provenance_completeness(backend_pool)
    fwd = {t.table: t for t in out.tables}["forward_observation"]
    assert {d.dimension: d.present for d in fwd.dimensions}["source"] == 0


@pytest.mark.db
async def test_identity_review_share_and_unresolved_list(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    p = backend_pool
    for t in ("OWN_EQ", "AUTO_EQ", "SUG_EQ", "NOLINK_EQ", "PATHLESS_EQ"):
        await instrument(p, t, symbol=None)
    await seed_positions(
        p, {"OWN_EQ": 400, "AUTO_EQ": 300, "SUG_EQ": 100, "NOLINK_EQ": 100, "PATHLESS_EQ": 100}
    )
    sec = await p.fetchval(
        "insert into public.security (kind, name) values ('equity', 'S') returning id"
    )
    await p.execute(
        "insert into public.instrument_security_link (ticker, security_id, status, score,"
        " decided_at, decided_by) values ('OWN_EQ', $1, 'confirmed', 1, now(), $2::uuid)",
        sec,
        OWNER,
    )
    await p.execute(
        "insert into public.instrument_security_link (ticker, security_id, status, score) values"
        " ('AUTO_EQ', $1, 'confirmed', 0.9), ('SUG_EQ', $1, 'suggested', 0.5),"
        " ('PATHLESS_EQ', null, 'confirmed', 0.9)",
        sec,
    )
    await p.execute(
        "insert into public.identity_match_decision (instrument_ticker, decision, score)"
        " values ('SUG_EQ', 'suggested', 0.5)"
    )
    out = await identity_review(p)
    assert (out.held_positions, out.reviewed, out.owner_reviewed, out.auto_confirmed) == (
        5,
        2,
        1,
        1,
    )
    assert out.reviewed_share == pytest.approx(0.4)
    assert out.meets_target is False and out.accounted_share == 1.0
    assert out.unresolved_count == 3
    by = {u.ticker: u for u in out.unresolved}
    assert by["SUG_EQ"].link_status == "suggested" and by["SUG_EQ"].last_decision == "suggested"
    assert by["NOLINK_EQ"].link_status == "no_link"
    assert by["PATHLESS_EQ"].link_status == "confirmed_without_path"
    assert (await api.get("/portfolio/identity-review")).json()["unresolved_count"] == 3
