# ruff: noqa: E501 - seed SQL stays readable
"""The Phase 5 X-Ray API against a real database. Needs STEWARD_TEST_DATABASE_URL (skipped otherwise).

Portfolio (EUR base, 1000): direct AAPL 300, CSPX 600 (rows AAPL .10, MSFT .30, Nestle .50, cash .05),
cash 100. The benchmark proxy for ^spx is CSPX itself, as in the default configuration."""

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.config.jobs import CSPX_ISIN
from tests.conftest import MigratedDatabase

OWNER = "7f0c3d4e-0000-4000-8000-0000000000cc"
pytestmark = pytest.mark.db
TODAY = dt.datetime.now(dt.UTC).date()
AAPL, MSFT, NESN = "US0378331005", "US5949181045", "CH0038863350"


async def seed(pool: asyncpg.Pool, admin_url: str) -> None:
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(
            "insert into auth.users (id, email) values ($1, 'o@example.com')", OWNER
        )
    finally:
        await admin.close()
    job = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values ('portfolio_sync', 'schedule', 'succeeded') returning id"
    )
    await pool.execute(
        "insert into public.account_snapshot (snapshot_date, job_id, captured_at, t212_account_id, account_currency, total_value, cash_available, cash_in_pies,"
        " cash_reserved, invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency, fx_source, fx_rate, cash_base, invested_base, total_base, weight_basis)"
        " values ($1, $2, now(), 1, 'EUR', 1000, 100, 0, 0, 900, 900, 0, 0, 'EUR', 'ecb', 1, 100, 900, 1000, 'total')",
        TODAY, job,
    )  # fmt: skip
    for ticker, isin, name, kind, value, country in (
        ("AAPL_US_EQ", AAPL, "Apple Inc.", "Common Stock", 300, "US"),
        ("CSPXl_EQ", CSPX_ISIN, "iShares Core S&P 500", "ETP", 600, "IE"),
        ("NESN_EQ", NESN, "Nestle SA", "Common Stock", None, "CH"),
    ):
        await pool.execute(
            "insert into public.instrument (t212_ticker, isin, name, currency, security_type, home_country) values ($1, $2, $3, 'USD', $4, $5)",
            ticker, isin, name, kind, country,
        )  # fmt: skip
        if value is not None:
            await pool.execute(
                "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity, avg_price, current_price, instrument_currency, value, account_currency,"
                " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
                " values ($1, $2, $3, now(), 1, $4, $4, 'USD', $4, 'EUR', $4, $4, 0, 'EUR', 'ecb', 1, $4)",
                TODAY, ticker, job, Decimal(value),
            )  # fmt: skip
    snap = await pool.fetchval(
        "insert into public.etf_snapshot (fund_isin, provider, portfolio_id, source, as_of, country_basis, currency_basis, raw)"
        " values ($1, 'ishares', '253743', 'ishares_json', $2, 'country_of_risk', 'issuer', '{}'::jsonb) returning id",
        CSPX_ISIN, TODAY,
    )  # fmt: skip
    for i, (name, isin, cls, weight, sector, country, ccy) in enumerate(
        (
            ("Apple Inc.", AAPL, "equity", "0.10", "Information Technology", "US", "USD"),
            ("Microsoft Corp", MSFT, "equity", "0.30", "Information Technology", "US", "USD"),
            ("Nestle SA", NESN, "equity", "0.50", "Consumer Staples", "CH", "CHF"),
            ("Cash", None, "cash", "0.05", None, None, None),
        )
    ):
        await pool.execute(
            "insert into public.etf_holding (snapshot_id, row_index, name, isin, asset_class, weight, sector, country, currency) values ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            snap, i, name, isin, cls, Decimal(weight), sector, country, ccy,
        )  # fmt: skip
    await pool.execute(
        "insert into public.watchlist_item (owner_id, ticker, list_name) values ($1, 'NESN_EQ', 'Ideas')",
        OWNER,
    )


@pytest.fixture
async def api(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> AsyncIterator[httpx.AsyncClient]:
    await seed(backend_pool, migrated_db.admin_url)
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def view(body: dict, dimension: str) -> dict:  # type: ignore[type-arg]
    return next(v for v in body["views"] if v["dimension"] == dimension)


def bucket(v: dict, key: str) -> dict:  # type: ignore[type-arg]
    return next(b for b in v["buckets"] if b["key"] == key)


async def test_every_view_adds_up_to_the_whole_portfolio(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/xray")).json()
    assert body["available"] and body["base_currency"] == "EUR" and body["total_value"] == 1000
    holdings = {h["ticker"]: h for h in body["holdings"]}
    assert (
        holdings["AAPL_US_EQ"]["weight"] == pytest.approx(0.30)
        and not holdings["AAPL_US_EQ"]["is_fund"]
    )
    assert holdings["CSPXl_EQ"]["is_fund"] and holdings["CSPXl_EQ"]["looked_through"] is True
    assert body["cash_weight"] == pytest.approx(0.10)
    assert len(body["views"]) == 14
    for v in body["views"]:
        assert v["total_weight"] == pytest.approx(1.0), v["dimension"]
        assert v["reconciles"], v["dimension"]
    sector = view(body, "sector")
    # direct AAPL .30 + the fund's AAPL .06 and MSFT .18
    assert bucket(sector, "Information Technology")["weight"] == pytest.approx(0.54)
    assert bucket(sector, "Consumer Staples")["weight"] == pytest.approx(0.30)
    assert bucket(sector, "__cash__")["weight"] == pytest.approx(0.10)
    # the fund's rows add to 95%: 5% of 600 is unknown, and its own cash row is 5% of 600
    assert bucket(sector, "__unknown_fund_weight__")["weight"] == pytest.approx(0.03)
    assert bucket(sector, "__fund_cash__")["weight"] == pytest.approx(0.03)
    assert [f["isin"] for f in body["funds"]] == [CSPX_ISIN]
    country = view(body, "country")
    assert country["mixed_basis"] and country["country_kinds"]["home_market"] == pytest.approx(0.30)
    assert country["country_kinds"]["country_of_risk"] == pytest.approx(0.54)  # .6 x (.10+.30+.50)
    # an owner-mapped or reported geography exists for nobody here
    assert view(body, "revenue_country")["classified_weight"] == 0


async def test_overlap_keeps_the_trail_and_removal_effect(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/xray/overlap")).json()
    assert body["shared_count"] == 1
    apple = body["issuers"][0]
    assert apple["key"] == f"isin:{AAPL}" and apple["weight"] == pytest.approx(0.36)
    assert {c["kind"] for c in apple["channels"]} == {"direct", "fund"}
    removal = next(r for r in body["removal"] if r["isin"] == CSPX_ISIN)
    assert removal["hhi_after"] == pytest.approx(1.0)  # only direct AAPL is left


async def test_concentration_levels_and_benchmark(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/xray/concentration")).json()
    assert [c["level"] for c in body["levels"]] == [
        "security",
        "issuer",
        "sector",
        "country",
        "fund",
    ]
    assert body["benchmark_label"] == "S&P 500"
    assert body["single_issuer"]["weight"] == pytest.approx(0.36)
    bench = (await api.get("/portfolio/xray/benchmark")).json()
    assert bench["available"] and bench["proxy_fund"] == "CSPX"
    assert "not the index's membership" in bench["definition"]
    country = next(d for d in bench["dimensions"] if d["dimension"] == "country")
    assert not country[
        "like_for_like"
    ]  # direct AAPL is a home market, the proxy is country of risk


async def test_fundamentals_are_withheld_with_reasons_when_no_statements_are_stored(
    api: httpx.AsyncClient,
) -> None:
    body = (await api.get("/portfolio/xray/fundamentals")).json()
    metrics = {m["id"]: m for m in body["metrics"]}
    assert set(metrics) == {
        "earnings_yield",
        "pe",
        "price_to_book",
        "dividend_yield",
        "profitability",
        "leverage",
    }
    assert all(m["value"] is None and m["reason"] for m in metrics.values())
    assert body["covered_weight"] == 0 and body["equity_weight"] == pytest.approx(
        0.36 + 0.18 + 0.30
    )


async def test_candidates_include_the_watchlist_and_search(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/xray/candidates")).json()
    (item,) = body["items"]
    assert (
        item["ticker"] == "NESN_EQ"
        and item["source"] == "watchlist"
        and item["list_name"] == "Ideas"
        and not item["held"]
    )
    found = (await api.get("/portfolio/xray/candidates", params={"q": "iShares"})).json()["items"]
    assert any(
        i["ticker"] == "CSPXl_EQ" and i["held"] and i["looked_through"] is True for i in found
    )


async def test_a_proposal_never_changes_saved_holdings(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    async def snapshot() -> tuple:  # type: ignore[type-arg]
        return (
            await backend_pool.fetch(
                "select ticker, value_base from public.position_snapshot order by ticker"
            ),
            await backend_pool.fetch(
                "select snapshot_date, total_base, cash_base from public.account_snapshot"
            ),
            await backend_pool.fetchval("select count(*) from public.watchlist_item"),
        )

    before = await snapshot()
    body = (
        await api.post(
            "/portfolio/xray/proposed",
            json={
                "weights": [{"ticker": "AAPL_US_EQ", "weight": 0.10}],
                "additions": [{"ticker": "NESN_EQ", "weight": 0.20}],
            },
        )
    ).json()
    assert body["saved_holdings_changed"] is False
    assert body["before"]["total_value"] == body["after"]["total_value"] == 1000
    # AAPL 300 -> 100 and Nestle 200 added: 600 + 100 + 200 = 900, cash 100
    assert body["after"]["cash_weight"] == pytest.approx(0.10)
    positions = {p["ticker"]: p for p in body["positions"]}
    assert positions["AAPL_US_EQ"]["delta"] == pytest.approx(-0.20)
    assert positions["NESN_EQ"]["kind"] == "added"
    # Nestle is now held directly and through CSPX
    assert body["after"]["shared_issuers"] >= 1
    for v in body["views"]:
        assert v["reconciles_before"] and v["reconciles_after"]
    assert await snapshot() == before  # nothing was written

    too_much = await api.post(
        "/portfolio/xray/proposed", json={"weights": [{"ticker": "AAPL_US_EQ", "weight": 0.9}]}
    )
    assert too_much.status_code == 422 and "cannot exceed 100%" in too_much.json()["detail"]
    assert await snapshot() == before
