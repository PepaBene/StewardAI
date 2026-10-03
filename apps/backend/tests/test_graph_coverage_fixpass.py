"""Fix-pass regressions: concentration missing-share counts, unknown (NULL) forward citations,
coverage summary entity_kind filter and derived staleness."""

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.config.jobs import (
    JobSchedule,
    JobsConfig,
    coverage_stale_after_hours,
    longest_gap_hours,
)
from steward.free_sources import store_patent_family
from steward.graph.decisions import decide_disclosure
from steward.graph.patents import patent_trend
from steward.graph.relationships import concentration, upsert_disclosure

TICKER = "EXM_US_EQ"
OWNER = "7f0c3d4e-0000-4000-8000-000000000001"


@pytest.fixture
async def client(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _confirm(pool: asyncpg.Pool, name: str | None, share: Decimal | None, n: int) -> None:
    did = await upsert_disclosure(
        pool,
        ticker=TICKER,
        relationship="customer",
        direction="issuer_sells_to",
        passage=f"Disclosure number {n} says a customer bought from the issuer.",
        source_url=f"https://sec.example/{n}",
        source="sec",
        counterparty_name=name,
        anonymous_counterparty=name is None,
        reported_share=share,
        share_basis="issuer_revenue",
        fiscal_period="FY2025",
        valid_from=dt.date(2025, 1, 1),
    )
    await decide_disclosure(pool, did, "confirm", reason="Owner checked the filing text")


@pytest.mark.db
async def test_concentration_reports_missing_shares_per_side(
    backend_pool: asyncpg.Pool, client: httpx.AsyncClient
) -> None:
    pool = backend_pool
    await pool.execute(
        "insert into public.instrument (t212_ticker, currency) values ($1,'USD')", TICKER
    )
    await _confirm(pool, "Northwind", Decimal("0.11"), 1)
    await _confirm(pool, "Contoso", None, 2)  # named, no share
    await _confirm(pool, None, None, 3)  # anonymous, no share
    await _confirm(pool, None, None, 4)
    row = (await concentration(pool, TICKER))[0]
    assert (row["named_missing_share_count"], row["anonymous_missing_share_count"]) == (1, 2)
    assert row["named_reported_share_sum"] == Decimal("0.11") and row["named_at_least"] is True
    # Nothing reported on the anonymous side: unknown, not 0.
    assert row["anonymous_reported_share_sum"] is None and row["anonymous_at_least"] is True
    assert row["total_reported_share_sum"] == Decimal("0.11") and row["total_at_least"] is True
    assert row["missing_share_count"] == 3  # old field kept
    body = (await client.get(f"/holdings/{TICKER}/customer-supplier-concentration")).json()
    period = body["periods"][0]
    assert period["anonymous_reported_share_sum"] is None
    assert period["anonymous_missing_share_count"] == 2


def test_patent_trend_treats_null_forward_citations_as_unknown() -> None:
    base = {"priority_date": dt.date(2025, 2, 1), "cpc_codes": ["A01B"]}
    only_unknown = patent_trend([{**base, "family_key": "F1", "citations_forward": None}])
    assert only_unknown[0]["forward_citations"] is None
    assert only_unknown[0]["families_with_citation_data"] == 0
    mixed = patent_trend(
        [
            {**base, "family_key": "F1", "citations_forward": None},
            {**base, "family_key": "F2", "citations_forward": 0},
            {**base, "family_key": "F3", "citations_forward": 4},
        ]
    )
    assert mixed[0]["forward_citations"] == 4 and mixed[0]["families_with_citation_data"] == 2
    assert mixed[0]["families"] == 3


@pytest.mark.db
async def test_unknown_forward_citations_are_stored_as_null(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await pool.execute(
        "insert into public.instrument (t212_ticker, currency) values ($1,'USD')", TICKER
    )
    kwargs = dict(
        priority_date=dt.date(2020, 1, 1),
        title="T",
        cpc_codes=["A01B"],
        jurisdictions=["EP"],
        backward_citations=2,
        legal_status="granted",
        source="epo_ops",
        source_url="https://example.org/f",
        ticker=TICKER,
        applicant="Example AG",
        ownership_confidence=Decimal("0.8"),
        ownership_basis="direct",
    )
    await store_patent_family(pool, family_key="F1", forward_citations=None, **kwargs)  # type: ignore[arg-type]
    await store_patent_family(pool, family_key="F2", forward_citations=0, **kwargs)  # type: ignore[arg-type]
    got = {
        r["family_key"]: r["citations_forward"]
        for r in await pool.fetch("select family_key, citations_forward from patent_family")
    }
    assert got == {"F1": None, "F2": 0}
    # A later source that knows the figure fills it in; NULL never lowers a known count.
    await store_patent_family(pool, family_key="F1", forward_citations=5, **kwargs)  # type: ignore[arg-type]
    await store_patent_family(pool, family_key="F1", forward_citations=None, **kwargs)  # type: ignore[arg-type]
    assert (
        await pool.fetchval("select citations_forward from patent_family where family_key='F1'")
        == 5
    )


def test_stale_interval_comes_from_the_job_cadence() -> None:
    jobs = JobsConfig()
    daily = JobSchedule(cron="30 11 * * *")
    assert longest_gap_hours(daily) == 24
    assert longest_gap_hours(JobSchedule(cron="15 23 * * 1-5")) == 72  # Fri -> Mon
    assert longest_gap_hours(JobSchedule(cron="0 4 * * 0", enabled=False)) is None
    assert coverage_stale_after_hours(jobs, "fred") == 48  # macro_collect daily x2
    assert coverage_stale_after_hours(jobs, "world_bank") is None  # job disabled by default
    assert coverage_stale_after_hours(jobs, "owner_review") is None  # no refreshing job


@pytest.mark.db
async def test_coverage_rows_derive_stale_and_summary_filters_entity_kind(
    backend_pool: asyncpg.Pool, client: httpx.AsyncClient
) -> None:
    p = backend_pool
    await p.execute("insert into public.instrument (t212_ticker, currency) values ('EXM','USD')")
    await p.execute(
        "insert into public.macro_observation (series_id,source,geography,period,value,unit,"
        " frequency,known_at,vintage,source_url) values ('cpi','fred','US','2026-08-01',2,"
        " 'percent','M','2026-09-01','v1','u')"
    )
    for provider, dataset, key, age in (
        ("fred", "cpi", "US", dt.timedelta(days=5)),  # daily job: stale
        ("ecb", "hicp", "EA", dt.timedelta(hours=2)),  # fresh
        (
            "owner_review",
            "corporate_actions",
            "EXM",
            dt.timedelta(days=400),
        ),  # no interval: unknown
    ):
        await p.execute(
            "insert into public.coverage_status (provider,dataset,entity_key,state,checked_at)"
            " values ($1,$2,$3,'available', now() - $4::interval)",
            provider,
            dataset,
            key,
            age,
        )
    rows = {r["provider"]: r for r in (await client.get("/data/coverage")).json()}
    assert rows["fred"]["stale"] is True and rows["fred"]["stale_after_hours"] == 48
    assert rows["ecb"]["stale"] is False
    assert (
        rows["owner_review"]["stale"] is None and rows["owner_review"]["stale_after_hours"] is None
    )

    full = (await client.get("/data/coverage/summary", params={"group_by": "source"})).json()
    assert full["total_entries"] == 3
    geo = (
        await client.get(
            "/data/coverage/summary", params={"group_by": "source", "entity_kind": "geography"}
        )
    ).json()
    assert geo["total_entries"] == 1 and [g["key"] for g in geo["groups"]] == ["fred"]
    sec = (
        await client.get(
            "/data/coverage/summary", params={"group_by": "source", "entity_kind": "security"}
        )
    ).json()
    assert [g["key"] for g in sec["groups"]] == ["owner_review"]
