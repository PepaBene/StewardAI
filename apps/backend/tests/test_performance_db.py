# ruff: noqa: E501 - seed SQL stays readable
"""The Phase 4 performance API against a real database: ledger, return windows, lots,
attribution, sleeves and the owner settings. Needs STEWARD_TEST_DATABASE_URL (skipped otherwise).

Scenario (EUR account, EUR base): a 1000 deposit on day 1; a buy of 10 AAA at 50 on day 2; on day
3 the price is 55 and a 5 dividend arrives. Total value 1000 -> 1000 -> 1055."""

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from tests.conftest import MigratedDatabase

OWNER = "7f0c3d4e-0000-4000-8000-0000000000bb"
pytestmark = pytest.mark.db
D1, D2, D3 = dt.date(2026, 1, 5), dt.date(2026, 1, 6), dt.date(2026, 1, 7)


def noon(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(11), tzinfo=dt.UTC)


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
        "insert into public.instrument (t212_ticker, name, currency, home_country) values ('AAA_US_EQ', 'Alpha Inc', 'EUR', 'DE')"
    )
    # (day, total, invested)
    for day, total, invested in ((D1, "1000", "0"), (D2, "1000", "500"), (D3, "1055", "550")):
        cash = Decimal(total) - Decimal(invested)
        await pool.execute(
            "insert into public.account_snapshot (snapshot_date, job_id, captured_at, t212_account_id, account_currency,"
            " total_value, cash_available, cash_in_pies, cash_reserved, invested_value, invested_cost, unrealized_ppl, realized_ppl,"
            " base_currency, fx_source, fx_rate, cash_base, invested_base, total_base, weight_basis)"
            " values ($1, $2, $3, 1, 'EUR', $4, $5, 0, 0, $6, $6, 0, 0, 'EUR', 'ecb', 1, $5, $6, $4, 'total')",
            day, job, noon(day), Decimal(total), cash, Decimal(invested),
        )  # fmt: skip
    for day, price in ((D2, "50"), (D3, "55")):
        await pool.execute(
            "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity, avg_price, current_price,"
            " instrument_currency, value, account_currency, value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
            " values ($1, 'AAA_US_EQ', $2, now(), 10, 50, $3, 'EUR', 10 * $3::numeric, 'EUR', 10 * $3::numeric, 500, 0, 'EUR', 'ecb', 1, 10 * $3::numeric)",
            day, job, Decimal(price),
        )  # fmt: skip
    rows = [
        ("cash:1", "cash", "DEPOSIT", None, None, None, "1000", D1),
        ("fill:1", "order", "BUY", "AAA_US_EQ", "10", "50", "-500", D2),
        ("dividend:1", "dividend", "ORDINARY", "AAA_US_EQ", "10", "0.5", "5", D3),
    ]
    for tid, kind, sub, ticker, qty, price, amount, day in rows:
        await pool.execute(
            "insert into public.transaction (id, type, subtype, ticker, quantity, price, amount, currency, occurred_at, raw, fees_captured_at)"
            " values ($1, $2, $3, $4, $5, $6, $7, 'EUR', $8, '{}'::jsonb, now())",
            tid, kind, sub, ticker, None if qty is None else Decimal(qty),
            None if price is None else Decimal(price), Decimal(amount), noon(day),
        )  # fmt: skip


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


async def test_performance_windows_ledger_health_and_unavailable_windows(
    api: httpx.AsyncClient,
) -> None:
    body = (await api.get("/portfolio/performance")).json()
    assert body["base_currency"] == "EUR" and body["as_of"] == "2026-01-07"
    windows = {w["label"]: w for w in body["windows"]}
    inception = windows["inception"]
    # 1000 -> 1000 -> 1055 with the deposit on the baseline day: TWR 5.5 %.
    assert inception["twr"]["value"] == pytest.approx(0.055)
    assert inception["gain"] == pytest.approx(55)
    assert inception["net_flows"] == 0
    # A year window the 3 days of history cannot reach is unavailable, not shortened.
    assert windows["1Y"]["twr"]["value"] is None and "history starts" in windows["1Y"]["reason"]
    ledger = body["ledger"]
    assert ledger["status"] == "clean", ledger
    assert ledger["by_kind"] == {"deposit": 1, "buy": 1, "dividend": 1}
    assert body["status"] in ("partial", "complete")
    assert [p["value"] for p in body["index"]] == pytest.approx([100, 100, 105.5])
    assert body["timeline"][0]["deposits"] == 1000 and body["timeline"][0]["dividends"] == 5


async def test_unexplained_cash_withholds_a_precise_return(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    # An unrecorded 300 deposit shows up as cash the ledger cannot explain on day 3.
    await backend_pool.execute(
        "update public.account_snapshot set total_value = 1355, total_base = 1355, cash_available = 805, cash_base = 805 where snapshot_date = $1",
        D3,
    )
    body = (await api.get("/portfolio/performance")).json()
    inception = next(w for w in body["windows"] if w["label"] == "inception")
    assert inception["twr"]["value"] is None
    assert "22.14%" in inception["twr"]["warnings"][0]
    assert "no complete return is published" in inception["twr"]["warnings"][0]
    assert body["ledger"]["status"] == "breaks" and body["ledger"]["cash_breaks_total"] == 1
    assert body["status"] != "complete"


async def test_lots_and_holding_period(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/performance/lots")).json()
    (lot,) = body["lots"]
    assert (
        lot["ticker"] == "AAA_US_EQ" and lot["quantity"] == 10 and lot["cost"] == pytest.approx(500)
    )
    assert lot["passes_on"] == "2029-01-07" and not lot["passed"]
    assert lot["target_reached"] is None and lot["forfeits_exemption_warning"] is False
    assert body["dividends"] == 5 and body["realized"] == [] and body["disclaimer"]
    assert body["exemption"][0]["headroom"] == body["exemption"][0]["limit"]


async def test_attribution_reconciles_to_the_gain(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/performance/attribution", params={"days": 30})).json()
    bridge = body["bridge"]
    assert bridge["gain"]["value"] == pytest.approx(55)
    assert bridge["components"]["price"] == pytest.approx(50)
    assert bridge["components"]["income"] == pytest.approx(5)
    assert bridge["residual"] == pytest.approx(0)
    (holding,) = body["holdings"]
    assert holding["total"] == pytest.approx(55) and holding["attributed"]
    assert body["groups"]["country"] == {"DE": pytest.approx(55)}


async def test_settings_are_versioned_and_drive_the_sleeve_comparison(
    api: httpx.AsyncClient,
) -> None:
    empty = (await api.get("/portfolio/performance/sleeves")).json()
    assert empty["status"] == "unavailable" and "core ETFs" in empty["message"]
    first = (await api.get("/portfolio/performance/settings")).json()
    assert first["version"] is None and first["settings"]["time_test_years"] == 3
    saved = await api.put(
        "/portfolio/performance/settings",
        json={"core_tickers": ["AAA_US_EQ"], "time_test_years": 3},
    )
    assert saved.status_code == 200 and isinstance(saved.json()["version"], int)
    again = (
        await api.put(
            "/portfolio/performance/settings",
            json={"core_tickers": ["AAA_US_EQ"], "time_test_years": 2},
        )
    ).json()
    assert again["version"] > saved.json()["version"]
    # The settings are a config domain now: history, diff and rollback come with it.
    versions = (await api.get("/config/performance/versions")).json()
    assert [v["id"] for v in versions] == [again["version"], saved.json()["version"]]
    assert versions[0]["active"] and not versions[1]["active"]
    diff = (
        await api.get(
            "/config/performance/diff",
            params={"from": saved.json()["version"], "to": again["version"]},
        )
    ).json()
    assert [c["path"] for c in diff["changes"]] == ["/time_test_years"]
    back = await api.post(f"/config/performance/versions/{saved.json()['version']}/rollback")
    assert back.status_code == 200, back.text
    assert (await api.get("/portfolio/performance/settings")).json()["settings"][
        "time_test_years"
    ] == 3
    assert (
        await api.put(
            "/portfolio/performance/settings",
            json={"core_tickers": ["AAA_US_EQ"], "time_test_years": 2},
        )
    ).status_code == 200
    assert (await api.get("/portfolio/performance/settings")).json()["settings"][
        "time_test_years"
    ] == 2
    sleeves = (await api.get("/portfolio/performance/sleeves")).json()
    assert sleeves["too_early"] is True and sleeves["review_item"] is False
    bad = await api.put(
        "/portfolio/performance/settings",
        json={"benchmark": {"components": [{"symbol": "A", "weight": "0.6", "currency": "EUR"}]}},
    )
    assert bad.status_code == 422


async def test_a_saved_report_keeps_the_figures_as_they_stood(api: httpx.AsyncClient) -> None:
    saved = await api.post("/portfolio/performance/saved", json={"name": "Before the move"})
    assert saved.status_code == 201
    item = saved.json()
    assert item["kind"] == "performance_report" and item["method_version"]
    listed = (await api.get("/portfolio/performance/saved")).json()
    assert [x["id"] for x in listed] == [item["id"]]
    opened = (await api.get(f"/portfolio/performance/saved/{item['id']}")).json()
    live = (await api.get("/portfolio/performance")).json()
    assert opened["report"]["windows"] == live["windows"]
    assert (await api.delete(f"/portfolio/performance/saved/{item['id']}")).status_code == 204
    assert (await api.get("/portfolio/performance/saved")).json() == []
    assert (await api.get(f"/portfolio/performance/saved/{item['id']}")).status_code == 404


async def test_an_entered_split_changes_the_lots_quantity(api: httpx.AsyncClient) -> None:
    before = (await api.get("/portfolio/performance/lots")).json()
    settings = (await api.get("/portfolio/performance/settings")).json()["settings"]
    settings["splits"] = [{"ticker": "AAA_US_EQ", "day": "2030-01-01", "ratio": "4", "note": ""}]
    assert (await api.put("/portfolio/performance/settings", json=settings)).status_code == 200
    after = (await api.get("/portfolio/performance/lots")).json()
    assert before["lots"] and len(after["lots"]) == len(before["lots"])
    assert float(after["lots"][0]["quantity"]) == pytest.approx(
        4 * float(before["lots"][0]["quantity"])
    )


async def test_an_entered_merger_moves_the_lot_to_the_new_ticker_and_keeps_cost_and_date(
    api: httpx.AsyncClient,
) -> None:
    before = (await api.get("/portfolio/performance/lots")).json()["lots"][0]
    settings = (await api.get("/portfolio/performance/settings")).json()["settings"]
    settings["mergers"] = [
        {
            "old_ticker": "AAA_US_EQ",
            "new_ticker": "BBB_US_EQ",
            "day": "2030-01-01",
            "ratio": "1.5",
            "note": "",
        }
    ]
    assert (await api.put("/portfolio/performance/settings", json=settings)).status_code == 200
    after = (await api.get("/portfolio/performance/lots")).json()["lots"][0]
    assert after["ticker"] == "BBB_US_EQ" and after["bought_on"] == before["bought_on"]
    assert after["quantity"] == pytest.approx(1.5 * before["quantity"])
    assert after["cost"] == pytest.approx(before["cost"])


async def test_an_entered_spin_off_gives_the_child_its_share_of_cost_and_the_parents_date(
    api: httpx.AsyncClient,
) -> None:
    before = (await api.get("/portfolio/performance/lots")).json()["lots"][0]
    settings = (await api.get("/portfolio/performance/settings")).json()["settings"]
    settings["spin_offs"] = [
        {
            "parent_ticker": "AAA_US_EQ",
            "child_ticker": "CCC_US_EQ",
            "day": "2029-06-01",
            "ratio": "0.5",
            "cost_share": "0.2",
            "note": "",
        }
    ]
    assert (await api.put("/portfolio/performance/settings", json=settings)).status_code == 200
    lots = {x["ticker"]: x for x in (await api.get("/portfolio/performance/lots")).json()["lots"]}
    assert lots["CCC_US_EQ"]["bought_on"] == before["bought_on"]
    assert lots["CCC_US_EQ"]["quantity"] == pytest.approx(0.5 * before["quantity"])
    assert lots["CCC_US_EQ"]["cost"] == pytest.approx(0.2 * before["cost"])
    assert lots["AAA_US_EQ"]["cost"] == pytest.approx(0.8 * before["cost"])
    assert lots["AAA_US_EQ"]["quantity"] == pytest.approx(before["quantity"])


async def test_csv_exports_match_the_pages(api: httpx.AsyncClient) -> None:
    lots = await api.get("/portfolio/performance/export", params={"kind": "lots"})
    assert lots.status_code == 200 and lots.headers["content-type"].startswith("text/csv")
    assert "attachment" in lots.headers["content-disposition"]
    rows = lots.text.strip().split("\n")
    assert rows[0].startswith("lot_id,ticker,name,bought_on,quantity")
    assert len(rows) == 2 and "AAA_US_EQ" in rows[1] and "2029-01-07" in rows[1]
    ledger = (await api.get("/portfolio/performance/export", params={"kind": "ledger"})).text
    assert len(ledger.strip().split("\n")) == 1 + 3  # deposit, buy, dividend
    realized = (await api.get("/portfolio/performance/export", params={"kind": "realized"})).text
    assert len(realized.strip().split("\n")) == 1  # nothing sold yet: header only
    attribution = (
        await api.get("/portfolio/performance/export", params={"kind": "attribution", "days": 30})
    ).text
    assert "AAA_US_EQ" in attribution
    assert (
        await api.get("/portfolio/performance/export", params={"kind": "nope"})
    ).status_code == 422


async def test_build_review_adds_the_performance_rows_end_to_end(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    from steward.config.jobs import ReviewSettings
    from steward.config.thesis import ThesesConfig
    from steward.review.monthly import build_review

    now = dt.datetime(2026, 1, 8, 9, tzinfo=dt.UTC)
    body, _ = await build_review(
        backend_pool, now, ReviewSettings(), None, None, None, ThesesConfig()
    )
    performance = [r for r in body.compliance if r.kind == "performance"]
    assert [r.id for r in performance] == ["sleeves:gap"]
    assert performance[0].status == "unknown"  # no core ETFs listed: never a pass
    (period,) = body.holding_periods
    assert period.ticker == "AAA_US_EQ" and str(period.passes_on) == "2029-01-07"
    assert "Performance checks" not in " ".join(body.notes if hasattr(body, "notes") else [])


async def test_performance_alerts_are_raised_once_and_can_be_turned_off(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    from steward.performance.alerts import PerformanceBreach, alert_performance

    job = await backend_pool.fetchval(
        "insert into public.job (job_type, trigger, status) values ('monthly_review', 'schedule', 'running') returning id"
    )
    gap = PerformanceBreach("sleeves:gap", "Stocks trail the core", "3.1 points a year")
    lot = PerformanceBreach(
        "time_test:AAA_US_EQ:lot-1", "AAA lot passes the test", "sell target reached early"
    )
    now = dt.datetime(2026, 1, 8, 9, tzinfo=dt.UTC)

    async def count(key_like: str) -> int:
        return int(
            await backend_pool.fetchval(
                "select count(*) from public.alert where dedupe_key like $1", key_like
            )
        )

    assert await alert_performance(backend_pool, [gap, lot], severity=0, now=now, job_id=job) == []
    assert await count("performance:%") == 0  # severity 0 raises nothing
    # The default notification rules send severity 3 only: severity 2 waits for the digest.
    assert await alert_performance(backend_pool, [gap, lot], severity=2, now=now, job_id=job) == []
    assert await count("performance:%") == 0
    created = await alert_performance(backend_pool, [gap, lot], severity=3, now=now, job_id=job)
    first = await count("performance:%")
    assert len(created) == 2 and first >= 2 and await count("performance:sleeves:gap:2026-01") >= 1
    again = await alert_performance(
        backend_pool, [gap, lot], severity=3, now=now + dt.timedelta(days=1), job_id=job
    )
    assert again == [] and await count("performance:%") == first  # same month's gap, same lot: once
    await alert_performance(
        backend_pool, [gap], severity=3, now=dt.datetime(2026, 2, 2, 9, tzinfo=dt.UTC), job_id=job
    )
    assert (
        await count("performance:sleeves:gap:2026-02") >= 1
    )  # a gap that lasts alerts again next month


async def test_the_performance_rollbacks_remove_what_their_migrations_added(
    api: httpx.AsyncClient, migrated_db: MigratedDatabase
) -> None:
    from pathlib import Path

    rollback_dir = Path(__file__).resolve().parents[3] / "supabase" / "rollback"
    await api.put("/portfolio/performance/settings", json={"core_tickers": ["AAA_US_EQ"]})
    await api.put(
        "/portfolio/performance/settings",
        json={"core_tickers": ["AAA_US_EQ"], "time_test_years": 2},
    )
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        assert (
            await conn.fetchval(
                "select count(*) from public.config_version where domain = 'performance'"
            )
            == 2
        )
        async with conn.transaction():
            await conn.execute(
                (rollback_dir / "20261014100000_performance_config.down.sql").read_text()
            )
            assert (
                await conn.fetchval(
                    "select count(*) from public.config_version where domain = 'performance'"
                )
                == 0
            )
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute(
                        "insert into public.config_version (domain, body, schema_version, active) values ('performance', '{}'::jsonb, 1, true)"
                    )
            # the guard on config_version is back on after the rollback
            with pytest.raises(asyncpg.PostgresError, match="immutable"):
                async with conn.transaction():
                    await conn.execute("delete from public.config_version")
            await conn.execute(
                (rollback_dir / "20261013100000_portfolio_performance.down.sql").read_text()
            )
            assert not await conn.fetchval(
                "select to_regclass('public.performance_setting') is not null"
            )
    finally:
        await conn.close()


async def test_monthly_review_rows_for_sleeves_and_holding_periods(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    from steward.performance.review import performance_review

    findings, lots = await performance_review(backend_pool)
    (gap,) = [f for f in findings if f.id == "sleeves:gap"]
    # No core ETFs listed yet: the row is unknown and says what to do, never a pass.
    assert gap.status == "unknown" and "core ETFs" in gap.detail
    (lot,) = lots
    assert (
        lot.ticker == "AAA_US_EQ"
        and str(lot.bought_on) == "2026-01-06"
        and str(lot.passes_on) == "2029-01-07"
    )
    assert lot.forfeits_exemption_warning is False
    await api.put("/portfolio/performance/settings", json={"core_tickers": ["AAA_US_EQ"]})
    findings, _ = await performance_review(backend_pool)
    (gap,) = [f for f in findings if f.id == "sleeves:gap"]
    # The only holding is now core, so the stocks sleeve is empty: nothing to compare, never a pass.
    assert gap.status == "unknown" and "No comparison yet" in gap.detail
