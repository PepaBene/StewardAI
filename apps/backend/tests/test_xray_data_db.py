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
        (
            "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
            "shares",
            "100",
            start,
            end,
            "shares",
        ),
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
            ManualSegmentRow(
                label="United States",
                axis="geographic",
                value=600,
                currency="USD",
                period_start=start,
                period_end=end,
                source="annual report 2025",
            ),
            ManualSegmentRow(
                label="Germany",
                axis="geographic",
                value=400,
                currency="USD",
                period_start=start,
                period_end=end,
                source="annual report 2025",
            ),
        ],
    )
    async with pool.acquire() as conn:
        await save_config(
            conn, "holding_profiles", HoldingProfilesConfig(profiles=[profile]), created_by=OWNER
        )


@pytest.fixture
async def api(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> AsyncIterator[httpx.AsyncClient]:
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
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_stored_statements_produce_issuer_facts_and_a_covered_fundamental(
    api: httpx.AsyncClient,
) -> None:
    body = (await api.get("/portfolio/xray/fundamentals")).json()
    metrics = {m["id"]: m for m in body["metrics"]}
    assert metrics["profitability"]["value"] == pytest.approx(0.5)  # gross profit 500 / assets 1000
    assert body["scored_issuers"] == 1
    assert body["covered_weight"] == pytest.approx(
        0.36
    )  # Apple, direct 0.30 plus 0.06 via the fund
    assert metrics["profitability"]["coverage"] == pytest.approx(0.36 / 0.84)
    # the other two issuers have no statements: named as excluded, not silently dropped
    excluded = {e["label"] for e in metrics["profitability"]["excluded"]}
    assert {"Microsoft Corp", "Nestle SA"} <= excluded


async def test_reported_revenue_geography_becomes_a_revenue_country_lens(
    api: httpx.AsyncClient,
) -> None:
    body = (await api.get("/portfolio/xray")).json()
    lens = view(body, "revenue_country")
    classified = {b["key"]: b for b in lens["buckets"] if b["kind"] == "classified"}
    assert set(classified) == {"US", "DE"}
    assert classified["US"]["weight"] / classified["DE"]["weight"] == pytest.approx(1.5)
    assert classified["US"]["basis"].get("reported", 0) > 0
    assert lens["reconciles"] is True


SOURCE = {"source_url": "https://example.com/report", "as_of": "2026-09-30"}


async def reported_europe(pool: asyncpg.Pool) -> None:
    """The company reports 600 for the United States and 400 for "Europe", which is no country."""
    start, end = dt.date(2025, 1, 1), dt.date(2025, 12, 31)
    rows = [
        ManualSegmentRow(
            label=label,
            axis="geographic",
            value=value,
            currency="USD",
            period_start=start,
            period_end=end,
            source="annual report 2025",
        )
        for label, value in (("United States", 600), ("Europe", 400))
    ]
    async with pool.acquire() as conn:
        await save_config(
            conn,
            "holding_profiles",
            HoldingProfilesConfig(profiles=[HoldingProfile(ticker=TICKER, manual_segments=rows)]),
            created_by=OWNER,
        )
    xray_service.clear_cache()


async def test_a_region_you_spread_reaches_the_revenue_country_lens_and_leaves_with_its_removal(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await reported_europe(backend_pool)

    def buckets(body: dict) -> dict:  # type: ignore[type-arg]
        return {
            b["key"]: b
            for b in view(body, "revenue_country")["buckets"]
            if b["kind"] == "classified"
        }

    assert set(buckets((await api.get("/portfolio/xray")).json())) == {"US"}  # Europe is no country

    bad = await api.post(
        "/portfolio/xray/inputs/regions",
        json={"region": "Europe", "countries": {"DE": 0.6, "FR": 0.3}, **SOURCE},
    )
    assert bad.status_code == 422  # does not add up to 1
    saved = await api.post(
        "/portfolio/xray/inputs/regions",
        json={"region": "Europe", "countries": {"Germany": 0.5, "France": 0.5}, **SOURCE},
    )
    assert saved.status_code == 201 and saved.json()["payload"]["countries"] == {
        "DE": 0.5,
        "FR": 0.5,
    }

    after = buckets((await api.get("/portfolio/xray")).json())  # the save dropped the cached X-Ray
    assert set(after) == {"US", "DE", "FR"}
    assert after["DE"]["weight"] == pytest.approx(after["FR"]["weight"])
    assert after["US"]["weight"] / after["DE"]["weight"] == pytest.approx(600 / 200)
    assert after["DE"]["basis"].get("owner", 0) > 0 and "reported" not in after["DE"]["basis"]

    listed = (await api.get("/portfolio/xray/inputs")).json()
    assert [(i["kind"], i["key"]) for i in listed] == [("region_split", "Europe")]
    assert (await api.delete(f"/portfolio/xray/inputs/{listed[0]['id']}")).status_code == 204
    assert (await api.delete(f"/portfolio/xray/inputs/{listed[0]['id']}")).status_code == 404
    assert set(buckets((await api.get("/portfolio/xray")).json())) == {"US"}


async def test_the_newest_entry_for_a_name_is_the_one_in_force(api: httpx.AsyncClient) -> None:
    for countries in ({"DE": 1.0}, {"FR": 1.0}):
        r = await api.post(
            "/portfolio/xray/inputs/regions",
            json={"region": "Europe", "countries": countries, **SOURCE},
        )
        assert r.status_code == 201
    listed = (await api.get("/portfolio/xray/inputs")).json()
    assert len(listed) == 1 and listed[0]["payload"]["countries"] == {"FR": 1.0}


async def test_supply_links_and_index_weights_are_compared_with_what_you_hold(
    api: httpx.AsyncClient,
) -> None:
    edge = {
        "supplier": "tsm",
        "customer": "AAPL",
        "product": "chips",
        "customer_share": 0.25,
        **SOURCE,
    }
    assert (await api.post("/portfolio/xray/inputs/supply-edges", json=edge)).status_code == 201
    assert (
        await api.post("/portfolio/xray/inputs/supply-edges", json={**edge, "supplier": "AAPL"})
    ).status_code == 422
    graph = (await api.get("/portfolio/xray/supply-chain")).json()
    nodes = {n["symbol"]: n for n in graph["nodes"]}
    assert nodes["TSM"]["held"] is False and nodes["AAPL"]["held"] is True
    # the fund's stored rows carry no ticker, so only the direct 0.30 can be matched by symbol
    assert nodes["AAPL"]["weight"] == pytest.approx(0.30)
    assert nodes["TSM"]["level"] == 0 and nodes["AAPL"]["level"] == 1 and graph["linked"] == []

    empty = (await api.get("/portfolio/xray/index-weights")).json()
    assert empty["comparisons"] == [] and empty["notes"]
    index = {
        "label": "Test index",
        "constituents": [
            {"name": "Apple", "isin": "US0378331005", "ticker": "AAPL", "weight": 0.6},
            {"name": "Other", "weight": 0.4},
        ],
        **SOURCE,
    }
    assert (await api.post("/portfolio/xray/inputs/index-weights", json=index)).status_code == 201
    over = {**index, "constituents": [{"name": "A", "weight": 0.8}, {"name": "B", "weight": 0.8}]}
    assert (await api.post("/portfolio/xray/inputs/index-weights", json=over)).status_code == 422
    result = (await api.get("/portfolio/xray/index-weights")).json()["comparisons"][0]
    assert result["label"] == "Test index" and result["source_url"] == SOURCE["source_url"]
    assert result["active_share"] is not None and 0 < result["active_share"] < 1
    assert result["unmatched"] == 1 and result["partial"] is False


CSPX = "IE00B5BMR087"


async def test_index_weights_are_derived_from_the_funds_stored_holdings_and_refreshed(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    first = await api.post(
        "/portfolio/xray/inputs/derive/index-weights", json={"fund_isin": CSPX, "label": "S&P 500"}
    )
    assert first.status_code == 200 and first.json()["changed"] is True
    item = first.json()["item"]
    assert item["origin"] == "derived" and item["derived_from"] == {"fund_isin": CSPX}
    assert (
        item["source_url"] == "https://www.ishares.com/uk/individual/en/products/253743"
        and item["as_of"]
    )
    total = sum(c["weight"] for c in item["payload"]["constituents"])
    assert total == pytest.approx(1.0)
    again = await api.post(
        "/portfolio/xray/inputs/derive/index-weights", json={"fund_isin": CSPX, "label": "S&P 500"}
    )
    assert again.json()["changed"] is False and again.json()["item"]["id"] == item["id"]

    result = (await api.get("/portfolio/xray/index-weights")).json()["comparisons"][0]
    assert result["label"] == "S&P 500" and result["active_share"] is not None

    counts = await derive_refresh(backend_pool)
    assert counts == {"checked": 1, "updated": 0, "failed": 0}  # nothing newer was stored

    # an entry you type yourself for the same name is newer, so a refresh leaves it alone
    typed = {
        "label": "S&P 500",
        "constituents": [{"name": "Apple", "isin": "US0378331005", "weight": 1.0}],
        **SOURCE,
    }
    assert (await api.post("/portfolio/xray/inputs/index-weights", json=typed)).status_code == 201
    assert await derive_refresh(backend_pool) == {"checked": 0, "updated": 0, "failed": 0}
    listed = (await api.get("/portfolio/xray/inputs")).json()
    assert [i["origin"] for i in listed] == ["owner"]


async def derive_refresh(pool: asyncpg.Pool) -> dict[str, int]:
    from steward.xray.derive import refresh_all

    return await refresh_all(pool)


async def test_a_region_derived_from_a_fund_reaches_the_revenue_country_lens(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await reported_europe(backend_pool)
    done = await api.post(
        "/portfolio/xray/inputs/derive/regions", json={"region": "Europe", "fund_isin": CSPX}
    )
    assert done.status_code == 200 and done.json()["item"]["origin"] == "derived"
    body = (await api.get("/portfolio/xray")).json()
    buckets = {
        b["key"]: b for b in view(body, "revenue_country")["buckets"] if b["kind"] == "classified"
    }
    assert {"US", "CH"} <= set(buckets) and buckets["CH"]["basis"].get("owner", 0) > 0


async def test_deriving_from_a_fund_that_is_not_looked_through_is_refused(
    api: httpx.AsyncClient,
) -> None:
    res = await api.post(
        "/portfolio/xray/inputs/derive/index-weights", json={"fund_isin": "IE00BZ163K21"}
    )
    assert res.status_code == 422 and "not one of the funds" in res.json()["detail"]
    benchmarks = await api.post("/portfolio/xray/inputs/derive/benchmarks")
    assert benchmarks.status_code == 200 and benchmarks.json()["saved"] == 1  # the S&P 500 proxy


async def test_confirmed_filing_disclosures_join_the_graph_but_unconfirmed_or_unnamed_ones_do_not(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    async def disclose(status: str, name: str | None, anonymous: bool) -> None:
        await backend_pool.execute(
            "insert into public.relationship_disclosure (ticker, relationship, direction, counterparty_name, anonymous_counterparty,"
            " reported_share, share_basis, fiscal_period, valid_to, passage, source_url, source, status)"
            " values ($1,'customer','issuer_sells_to',$2,$3,0.2,'issuer_revenue','FY2025',$4,'passage','https://sec.test/10k','xbrl',$5)",
            TICKER, name, anonymous, dt.date(2025, 12, 31), status,
        )  # fmt: skip

    await disclose("confirmed", "Contoso Ltd", False)
    await disclose("suggested", "Fabrikam Inc", False)
    await disclose("confirmed", None, True)
    graph = (await api.get("/portfolio/xray/supply-chain")).json()
    assert [(e["supplier"], e["customer"], e["origin"]) for e in graph["edges"]] == [
        ("AAPL", "CONTOSO LTD", "filing")
    ]
    edge = graph["edges"][0]
    assert edge["id"] is None and edge["customer_share"] == pytest.approx(0.2)
    assert (
        edge["source_url"] == "https://sec.test/10k"
        and edge["fiscal_period"] == "FY2025"
        and edge["as_of"] == "2025-12-31"
    )
    # an entry you type for the same pair replaces the filing's
    typed = {"supplier": "AAPL", "customer": "contoso ltd", "product": "devices", **SOURCE}
    assert (await api.post("/portfolio/xray/inputs/supply-edges", json=typed)).status_code == 201
    graph = (await api.get("/portfolio/xray/supply-chain")).json()
    assert [e["origin"] for e in graph["edges"]] == ["owner"]


async def test_a_future_date_or_a_missing_source_is_refused(api: httpx.AsyncClient) -> None:
    body = {"region": "Europe", "countries": {"DE": 1.0}}
    assert (
        await api.post(
            "/portfolio/xray/inputs/regions",
            json={**body, "source_url": "notes", "as_of": "2026-01-01"},
        )
    ).status_code == 422
    assert (
        await api.post(
            "/portfolio/xray/inputs/regions",
            json={**body, "source_url": "https://x.test", "as_of": "2999-01-01"},
        )
    ).status_code == 422


async def test_each_fund_is_listed_with_why_it_has_no_tracking_difference(
    api: httpx.AsyncClient,
) -> None:
    body = (await api.get("/portfolio/xray/funds-vs-benchmark")).json()
    # the only held fund is also the default benchmark proxy, so it is listed once
    assert len(body["rows"]) == 1 and body["rows"][0]["is_proxy"]
    assert body["fund_weight"] == pytest.approx(0.60)
    assert all(r["tracking_difference"] is None and r["reason"] for r in body["rows"])
    assert body["average"] is None and body["covered_weight"] == 0
    assert any("No held fund has a benchmark" in n for n in body["notes"])
