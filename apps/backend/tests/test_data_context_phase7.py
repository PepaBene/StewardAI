"""Phase 7 read models: coverage/access API (D7.1), holding data context (D7.2), portfolio
data context (D7.3), research context and chat tools (D7.4), evidence summary (D7.7).

All data is seeded; nothing is fetched. Fixtures for the Schedule 13D/G flows come from
test_control_stakes (documented XML layout, not live recordings).
"""

import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.data_context_routes import router as data_context_router
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from api.research_context_routes import router as research_context_router
from api.source_routes import redact
from steward.chat.tools import TOOL_DEFINITIONS, ChatTools
from steward.data_context.common import provenance_of
from steward.data_context.research import company_context
from steward.research.gather import data_context_section
from steward.research.sources import Registry
from tests.test_control_stakes import ACC_13D, ACC_13DA, run_collector, seed

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
UTC = dt.UTC


def at(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=UTC)


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.include_router(data_context_router)
    app.include_router(research_context_router)

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def anonymous_api(auth_configured: None) -> AsyncIterator[httpx.AsyncClient]:
    """Same routes, real owner dependency, no bearer token."""
    app = create_app()
    app.include_router(data_context_router)
    app.include_router(research_context_router)

    async def no_pool() -> None:
        return None

    app.dependency_overrides[get_pool] = no_pool
    app.dependency_overrides[get_pool_or_error] = no_pool
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def walk_keys(node: object) -> list[str]:
    if isinstance(node, dict):
        return [str(k) for k in node] + [k for v in node.values() for k in walk_keys(v)]
    if isinstance(node, list):
        return [k for v in node for k in walk_keys(v)]
    return []


# ---------------------------------------------------------------------------
# Pure units
# ---------------------------------------------------------------------------


def test_redact_masks_credentials_in_free_text() -> None:
    text = "GET /q?apikey=ABCD1234&x=1 failed; Authorization: Bearer abc.def; token=zzz"
    out = redact(text) or ""
    assert "ABCD1234" not in out and "abc.def" not in out and "zzz" not in out
    assert redact("a" * 40) == "[redacted]"
    assert redact(None) is None
    assert redact("quota exhausted until tomorrow") == "quota exhausted until tomorrow"


def test_provenance_separates_primary_from_vendor() -> None:
    assert provenance_of("sec_xbrl") == "primary"
    assert provenance_of("ecb") == "primary"
    assert provenance_of("alpha_vantage") == "vendor"
    assert provenance_of("finnhub") == "vendor"
    assert provenance_of("owner_review") == "owner"
    assert provenance_of("something_else") == "unclassified"
    assert provenance_of(None) == "unclassified"


def test_chat_tools_are_declared() -> None:
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert {"country_context", "company_context"} <= names


# ---------------------------------------------------------------------------
# Owner-only
# ---------------------------------------------------------------------------

ROUTES = [
    "/holdings/EXM_US_EQ/data-context",
    "/portfolio/data-context",
    "/holdings/EXM_US_EQ/evidence-summary",
    "/holdings/EXM_US_EQ/evidence/deals",
    "/research-context/country/DE",
    "/research-context/industry/8542",
    "/research-context/company/EXM_US_EQ",
]


@pytest.mark.parametrize("path", ROUTES)
async def test_new_routes_require_the_owner(anonymous_api: httpx.AsyncClient, path: str) -> None:
    response = await anonymous_api.get(path)
    assert response.status_code == 401


@pytest.mark.db
async def test_future_cutoff_is_rejected(api: httpx.AsyncClient) -> None:
    future = (dt.datetime.now(UTC) + dt.timedelta(days=2)).isoformat()
    for path in ("/holdings/EXM_US_EQ/data-context", "/research-context/country/DE"):
        response = await api.get(path, params={"as_of": future})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# D7.1 coverage and access
# ---------------------------------------------------------------------------

SECRET = "sk-live-SUPERSECRETKEY1234567890abcdefghij"


@pytest.mark.db
async def test_access_and_coverage_filters_headroom_and_no_secret_leak(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINNHUB_API_KEY", SECRET)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", SECRET)
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    p = backend_pool
    await p.execute(
        "update public.provider_access set quota=25, quota_period='day', state='paid_only',"
        " last_success_at=now()-interval '10 days', detail=$1"
        " where provider='alpha_vantage' and endpoint='daily_adjusted'",
        f"denied for apikey={SECRET}",
    )
    await p.execute(
        "update public.provider_access set quota=100, quota_period='week', state='available',"
        " last_success_at=now() where provider='finnhub' and endpoint='peers'"
    )
    await p.execute(
        "insert into public.provider_quota_usage"
        " (provider,endpoint,window_kind,window_start,window_end,credits,requests)"
        " values ('alpha_vantage','daily_adjusted','day',now()-interval '1 hour',"
        " now()+interval '23 hours',10,10)"
    )
    await p.execute(
        "insert into public.provider_fetch_audit (provider,endpoint,state,status_code,"
        " error_redacted) values ('alpha_vantage','daily_adjusted','paid_only',403,$1)",
        f"premium endpoint, key {SECRET} rejected",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        everything = await client.get("/sources/access")
        assert everything.status_code == 200
        assert SECRET not in everything.text and "SUPERSECRETKEY" not in everything.text
        assert "credential_env" not in walk_keys(everything.json())
        rows = {(r["provider"], r["endpoint"]): r for r in everything.json()}

        av = rows[("alpha_vantage", "daily_adjusted")]
        assert av["quota"] is not None and Decimal(av["quota_used"]) == 10
        assert Decimal(av["quota_remaining"]) == 15
        assert Decimal(av["quota_headroom"]) == Decimal("0.6")
        assert av["freshness"] == "stale" and av["last_denial_state"] == "paid_only"
        assert av["last_denial_status_code"] == 403 and "[redacted]" in av["last_denial_detail"]
        assert "[redacted]" in av["detail"]

        # A weekly quota is not tracked per window: headroom is unknown, never invented.
        peers = rows[("finnhub", "peers")]
        assert peers["quota_headroom"] is None and peers["quota_used"] is None
        assert "week" in peers["headroom_reason"] and peers["freshness"] == "recent"
        # No quota recorded at all.
        assert rows[("finnhub", "quote")]["quota_headroom"] is None
        assert rows[("finnhub", "quote")]["freshness"] == "never_succeeded"

        only_denied = (await client.get("/sources/access", params={"denied_only": "true"})).json()
        assert [(r["provider"], r["endpoint"]) for r in only_denied] == [
            ("alpha_vantage", "daily_adjusted")
        ]
        by_state = (await client.get("/sources/access", params={"state": "paid_only"})).json()
        assert all(r["state"] == "paid_only" for r in by_state) and by_state
        by_provider = (await client.get("/sources/access", params={"provider": "finnhub"})).json()
        assert {r["provider"] for r in by_provider} == {"finnhub"}

        await p.execute(
            "insert into public.instrument (t212_ticker, currency) values ('EXM','USD')"
        )
        await p.execute(
            "insert into public.macro_observation (series_id,source,geography,period,value,unit,"
            " frequency,known_at,vintage,source_url) values ('cpi','oecd','DE','2026-08-01',2,"
            " 'percent','M','2026-09-01','v1','u')"
        )
        for provider, dataset, key, state, exp, avail, last in (
            ("oecd", "cpi", "DE", "available", 12, 12, "2026-08-01"),
            ("oecd", "cpi", "FR", "not_covered", None, None, None),
            ("sec_xbrl", "revenue", "EXM", "available", 8, 6, "2026-06-30"),
            ("finnhub", "peers", "EXM", "paid_only", None, None, None),
        ):
            await p.execute(
                "insert into public.coverage_status (provider,dataset,entity_key,state,"
                " expected_periods,available_periods,latest_period,reason) values"
                " ($1,$2,$3,$4,$5,$6,$7,$8)",
                provider,
                dataset,
                key,
                state,
                exp,
                avail,
                dt.date.fromisoformat(last) if last else None,
                f"tested with token={SECRET}" if state == "paid_only" else None,
            )
        coverage = (await client.get("/data/coverage")).json()
        assert SECRET not in json.dumps(coverage)
        kinds = {(c["entity_key"], c["entity_kind"]) for c in coverage}
        assert ("EXM", "security") in kinds and ("DE", "geography") in kinds
        assert ("FR", "other") in kinds  # unknown entity kind stays unknown, not guessed
        revenue = next(c for c in coverage if c["dataset"] == "revenue")
        assert Decimal(revenue["coverage_ratio"]) == Decimal("0.75")
        assert revenue["metric"] == "revenue" and revenue["source"] == "sec_xbrl"
        unknown = next(c for c in coverage if c["entity_key"] == "FR")
        assert unknown["coverage_ratio"] is None and unknown["age_days"] is None

        filtered = (await client.get("/data/coverage", params={"entity_kind": "geography"})).json()
        assert [c["entity_key"] for c in filtered] == ["DE"]
        by_period = (await client.get("/data/coverage", params={"period": "2026-07-01"})).json()
        assert {c["entity_key"] for c in by_period} >= {"FR", "EXM"}  # open spans match
        assert (await client.get("/data/coverage", params={"state": "paid_only"})).json()[0][
            "reason"
        ] == "tested with token=[redacted]"

        summary = (await client.get("/data/coverage/summary", params={"group_by": "source"})).json()
        assert summary["total_entries"] == 4
        oecd = next(g for g in summary["groups"] if g["key"] == "oecd")
        assert oecd["entries"] == 2 and oecd["by_state"] == {"available": 1, "not_covered": 1}
        # Denominators: only entries stating an expected count contribute to the sum.
        assert oecd["expected_periods"] == 12 and oecd["entries_with_expected"] == 1
        geo = (await client.get("/data/coverage/summary", params={"group_by": "geography"})).json()
        assert {g["key"] for g in geo["groups"]} == {"DE", None}


# ---------------------------------------------------------------------------
# Shared seed for D7.2 - D7.4
# ---------------------------------------------------------------------------


async def seed_world(p: asyncpg.Pool) -> dict[str, int]:
    """Two instruments, one with a confirmed issuer link; macro, trade and mapping rows."""
    for ticker, symbol in (("EXM_US_EQ", "EXM"), ("UNL_US_EQ", "UNL")):
        await p.execute(
            "insert into public.instrument (t212_ticker,name,currency,price_symbol)"
            " values ($1,$2,'USD',$3)",
            ticker,
            f"{ticker} name",
            symbol,
        )
    issuer = await p.fetchval(
        "insert into public.issuer (legal_name,country) values ('Example Corp','US') returning id"
    )
    security = await p.fetchval(
        "insert into public.security (issuer_id,kind,name,currency)"
        " values ($1,'common','Example Corp','USD') returning id",
        issuer,
    )
    await p.execute(
        "insert into public.instrument_security_link (ticker,security_id,status)"
        " values ('EXM_US_EQ',$1,'confirmed')",
        security,
    )
    await p.execute(
        "insert into public.instrument_security_link (ticker,status)"
        " values ('UNL_US_EQ','unresolved')"
    )
    # Active exposure: 50 % reported DE, 20 % estimated US, 30 % reported HS 8542, plus a
    # zero-weight FR mapping that must never produce an effect.
    for geo, hs, basis, weight in (
        ("DE", None, "reported", "0.5"),
        ("US", None, "estimated", "0.2"),
        (None, "8542", "reported", "0.3"),
    ):
        await p.execute(
            "insert into public.exposure_mapping (issuer_id,segment,geography,hs_revision,hs_code,"
            " basis,weight,valid_from) values ($1,'segment',$2,$3,$4,$5,$6,'2026-01-01')",
            issuer,
            geo,
            "HS2022" if hs else None,
            hs,
            basis,
            Decimal(weight),
        )
    await p.execute(
        "insert into public.exposure_mapping (issuer_id,segment,geography,basis,weight,valid_from)"
        " values ($1,'nothing','FR','reported',0,'2026-01-01')",
        issuer,
    )
    # Mappings count from the day they were entered, not from their valid_from.
    await p.execute("update public.exposure_mapping set created_at='2026-01-02'")
    macro = [
        ("DE", "2026-07-01", "2.0", "v1", 0, "2026-08-05"),
        ("DE", "2026-08-01", "2.4", "v1", 0, "2026-09-05"),
        ("DE", "2026-08-01", "2.9", "v2", 1, "2026-09-25"),  # later revision
        ("US", "2026-07-01", "3.0", "v1", 0, "2026-08-05"),
        ("US", "2026-08-01", "3.5", "v1", 0, "2026-09-05"),
        ("FR", "2026-07-01", "1.0", "v1", 0, "2026-08-05"),
        ("FR", "2026-08-01", "1.2", "v1", 0, "2026-09-05"),
    ]
    for geo, period, value, vintage, revision, known in macro:
        await p.execute(
            "insert into public.macro_observation (series_id,source,geography,period,value,unit,"
            " frequency,known_at,vintage,revision,source_url) values ('cpi_yoy','oecd',$1,$2,$3,"
            " 'percent','M',$4,$5,$6,'https://example.test/oecd')",
            geo,
            dt.date.fromisoformat(period),
            Decimal(value),
            at(known),
            vintage,
            revision,
        )
    await p.execute(
        "insert into public.trade_observation (reporter,partner,flow,hs_revision,hs_code,period,"
        " trade_value,known_at,source_url) values ('DE','US','export','HS2022','8542',"
        " '2026-06-01',1000,'2026-09-01','https://example.test/comtrade'),"
        " ('DE','FR','export','HS2022','8542','2026-06-01',500,'2026-09-01','u')"
    )
    return {"issuer": issuer, "security": security}


async def seed_company(p: asyncpg.Pool, ids: dict[str, int]) -> None:
    for source, value, revision, known in (
        ("sec_xbrl", "100", 0, "2026-08-01"),
        ("sec_xbrl", "110", 1, "2026-09-20"),  # restatement learned later
        ("alpha_vantage", "103", 0, "2026-08-02"),
    ):
        await p.execute(
            "insert into public.financial_statement_fact (issuer_id,ticker,concept,label,"
            " statement,period_end,fiscal_period,basis,value,unit,currency,source,source_url,"
            " known_at,revision) values ($1,'EXM_US_EQ','Revenues','Revenue','income',"
            " '2026-06-30','Q2','reported',$2,'USD','USD',$3,'https://example.test/f',$4,$5)",
            ids["issuer"],
            Decimal(value),
            source,
            at(known),
            revision,
        )
    # A note carries text, never a value: unknown stays null.
    await p.execute(
        "insert into public.financial_statement_fact (ticker,concept,statement,period_end,"
        " basis,value,unit,source,source_url,known_at,text_block) values ('EXM_US_EQ',"
        " 'LeasesNote','note','2026-06-30','reported',null,'text','sec_xbrl','u',"
        " '2026-08-01','Lease obligations extend to 2031.')"
    )
    pref = await p.fetchval(
        "insert into public.source_observation (domain,metric,issuer_id,period_start,period_end,"
        " value,unit,source,source_url,known_at,access_state) values ('fundamentals','revenue',"
        " $1,'2026-04-01','2026-06-30',100,'USD','sec_xbrl','u','2026-08-01','available')"
        " returning id",
        ids["issuer"],
    )
    alt = await p.fetchval(
        "insert into public.source_observation (domain,metric,issuer_id,period_start,period_end,"
        " value,unit,source,source_url,known_at,access_state) values ('fundamentals','revenue',"
        " $1,'2026-04-01','2026-06-30',103,'USD','alpha_vantage','u','2026-08-02','available')"
        " returning id",
        ids["issuer"],
    )
    await p.execute(
        "insert into public.source_conflict (metric,preferred_observation_id,"
        " alternative_observation_id,tolerance,difference) values ('revenue',$1,$2,0.01,3)",
        pref,
        alt,
    )
    await p.execute(
        "insert into public.company_graph_edge (subject_key,object_key,object_name,edge_type,"
        " direction,status,evidence_tier,source,source_url,passage,known_at) values"
        " ('EXM_US_EQ','acme','Acme Ltd','supplier','incoming','confirmed','primary','sec_xbrl',"
        " 'https://example.test/e1','Acme supplies our wafers under a 2024 agreement.',"
        " '2026-08-10'),"
        " ('EXM_US_EQ','zed','Zed Inc','partner','symmetric','suggested','candidate','diffbot',"
        " 'https://example.test/e2',null,'2026-08-11')"
    )


@pytest.mark.db
async def test_holding_data_context_composes_provenance_disagreements_and_gaps(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    ids = await seed_world(backend_pool)
    await seed_company(backend_pool, ids)

    body = (
        await api.get("/holdings/EXM_US_EQ/data-context", params={"as_of": "2026-09-10T00:00:00Z"})
    ).json()
    history = body["financial_history"]
    revenue = [f for f in history["facts"] if f["concept"] == "Revenues"]
    # The restatement known only on 2026-09-20 is invisible at the earlier cutoff.
    assert sorted(f["revision"] for f in revenue) == [0, 0]
    assert {f["provenance"] for f in revenue} == {"primary", "vendor"}
    assert history["provenance"] == {"primary": 2, "vendor": 1}
    note = next(f for f in history["facts"] if f["concept"] == "LeasesNote")
    assert note["value"] is None and note["has_text_block"] is True
    assert "Lease obligations" not in json.dumps(history)  # the text itself is not duplicated

    assert body["identity"]["status"] == "confirmed"
    disagreement = body["source_disagreements"][0]
    assert disagreement["preferred_provenance"] == "primary"
    assert disagreement["alternative_provenance"] == "vendor"
    assert Decimal(disagreement["difference"]) == 3

    macro = body["country_exposure"]
    assert {o["geography"] for o in macro["observations"]} == {"DE", "US"}
    de = next(o for o in macro["observations"] if o["geography"] == "DE")
    assert Decimal(de["value"]) == Decimal("2.4")  # not the later 2.9 revision
    trade = body["trade_exposure"][0]
    assert trade["hs_code"] == "8542" and trade["latest_period"] == "2026-06-01"
    assert [Decimal(r["trade_value"]) for r in trade["rows"]] == [1000, 500]

    network = body["business_network"]
    assert {e["status"] for e in network["edges"]} == {"confirmed", "suggested"}
    gap_domains = {g["domain"] for g in body["gaps"]}
    assert {"forward_data", "patents"} <= gap_domains
    assert "financial_history" not in gap_domains and "business_network" not in gap_domains
    assert set(body["research_observation_provenance"]) == set()

    latest = (await api.get("/holdings/EXM_US_EQ/data-context")).json()
    latest_revenue = [f for f in latest["financial_history"]["facts"] if f["concept"] == "Revenues"]
    assert sorted(f["revision"] for f in latest_revenue) == [0, 0, 1]
    de_latest = next(
        o for o in latest["country_exposure"]["observations"] if o["geography"] == "DE"
    )
    assert Decimal(de_latest["value"]) == Decimal("2.9")


@pytest.mark.db
async def test_unresolved_holding_reports_gaps_not_empty_success(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed_world(backend_pool)
    body = (await api.get("/holdings/UNL_US_EQ/data-context")).json()
    by_domain = {g["domain"]: g for g in body["gaps"]}
    assert by_domain["identity"]["state"] == "unresolved"
    assert by_domain["country_exposure"]["state"] == "missing"
    assert body["financial_history"]["total_rows"] == 0
    assert body["financial_history"]["earliest_period"] is None
    assert body["source_disagreements"] == []
    unknown = (await api.get("/holdings/NOPE/data-context")).json()
    assert {g["domain"]: g["state"] for g in unknown["gaps"]}["identity"] == "missing"


# ---------------------------------------------------------------------------
# D7.3 portfolio
# ---------------------------------------------------------------------------


async def seed_positions(p: asyncpg.Pool) -> None:
    job = await p.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'manual',"
        " 'succeeded') returning id"
    )
    await p.execute(
        "insert into account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency,"
        " fx_source, fx_rate, cash_base, invested_base, total_base, weight_basis) values"
        " ('2026-09-25', $1, now(), 1, 'USD', 1000, 0, 0, 0, 1000, 1000, 0, 0, 'USD', 'ecb',"
        " 1, 0, 1000, 1000, 'total')",
        job,
    )
    for ticker, value in (("EXM_US_EQ", 600), ("UNL_US_EQ", 400)):
        await p.execute(
            "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
            " avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
            " values ('2026-09-25', $2, $1, now(), 1, 1, 1, 'USD', $3, 'USD', $3, $3, 0, 'USD',"
            " 'ecb', 1, $3)",
            job,
            ticker,
            Decimal(value),
        )
    await p.execute(
        "insert into public.price_return_series (symbol,start_date,end_date,currency,"
        " adjustment_basis,source,value,coverage,expected_sessions,observed_sessions,"
        " missing_actions,known_at) values"
        " ('EXM','2026-01-01','2026-09-01','USD','price_raw','yahoo',0.1,1,170,170,'{}',"
        "  '2026-09-02'),"
        " ('EXM','2026-01-01','2026-09-01','USD','total_return','yahoo',null,0.9,170,153,"
        "  '{cash_dividend}','2026-09-02')"
    )


@pytest.mark.db
async def test_portfolio_context_rolls_up_only_mapped_holdings_with_denominators(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed_world(backend_pool)
    await seed_positions(backend_pool)
    body = (await api.get("/portfolio/data-context", params={"as_of": "2026-09-26"})).json()
    assert Decimal(body["total_value"]) == 1000 and body["position_count"] == 2
    ident = body["identity_coverage"]
    assert Decimal(ident["numerator"]) == 600 and Decimal(ident["denominator"]) == 1000

    country = body["country_concentration"]
    assert Decimal(country["mapped"]["numerator"]) == 420  # 600 x (0.5 + 0.2)
    assert Decimal(country["mapped"]["denominator"]) == 1000
    assert Decimal(country["unmapped_value"]) == 580  # 400 unresolved + 600 x 0.3 unmapped
    assert country["unmapped_tickers"] == ["UNL_US_EQ"]
    rows = {r["key"]: r for r in country["rows"]}
    assert set(rows) == {"DE", "US"}  # the zero-weight FR mapping creates nothing
    assert Decimal(rows["DE"]["exposed_value"]) == 300
    assert Decimal(rows["DE"]["share_of_portfolio"]["ratio"]) == Decimal("0.3")
    assert Decimal(rows["DE"]["share_of_mapped"]["denominator"]) == 420
    assert set(rows["DE"]["by_basis"]) == {"reported"}
    assert set(rows["US"]["by_basis"]) == {"estimated"}
    expected_hhi = (Decimal(300) / 420) ** 2 + (Decimal(120) / 420) ** 2
    assert abs(Decimal(country["hhi"]) - expected_hhi) < Decimal("0.0000001")
    assert Decimal(country["hhi_denominator"]) == 420

    product = body["product_concentration"]
    assert [r["key"] for r in product["rows"]] == ["8542"]
    assert product["rows"][0]["hs_revision"] == "HS2022"
    assert Decimal(product["rows"][0]["exposed_value"]) == 180

    sens = next(s for s in body["macro_sensitivities"] if s["series_id"] == "cpi_yoy")
    # The 2.9 revision was learned on 2026-09-25, before the 2026-09-26 end-of-day cutoff.
    assert Decimal(sens["reported"]["weighted_change"]) == Decimal("0.9")  # 2.9 - 2.0
    assert Decimal(sens["inferred"]["weighted_change"]) == Decimal("0.5")  # 3.5 - 3.0
    assert sens["reported"]["geographies"] == ["DE"] and sens["inferred"]["geographies"] == ["US"]
    assert Decimal(sens["reported"]["coverage"]["ratio"]) == 1
    assert Decimal(sens["portfolio_value"]) == 1000
    assert "FR" not in json.dumps(sens)

    coverage = {b["adjustment_basis"]: b for b in body["adjusted_return_coverage"]["bases"]}
    assert Decimal(coverage["price_raw"]["complete"]["numerator"]) == 600
    assert Decimal(coverage["price_raw"]["complete"]["denominator"]) == 1000
    assert coverage["price_raw"]["missing_tickers"] == ["UNL_US_EQ"]
    assert coverage["total_return"]["partial_tickers"] == ["EXM_US_EQ"]
    assert Decimal(coverage["total_return"]["complete"]["numerator"]) == 0
    assert "value" not in walk_keys(body["adjusted_return_coverage"])  # no manufactured return

    assert body["fund_overlap"]["funds"] == []
    assert any(g["domain"] == "fund_overlap" for g in body["gaps"])
    assert any(g["domain"] == "identity" for g in body["gaps"])


@pytest.mark.db
async def test_portfolio_context_point_in_time_excludes_later_revisions(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed_world(backend_pool)
    await seed_positions(backend_pool)
    body = (await api.get("/portfolio/data-context", params={"as_of": "2026-09-25"})).json()
    sens = next(s for s in body["macro_sensitivities"] if s["series_id"] == "cpi_yoy")
    assert Decimal(sens["reported"]["weighted_change"]) == Decimal("0.9")
    # Exposure created after the cutoff is not used; the snapshot itself must also exist.
    assert (await api.get("/portfolio/data-context", params={"as_of": "2026-09-01"})).json()[
        "snapshot_date"
    ] is None
    await backend_pool.execute("update public.macro_observation set known_at='2026-09-20'")
    early = (await api.get("/portfolio/data-context", params={"as_of": "2026-09-19"})).json()
    assert early["macro_sensitivities"] == []  # nothing known yet -> no effect is invented


@pytest.mark.db
async def test_portfolio_context_without_snapshot_is_an_explicit_gap(
    api: httpx.AsyncClient,
) -> None:
    body = (await api.get("/portfolio/data-context")).json()
    assert body["snapshot_date"] is None and body["country_concentration"] is None
    assert body["gaps"][0]["state"] == "missing"


# ---------------------------------------------------------------------------
# D7.4 research context and chat tools
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_research_context_claims_carry_known_as_of_and_respect_cutoff(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    ids = await seed_world(backend_pool)
    await seed_company(backend_pool, ids)

    country = (
        await api.get("/research-context/country/de", params={"as_of": "2026-09-10T00:00:00Z"})
    ).json()
    cpi = next(c for c in country["claims"] if c["domain"] == "macro")
    assert Decimal(cpi["value"]) == Decimal("2.4") and cpi["known_as_of"] == "2026-09-05"
    assert cpi["provenance"] == "primary" and "2.0" in cpi["note"]  # change from prior period
    trade = [c for c in country["claims"] if c["domain"] == "trade"]
    assert trade and all(c["known_as_of"] == "2026-09-01" for c in trade)
    assert not country["gaps"]
    later = (await api.get("/research-context/country/DE")).json()
    assert Decimal(next(c for c in later["claims"] if c["domain"] == "macro")["value"]) == Decimal(
        "2.9"
    )
    empty = (await api.get("/research-context/country/JP")).json()
    assert empty["claims"] == [] and {g["domain"] for g in empty["gaps"]} == {"macro", "trade"}

    industry = (await api.get("/research-context/industry/8542")).json()
    assert len(industry["claims"]) == 2 and "1 issuers" in industry["method"]
    assert (await api.get("/research-context/industry/9999")).json()["gaps"][0]["domain"] == "trade"
    assert (await api.get("/research-context/industry/xx")).status_code == 422

    company = (
        await api.get(
            "/research-context/company/EXM_US_EQ", params={"as_of": "2026-09-10T00:00:00Z"}
        )
    ).json()
    domains = {c["domain"] for c in company["claims"]}
    assert {"financial", "business_network"} <= domains
    revenue = [c for c in company["claims"] if c["domain"] == "financial"]
    assert all(Decimal(c["value"]) in {Decimal(100), Decimal(103)} for c in revenue)
    assert all(c["known_as_of"] for c in company["claims"])
    edges = [c for c in company["claims"] if c["domain"] == "business_network"]
    assert [c["text_value"] for c in edges] == ["Acme Ltd"]  # confirmed only
    assert {g["domain"] for g in company["gaps"]} >= {"forward", "patents"}


@pytest.mark.db
async def test_chat_tools_register_cited_observations_and_gather_adds_context(
    backend_pool: asyncpg.Pool,
) -> None:
    ids = await seed_world(backend_pool)
    await seed_company(backend_pool, ids)
    registry = Registry()
    chat = ChatTools(backend_pool, registry, conversation_id=1, message_id=None)
    result = await chat.call("country_context", {"geography": "de", "as_of": "2026-09-10"})
    claim = next(c for c in result["claims"] if c["domain"] == "macro")
    assert claim["source_id"].startswith("S") and claim["known_as_of"] == "2026-09-05"
    source = registry.get(claim["source_id"])
    assert source is not None and source.kind == "observation"
    assert source.as_of == "2026-09-05" and "known as of 2026-09-05" in (source.snippet or "")
    assert (await chat.call("country_context", {"geography": "DE", "as_of": "nope"})) == {
        "error": "as_of must be an ISO date"
    }
    company = await chat.call("company_context", {"ticker": "EXM_US_EQ"})
    assert company["claims"] and all("source_id" in c for c in company["claims"])

    section = await data_context_section(backend_pool, Registry(), "EXM_US_EQ")
    assert section.sentences and all(s.source_ids for s in section.sentences)
    assert all("known as of" in s.text for s in section.sentences)
    none = await data_context_section(backend_pool, Registry(), "UNL_US_EQ")
    assert none.sentences == [] and none.note

    context = await company_context(backend_pool, "EXM_US_EQ", at("2026-07-01"))
    assert context.claims == []  # nothing was known yet


# ---------------------------------------------------------------------------
# D7.7 evidence summary
# ---------------------------------------------------------------------------

A_ORIG = "0001234567-26-000001"
A_AMEND = "0001234567-26-000002"


async def seed_evidence(p: asyncpg.Pool) -> None:
    # Filed note with a stored evidence quote, plus a later revision of the same note.
    doc = await p.fetchval(
        "insert into public.source_document (regulator,external_id,stable_url)"
        " values ('sec','0000111111-26-000010','https://example.test/10q') returning id"
    )
    version = await p.fetchval(
        "insert into public.source_document_version (document_id,content_hash,known_at)"
        " values ($1,'h1','2026-08-01') returning id",
        doc,
    )
    evidence_id = await p.fetchval(
        "insert into public.source_evidence (document_version_id,quote)"
        " values ($1,'Operating lease commitments total USD 40 million.') returning id",
        version,
    )
    for text, known, revision, ev in (
        ("original text block", "2026-08-01", 0, evidence_id),
        ("restated text block", "2026-09-28", 1, None),
    ):
        await p.execute(
            "insert into public.financial_statement_fact (ticker,concept,statement,period_end,"
            " basis,unit,source,source_url,known_at,text_block,revision,evidence_id,form)"
            " values ('EXM_US_EQ','LeasesNote','note','2026-06-30','reported','text','sec_xbrl',"
            " 'https://example.test/10q',$1,$2,$3,$4,'10-Q')",
            at(known),
            text,
            revision,
            ev,
        )

    # Insider: an original buy later restated by an amendment known on 2026-09-20.
    await p.execute(
        "insert into insider_filing (accession,ticker,issuer_cik,form,filed,source_url)"
        " values ($1,'EXM_US_EQ','0000111111','4/A','2026-09-20','https://example.test/4a')",
        A_AMEND,
    )
    await p.execute(
        "insert into insider_filing (accession,ticker,issuer_cik,form,filed,source_url,"
        " superseded_by_accession,amendment_link_status) values ($1,'EXM_US_EQ','0000111111',"
        " '4','2026-08-12','https://example.test/4',$2,'not_amendment')",
        A_ORIG,
        A_AMEND,
    )
    await p.execute(
        "update insider_filing set amends_accession=$1, amendment_link_status='linked'"
        " where accession=$2",
        A_ORIG,
        A_AMEND,
    )
    for accession, shares, note in ((A_ORIG, 100, "Open market purchase."), (A_AMEND, 120, None)):
        await p.execute(
            "insert into insider_transaction (accession,row_index,reporting_owner_cik,owner_name,"
            " transaction_code,shares,price_per_share,transaction_date,acquired_disposed,"
            " direct_indirect,is_officer,economic_class,classification_basis,footnote_text)"
            " values ($1,0,'0000222222','Jane Roe','P',$2,10,'2026-08-10','A','D',true,"
            " 'open_market_buy','code P',$3)",
            accession,
            Decimal(shares),
            note,
        )

    # Dependencies: one confirmed with passage, one suggested, one confirmed disclosure whose
    # share is unknown, one anonymous.
    await p.execute(
        "insert into public.company_graph_edge (subject_key,object_key,object_name,edge_type,"
        " direction,status,evidence_tier,source,source_url,passage,known_at) values"
        " ('EXM_US_EQ','acme','Acme Ltd','supplier','incoming','confirmed','primary','sec_xbrl',"
        " 'https://example.test/e1','Acme supplies wafers under a 2024 agreement.','2026-08-10'),"
        " ('EXM_US_EQ','zed','Zed Inc','partner','symmetric','suggested','candidate','diffbot',"
        " 'https://example.test/e2',null,'2026-08-11')"
    )
    await p.execute(
        "insert into public.relationship_disclosure (ticker,relationship,direction,"
        " counterparty_name,anonymous_counterparty,fiscal_period,valid_from,passage,source_url,"
        " source,status,known_at) values"
        " ('EXM_US_EQ','customer','issuer_sells_to','Big Co',false,'FY2025','2025-01-01',"
        "  'One customer accounted for a significant portion of revenue.','https://example.test/d1',"
        "  'sec_xbrl','confirmed','2026-08-15'),"
        " ('EXM_US_EQ','customer','issuer_sells_to',null,true,'FY2025','2025-01-01',"
        "  'Customer A represented more than ten percent of net sales.','https://example.test/d2',"
        "  'sec_xbrl','confirmed','2026-08-15')"
    )

    # Deals: announced (confirmed), closed (only suggested), approved (confirmed, later).
    # A confirmed event must carry evidence (company_event_confirmed_needs_evidence).
    deal_doc = await p.fetchval(
        "insert into public.source_document (regulator,external_id,stable_url)"
        " values ('sec','0000111111-26-000099','https://example.test/8k') returning id"
    )
    deal_version = await p.fetchval(
        "insert into public.source_document_version (document_id,content_hash,known_at)"
        " values ($1,'deal-h1','2026-08-01') returning id",
        deal_doc,
    )
    deal_evidence = await p.fetchval(
        "insert into public.source_evidence (document_version_id,quote)"
        " values ($1,'The parties entered into a merger agreement.') returning id",
        deal_version,
    )
    for state, status, known, effective in (
        ("announced", "confirmed", "2026-08-01", None),
        ("closed", "suggested", "2026-09-01", "2026-09-01"),
        ("approved", "confirmed", "2026-09-10", "2026-09-10"),
    ):
        await p.execute(
            "insert into public.company_event (ticker,event_type,event_state,announced_at,"
            " effective_at,known_at,source,source_url,counterparty_key,status,detail,evidence_id)"
            " values ('EXM_US_EQ','deal',$1,'2026-08-01',$2,$3,'sec_xbrl',$4,'target-1',$5,"
            '  \'{"passage": "The parties entered into a merger agreement."}\'::jsonb, $6)',
            state,
            dt.date.fromisoformat(effective) if effective else None,
            at(known),
            f"https://example.test/deal/{state}",
            status,
            deal_evidence if status == "confirmed" else None,
        )

    # Consensus: two vintages of one series, and a provider estimate that is NOT consensus.
    for vintage, value, known in (
        ("2026-07", "1.00", "2026-07-20"),
        ("2026-09", "1.10", "2026-09-12"),
    ):
        await p.execute(
            "insert into public.forward_observation (ticker,kind,provider,metric,fiscal_period,"
            " value,analyst_count,vintage,known_at,source_url,observation_key) values"
            " ('EXM_US_EQ','published_analyst_consensus','issuer_ir','eps','FY2026',$1,12,$2,$3,"
            " 'https://example.test/consensus', md5('consensus' || $2))",
            Decimal(value),
            vintage,
            at(known),
        )
    await p.execute(
        "insert into public.forward_observation (ticker,kind,provider,metric,fiscal_period,value,"
        " known_at,source_url,observation_key) values ('EXM_US_EQ','event_calendar_estimate',"
        " 'finnhub','eps','FY2026',9,'2026-09-01','u', md5('calendar'))"
    )


@pytest.mark.db
async def test_evidence_summary_keeps_channels_separate_and_cites_every_item(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed(backend_pool)
    assert await run_collector(backend_pool) == "succeeded"
    await seed_evidence(backend_pool)

    body = (await api.get("/holdings/EXM_US_EQ/evidence-summary")).json()
    keys = walk_keys(body["positions"])
    assert not [k for k in keys if "total" in k.lower() or "combined" in k.lower()]
    assert (
        "additive" in body["positions"]["not_additive"]
        or "combined" in body["positions"]["not_additive"]
    )
    channels = {
        body["positions"]["control"]["channel"],
        body["positions"]["institutional"]["channel"],
        body["positions"]["funds"]["channel"],
    }
    assert channels == {"beneficial_ownership_13dg", "institutional_13f", "fund_positions_nport"}
    control = body["positions"]["control"]["stakes"]
    current = next(s for s in control if not s["superseded"])
    assert current["stake"]["accession"] != ACC_13D
    assert all(s["known_at"] and s["coverage_reason"] for s in control)
    g13 = next(s for s in control if s["stake"]["schedule"] == "13G")
    assert g13["source_passage"] is None and "passive" in g13["passage_reason"]
    assert body["positions"]["institutional"]["coverage"]["state"] == "not_covered"
    assert body["positions"]["funds"]["coverage"]["state"] == "not_covered"

    # Latest revision of the note, with its stored passage kept until a quote is missing.
    note = body["filed_notes"]["notes"][0]
    assert note["revision"] == 1 and note["passage_basis"] == "filed_text_block"
    assert note["passage"] == "restated text block" and note["known_at"].startswith("2026-09-28")

    insider = body["insider_activity"]
    assert Decimal(insider["activity"]["net_discretionary"]["bought_shares"]) == 120
    assert [d["accession"] for d in insider["net_discretionary_derived_from"]] == [A_AMEND]
    assert insider["net_discretionary_derived_from"][0]["passage_reason"]

    deps = body["dependencies"]
    assert {d["counterparty_name"] for d in deps["dependencies"]} == {"Acme Ltd", "Big Co", None}
    assert deps["excluded_unconfirmed"] == 1
    anonymous = next(d for d in deps["dependencies"] if d["anonymous_counterparty"])
    assert anonymous["counterparty_name"] is None and anonymous["reported_share"] is None
    assert all(d["passage"] for d in deps["dependencies"])

    deal = body["deals"]["deals"][0]
    assert deal["current_state"] == "approved"  # the suggested 'closed' event never counts
    assert [e["event_state"] for e in deal["events"]] == ["announced", "closed", "approved"]
    assert deal["events"][0]["passage"].startswith("The parties")

    series = body["consensus"]["series"]
    assert len(series) == 1 and [v["vintage"] for v in series[0]["vintages"]] == [
        "2026-07",
        "2026-09",
    ]
    assert Decimal(series[0]["revision_since_previous"]) == Decimal("0.10")
    assert len(series[0]["revision_inputs"]) == 2
    assert {g["domain"] for g in body["gaps"]} >= {"institutional_13f", "fund_positions"}


@pytest.mark.db
async def test_evidence_point_in_time_excludes_later_revisions_and_amendments(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed(backend_pool)
    assert await run_collector(backend_pool) == "succeeded"
    await seed_evidence(backend_pool)
    early = (
        await api.get(
            "/holdings/EXM_US_EQ/evidence-summary", params={"as_of": "2026-09-05T00:00:00Z"}
        )
    ).json()
    note = early["filed_notes"]["notes"][0]
    assert note["revision"] == 0 and note["passage_basis"] == "source_evidence_quote"
    assert note["passage"].startswith("Operating lease commitments")
    insider = early["insider_activity"]
    # The amendment was filed on 2026-09-20: unknown at the cutoff, so the original counts.
    assert Decimal(insider["activity"]["net_discretionary"]["bought_shares"]) == 100
    assert [d["accession"] for d in insider["net_discretionary_derived_from"]] == [A_ORIG]
    assert insider["net_discretionary_derived_from"][0]["passage"] == "Open market purchase."

    deal = early["deals"]["deals"][0]
    assert deal["current_state"] == "announced" and len(deal["events"]) == 2
    series = early["consensus"]["series"][0]
    assert [v["vintage"] for v in series["vintages"]] == ["2026-07"]
    assert series["revision_since_previous"] is None  # one vintage: no revision invented

    # 13D/A filed 2025-09-15; before it only the initial 13D is public and it is current.
    mid = (
        await api.get(
            "/holdings/EXM_US_EQ/evidence/positions", params={"as_of": "2025-06-01T00:00:00Z"}
        )
    ).json()
    stakes = mid["control"]["stakes"]
    assert [s["stake"]["accession"] for s in stakes] == [ACC_13D]
    assert stakes[0]["superseded"] is False
    after = (
        await api.get(
            "/holdings/EXM_US_EQ/evidence/positions", params={"as_of": "2025-10-01T00:00:00Z"}
        )
    ).json()
    flags = {s["stake"]["accession"]: s["superseded"] for s in after["control"]["stakes"]}
    assert flags == {ACC_13DA: False, ACC_13D: True}
    nothing = (
        await api.get(
            "/holdings/EXM_US_EQ/evidence/dependencies", params={"as_of": "2026-01-01T00:00:00Z"}
        )
    ).json()
    assert nothing["dependencies"] == [] and nothing["coverage"]["state"] == "not_covered"


@pytest.mark.db
async def test_empty_holding_reports_explicit_coverage_reasons(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed(backend_pool)
    body = (await api.get("/holdings/EXM_US_EQ/evidence-summary")).json()
    for section in ("filed_notes", "insider_activity", "dependencies", "deals", "consensus"):
        assert body[section]["coverage"]["state"] == "not_covered"
        assert body[section]["coverage"]["reason"]
    assert body["filed_notes"]["notes"] == [] and body["deals"]["deals"] == []
    assert len(body["gaps"]) >= 7
    domain = await api.get("/holdings/EXM_US_EQ/evidence/consensus")
    assert domain.status_code == 200 and domain.json()["series"] == []
    assert (await api.get("/holdings/EXM_US_EQ/evidence/nonsense")).status_code == 422
