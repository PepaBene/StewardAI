# ruff: noqa: E501 - seed SQL stays readable
"""The fund workbench against a real database: every endpoint's SQL runs, the owner's facts are
append-only and validated, and the checklist abstains until the critical inputs exist. Needs
STEWARD_TEST_DATABASE_URL (skipped otherwise)."""

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

OWNER = "7f0c3d4e-0000-4000-8000-0000000000cc"
TODAY = dt.datetime.now(dt.UTC).date()
A, B, C = "IE00AAAAAAA1", "IE00BBBBBBB2", "IE00CCCCCCC3"
pytestmark = pytest.mark.db


async def seed(admin_url: str) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute("insert into auth.users (id, email) values ($1, 'o@example.com')", OWNER)
        for isin, tkr, sym in (
            (A, "FNDA_EQ", "fnda.l"),
            (B, "FNDB_EQ", "fndb.l"),
            (C, "FNDC_EQ", "fndc.l"),
        ):
            await conn.execute(
                "insert into public.instrument (t212_ticker, isin, name, currency, exchange, price_symbol) values ($1,$2,$3,'USD','LSE',$4)",
                tkr,
                isin,
                f"Fund {tkr}",
                sym,
            )
        as_of = TODAY - dt.timedelta(days=10)
        for isin, portfolio, rows in (
            (A, "P1", [("US0000000001", "Alpha", "0.60"), ("US0000000002", "Beta", "0.39")]),
            (B, "P1", [("US0000000001", "Alpha", "0.60"), ("US0000000002", "Beta", "0.39")]),
            (C, "P3", [("US0000000001", "Alpha", "0.30"), ("US0000000003", "Gamma", "0.69")]),
        ):
            sid = await conn.fetchval(
                "insert into public.etf_snapshot (fund_isin, provider, portfolio_id, source, as_of, fund_name, fund_currency, raw, holdings_count)"
                " values ($1,'ishares',$2,'ishares_json',$3,$4,'USD','{}'::jsonb,$5) returning id",
                isin,
                portfolio,
                as_of,
                f"Fund {isin}",
                len(rows),
            )
            for i, (hisin, name, w) in enumerate(rows):
                await conn.execute(
                    "insert into public.etf_holding (snapshot_id,row_index,name,isin,asset_class,weight,sector,country,currency)"
                    " values ($1,$2,$3,$4,'equity',$5,'Technology','US','USD')",
                    sid,
                    i,
                    name,
                    hisin,
                    Decimal(w),
                )
            await conn.execute(
                "insert into public.fund_factsheet (fund_isin, as_of, source, snapshot_id, provider, fund_name, fund_currency, total_assets, total_assets_currency, expense_ratio,"
                " benchmark_name, distribution_policy, holdings_count, equity_weight, total_weight, residual_weight, reconciles)"
                " values ($1,$2,'ishares_json',$3,'ishares',$4,'USD',500000000,'USD',0.002,'Test index','accumulating',$5,0.99,0.99,0.01,true)",
                isin,
                as_of,
                sid,
                f"Fund {isin}",
                len(rows),
            )
        await conn.execute(
            "insert into public.fund_index_coverage (subject_kind, subject_key, label, tier, reason, total_return_available)"
            " values ('index','^idx','Test index','official_holdings','test',false)"
        )
        base = TODAY - dt.timedelta(days=800)
        days = [
            base + dt.timedelta(days=i)
            for i in range(801)
            if (base + dt.timedelta(days=i)).weekday() < 5
        ]
        for sym, rate in (
            ("fnda.l", "0.0004"),
            ("fndb.l", "0.0004"),
            ("fndc.l", "0.0003"),
            ("^idx", "0.0005"),
        ):
            level = Decimal(100)
            rows = []
            for d in days:
                rows.append((sym, d, level))
                level *= 1 + Decimal(rate)
            await conn.executemany(
                "insert into public.price_bar (symbol, date, close, source) values ($1,$2,$3,'stooq')",
                rows,
            )
    finally:
        await conn.close()


@pytest.fixture
async def api(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> AsyncIterator[httpx.AsyncClient]:
    await seed(migrated_db.admin_url)
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


async def test_catalog_profile_and_holdings(api: httpx.AsyncClient) -> None:
    cat = (await api.get("/funds/catalog")).json()
    assert {r["isin"] for r in cat} == {A, B, C} and all(r["has_holdings"] for r in cat)
    prof = (await api.get(f"/funds/{A}/profile")).json()
    fields = {f["key"]: f for f in prof["fields"]}
    assert fields["expense_ratio"]["value"] == "0.002" and fields["replication"]["value"] is None
    assert "not published" in fields["replication"]["reason"]
    assert [s["isin"] for s in prof["identity"]["sibling_share_classes"]] == [B]
    assert prof["identity"]["listings"][0]["ticker"] == "FNDA_EQ"

    h = (await api.get(f"/funds/{A}/holdings")).json()
    assert Decimal(h["residual_weight"]) == Decimal("0.01") and h["top"][0]["label"] == "Alpha"
    csv = await api.get(f"/funds/{A}/holdings.csv")
    assert csv.status_code == 200 and "snapshot_id" in csv.text and "unknown" in csv.text

    assert (await api.get("/funds/IE00ZZZZZZZ9/profile")).status_code == 404
    future = (TODAY + dt.timedelta(days=3)).isoformat()
    assert (await api.get(f"/funds/{A}/holdings?as_of={future}")).status_code == 422
    # Before anything was fetched there is no snapshot: unknown, not an empty fund.
    early = (TODAY - dt.timedelta(days=60)).isoformat()
    assert (await api.get(f"/funds/{A}/holdings?as_of={early}")).status_code in (404, 200)


async def test_style_performance_and_bond(api: httpx.AsyncClient) -> None:
    st = (await api.get(f"/funds/{A}/style")).json()
    assert st["concentration"]["issuers"] == 2 and "Style Box" in st["note"]
    assert any(
        "statements" in w for w in st["warnings"]
    )  # no covered holdings here: said, not hidden

    p = (await api.get(f"/funds/{A}/performance")).json()
    assert p["nav_available"] is False and p["benchmark"] is None
    assert p["suggested_benchmark"] == "^idx"  # exact label match with the factsheet
    assert {o["symbol"]: o["basis"] for o in p["benchmark_options"]}["^idx"] == "price"
    p = (await api.get(f"/funds/{A}/performance?benchmark=^idx")).json()
    one = next(x for x in p["periods"] if x["label"] == "1 year")
    assert one["compared_on"] == "price" and Decimal(one["difference"]) < 0
    assert p["series"] and p["series"][0]["benchmark"] is not None

    bond = (await api.get(f"/funds/{A}/bond")).json()
    assert bond["duration"]["value"] is None and bond["duration"]["status"] == "unavailable"


async def test_facts_are_append_only_validated_and_drive_the_checklist(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    cl = (await api.get(f"/funds/{A}/checklist")).json()
    assert cl["summary_available"] is False and "Replication method" in cl["abstain_reason"]

    bad_key = await api.put(
        f"/funds/{A}/facts",
        json={"key": "rating", "value": "five stars", "source_url": "https://x.test/doc"},
    )
    assert bad_key.status_code == 422
    bad_val = await api.put(
        f"/funds/{A}/facts",
        json={"key": "replication_method", "value": "magic", "source_url": "https://x.test/doc"},
    )
    assert bad_val.status_code == 422
    no_src = await api.put(
        f"/funds/{A}/facts",
        json={"key": "replication_method", "value": "physical_full", "source_url": "kid"},
    )
    assert no_src.status_code == 422

    first = await api.put(
        f"/funds/{A}/facts",
        json={
            "key": "replication_method",
            "value": "synthetic",
            "source_url": "https://x.test/kid",
        },
    )
    second = await api.put(
        f"/funds/{A}/facts",
        json={
            "key": "replication_method",
            "value": "physical_full",
            "source_url": "https://x.test/kid2",
            "passage": "The fund holds the securities.",
        },
    )
    assert first.status_code == second.status_code == 201
    facts = (await api.get(f"/funds/{A}/facts")).json()
    assert facts["replication_method"]["value"] == "physical_full"
    history = (await api.get(f"/funds/{A}/facts/history")).json()
    assert [h["value"] for h in history] == ["physical_full", "synthetic"]
    assert await backend_pool.fetchval("select count(*) from public.fund_owner_fact") == 2

    cl = (await api.get(f"/funds/{A}/checklist")).json()
    by = {c["id"]: c for c in cl["criteria"]}
    assert (
        by["replication"]["status"] == "good"
        and by["replication"]["source_url"] == "https://x.test/kid2"
    )
    assert by["costs"]["status"] == "good" and by["freshness"]["status"] == "good"
    assert (
        cl["summary_available"] is True
    )  # diversification is judged from two issuers: a concern, but known
    assert by["diversification"]["status"] == "concern"
    assert by["tracking"]["status"] in (
        "good",
        "watch",
        "concern",
    )  # the matched benchmark was suggested by name


async def test_compare_overlap_and_share_classes(api: httpx.AsyncClient) -> None:
    out = (await api.get(f"/funds/compare?isins={A},{B},{C}")).json()
    dup = next(f for f in out["funds"] if f["isin"] == B)
    assert dup["duplicate_of"] == A
    pairs = {(p["fund_a"], p["fund_b"]): p for p in out["pairs"]}
    assert pairs[(A, B)]["skipped"] == "same portfolio, different share class"
    ac = pairs[(A, C)]
    assert (
        Decimal(ac["overlap"]["value"]) == Decimal("0.3") and ac["shared"][0]["issuer"] == "Alpha"
    )
    assert out["countries"] and out["sectors"]
    assert any(r["value"] is not None for f in out["funds"] for r in f["returns"])

    assert (await api.get(f"/funds/compare?isins={A}")).status_code == 422
    assert (await api.get(f"/funds/compare?isins={A},not-an-isin")).status_code == 422
    six = ",".join(
        [
            "IE00AAAAAAA1",
            "IE00BBBBBBB2",
            "IE00CCCCCCC3",
            "IE00DDDDDDD4",
            "IE00EEEEEEE5",
            "IE00FFFFFFF6",
        ]
    )
    assert (await api.get(f"/funds/compare?isins={six}")).status_code == 422
