# ruff: noqa: E501, RUF001 - seed SQL stays readable
"""The company workbench against a real database: every endpoint's SQL runs, owner-only saves
version and refuse bad input. Needs STEWARD_TEST_DATABASE_URL (skipped otherwise)."""

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.company.statements import _concepts_of
from tests.conftest import MigratedDatabase

OWNER = "7f0c3d4e-0000-4000-8000-0000000000bb"
TODAY = dt.datetime.now(dt.UTC).date()
TICKER = "AAA_US_EQ"
pytestmark = pytest.mark.db


async def seed(admin_url: str) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute("insert into auth.users (id, email) values ($1, 'o@example.com')", OWNER)
        await conn.execute(
            "insert into public.instrument (t212_ticker, isin, name, currency, exchange, price_symbol, home_country, next_report_date)"
            " values ($1, 'US0000000001', 'Alpha Inc', 'USD', 'NYSE', 'aaa.us', 'US', $2)",
            TICKER,
            TODAY + dt.timedelta(days=20),
        )
        known = dt.datetime(2026, 2, 20, tzinfo=dt.UTC)

        async def fact(
            item: str,
            statement: str,
            value: str,
            start: dt.date | None,
            end: dt.date,
            unit: str = "USD",
            accession: str = "0000000001-26-000001",
        ) -> None:
            concept = _concepts_of(item)[0]
            await conn.execute(
                "insert into public.financial_statement_fact (ticker, concept, line_item, statement, period_start, period_end, basis, value, unit, currency,"
                " source, source_url, filing_url, accession, form, known_at, validation) values ($1,$2,$3,$4,$5,$6,'US_GAAP',$7,$8,'USD','sec','https://sec.test/x',"
                "'https://sec.test/filing', $9, '10-K', $10, '{\"reconciled\": true}'::jsonb)",
                TICKER,
                concept,
                item,
                statement,
                start,
                end,
                Decimal(value),
                unit,
                accession,
                known,
            )

        for year, rev, cogs, op, ni, cfo, capex, assets in (
            (2023, "900", "540", "150", "100", "130", "30", "800"),
            (2024, "1000", "560", "200", "140", "160", "40", "900"),
            (2025, "1100", "600", "230", "170", "190", "40", "1000"),
        ):
            s, e = dt.date(year, 1, 1), dt.date(year, 12, 31)
            await fact("revenue", "income", rev, s, e)
            await fact("cost_of_revenue", "income", cogs, s, e)
            await fact("operating_income", "income", op, s, e)
            await fact("net_income", "income", ni, s, e)
            await fact("weighted_shares_diluted", "income", "100", s, e, "shares")
            await fact("cash_from_operations", "cash_flow", cfo, s, e)
            await fact("capex", "cash_flow", capex, s, e)
            await fact("total_assets", "balance_sheet", assets, None, e)
            await fact("total_liabilities_and_equity", "balance_sheet", assets, None, e)
            await fact("cash", "balance_sheet", "200", None, e)
            await fact("long_term_debt_total", "balance_sheet", "100", None, e)
        base = TODAY - dt.timedelta(days=500)
        await conn.executemany(
            "insert into public.price_bar (symbol, date, close, source) values ('aaa.us',$1,$2,'stooq')",
            [(base + dt.timedelta(days=i), Decimal("40") + Decimal(i) / 50) for i in range(501)],
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


def ratio(view: dict, rid: str) -> dict:
    return next(r for g in view["groups"] for r in g["ratios"] if r["id"] == rid)


async def test_statements_link_every_cell_and_balance(api: httpx.AsyncClient) -> None:
    v = (await api.get(f"/companies/{TICKER}/statements?units=1")).json()
    assert [c["label"] for c in v["columns"]] == ["FY Dec 2023", "FY Dec 2024", "FY Dec 2025"]
    revenue = next(
        ln
        for t in v["statements"]
        if t["statement"] == "income"
        for ln in t["lines"]
        if ln["key"] == "revenue"
    )
    cell = revenue["cells"][v["columns"][-1]["id"]]
    assert (
        Decimal(cell["value"]) == 1100
        and cell["accession"]
        and cell["filing_url"]
        and cell["concept"]
    )
    gross = next(
        ln
        for t in v["statements"]
        if t["statement"] == "income"
        for ln in t["lines"]
        if ln["key"] == "gross_profit"
    )
    assert gross["cells"][v["columns"][-1]["id"]]["derived"] == "revenue − cost of revenue"
    assert all(c["passed"] for c in v["checks"] if c["name"] == "balance_sheet_balances")

    csv = await api.get(f"/companies/{TICKER}/statements.csv?units=1")
    assert (
        csv.status_code == 200
        and "text/csv" in csv.headers["content-type"]
        and "input_hash" in csv.text
    )

    reported = (await api.get(f"/companies/{TICKER}/statements?basis=reported&units=1")).json()
    assert any(
        ln["key"].startswith("us-gaap:") for t in reported["statements"] for ln in t["lines"]
    )


async def test_ratios_reproduce_from_cells_and_peer_median_is_withheld(
    api: httpx.AsyncClient,
) -> None:
    v = (await api.get(f"/companies/{TICKER}/ratios?peers=global")).json()
    net = ratio(v, "net_margin")
    assert abs(Decimal(net["latest"]["value"]) - Decimal(170) / Decimal(1100)) < Decimal("0.00001")
    assert net["inputs"] and net["inputs"][0]["accession"]
    assert ratio(v, "gross_margin")["latest"]["value"] is not None
    # No sector is stored here, so peers cannot be chosen and the reason is given.
    assert ratio(v, "pe")["peer"]["median"] is None and v["peer_excluded"]
    assert (
        ratio(v, "altman_z")["latest"]["value"] is None
        and "retained earnings" in ratio(v, "altman_z")["latest"]["reason"]
    )


async def test_valuation_saves_immutable_versions(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    out = (await api.post(f"/companies/{TICKER}/valuation", json={"selected": "base"})).json()
    assert {s["name"] for s in out["scenarios"]} == {"bear", "base", "bull"} and out["sensitivity"]
    body = {
        "name": "My set",
        "request": {"scenarios": [{"name": "base", "growth": ["0.05"]}], "selected": "base"},
    }
    first = (await api.post(f"/companies/{TICKER}/valuation/saved", json=body)).json()
    second = (await api.post(f"/companies/{TICKER}/valuation/saved", json=body)).json()
    assert (first["version"], second["version"]) == (1, 2) and first["method_version"] == "1.0.0"
    assert len((await api.get(f"/companies/{TICKER}/valuation/saved")).json()) == 2
    with pytest.raises(asyncpg.PostgresError, match="immutable"):
        await backend_pool.execute(
            "update public.company_valuation_scenario set note='x' where id=$1", first["id"]
        )


async def test_case_items_need_a_citation_to_be_confirmed(api: httpx.AsyncClient) -> None:
    bad = await api.post(
        f"/companies/{TICKER}/case",
        json={
            "kind": "moat",
            "moat_type": "scale",
            "statement": "Cost advantage from scale",
            "status": "confirmed",
        },
    )
    assert bad.status_code == 422 and "citation" in bad.text
    no_type = await api.post(
        f"/companies/{TICKER}/case", json={"kind": "moat", "statement": "Some moat"}
    )
    assert no_type.status_code == 422
    ok = await api.post(
        f"/companies/{TICKER}/case",
        json={
            "kind": "moat",
            "moat_type": "scale",
            "statement": "Largest plant network in the region",
            "status": "confirmed",
            "confidence": "medium",
            "evidence": [{"url": "https://example.com/10k", "passage": "we operate 40 plants"}],
        },
    )
    assert ok.status_code == 201
    item = ok.json()
    view = (await api.get(f"/companies/{TICKER}/case")).json()
    assert [i["id"] for i in view["items"]] == [item["id"]]
    assert (await api.delete(f"/companies/{TICKER}/case/{item['id']}")).status_code == 204
    assert (await api.get(f"/companies/{TICKER}/case")).json()["items"] == []


async def test_peer_groups_and_evidence_tabs_run(api: httpx.AsyncClient) -> None:
    saved = (
        await api.post(
            f"/companies/{TICKER}/peer-groups",
            json={"name": "Core", "peers": ["bbb_us_eq", "AAA_US_EQ", "CCC_US_EQ"]},
        )
    ).json()
    assert saved["peers"] == ["BBB_US_EQ", "CCC_US_EQ"]  # sorted, upper-cased, never itself
    compare = (
        await api.get(f"/companies/{TICKER}/peers?scope=group&group_id={saved['id']}")
    ).json()
    assert (
        compare["companies"][0]["is_subject"]
        and compare["rows"]
        and any("comparable peers" in w for w in compare["warnings"])
    )
    assert {e["ticker"] for e in compare["excluded"]} == {"BBB_US_EQ", "CCC_US_EQ"}
    assert (await api.delete(f"/companies/{TICKER}/peer-groups/{saved['id']}")).status_code == 204

    overview = (await api.get(f"/companies/{TICKER}/overview")).json()
    assert (
        overview["name"] == "Alpha Inc"
        and overview["last_close"] is not None
        and overview["upcoming"][0]["kind"] == "results"
    )
    assert any(c["area"] == "Filed statements" and c["present"] for c in overview["coverage"])
    est = (await api.get(f"/companies/{TICKER}/estimates")).json()
    assert est["points"] == [] and any("No forward consensus" in n for n in est["notes"])
    assert (await api.get(f"/companies/{TICKER}/network")).status_code == 200
    alloc = (await api.get(f"/companies/{TICKER}/capital-allocation")).json()
    assert len(alloc["years"]) == 3 and alloc["years"][-1]["fcf"] is not None
    assert (await api.get(f"/companies/{TICKER}/governance")).status_code == 200
    assert (await api.get("/companies/NOPE_US_EQ/overview")).status_code == 404
    assert (await api.get("/companies/NOPE_US_EQ/statements")).json()["columns"] == []
