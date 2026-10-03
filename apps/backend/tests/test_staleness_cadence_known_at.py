"""Second-pass backend items: research known_at for 13F / web search / thesis / strategy,
documented default staleness for providers without a refresh job, and the macro next-release
estimate derived from stored first-release timestamps."""

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
    COVERAGE_DEFAULT_REFRESH_HOURS,
    COVERAGE_PROVIDER_JOB,
    JobsConfig,
    coverage_stale_policy,
)
from steward.jobs.runner import run_job
from steward.macro.read import context_for_holding, observed_next_release
from tests.test_macro_fixes import _holding, _issuer, _map
from tests.test_phase5 import FakeResearchWriter, research_handler_with, seed_holding

pytestmark = pytest.mark.db

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


# ---------------------------------------------------------------------------------------
# (1) Research known_at
# ---------------------------------------------------------------------------------------


async def test_research_sources_carry_13f_search_thesis_and_strategy_known_at(
    backend_pool: asyncpg.Pool, client: httpx.AsyncClient
) -> None:
    await seed_holding(backend_pool)
    await backend_pool.execute(
        "insert into form13f_dataset (url) values ('https://sec.test/13f.zip')"
    )
    await backend_pool.execute(
        "insert into form13f_filing (accession, cik, manager_name, period, filed,"
        " submission_type, dataset_url) values ('0000000001-26-000001', '0000000001',"
        " 'Big Fund', '2026-06-30', '2026-08-10', '13F-HR', 'https://sec.test/13f.zip')"
    )
    await backend_pool.execute(
        "insert into institutional_holding (accession, infotable_sk, cusip, ticker, value_usd,"
        " shares, shares_type) values ('0000000001-26-000001', 1, '037833100', 'AAPL_US_EQ', 1,"
        " 1000000, 'SH')"
    )
    config_created = {
        r["domain"]: r["created_at"]
        for r in await backend_pool.fetch(
            "select domain, created_at from config_version where active"
            " and domain in ('theses','strategy')"
        )
    }
    before = dt.datetime.now(dt.UTC)
    assert (
        await run_job(
            backend_pool,
            "research",
            research_handler_with(FakeResearchWriter()),
            trigger="manual",
            params={"ticker": "AAPL_US_EQ", "profile": "standard"},
        )
        == "succeeded"
    )
    after = dt.datetime.now(dt.UTC)
    listed = (await client.get("/research", params={"ticker": "AAPL_US_EQ"})).json()
    body = (await client.get(f"/research/{listed[0]['id']}")).json()["body"]
    by_kind: dict[str, list[dict[str, object]]] = {}
    for source in body["sources"]:
        by_kind.setdefault(source["kind"], []).append(source)

    # 13F: the stored filing date (the only 13F time stored), per period and per filing.
    assert by_kind["filing13f"], "ownership section should cite 13F"
    assert {s["known_at"] for s in by_kind["filing13f"]} == {"2026-08-10"}
    # Thesis / strategy: created_at of the cited config version.
    (thesis,) = by_kind["thesis"]
    (strategy,) = by_kind["strategy"]
    assert thesis["known_at"] == config_created["theses"].astimezone(dt.UTC).isoformat()
    assert strategy["known_at"] == config_created["strategy"].astimezone(dt.UTC).isoformat()
    # Web search: the retrieval time captured while gathering this run.
    assert by_kind["search"]
    for source in by_kind["search"]:
        stamp = dt.datetime.fromisoformat(str(source["known_at"]))
        assert before <= stamp <= after
    # A sentence citing only 13F and a search result now has known-at bounds.
    ownership = next(s for s in body["sections"] if s["id"] == "ownership")
    assert ownership["sentences"][0]["known_at_max"] == "2026-08-10"


# ---------------------------------------------------------------------------------------
# (2) Staleness defaults
# ---------------------------------------------------------------------------------------


def test_documented_default_table_has_a_basis_for_every_entry() -> None:
    for provider, (hours, basis) in COVERAGE_DEFAULT_REFRESH_HOURS.items():
        assert basis.strip(), provider
        assert hours is None or hours > 0
    # A provider with a job never takes a default: the job schedule wins (or stays null).
    assert not set(COVERAGE_DEFAULT_REFRESH_HOURS) & set(COVERAGE_PROVIDER_JOB)


def test_stale_policy_job_default_and_unknown() -> None:
    jobs = JobsConfig()
    assert coverage_stale_policy(jobs, "fred") == (48, "job_schedule")
    assert coverage_stale_policy(jobs, "world_bank") == (None, None)  # its job is disabled
    assert coverage_stale_policy(jobs, "diffbot") == (1440, "documented_default")  # monthly x2
    assert coverage_stale_policy(jobs, "people_data_labs") == (1440, "documented_default")
    assert coverage_stale_policy(jobs, "epo_ops") == (1440, "documented_default")
    assert coverage_stale_policy(jobs, "finnhub") == (336, "documented_default")  # weekly x2
    assert coverage_stale_policy(jobs, "owner_review") == (None, None)
    assert coverage_stale_policy(jobs, "twelve_data") == (None, None)
    assert coverage_stale_policy(jobs, "never_heard_of_it") == (None, None)


async def test_coverage_route_exposes_stale_basis(
    backend_pool: asyncpg.Pool, client: httpx.AsyncClient
) -> None:
    await backend_pool.execute(
        "insert into public.instrument (t212_ticker, currency) values ('EXM','USD')"
    )
    for provider, dataset, key, age in (
        ("fred", "cpi", "US", dt.timedelta(days=5)),  # job_schedule: stale
        ("diffbot", "organization", "EXM", dt.timedelta(days=45)),  # default 60 d: fresh
        ("epo_ops", "patent_family", "EXM", dt.timedelta(days=90)),  # default 60 d: stale
        ("owner_review", "corporate_actions", "EXM", dt.timedelta(days=400)),  # not judged
    ):
        await backend_pool.execute(
            "insert into public.coverage_status (provider,dataset,entity_key,state,checked_at)"
            " values ($1,$2,$3,'available', now() - $4::interval)",
            provider,
            dataset,
            key,
            age,
        )
    rows = {r["provider"]: r for r in (await client.get("/data/coverage")).json()}
    assert (rows["fred"]["stale_basis"], rows["fred"]["stale"]) == ("job_schedule", True)
    assert rows["diffbot"]["stale_after_hours"] == 1440
    assert (rows["diffbot"]["stale_basis"], rows["diffbot"]["stale"]) == (
        "documented_default",
        False,
    )
    assert (rows["epo_ops"]["stale_basis"], rows["epo_ops"]["stale"]) == (
        "documented_default",
        True,
    )
    assert rows["owner_review"]["stale_basis"] is None
    assert rows["owner_review"]["stale"] is None
    assert rows["owner_review"]["stale_after_hours"] is None


# ---------------------------------------------------------------------------------------
# (3) Macro next release (observed cadence only)
# ---------------------------------------------------------------------------------------


def _utc(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=dt.UTC)


def test_observed_next_release_is_median_gap_after_the_latest_release() -> None:
    as_of = _utc("2026-06-20")
    releases = [_utc(d) for d in ("2026-03-10", "2026-04-10", "2026-05-12", "2026-06-10")]
    # gaps 31, 32, 29 days -> median 31 -> 2026-07-11
    assert observed_next_release(releases, as_of) == (dt.date(2026, 7, 11), "observed_cadence")
    # Fewer than four stored releases: nothing is claimed.
    assert observed_next_release(releases[:3], as_of) == (None, None)
    assert observed_next_release([], as_of) == (None, None)
    # Duplicate timestamps (a backfill stamped once) count once.
    assert observed_next_release([*releases[:3], releases[2]], as_of) == (None, None)
    # An estimate already in the past is not shown.
    assert observed_next_release(releases, _utc("2026-12-01")) == (None, None)


async def _obs(
    pool: asyncpg.Pool, series: str, period: str, rev: int, known: str, release: str | None
) -> None:
    await pool.execute(
        "insert into macro_observation (series_id,source,geography,period,value,unit,frequency,"
        " release_at,known_at,vintage,revision,source_url) values ($1,'oecd','DE',$2,$3,'%',"
        "'monthly',$4,$5,$6,$7,'u')",
        series,
        dt.date.fromisoformat(period),
        Decimal("1.5"),
        _utc(release) if release else None,
        _utc(known),
        f"v{rev}",
        rev,
    )


async def test_macro_context_estimates_next_release_from_stored_cadence_only(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer = await _issuer(backend_pool)
    await _holding(backend_pool, "MAPPED", issuer)
    await _map(backend_pool, issuer, "1", geography="DE")
    await backend_pool.execute("update exposure_mapping set created_at='2026-02-01'")
    # cpi: five monthly first releases (a later revision must not count as a release).
    for period, release in (
        ("2026-01-01", "2026-02-10"),
        ("2026-02-01", "2026-03-10"),
        ("2026-03-01", "2026-04-10"),
        ("2026-04-01", "2026-05-10"),
        ("2026-05-01", "2026-06-10"),
    ):
        await _obs(backend_pool, "cpi", period, 0, release, release)
    await _obs(backend_pool, "cpi", "2026-05-01", 1, "2026-06-25", "2026-06-25")  # revision
    # gdp: only three stored releases -> no estimate.
    for period, release in (
        ("2026-01-01", "2026-02-15"),
        ("2026-02-01", "2026-03-15"),
        ("2026-03-01", "2026-04-15"),
    ):
        await _obs(backend_pool, "gdp", period, 0, release, release)
    # jobs: five periods but no release_at stored -> no estimate.
    for i in range(5):
        await _obs(backend_pool, "jobs", f"2026-0{i + 1}-01", 0, f"2026-0{i + 2}-20", None)
    as_of = _utc("2026-07-01")
    for history in (0, 3):  # independent of the history window
        context = await context_for_holding(backend_pool, "MAPPED", as_of, history)
        got = {o.series_id: o for o in context.observations}
        # Gaps 28, 31, 30, 31 days -> median 30.5 d after 2026-06-10 -> 2026-07-10.
        assert got["cpi"].next_release_estimate == dt.date(2026, 7, 10)
        assert got["cpi"].next_release_basis == "observed_cadence"
        assert got["gdp"].next_release_estimate is None and got["gdp"].next_release_basis is None
        assert got["jobs"].next_release_estimate is None and got["jobs"].next_release_basis is None
    # Point in time: as of 2026-05-20 only four releases are known (latest 2026-05-10).
    early = await context_for_holding(backend_pool, "MAPPED", _utc("2026-05-20"))
    cpi = next(o for o in early.observations if o.series_id == "cpi")
    assert cpi.next_release_estimate == dt.date(2026, 6, 9)  # 05-10 + median(28,31,30)=30 d
