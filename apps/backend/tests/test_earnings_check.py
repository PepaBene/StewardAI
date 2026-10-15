"""The earnings check end to end against a real Postgres: EDGAR and the document mocked, the
extraction replaced by the golden labels (no API key in CI), evidence in memory.

Phase 2 ACs covered here:
- every stored fact has a resolvable evidence_path pointing at the archived source;
- the verdict job produces a verdict for every KPI;
- editing a KPI threshold and saving is reflected in the next verdict run, no code change;
- the holding API returns each verdict's facts, snippets and evidence link.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from api.routes import get_storage
from steward.config import HoldingProfilesConfig, ThesesConfig, VerificationConfig
from steward.config.store import save_config
from steward.earnings.check import EarningsDeps, earnings_check, thesis_verdicts_handler
from steward.earnings.golden import OracleExtractor, load_cases
from steward.jobs.runner import JobContext, run_job
from steward.sources.edgar import EdgarClient
from steward.storage import MemoryStorage
from tests.conftest import MigratedDatabase

pytestmark = pytest.mark.db

OWNER = "00000000-0000-0000-0000-00000000000a"
TICKER = "AAPL_US_EQ"
CASES = {c.id: c for c in load_cases()}
Q3 = CASES["aapl-fy2025-q3"]
Q4 = CASES["aapl-fy2025-q4"]

THESIS: dict[str, Any] = {
    "ticker": TICKER,
    "why_owned": "Services growth on a huge installed base.",
    "kpis": [
        {
            "id": "growth",
            "metric": "revenue_growth_yoy",
            "operator": ">=",
            "threshold": 5,
            "unit": "%",
        },
        {
            "id": "gross-margin",
            "metric": "gross_margin",
            "operator": ">=",
            "threshold": 45,
            "unit": "%",
        },
        {
            "id": "fcf",
            "metric": "free_cash_flow",
            "operator": ">",
            "threshold": 0,
            "unit": "USD bn",
        },
    ],
}
PROFILE = {
    "ticker": TICKER,
    "company_name": "Apple Inc.",
    "sec_cik": "320193",
    "fiscal_year_end_month": 9,
    "reporting_currency": "USD",
    "ir_url": "https://investor.apple.com/",
}


def _index_html(filename: str) -> str:
    return (
        "<table><tr><td>1</td><td>8-K</td><td>primary.htm</td><td>8-K</td><td>1</td></tr>"
        f"<tr><td>2</td><td>EX-99.1</td><td>{filename}</td><td>EX-99.1</td><td>2</td></tr>"
        "</table>"
    )


def _edgar_handler(request: httpx.Request) -> httpx.Response:
    cases = [Q3, Q4]
    if request.url.host == "data.sec.gov":
        recent: dict[str, list[str]] = {
            "form": [],
            "accessionNumber": [],
            "filingDate": [],
            "reportDate": [],
            "primaryDocument": [],
            "items": [],
        }
        for case in cases:
            accession = case.data["filing_index_url"].rsplit("/", 1)[1].removesuffix("-index.html")
            recent["form"].append("8-K")
            recent["accessionNumber"].append(accession)
            recent["filingDate"].append(case.data["report_date"])
            recent["reportDate"].append(case.data["report_date"])
            recent["primaryDocument"].append("primary.htm")
            recent["items"].append("2.02,9.01")
        return httpx.Response(200, json={"filings": {"recent": recent}})
    for case in cases:
        if str(request.url) == case.data["filing_index_url"]:
            return httpx.Response(200, text=_index_html(case.data["source_url"].rsplit("/", 1)[1]))
        if str(request.url) == case.data["source_url"]:
            return httpx.Response(
                200, content=case.document.data, headers={"content-type": "text/html"}
            )
    return httpx.Response(404)


@pytest.fixture
async def deps() -> AsyncIterator[EarningsDeps]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(_edgar_handler))
    yield EarningsDeps(
        http=http,
        storage=MemoryStorage(),
        extractor=OracleExtractor(list(CASES.values())),
        edgar=EdgarClient(http, "StewardAI test test@example.com"),
        locator=None,
    )
    await http.aclose()


async def _setup(pool: asyncpg.Pool, **thesis_overrides: Any) -> None:
    await pool.execute(
        "insert into instrument (t212_ticker, name, currency) values ($1, 'Apple', 'USD')"
        " on conflict do nothing",
        TICKER,
    )
    async with pool.acquire() as conn:
        await save_config(
            conn,
            "theses",
            ThesesConfig.model_validate({"theses": [{**THESIS, **thesis_overrides}]}),
            created_by=None,
        )
        await save_config(
            conn,
            "holding_profiles",
            HoldingProfilesConfig.model_validate({"profiles": [PROFILE]}),
            created_by=None,
        )


async def _check(pool: asyncpg.Pool, deps: EarningsDeps, report_date: str) -> str:
    async def handler(ctx: JobContext) -> None:
        await earnings_check(ctx, deps)

    return await run_job(
        pool,
        "earnings_check",
        handler,
        trigger="manual",
        params={"ticker": TICKER, "report_date": report_date},
    )


async def test_earnings_check_stores_verified_facts_with_evidence_and_verdicts(
    backend_pool: asyncpg.Pool, deps: EarningsDeps
) -> None:
    await _setup(backend_pool)
    assert await _check(backend_pool, deps, "2025-07-31") == "succeeded"

    steps = await backend_pool.fetch("select name, status from job_step order by step_index")
    assert [s["name"] for s in steps] == [
        "load_config",
        "locate",
        "fetch_and_archive",
        "extract",
        "validate_and_store",
        "verdicts",
    ]
    locate = json.loads(
        await backend_pool.fetchval("select detail from job_step where name = 'locate'")
    )
    assert locate["source"] == "edgar" and locate["url"] == Q3.data["source_url"]

    facts = await backend_pool.fetch("select * from fact order by id")
    assert len(facts) == len(Q3.figures)
    assert {f["status"] for f in facts} == {"verified"}
    storage = deps.storage
    assert isinstance(storage, MemoryStorage)
    for f in facts:  # every fact resolves to the archived bytes it was read from
        assert f["evidence_path"] in storage.objects
        assert storage.objects[f["evidence_path"]][0] == Q3.document.data
        assert f["source_url"] == Q3.data["source_url"]
    revenue = next(f for f in facts if f["metric"] == "revenue" and f["fiscal_year"] == 2025)
    assert revenue["value_units"] == 94036000000 and revenue["period"] == "FY2025 Q3"
    assert await backend_pool.fetchval("select count(*) from evidence") == 1

    verdicts = {
        (v["kpi_id"], v["period"]): v
        for v in await backend_pool.fetch("select * from verdict order by id")
    }
    assert verdicts[("growth", "FY2025 Q3")]["status"] == "pass"
    assert verdicts[("gross-margin", "FY2025 Q3")]["status"] == "pass"
    # Not in an Apple release: explicitly unknown, never omitted.
    assert verdicts[("fcf", None)]["status"] == "unknown"
    assert (
        await backend_pool.fetchval(
            "select last_report_date::text from instrument where t212_ticker = $1", TICKER
        )
        == "2025-07-31"
    )

    # Running it again re-reads the same release: nothing new is stored.
    assert await _check(backend_pool, deps, "2025-07-31") == "succeeded"
    assert await backend_pool.fetchval("select count(*) from fact") == len(facts)
    assert await backend_pool.fetchval("select count(*) from verdict") == len(verdicts)


async def test_a_saved_threshold_change_reaches_the_next_verdict_run(
    backend_pool: asyncpg.Pool, deps: EarningsDeps
) -> None:
    await _setup(backend_pool)
    assert await _check(backend_pool, deps, "2025-07-31") == "succeeded"
    before = await backend_pool.fetchrow(
        "select status, config_version_id from verdict where kpi_id = 'growth'"
        " and period = 'FY2025 Q3' order by id desc limit 1"
    )
    assert before["status"] == "pass"

    raised = {**THESIS["kpis"][0], "threshold": 12}
    await _setup(backend_pool, kpis=[raised, *THESIS["kpis"][1:]])
    assert (
        await run_job(
            backend_pool,
            "thesis_verdicts",
            thesis_verdicts_handler,
            trigger="manual",
            params={"ticker": TICKER},
        )
        == "succeeded"
    )
    after = await backend_pool.fetchrow(
        "select status, threshold, config_version_id from verdict where kpi_id = 'growth'"
        " and period = 'FY2025 Q3' order by id desc limit 1"
    )
    assert after["status"] == "breach" and after["threshold"] == 12
    assert after["config_version_id"] != before["config_version_id"]


async def test_sanity_bound_holds_a_figure_and_restatements_keep_both(
    backend_pool: asyncpg.Pool, deps: EarningsDeps
) -> None:
    await _setup(backend_pool)
    async with backend_pool.acquire() as conn:
        await save_config(
            conn, "verification", VerificationConfig(sanity_max_change_qoq=0.05), created_by=None
        )
    assert await _check(backend_pool, deps, "2025-07-31") == "succeeded"
    assert await _check(backend_pool, deps, "2025-10-30") == "succeeded"
    held = await backend_pool.fetch(
        "select metric, status_reason from fact"
        " where status = 'unverified' and fiscal_period = 'Q4'"
    )
    # Apple's September quarter revenue is >5 % above June's: held for review, not used.
    assert "revenue" in {h["metric"] for h in held}
    assert all(h["status_reason"].startswith("sanity_bound") for h in held)


async def test_facts_are_immutable_apart_from_review_state(
    backend_pool: asyncpg.Pool, deps: EarningsDeps
) -> None:
    await _setup(backend_pool)
    await _check(backend_pool, deps, "2025-07-31")
    fact_id = await backend_pool.fetchval("select min(id) from fact")
    with pytest.raises(asyncpg.RaiseError, match="immutable"):
        await backend_pool.execute("update fact set value = 1 where id = $1", fact_id)
    # The backend has no delete grant at all; the trigger backs that up for owners.
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await backend_pool.execute("delete from fact where id = $1", fact_id)
    await backend_pool.execute(
        "update fact set status = 'quarantined', status_reason = 'test' where id = $1", fact_id
    )


async def test_no_document_fails_the_job_with_what_to_configure(
    backend_pool: asyncpg.Pool, deps: EarningsDeps
) -> None:
    await _setup(backend_pool)
    assert await _check(backend_pool, deps, "2024-03-01") == "failed"
    error = await backend_pool.fetchval("select error from job")
    assert "no results document found" in error and "sec_cik" in error


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
async def api(backend_pool: asyncpg.Pool, deps: EarningsDeps) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    async def storage_override() -> AsyncIterator[Any]:
        yield deps.storage

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_storage] = storage_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_holding_page_api_links_every_verdict_to_its_evidence(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool, deps: EarningsDeps
) -> None:
    assert (await api.get("/holdings/NOPE_EQ")).status_code == 404
    await _setup(backend_pool)
    await _check(backend_pool, deps, "2025-07-31")

    detail = (await api.get(f"/holdings/{TICKER}")).json()
    assert detail["thesis"]["why_owned"].startswith("Services")
    assert detail["profile"]["fiscal_year_end_month"] == 9
    assert detail["last_earnings_check"]["status"] == "succeeded"
    kpis = {k["kpi"]["id"]: k for k in detail["kpis"]}
    growth = kpis["growth"]["current"]
    assert growth["status"] == "pass" and growth["period"] == "FY2025 Q3"
    assert growth["confidence"] == "verified" and growth["stale"] is False
    assert kpis["fcf"]["current"]["status"] == "unknown"
    assert kpis["fcf"]["current"]["confidence"] == "unconfirmed"
    facts = {f["id"]: f for f in detail["facts"]}
    assert all(f["confidence"] == "verified" for f in facts.values())

    # Every fact behind every verdict opens its snippet and archived source.
    for kpi in detail["kpis"]:
        for verdict in kpi["history"]:
            for fact_id in verdict["fact_ids"]:
                link = (await api.get(f"/facts/{fact_id}/evidence")).json()
                assert link["source_url"] == Q3.data["source_url"]
                assert link["evidence_url"].startswith("memory://evidence/")
                assert link["context"] and facts[fact_id]["value_as_written"] in link["context"]
    assert (await api.get("/facts/999999/evidence")).status_code == 404


async def test_review_endpoint_only_touches_held_facts(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool, deps: EarningsDeps
) -> None:
    await _setup(backend_pool)
    await _check(backend_pool, deps, "2025-07-31")
    fact_id = await backend_pool.fetchval("select min(id) from fact")
    response = await api.post(
        f"/facts/{fact_id}/review", json={"action": "accept", "reason": "looks right"}
    )
    assert response.status_code == 409 and "cannot be accepted" in response.json()["detail"]
    missing_reason = await api.post(f"/facts/{fact_id}/review", json={"action": "reject"})
    assert missing_reason.status_code == 422


async def test_held_fact_review_requeues_verdicts(
    api: httpx.AsyncClient,
    backend_pool: asyncpg.Pool,
    deps: EarningsDeps,
    migrated_db: MigratedDatabase,
) -> None:
    await _setup(backend_pool)
    async with backend_pool.acquire() as conn:
        await save_config(
            conn, "verification", VerificationConfig(sanity_max_change_qoq=0.05), created_by=None
        )
    await _check(backend_pool, deps, "2025-07-31")
    await _check(backend_pool, deps, "2025-10-30")
    held = await backend_pool.fetchval(
        "select id from fact where status = 'unverified' and metric = 'revenue' limit 1"
    )
    response = await api.post(f"/facts/{held}/review", json={"action": "accept", "reason": "ok"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "verified" and body["verdict_job"]["job_type"] == "thesis_verdicts"
    row = await backend_pool.fetchrow(
        "select status, reviewed_by::text from fact where id = $1", held
    )
    assert row["status"] == "verified" and row["reviewed_by"] == OWNER

    queued = await api.post(
        "/jobs/earnings-check", json={"ticker": TICKER, "report_date": "2025-10-30"}
    )
    assert queued.status_code == 202
    params = await backend_pool.fetchval(
        "select params from job where id = $1", queued.json()["id"]
    )
    assert json.loads(params) == {"ticker": TICKER, "report_date": "2025-10-30"}


async def test_config_endpoints_for_profiles_and_verification(api: httpx.AsyncClient) -> None:
    assert (await api.get("/config/holding-profiles")).status_code == 404
    bad = await api.post(
        "/config/holding-profiles/validate",
        json={"profiles": [PROFILE, {**PROFILE, "fiscal_year_end_month": 13}]},
    )
    assert bad.status_code == 422
    saved = await api.put("/config/holding-profiles", json={"profiles": [PROFILE]})
    assert saved.status_code == 200
    assert (await api.get("/config/holding-profiles")).json()["body"]["profiles"][0][
        "sec_cik"
    ] == "320193"
    verification = await api.put("/config/verification", json={"sanity_max_change_qoq": 0.3})
    assert verification.json()["body"]["sanity_max_change_qoq"] == 0.3
