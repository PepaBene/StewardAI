# ruff: noqa: E501 - seed SQL stays readable
"""The X-Ray paths that read stored statements (`_facts`) and reported revenue geography
(`_revenue`), against a real database. Needs STEWARD_TEST_DATABASE_URL (skipped otherwise).

Same portfolio as test_xray_db: Apple is 0.30 direct plus 0.06 through the S&P 500 fund (0.36)."""

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.config.holding_profiles import (
    HoldingProfile,
    HoldingProfilesConfig,
    ManualSegmentRow,
)
from steward.config.store import save_config
from steward.xray import service as xray_service
from tests.conftest import MigratedDatabase
from tests.test_xray_db import OWNER, seed, view

pytestmark = pytest.mark.db
TICKER = "AAPL_US_EQ"


async def add_statements(pool: asyncpg.Pool) -> None:
    known = dt.datetime(2026, 2, 20, tzinfo=dt.UTC)
    start, end = dt.date(2025, 1, 1), dt.date(2025, 12, 31)
    rows = (
        ("us-gaap:Revenues", "income", "1000", start, end, "USD"),
        ("us-gaap:GrossProfit", "income", "500", start, end, "USD"),
        ("us-gaap:OperatingIncomeLoss", "income", "200", start, end, "USD"),
        ("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding", "shares", "100", start, end, "shares"),
        ("us-gaap:Assets", "balance_sheet", "1000", None, end, "USD"),
    )
    for concept, statement, value, s, e, unit in rows:
        await pool.execute(
            "insert into public.financial_statement_fact (ticker, concept, statement, period_start, period_end, basis, value, unit, currency,"
            " source, source_url, known_at, validation) values ($1,$2,$3,$4,$5,'US_GAAP',$6,$7,'USD','sec','https://sec.test/x',$8,'{}'::jsonb)",
            TICKER, concept, statement, s, e, Decimal(value), unit, known,
        )  # fmt: skip


async def add_reported_geography(pool: asyncpg.Pool) -> None:
    """Revenue by country as the owner entered it from an annual report (the manual path of the
    segment read, which the X-Ray treats as reported geography)."""
    start, end = dt.date(2025, 1, 1), dt.date(2025, 12, 31)
    profile = HoldingProfile(
        ticker=TICKER,
        manual_segments=[
            ManualSegmentRow(label="United States", axis="geographic", value=600, currency="USD", period_start=start, period_end=end, source="annual report 2025"),
            ManualSegmentRow(label="Germany", axis="geographic", value=400, currency="USD", period_start=start, period_end=end, source="annual report 2025"),
        ],
    )
    async with pool.acquire() as conn:
        await save_config(conn, "holding_profiles", HoldingProfilesConfig(profiles=[profile]), created_by=OWNER)


@pytest.fixture
async def api(migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    await seed(backend_pool, migrated_db.admin_url)
    await add_statements(backend_pool)
    await add_reported_geography(backend_pool)
    xray_service.clear_cache()
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_stored_statements_produce_issuer_facts_and_a_covered_fundamental(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/xray/fundamentals")).json()
    metrics = {m["id"]: m for m in body["metrics"]}
    assert metrics["profitability"]["value"] == pytest.approx(0.5)  # gross profit 500 / assets 1000
    assert body["scored_issuers"] == 1
    assert body["covered_weight"] == pytest.approx(0.36)  # Apple, direct 0.30 plus 0.06 via the fund
    assert metrics["profitability"]["coverage"] == pytest.approx(0.36 / 0.84)
    # the other two issuers have no statements: named as excluded, not silently dropped
    excluded = {e["label"] for e in metrics["profitability"]["excluded"]}
    assert {"Microsoft Corp", "Nestle SA"} <= excluded


async def test_reported_revenue_geography_becomes_a_revenue_country_lens(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/xray")).json()
    lens = view(body, "revenue_country")
    classified = {b["key"]: b for b in lens["buckets"] if b["kind"] == "classified"}
    assert set(classified) == {"US", "DE"}
    assert classified["US"]["weight"] / classified["DE"]["weight"] == pytest.approx(1.5)
    assert classified["US"]["basis"].get("reported", 0) > 0
    assert lens["reconciles"] is True
