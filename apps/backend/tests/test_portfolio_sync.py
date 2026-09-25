"""The portfolio sync job end to end: mocked Trading212 and ČNB, a real Postgres as
app_backend, then the API reading what the job wrote.

Phase 1 ACs covered here:
- consecutive scheduled runs complete with no error and produce one snapshot row per
  position per day;
- GET /portfolio/overview and /portfolio/holdings return data matching the latest
  position_snapshot;
- the overview's freshness timestamp is the last successful sync's job_step timestamp;
- changing a target weight in config changes the reported drift with no code change.
"""

import copy
import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from steward.jobs.runner import JobContext, run_job
from steward.portfolio.sync import SyncDeps, portfolio_sync
from steward.trading212 import Trading212Client
from tests.conftest import MigratedDatabase
from tests.test_fx import CNB_TEXT

pytestmark = pytest.mark.db

OWNER = "00000000-0000-0000-0000-00000000000a"

SUMMARY = {
    "id": 51320716,
    "currency": "CZK",
    "totalValue": 60000.0,
    "cash": {"availableToTrade": 5000.0, "inPies": 0, "reservedForOrders": 0},
    "investments": {
        "currentValue": 55000.0,
        "realizedProfitLoss": 0,
        "totalCost": 50000.0,
        "unrealizedProfitLoss": 5000.0,
    },
}


def _position(
    ticker: str, currency: str, qty: float, price: float, value_czk: float
) -> dict[str, Any]:
    return {
        "instrument": {
            "ticker": ticker,
            "name": ticker.split("_")[0],
            "isin": "US0378331005" if ticker == "AAPL_US_EQ" else None,
            "currency": currency,
        },
        "quantity": qty,
        "averagePricePaid": price * 0.9,
        "currentPrice": price,
        "walletImpact": {
            "currency": "CZK",
            "currentValue": value_czk,
            "totalCost": value_czk * 0.9,
            "unrealizedProfitLoss": value_czk * 0.1,
        },
    }


POSITIONS = [
    _position("AAPL_US_EQ", "USD", 5, 228.37, 24400.0),
    _position("ASMLa_EQ", "EUR", 1, 610.2, 14850.0),
    _position("ULVRl_EQ", "GBX", 10, 4312.5, 12200.0),
]


def _fill(order_id: int, fill_id: int | None, side: str = "BUY") -> dict[str, Any]:
    item: dict[str, Any] = {
        "order": {"id": order_id, "ticker": "AAPL_US_EQ", "side": side, "status": "FILLED"},
    }
    if fill_id is not None:
        item["fill"] = {
            "id": fill_id,
            "filledAt": "2026-09-20T14:00:00Z",
            "quantity": 1,
            "price": 220.0,
            "walletImpact": {"currency": "CZK", "netValue": 4700.0},
        }
    return item


class FakeUpstreams:
    """Trading212 (demo host) and ČNB behind one MockTransport, with editable data."""

    def __init__(self) -> None:
        self.positions = copy.deepcopy(POSITIONS)
        self.order_pages: list[list[dict[str, Any]]] = [
            [_fill(3, 30), _fill(2, None)],  # newest first; order 2 was cancelled
            [_fill(1, 10)],
        ]
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(f"{path}?{request.url.query.decode()}")
        if request.url.host == "www.cnb.cz":
            # Same rates every day, dated as requested (the real file is dated by ČNB).
            requested = dt.datetime.strptime(request.url.params["date"], "%d.%m.%Y")
            header = f"{requested:%d %b %Y} #1"
            return httpx.Response(200, text=header + CNB_TEXT[CNB_TEXT.index("\n") :])
        if path.endswith("/equity/account/summary"):
            return httpx.Response(200, json=SUMMARY)
        if path.endswith("/equity/positions"):
            return httpx.Response(200, json=self.positions)
        if path.endswith("/equity/history/orders"):
            cursor = request.url.params.get("cursor")
            page = int(cursor) if cursor else 0
            has_next = page + 1 < len(self.order_pages)
            return httpx.Response(
                200,
                json={
                    "items": self.order_pages[page],
                    "nextPagePath": (
                        f"/api/v0/equity/history/orders?limit=50&cursor={page + 1}"
                        if has_next
                        else None
                    ),
                },
            )
        if path.endswith("/equity/history/dividends"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "reference": "div-1",
                            "ticker": "AAPL_US_EQ",
                            "type": "ORDINARY",
                            "amount": 12.5,
                            "currency": "CZK",
                            "quantity": 5,
                            "grossAmountPerShare": 0.26,
                            "paidOn": "2026-08-14T00:00:00Z",
                        }
                    ],
                    "nextPagePath": None,
                },
            )
        if path.endswith("/equity/history/transactions"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "type": "DEPOSIT",
                            "amount": 125000.0,
                            "currency": "CZK",
                            "reference": "dep-1",
                            "dateTime": "2026-09-01T10:00:00Z",
                        }
                    ],
                    "nextPagePath": None,
                },
            )
        return httpx.Response(404)


async def _no_sleep(_: float) -> None:
    return None


async def _sync_at(pool: asyncpg.Pool, fake: FakeUpstreams, when: dt.datetime) -> str:
    transport = httpx.MockTransport(fake.handler)
    async with (
        Trading212Client("k", "s", env="demo", transport=transport, sleep=_no_sleep) as t212,
        httpx.AsyncClient(transport=transport) as http,
    ):
        deps = SyncDeps(t212=t212, http=http, now=lambda: when)

        async def handler(ctx: JobContext) -> None:
            await portfolio_sync(ctx, deps)

        return await run_job(pool, "portfolio_sync", handler, trigger="schedule")


def _evening(day: int) -> dt.datetime:
    return dt.datetime(2026, 9, day, 20, 30, tzinfo=dt.UTC)  # 22:30 Prague


async def test_consecutive_daily_runs_write_one_row_per_position_per_day(
    backend_pool: asyncpg.Pool,
) -> None:
    fake = FakeUpstreams()
    for day in range(21, 26):  # five consecutive days
        assert await _sync_at(backend_pool, fake, _evening(day)) == "succeeded"

    jobs = await backend_pool.fetch("select status, error from job order by id")
    assert [j["status"] for j in jobs] == ["succeeded"] * 5
    counts = await backend_pool.fetch(
        "select snapshot_date, count(*) as n, count(distinct ticker) as tickers"
        " from position_snapshot group by snapshot_date order by snapshot_date"
    )
    assert [(c["snapshot_date"].day, c["n"], c["tickers"]) for c in counts] == [
        (day, 3, 3) for day in range(21, 26)
    ]
    assert await backend_pool.fetchval("select count(*) from account_snapshot") == 5

    # A re-run on the same day replaces that day's rows; a sold position disappears.
    fake.positions = fake.positions[:2]
    assert await _sync_at(backend_pool, fake, _evening(25)) == "succeeded"
    tickers = await backend_pool.fetch(
        "select ticker from position_snapshot where snapshot_date = '2026-09-25' order by ticker"
    )
    assert [t["ticker"] for t in tickers] == ["AAPL_US_EQ", "ASMLa_EQ"]


async def test_snapshot_values_and_steps(backend_pool: asyncpg.Pool) -> None:
    fake = FakeUpstreams()
    assert await _sync_at(backend_pool, fake, _evening(25)) == "succeeded"

    row = await backend_pool.fetchrow("select * from position_snapshot where ticker = 'ULVRl_EQ'")
    # 10 x 4312.5 pence = 431.25 GBP, x 28.299 CZK (ČNB fixing of 25 Sep 2026).
    assert row["value_base"] == Decimal("10") * Decimal("4312.5") * Decimal("0.28299")
    assert row["fx_source"] == "cnb" and row["fx_rate_date"] == dt.date(2026, 9, 25)
    instrument = await backend_pool.fetchrow(
        "select name, isin, currency from instrument where t212_ticker = 'AAPL_US_EQ'"
    )
    assert dict(instrument) == {"name": "AAPL", "isin": "US0378331005", "currency": "USD"}
    # Stored as Trading212 reports it (minor unit), as the instrument schema documents.
    assert (
        await backend_pool.fetchval(
            "select currency from instrument where t212_ticker = 'ULVRl_EQ'"
        )
        == "GBX"
    )

    steps = await backend_pool.fetch("select name, status from job_step order by step_index")
    assert [s["name"] for s in steps] == [
        "load_config",
        "trading212_account",
        "trading212_positions",
        "fx_rates",
        "snapshot",
        "transactions",
        "drift",
    ]
    assert {s["status"] for s in steps} == {"succeeded"}
    assert await backend_pool.fetchval("select count(*) from fx_rate where source = 'cnb'") == 4


async def test_transactions_backfill_then_stop_at_known_items(backend_pool: asyncpg.Pool) -> None:
    fake = FakeUpstreams()
    assert await _sync_at(backend_pool, fake, _evening(24)) == "succeeded"
    rows = await backend_pool.fetch("select id, type, subtype, amount from transaction order by id")
    assert [(r["id"], r["type"]) for r in rows] == [
        ("cash:dep-1", "cash"),
        ("dividend:div-1", "dividend"),
        ("fill:10", "order"),
        ("fill:30", "order"),
    ]  # the cancelled order (no fill) is not a transaction
    orders_pages = [r for r in fake.requests if "/history/orders" in r]
    assert len(orders_pages) == 2  # backfill read every page

    fake.requests.clear()
    fake.order_pages[0].insert(0, _fill(4, 40, side="SELL"))
    assert await _sync_at(backend_pool, fake, _evening(25)) == "succeeded"
    assert [r for r in fake.requests if "/history/orders" in r] == [
        "/api/v0/equity/history/orders?limit=50"
    ]  # stopped at the first page with a known fill
    new = await backend_pool.fetchrow(
        "select subtype, amount from transaction where id = 'fill:40'"
    )
    assert new["subtype"] == "SELL" and new["amount"] == Decimal("4700.0")
    detail = await backend_pool.fetchval(
        "select detail from job_step where name = 'transactions' order by id desc limit 1"
    )
    assert json.loads(detail)["orders_new"] == 1


async def test_fx_fetch_failure_falls_back_to_stored_rates(backend_pool: asyncpg.Pool) -> None:
    fake = FakeUpstreams()
    assert await _sync_at(backend_pool, fake, _evening(25)) == "succeeded"

    broken = FakeUpstreams()
    original = broken.handler

    def cnb_down(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.cnb.cz":
            return httpx.Response(503)
        return original(request)

    broken.handler = cnb_down  # type: ignore[method-assign]
    assert await _sync_at(backend_pool, broken, _evening(26)) == "succeeded"
    detail = json.loads(
        await backend_pool.fetchval(
            "select detail from job_step where name = 'fx_rates' order by id desc limit 1"
        )
    )
    assert detail["fetch_error"] == "ČNB returned HTTP 503" and detail["used_stored_rates"] == 4


async def test_unconvertible_currency_fails_the_job_before_writing(
    backend_pool: asyncpg.Pool,
) -> None:
    fake = FakeUpstreams()
    fake.positions.append(_position("NESN_EQ", "CHF", 1, 100.0, 2500.0))
    assert await _sync_at(backend_pool, fake, _evening(25)) == "failed"
    error = await backend_pool.fetchval("select error from job")
    assert "no CNB reference rate for CHF" in error
    assert await backend_pool.fetchval("select count(*) from position_snapshot") == 0


# ---------------------------------------------------------------------------
# API over the synced data
# ---------------------------------------------------------------------------


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


async def test_overview_and_holdings_before_the_first_sync(api: httpx.AsyncClient) -> None:
    overview = (await api.get("/portfolio/overview")).json()
    assert overview["freshness"] is None and overview["allocation"] == []
    holdings = (await api.get("/portfolio/holdings")).json()
    assert holdings == {
        "freshness": None,
        "base_currency": None,
        "account_currency": None,
        "holdings": [],
    }


async def test_api_returns_the_latest_snapshot(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    fake = FakeUpstreams()
    await _sync_at(backend_pool, fake, _evening(24))
    fake.positions[0]["currentPrice"] = 240.0
    await _sync_at(backend_pool, fake, _evening(25))

    response = await api.get("/portfolio/holdings")
    assert response.status_code == 200
    body = response.json()
    assert body["freshness"]["snapshot_date"] == "2026-09-25"
    latest = await backend_pool.fetch(
        "select ticker, value_base, quantity, current_price from position_snapshot"
        " where snapshot_date = '2026-09-25' order by value_base desc"
    )
    assert [h["ticker"] for h in body["holdings"]] == [r["ticker"] for r in latest]
    for holding, row in zip(body["holdings"], latest, strict=True):
        assert holding["value_base"] == pytest.approx(float(row["value_base"]))
        assert holding["current_price"] == pytest.approx(float(row["current_price"]))
    assert body["holdings"][0]["current_price"] == 240.0

    overview = (await api.get("/portfolio/overview")).json()
    assert overview["account"]["total_value"] == 60000.0  # as Trading212 reports it
    assert overview["base"]["base_currency"] == "CZK"
    assert overview["base"]["total"] == pytest.approx(
        sum(float(r["value_base"]) for r in latest) + 5000.0
    )
    assert [s["key"] for s in overview["allocation"]][-1] == "CASH"
    assert sum(s["weight"] for s in overview["allocation"]) == pytest.approx(1.0)

    # Freshness = the finish time of the last step of the sync that wrote the snapshot.
    job_id = overview["freshness"]["job_id"]
    last_step = await backend_pool.fetchval(
        "select max(finished_at) from job_step where job_id = $1", job_id
    )
    assert dt.datetime.fromisoformat(overview["freshness"]["synced_at"]) == last_step
    assert overview["last_sync"]["status"] == "succeeded"


async def test_saving_a_strategy_changes_the_drift(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await _sync_at(backend_pool, FakeUpstreams(), _evening(25))
    overview = (await api.get("/portfolio/overview")).json()
    aapl_weight = next(p for p in overview["drift"]["positions"] if p["ticker"] == "AAPL_US_EQ")[
        "weight"
    ]

    strategy: dict[str, Any] = {
        "base_currency": "CZK",
        "target_positions": [{"ticker": "AAPL_US_EQ", "target_weight": 0.2, "band": 0.02}],
    }
    saved = await api.put("/config/strategy", json=strategy, params={"note": "first targets"})
    assert saved.status_code == 200 and saved.json()["note"] == "first targets"
    first = (await api.get("/portfolio/overview")).json()
    aapl = next(p for p in first["drift"]["positions"] if p["ticker"] == "AAPL_US_EQ")
    assert aapl["drift"] == pytest.approx(aapl_weight - 0.2)

    strategy["target_positions"][0]["target_weight"] = 0.3
    assert (await api.put("/config/strategy", json=strategy)).status_code == 200
    second = (await api.get("/portfolio/overview")).json()
    aapl = next(p for p in second["drift"]["positions"] if p["ticker"] == "AAPL_US_EQ")
    assert aapl["drift"] == pytest.approx(aapl_weight - 0.3)
    assert second["strategy_config_version_id"] != first["strategy_config_version_id"]

    assert (await api.get("/config/strategy")).json()["body"]["target_positions"][0][
        "target_weight"
    ] == 0.3
    invalid = await api.put("/config/strategy", json={"base_currency": "czk"})
    assert invalid.status_code == 422


async def test_jobs_config_defaults_until_saved(api: httpx.AsyncClient) -> None:
    assert (await api.get("/config/jobs")).status_code == 404
    saved = await api.put("/config/jobs", json={"portfolio_sync": {"cron": "0 23 * * 1-5"}})
    assert saved.status_code == 200
    assert saved.json()["body"]["portfolio_sync"] == {
        "enabled": True,
        "cron": "0 23 * * 1-5",
        "timezone": "Europe/Prague",
    }


async def test_manual_sync_is_queued_for_the_worker(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    if not any("pgmq" in name for name in migrated_db.applied):
        pytest.skip("pgmq extension not available on this server")
    response = await api.post("/jobs/portfolio-sync")
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "queued" and job["trigger"] == "manual"
    assert (
        await backend_pool.fetchval("select requested_by::text from job where id = $1", job["id"])
        == OWNER
    )
    message = await backend_pool.fetchval("select message from pgmq.q_jobs")
    assert json.loads(message) == {"job_id": job["id"], "job_type": "portfolio_sync"}
