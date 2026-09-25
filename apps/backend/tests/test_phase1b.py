"""Phase 1b: config history (list, diff, rollback, export/import), the transactions view,
readiness, per-step cost from the Budget config, and the sign-off check."""

import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.config import BudgetConfig
from steward.config.budget import ModelPrice
from steward.config.store import save_config
from steward.costs import PriceTable
from steward.heartbeat import beat
from steward.jobs.runner import JobContext, StepUsage, run_job
from steward.signoff import signoff

pytestmark = pytest.mark.db

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
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


def strategy(weight: float) -> dict[str, Any]:
    return {
        "base_currency": "CZK",
        "target_positions": [{"ticker": "AAPL_US_EQ", "target_weight": weight, "band": 0.02}],
    }


# ---------------------------------------------------------------------------
# Config history
# ---------------------------------------------------------------------------


async def test_history_diff_and_rollback(api: httpx.AsyncClient) -> None:
    ids = []
    for weight in (0.10, 0.20, 0.30):
        response = await api.put("/config/strategy", json=strategy(weight))
        assert response.status_code == 200, response.text
        ids.append(response.json()["id"])

    versions = (await api.get("/config/strategy/versions")).json()
    # The seeded placeholder strategy is the oldest version.
    assert [v["id"] for v in versions[:3]] == ids[::-1]
    assert versions[0]["active"] and not any(v["active"] for v in versions[1:])
    assert versions[0]["changes"] == 1

    diff = (await api.get("/config/strategy/diff", params={"from": ids[0], "to": ids[2]})).json()
    assert diff["changes"] == [
        {"path": "/target_positions/0/target_weight", "op": "replace", "old": 0.1, "new": 0.3}
    ]

    rolled = await api.post(f"/config/strategy/versions/{ids[0]}/rollback")
    assert rolled.status_code == 200, rolled.text
    new = rolled.json()
    assert new["id"] not in ids and new["active"]
    assert new["note"] == f"Rolled back to version #{ids[0]}"
    old = (await api.get(f"/config/strategy/versions/{ids[0]}")).json()
    assert new["body"] == old["body"]
    active = (await api.get("/config/strategy")).json()
    assert active["id"] == new["id"]
    assert active["body"]["target_positions"][0]["target_weight"] == 0.1

    assert (await api.post("/config/strategy/versions/999999/rollback")).status_code == 404
    other = await api.get(f"/config/theses/versions/{ids[0]}")
    assert other.status_code == 404  # a strategy version is not a theses version


async def test_rollback_restores_the_previous_drift(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await seed_snapshot(backend_pool)
    first = (await api.put("/config/strategy", json=strategy(0.50))).json()
    drift_before = await position_drift(api)
    await api.put("/config/strategy", json=strategy(0.10))
    assert await position_drift(api) != drift_before
    await api.post(f"/config/strategy/versions/{first['id']}/rollback")
    assert await position_drift(api) == drift_before


async def position_drift(api: httpx.AsyncClient) -> float:
    overview = (await api.get("/portfolio/overview")).json()
    return next(p["drift"] for p in overview["drift"]["positions"] if p["ticker"] == "AAPL_US_EQ")


async def test_export_then_import_into_an_empty_project(
    api: httpx.AsyncClient, migrated_db: Any, backend_pool: asyncpg.Pool
) -> None:
    await api.put("/config/strategy", json=strategy(0.25))
    await api.put("/config/budget", json=BudgetConfig().model_dump(mode="json"))
    await api.put(
        "/config/theses",
        json={"theses": [{"ticker": "AAPL_US_EQ", "why_owned": "Services growth"}]},
    )
    exported = (await api.get("/config/export")).json()
    assert set(exported["domains"]) >= {"strategy", "budget", "theses"}

    # An "empty project": wipe every version (as a superuser; the backend cannot delete).
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        await admin.execute("alter table public.config_version disable trigger user")
        await admin.execute("delete from public.config_version")
        await admin.execute("alter table public.config_version enable trigger user")
    finally:
        await admin.close()

    result = await api.post("/config/import", json=exported)
    assert result.status_code == 200, result.text
    assert set(result.json()["saved"]) == set(exported["domains"])
    rows = await backend_pool.fetch("select domain, body from config_version where active")
    assert {r["domain"]: json.loads(r["body"]) for r in rows} == {
        d: v["body"] for d, v in exported["domains"].items()
    }


async def test_invalid_import_lists_every_error_and_saves_nothing(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    before = await backend_pool.fetchval("select count(*) from config_version")
    bad = {
        "domains": {
            "strategy": {"body": {"base_currency": "czk", "target_positions": [
                {"ticker": "A", "target_weight": 0.8}, {"ticker": "B", "target_weight": 0.5}]}},
            "jobs": {"body": {"portfolio_sync": {"cron": "not a cron"}}},
            "notifications": {"body": {"daily_alert_cap": 0}},
            "research": {"body": {}},
            "budget": {"body": BudgetConfig().model_dump(mode="json")},
        }
    }  # fmt: skip
    response = await api.post("/config/import", json=bad)
    assert response.status_code == 422
    locs = [tuple(e["loc"][:2]) for e in response.json()["detail"]]
    assert {loc[1] for loc in locs} == {"strategy", "jobs", "notifications", "research"}
    assert await backend_pool.fetchval("select count(*) from config_version") == before


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


async def seed_transactions(pool: asyncpg.Pool) -> None:
    base = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    rows = []
    for i in range(57):
        kind = ("order", "dividend", "cash")[i % 3]
        rows.append(
            (
                f"{kind}:{i}",
                kind,
                {"order": "BUY", "dividend": "ORDINARY", "cash": "DEPOSIT"}[kind],
                None if kind == "cash" else ("AAPL_US_EQ" if i % 2 else "MSFT_US_EQ"),
                Decimal(f"{i}.37"),
                "EUR" if i % 5 else "CZK",
                # Several rows share a timestamp, to exercise the keyset cursor.
                base + dt.timedelta(days=i // 2),
            )
        )
    await pool.executemany(
        "insert into public.transaction (id, type, subtype, ticker, amount, currency,"
        " occurred_at, raw) values ($1, $2, $3, $4, $5, $6, $7, '{}')",
        rows,
    )


async def test_every_transaction_appears_once_across_pages(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await seed_transactions(backend_pool)
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 10}
        if cursor:
            params["cursor"] = cursor
        page = (await api.get("/portfolio/transactions", params=params)).json()
        assert page["total_count"] == 57
        seen += [t["id"] for t in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 57


async def test_dividend_total_equals_sql_sum(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await seed_transactions(backend_pool)
    params = {"type": "dividend", "ticker": "AAPL_US_EQ", "from": "2026-01-05", "to": "2026-01-20"}
    page = (await api.get("/portfolio/transactions", params=params)).json()
    expected = await backend_pool.fetch(
        "select currency, sum(amount) as s, count(*) as n from transaction where type = 'dividend'"
        " and ticker = 'AAPL_US_EQ' and occurred_at >= '2026-01-05' and occurred_at < '2026-01-21'"
        " group by currency order by currency"
    )
    assert [(d["currency"], Decimal(str(d["amount"])), d["count"]) for d in page["dividends"]] == [
        (r["currency"], r["s"], r["n"]) for r in expected
    ]
    assert {t["type"] for t in page["items"]} == {"dividend"}
    assert page["total_count"] == sum(r["n"] for r in expected)


# ---------------------------------------------------------------------------
# Readiness and cost
# ---------------------------------------------------------------------------


async def test_readiness_follows_the_worker_heartbeat(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    missing = await api.get("/health/ready")
    assert missing.status_code == 503 and missing.json()["database"] is True
    await beat(backend_pool, dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10))
    assert (await api.get("/health/ready")).status_code == 503
    await beat(backend_pool)
    ok = await api.get("/health/ready")
    assert ok.status_code == 200 and ok.json()["worker_alive"] is True


async def test_step_cost_uses_the_budget_prices(backend_pool: asyncpg.Pool) -> None:
    budget = BudgetConfig(
        prices=[ModelPrice(model="claude-sonnet-5", input_per_mtok=3.3, output_per_mtok=12.7)],
        web_search_per_1000=11,
    )
    async with backend_pool.acquire() as conn:
        await save_config(conn, "budget", budget, created_by=None)

    usages = [
        StepUsage("claude-sonnet-5", 123_456, 7_890, 1_000, 55_000, 3, False),
        StepUsage("claude-sonnet-5", 1_000_000, 200_000, 0, 0, 0, True),
        StepUsage("claude-unknown-9", 10, 10),
    ]

    async def handler(ctx: JobContext) -> None:
        for i, usage in enumerate(usages):
            async with ctx.step(f"ai:{i}") as step:
                step.usage = usage

    assert await run_job(backend_pool, "news_ingest", handler, trigger="manual") == "succeeded"
    rows = await backend_pool.fetch(
        "select model, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens,"
        " searches, batch, cost_usd, prices from job_step order by step_index"
    )
    table = PriceTable(budget)
    for row, usage in zip(rows, usages, strict=True):
        expected = table.cost(
            usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            searches=usage.searches,
            batch=usage.batch,
        )
        assert row["cost_usd"] == expected
    # By hand, to the cent: 123,456 x 3.3 + 1,000 x 1.25 x 3.3 + 55,000 x 0.1 x 3.3
    # + 7,890 x 12.7 (per million) + 3 searches x 11 / 1000.
    by_hand = (
        Decimal(123_456) * Decimal("3.3")
        + Decimal(1_000) * Decimal("1.25") * Decimal("3.3")
        + Decimal(55_000) * Decimal("0.1") * Decimal("3.3")
        + Decimal(7_890) * Decimal("12.7")
    ) / 10**6 + Decimal(3) * Decimal(11) / 1000
    assert round(rows[0]["cost_usd"], 2) == round(by_hand, 2)
    # Batch: (1M x 3.3 + 200k x 12.7) / 1M = 5.84, at half price.
    assert rows[1]["cost_usd"] == Decimal("2.920000")
    assert rows[2]["cost_usd"] is None and rows[2]["prices"] is None
    assert json.loads(rows[0]["prices"])["input_per_mtok"] == 3.3


# ---------------------------------------------------------------------------
# Sign-off
# ---------------------------------------------------------------------------


async def seed_snapshot(pool: asyncpg.Pool, day: dt.date = dt.date(2026, 3, 2)) -> int:
    job = await pool.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'schedule',"
        " 'succeeded') returning id"
    )
    await pool.executemany(
        "insert into instrument (t212_ticker, currency) values ($1, $2) on conflict do nothing",
        [("AAPL_US_EQ", "USD"), ("SAP_EQ", "EUR"), ("VOD_EQ", "GBX")],
    )
    await pool.execute(
        "insert into account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency, fx_source,"
        " fx_rate, cash_base, invested_base, total_base, weight_basis)"
        " values ($1, $2, now(), 1, 'EUR', 1100, 100, 0, 0, 1000, 900, 100, 0, 'CZK', 'cnb',"
        " 25, 2500, 25000, 27500, 'total')",
        day,
        job,
    )
    for ticker, ccy, value in (
        ("AAPL_US_EQ", "USD", 500),
        ("SAP_EQ", "EUR", 300),
        ("VOD_EQ", "GBX", 200),
    ):
        await pool.execute(
            "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
            " avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base,"
            " weight) values ($1, $2, $3, now(), 1, 1, 1, $4, 1, 'EUR', $5, 1, 0, 'CZK', 'cnb',"
            " 25, $6, 0.1)",
            day,
            ticker,
            job,
            ccy,
            value,
            value * 25,
        )  # fmt: skip
    await pool.execute(
        "insert into job_step (job_id, step_index, name, status, detail) values"
        " ($1, 0, 'trading212_positions', 'succeeded', '{\"positions\": 3}')",
        job,
    )
    return int(job)


async def test_signoff_reports_the_phase1_checks(backend_pool: asyncpg.Pool) -> None:
    for i in range(3):
        await seed_snapshot(backend_pool, dt.date(2026, 3, 2) + dt.timedelta(days=i))
    report = await signoff(backend_pool, required_runs=3)
    by_name = {c.name: c for c in report.checks}
    assert by_name["one snapshot row per position per day"].ok
    assert by_name["at least 3 positions in 3 currencies"].ok
    assert by_name["positions + cash equal Trading212's total"].ok
    # The seeded runs were all created today: not one scheduled run per day.
    assert not by_name["3 consecutive scheduled syncs"].ok
    assert report.figures["Trading212 total (EUR)"] == "1,100.00"
