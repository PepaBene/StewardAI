"""Phase 15 gap-fix: command palette depth extended to facts, not just KPI cells.

fact_index() (steward.earnings.read) backs GET /facts/index — the newest fact per
(ticker, metric) across every holding, small and cheap, so the frontend palette can
resolve a typed metric and ticker straight to that figure's evidence page.
"""

import hashlib

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from steward.earnings.read import fact_index

pytestmark = pytest.mark.db

OWNER = "00000000-0000-0000-0000-00000000000f"


async def _fact(
    pool: asyncpg.Pool,
    ticker: str,
    metric: str,
    *,
    period: str = "FY2025 Q3",
    status: str = "verified",
) -> int:
    sha = hashlib.sha256(f"{ticker}:{metric}:{period}".encode()).hexdigest()
    storage_path = f"evidence/{sha}.html"
    await pool.execute(
        "insert into public.evidence (sha256, storage_path, source_url, content_type,"
        " byte_size) values ($1, $2, 'https://ir.test/x', 'text/html', 10)"
        " on conflict do nothing",
        sha,
        storage_path,
    )
    fact_id: int = await pool.fetchval(
        "insert into public.fact (ticker, metric, basis, fiscal_year, fiscal_period, period,"
        " value, value_as_written, scale, unit, source_url, snippet, evidence_sha256,"
        " evidence_path, status, checks)"
        " values ($1, $2, 'reported', 2025, 'Q3', $3, 1, '1', 'units', 'count',"
        " 'https://ir.test/x', 'snippet', $4, $5, $6, '[]'::jsonb)"
        " returning id",
        ticker,
        metric,
        period,
        sha,
        storage_path,
        status,
    )
    return fact_id


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> httpx.AsyncClient:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_no_facts_is_an_empty_index(backend_pool: asyncpg.Pool) -> None:
    assert await fact_index(backend_pool) == []


async def test_one_entry_per_ticker_and_metric_at_its_newest_period(
    backend_pool: asyncpg.Pool,
) -> None:
    await _fact(backend_pool, "AAPL_US_EQ", "revenue", period="FY2025 Q2")
    newest_id = await _fact(backend_pool, "AAPL_US_EQ", "revenue", period="FY2025 Q3")
    await _fact(backend_pool, "AAPL_US_EQ", "eps_diluted", period="FY2025 Q3")

    index = await fact_index(backend_pool)
    by_metric = {e.metric: e for e in index}
    assert set(by_metric) == {"revenue", "eps_diluted"}
    assert by_metric["revenue"].fact_id == newest_id
    assert by_metric["revenue"].period == "FY2025 Q3"


async def test_a_rejected_fact_never_surfaces(backend_pool: asyncpg.Pool) -> None:
    await _fact(backend_pool, "AAPL_US_EQ", "revenue", status="rejected")
    assert await fact_index(backend_pool) == []


async def test_carries_the_instrument_name_when_known(backend_pool: asyncpg.Pool) -> None:
    await backend_pool.execute(
        "insert into public.instrument (t212_ticker, name) values ($1, $2)",
        "AAPL_US_EQ",
        "Apple",
    )
    await _fact(backend_pool, "AAPL_US_EQ", "revenue")
    [entry] = await fact_index(backend_pool)
    assert entry.name == "Apple"


async def test_fact_index_route(api: httpx.AsyncClient, backend_pool: asyncpg.Pool) -> None:
    await _fact(backend_pool, "AAPL_US_EQ", "revenue")
    response = await api.get("/facts/index")
    assert response.status_code == 200
    body = response.json()
    assert body == [
        {
            "ticker": "AAPL_US_EQ",
            "name": None,
            "metric": "revenue",
            "fact_id": body[0]["fact_id"],
            "period": "FY2025 Q3",
        }
    ]
