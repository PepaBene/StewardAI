# ruff: noqa: E501 - seed SQL stays readable
"""Discovery against a real database: universe view, screens, saved versions, snapshots.
Needs STEWARD_TEST_DATABASE_URL (skipped otherwise)."""

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

OWNER = "7f0c3d4e-0000-4000-8000-0000000000aa"
TODAY = dt.datetime.now(dt.UTC).date()
pytestmark = pytest.mark.db


async def seed(admin_url: str) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute("insert into auth.users (id, email) values ($1, 'o@example.com')", OWNER)
        for ticker, name, symbol in (("AAA_US_EQ", "Alpha Inc", "aaa.us"), ("BBB_US_EQ", "Beta Inc", None), ("OLD_US_EQ", "Old Corp", "old.us")):
            await conn.execute(
                "insert into public.instrument (t212_ticker, isin, name, currency, exchange, price_symbol, home_country)"
                " values ($1, $2, $3, 'USD', 'NYSE', $4, 'US')",
                ticker, f"US{ticker[:3]}00000{len(name)}"[:12].ljust(12, "0")[:11] + "0", name, symbol)
        issuer = await conn.fetchval("insert into public.issuer (legal_name, country) values ('Alpha Inc','US') returning id")
        sec = await conn.fetchval("insert into public.security (issuer_id, kind, name, currency) values ($1,'equity','Alpha','USD') returning id", issuer)
        old = await conn.fetchval("insert into public.security (kind, name, currency) values ('equity','Old','USD') returning id")
        listing = await conn.fetchval("insert into public.security_listing (security_id, mic, ticker, currency, valid_from) values ($1,'XNYS','AAA','USD','2010-01-01') returning id", sec)
        old_listing = await conn.fetchval(
            "insert into public.security_listing (security_id, mic, ticker, currency, status, valid_from, valid_to)"
            " values ($1,'XNYS','OLD','USD','terminated','2012-01-01','2022-06-30') returning id", old)
        await conn.execute("insert into public.instrument_security_link (ticker, security_id, listing_id, status) values ('AAA_US_EQ',$1,$2,'confirmed')", sec, listing)
        await conn.execute("insert into public.instrument_security_link (ticker, security_id, listing_id, status) values ('OLD_US_EQ',$1,$2,'confirmed')", old, old_listing)

        known = dt.datetime(2026, 2, 20, tzinfo=dt.UTC)

        async def fact(concept: str, statement: str, value: str, start: dt.date | None, end: dt.date, unit: str = "USD") -> None:
            await conn.execute(
                "insert into public.financial_statement_fact (ticker, concept, statement, period_start, period_end, basis, value, unit, currency,"
                " source, source_url, known_at, validation) values ('AAA_US_EQ',$1,$2,$3,$4,'US_GAAP',$5,$6,'USD','sec','https://sec.test/x',$7,'{}'::jsonb)",
                concept, statement, start, end, Decimal(value), unit, known)

        for year, rev, gp, op in ((2024, "900", "420", "170"), (2025, "1000", "500", "200")):
            start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
            await fact("us-gaap:Revenues", "income", rev, start, end)
            await fact("us-gaap:GrossProfit", "income", gp, start, end)
            await fact("us-gaap:OperatingIncomeLoss", "income", op, start, end)
            await fact("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding", "shares", "100", start, end, "shares")
        await fact("us-gaap:Assets", "balance_sheet", "1000", None, dt.date(2025, 12, 31))

        base = TODAY - dt.timedelta(days=500)
        await conn.executemany(
            "insert into public.price_bar (symbol, date, close, source) values ('aaa.us',$1,$2,'stooq')",
            [(base + dt.timedelta(days=i), Decimal("50") + Decimal(i) / 20) for i in range(501)])
    finally:
        await conn.close()


@pytest.fixture
async def api(migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    await seed(migrated_db.admin_url)
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


def f(metric: str, op: str, value: object) -> dict[str, object]:
    return {"metric": metric, "op": op, "value": value}


async def test_universe_view_screen_accounting_and_unknown_handling(api: httpx.AsyncClient, backend_pool: asyncpg.Pool) -> None:
    rows = await backend_pool.fetch("select ticker, has_statements, has_prices, status from public.investment_universe order by ticker")
    assert {r["ticker"]: (r["has_statements"], r["has_prices"], r["status"]) for r in rows} == {
        "AAA_US_EQ": (True, True, "active"), "BBB_US_EQ": (False, False, "active"), "OLD_US_EQ": (False, False, "terminated")}

    out = (await api.post("/discover/stocks", json={"filters": [f("gross_margin", "gte", "0.4")]})).json()
    assert out["universe_size"] == 2  # OLD is terminated: not in today's universe
    assert [r["key"] for r in out["rows"]] == ["AAA_US_EQ"]
    assert out["matched"] + out["failed_filter"] + out["unknown_excluded"] + out["no_data"] == out["universe_size"]
    assert out["no_data"] == 1 and any("terminated" in w for w in out["warnings"])
    row = out["rows"][0]
    assert Decimal(row["values"]["gross_margin"]["value"]) == Decimal("0.5")
    assert row["values"]["market_cap"]["value"] is not None and row["checks"][0]["outcome"] == "pass"
    assert out["method_version"] == "2.0.0" and out["definition_hash"] and out["result_hash"]

    # No issuer has a dividend history: the filter is refused rather than passing everyone as 0 >= -1.
    zero = await api.post("/discover/stocks", json={"filters": [f("dividend_yield", "gte", "-1")]})
    assert zero.status_code == 422 and "no issuer in this universe has a value" in zero.text


async def test_catalogue_disables_estimates_and_empty_metrics_with_reasons(api: httpx.AsyncClient) -> None:
    cat = (await api.get("/discover/catalogue")).json()
    metrics = {m["id"]: m for m in cat["metrics"]}
    assert metrics["forward_pe"]["enabled"] is False and "no consensus is estimated" in metrics["forward_pe"]["disabled_reason"]
    assert metrics["gross_margin"]["enabled"] is True and metrics["gross_margin"]["known"] == 1 and metrics["gross_margin"]["total"] == 2
    assert metrics["sector"]["enabled"] is False
    rejected = await api.post("/discover/stocks", json={"filters": [f("forward_pe", "lte", "20")]})
    assert rejected.status_code == 422 and "disabled" in rejected.text
    pinned = await api.post("/discover/stocks", json={"filters": [{**f("roic", "gte", "0.1"), "definition_version": "9"}]})
    assert pinned.status_code == 422


async def test_saved_screens_version_immutably_and_runs_are_recorded(api: httpx.AsyncClient, backend_pool: asyncpg.Pool) -> None:
    body = {"name": "Margins", "kind": "stock", "stock": {"filters": [f("gross_margin", "gte", "0.4")]}}
    created = (await api.post("/discover/screens", json=body)).json()
    sid = created["id"]
    assert created["latest_version"] == 1
    assert (await api.post("/discover/screens", json=body)).status_code == 409
    same = (await api.put(f"/discover/screens/{sid}", json=body)).json()
    assert same["latest_version"] == 1 and same["definition_hash"] == created["definition_hash"]
    body["stock"] = {"filters": [f("gross_margin", "gte", "0.6")]}
    bumped = (await api.put(f"/discover/screens/{sid}", json=body)).json()
    assert bumped["latest_version"] == 2

    v1 = (await api.post(f"/discover/screens/{sid}/run?version=1")).json()
    v2 = (await api.post(f"/discover/screens/{sid}/run")).json()
    assert v1["matched"] == 1 and v2["matched"] == 0  # the same screen keeps meaning per version
    again = (await api.post(f"/discover/screens/{sid}/run?version=1")).json()
    assert again["result_hash"] == v1["result_hash"]
    detail = (await api.get(f"/discover/screens/{sid}")).json()
    assert [v["version"] for v in detail["versions"]] == [2, 1] and len(detail["runs"]) == 3
    assert detail["runs"][0]["universe_size"] == 2

    with pytest.raises(asyncpg.PostgresError, match="immutable"):
        await backend_pool.execute("update public.screen_definition_version set definition='{}'::jsonb where screen_id=$1", sid)
    assert (await api.delete(f"/discover/screens/{sid}")).status_code == 204
    assert (await api.get("/discover/screens")).json() == []


async def test_universe_snapshots_keep_delisted_names_for_past_dates(api: httpx.AsyncClient) -> None:
    first = (await api.post("/discover/universe/snapshots")).json()
    second = (await api.post("/discover/universe/snapshots")).json()
    assert first["id"] == second["id"]  # one snapshot per date, never rewritten
    past = (await api.post("/discover/stocks", json={"as_of": "2021-12-31"})).json()
    assert past["universe_size"] == 3  # OLD was still listed on 2021-12-31
    no_data = {k for g in past["excluded"] for k in g["keys"]}
    assert no_data == {"BBB_US_EQ", "OLD_US_EQ"} and past["matched"] == 1  # counted, not dropped
    now = (await api.post("/discover/stocks", json={"universe": "snapshot", "snapshot_id": first["id"]})).json()
    assert now["universe_label"] == "Frozen universe"
    missing = await api.post("/discover/stocks", json={"universe": "snapshot", "as_of": "2000-01-01"})
    assert missing.status_code == 404


async def test_watchlist_universe_column_sets_export_and_baselines(api: httpx.AsyncClient) -> None:
    await api.post("/watchlist", json={"ticker": "AAA_US_EQ"})
    watch = (await api.post("/discover/stocks", json={"universe": "watchlist"})).json()
    assert watch["universe_size"] == 1
    saved = (await api.post("/discover/column-sets", json={"kind": "stock", "name": "Quality", "columns": ["gross_margin", "roic"]})).json()
    assert (await api.get("/discover/column-sets")).json()[0]["id"] == saved["id"]
    assert (await api.post("/discover/column-sets", json={"kind": "stock", "name": "Bad", "columns": ["nope"]})).status_code == 422
    export = await api.post("/discover/export", json={"kind": "stock", "columns": ["gross_margin", "dividend_yield"], "stock": {"missing": "keep_unknown"}})
    assert export.headers["content-type"].startswith("text/csv")
    lines = export.text.splitlines()
    assert lines[0].startswith("# StewardAI discovery export")
    aaa = next(ln for ln in lines if ln.startswith("AAA_US_EQ"))
    cells = aaa.split(",")
    assert cells[2] == "0.5" and cells[6] == "" and cells[7] != ""  # dividend yield blank with a reason, not 0
    assert (await api.get("/discover/baselines?scope=saved_set")).status_code == 422
    base = (await api.get("/discover/baselines?scope=global&metrics=gross_margin")).json()
    assert base[0]["comparable"] == 1 and base[0]["median"] is None and "needs 5" in base[0]["median_withheld_reason"]
    fund = (await api.post("/discover/funds", json={"filters": [f("expense_ratio", "lte", "0.01")]})).json()
    assert fund["universe_size"] == 0 and fund["kind"] == "fund"
    assert (await api.get("/discover/catalogue?kind=fund")).status_code == 200
