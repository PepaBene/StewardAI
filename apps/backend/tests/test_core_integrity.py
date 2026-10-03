"""Core stream audit regressions: provider-state latch (A6), identity matching (A7),
observation identity and revisions (A8), listing/lifecycle history (B2, B3, D1.8, D1.9), retry and
rate-limit handling (B4, B8, B9), identity override validation (B5), NULLS NOT DISTINCT (B6),
seed honesty (B7), append-only evidence and foreign-key indexes (C-13, B52).

Fixtures under tests/fixtures/reference/lifecycle_* follow the documented GLEIF and ESMA FIRDS
payload layouts; they are synthetic, not recordings from the live services.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.jobs.runner import JobContext, run_job
from steward.reference.lifecycle import (
    aliases_as_of,
    firds_name_history,
    issuer_snapshot_as_of,
    legal_name_as_of,
    relationships_as_of,
    sync_firds_names,
    sync_gleif_issuer,
)
from steward.reference.matching import (
    Candidate,
    listing_as_of,
    match_identity,
    normalize_identifier,
    record_match_decision,
)
from steward.settings import Settings
from steward.sources import esma, gleif, wikidata
from steward.sources.audit import classify_probe, run_source_audit
from steward.sources.base import (
    AccessResult,
    AccessState,
    Priority,
    SourceObservation,
    access_from_http,
    quota_denial_text,
    rate_limit_wait,
    retry_after_at,
)
from steward.sources.documents import (
    document_version_as_of,
    document_versions_as_of,
    link_public_document,
    record_evidence_as_of,
)
from steward.sources.governor import (
    Budget,
    HardBlocked,
    PostgresRequestGovernor,
    QuotaExceeded,
    RequestGovernor,
    RetryPending,
)
from steward.sources.store import (
    gate_decision,
    provider_gate,
    publish_observations,
    record_access,
)
from tests.conftest import MIGRATIONS_DIR

FIX = Path(__file__).parent / "fixtures" / "reference"
UTC = dt.UTC
OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
NOW = dt.datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def fixture(name: str) -> Any:
    return json.loads((FIX / name).read_text())


# ---------------------------------------------------------------- A6: provider state latch


@pytest.mark.db
async def test_transient_failures_never_close_the_gate_but_retry_after_does(
    backend_pool: asyncpg.Pool,
) -> None:
    provider, endpoint = "finnhub", "earnings_calendar"
    await record_access(backend_pool, provider, endpoint, AccessResult(AccessState.AVAILABLE))
    for state in (
        AccessState.TEMPORARILY_FAILED,
        AccessState.QUOTA_EXHAUSTED,
        AccessState.NOT_COVERED,
        AccessState.TEMPORARILY_FAILED,
    ):
        await record_access(backend_pool, provider, endpoint, AccessResult(state), status_code=503)
    row = await backend_pool.fetchrow(
        "select state,last_attempt_state,consecutive_failures,last_success_at,hard_blocked"
        " from public.provider_access where provider=$1 and endpoint=$2",
        provider,
        endpoint,
    )
    assert row is not None
    # Four bad attempts later the durable verdict is still `available` (it used to be the
    # last attempt's state, which closed every `state = 'available'` collector gate).
    assert row["state"] == "available" and row["last_success_at"] is not None
    assert row["last_attempt_state"] == "temporarily_failed" and not row["hard_blocked"]
    assert row["consecutive_failures"] == 1  # reset by the empty 2xx, then one more failure
    assert (await provider_gate(backend_pool, provider, endpoint)).open

    # A provider Retry-After is a binding window the governor enforces; it lapses by itself.
    later = NOW + dt.timedelta(minutes=10)
    result = AccessResult(AccessState.QUOTA_EXHAUSTED, retry_at=later)
    await record_access(backend_pool, provider, endpoint, result, status_code=429)
    gate = await provider_gate(backend_pool, provider, endpoint, now=NOW)
    assert not gate.open and gate.retry_at == later and gate.state == "available"
    governor = PostgresRequestGovernor(backend_pool)
    budget = Budget(daily=Decimal(100))
    with pytest.raises(RetryPending) as raised:
        await governor.reserve(provider, endpoint, Decimal(1), budget, now=NOW)
    assert raised.value.retry_at == later
    await governor.reserve(provider, endpoint, Decimal(1), budget, now=later + dt.timedelta(1))
    assert (
        await backend_pool.fetchval(
            "select public.provider_gate_open($1,$2,$3)", provider, endpoint, NOW
        )
        is False
    )
    assert (
        await backend_pool.fetchval(
            "select public.provider_gate_open($1,$2,$3)", provider, endpoint, later
        )
        is True
    )

    # A success clears the wait.
    await record_access(backend_pool, provider, endpoint, AccessResult(AccessState.AVAILABLE))
    assert (await provider_gate(backend_pool, provider, endpoint, now=NOW)).open


@pytest.mark.db
async def test_durable_denials_latch_and_survive_later_transient_failures(
    backend_pool: asyncpg.Pool,
) -> None:
    provider, endpoint = "fmp", "profile"
    await record_access(
        backend_pool, provider, endpoint, AccessResult(AccessState.PAID_ONLY), status_code=402
    )
    await record_access(
        backend_pool, provider, endpoint, AccessResult(AccessState.TEMPORARILY_FAILED)
    )
    row = await backend_pool.fetchrow(
        "select state,hard_blocked from public.provider_access where provider=$1 and endpoint=$2",
        provider,
        endpoint,
    )
    assert row is not None and row["state"] == "paid_only" and row["hard_blocked"]
    gate = await provider_gate(backend_pool, provider, endpoint)
    assert not gate.open and "hard-blocked" in str(gate.reason)
    with pytest.raises(HardBlocked):
        await PostgresRequestGovernor(backend_pool).reserve(
            provider, endpoint, Decimal(1), Budget(daily=Decimal(5))
        )


def test_gate_decision_is_one_rule() -> None:
    def row(**kw: Any) -> dict[str, Any]:
        return {"state": "available", "hard_blocked": False, "retry_at": None, **kw}

    assert gate_decision(row(), NOW).open
    assert gate_decision(row(state="access_unverified"), NOW).open  # the attempt is the probe
    assert not gate_decision(row(state="paid_only"), NOW).open
    assert not gate_decision(row(hard_blocked=True), NOW).open
    assert not gate_decision(row(retry_at=NOW + dt.timedelta(seconds=5)), NOW).open
    assert gate_decision(row(retry_at=NOW - dt.timedelta(seconds=5)), NOW).open
    assert not gate_decision(None, NOW).open


async def test_in_memory_governor_honors_retry_at_and_priority() -> None:
    governor = RequestGovernor()
    budget = Budget(daily=Decimal(10), reserve=Decimal(3))
    await governor.defer("demo", "data", NOW + dt.timedelta(minutes=5))
    with pytest.raises(RetryPending):
        await governor.reserve("demo", "data", Decimal(1), budget, now=NOW)
    later = NOW + dt.timedelta(minutes=6)
    # NORMAL leaves the reserve (3), BACKGROUND leaves twice that (6), URGENT may spend it all.
    for _ in range(4):
        await governor.reserve("demo", "bg", Decimal(1), budget, priority=Priority.BACKGROUND)
    with pytest.raises(QuotaExceeded):
        await governor.reserve("demo", "bg", Decimal(1), budget, priority=Priority.BACKGROUND)
    for _ in range(3):
        await governor.reserve("demo", "bg", Decimal(1), budget)
    with pytest.raises(QuotaExceeded):
        await governor.reserve("demo", "bg", Decimal(1), budget)
    for _ in range(3):
        await governor.reserve("demo", "bg", Decimal(1), budget, urgent=True)
    with pytest.raises(QuotaExceeded):
        await governor.reserve("demo", "bg", Decimal(1), budget, urgent=True)
    await governor.reserve("demo", "data", Decimal(1), budget, now=later)


@pytest.mark.db
async def test_usage_ledger_keeps_the_job_id_and_honors_reset_timezone(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute(
        "update public.provider_access set reset_timezone='America/New_York'"
        " where provider='eodhd' and endpoint='eod'"
    )
    governor = PostgresRequestGovernor(backend_pool)
    budget = Budget(daily=Decimal(1))
    late_us_evening = dt.datetime(2026, 10, 2, 3, 0, tzinfo=UTC)  # 23:00 on Oct 1 in New York
    await governor.reserve("eodhd", "eod", Decimal(1), budget, now=late_us_evening, job_id=77)
    with pytest.raises(QuotaExceeded):
        await governor.reserve("eodhd", "eod", Decimal(1), budget, now=late_us_evening)
    # Two hours later it is already Oct 2 in New York: a new provider day, though the same UTC day.
    await governor.reserve(
        "eodhd", "eod", Decimal(1), budget, now=late_us_evening + dt.timedelta(hours=2), job_id=78
    )
    rows = await backend_pool.fetch(
        "select window_start,window_end,last_job_id from public.provider_quota_usage"
        " where provider='eodhd' and endpoint='eod' and window_kind='day' order by window_start"
    )
    assert [r["last_job_id"] for r in rows] == [77, 78]
    assert rows[0]["window_start"] == dt.datetime(2026, 10, 1, 4, 0, tzinfo=UTC)  # EDT midnight
    assert rows[0]["window_end"] == rows[1]["window_start"]
    await record_access(
        backend_pool, "eodhd", "eod", AccessResult(AccessState.AVAILABLE), job_id=78
    )
    assert (
        await backend_pool.fetchval(
            "select job_id from public.provider_fetch_audit where provider='eodhd' order by id desc"
        )
        == 78
    )


@pytest.mark.db
async def test_seeded_rows_do_not_claim_availability_without_a_probe(
    backend_pool: asyncpg.Pool,
) -> None:
    unprobed = await backend_pool.fetch(
        "select provider,endpoint from public.provider_access"
        " where state='available' and last_success_at is null"
    )
    assert [tuple(r) for r in unprobed] == []
    # They stay callable (not blocked): the first successful request is the probe.
    assert (await provider_gate(backend_pool, "eurostat", "dissemination")).open
    await record_access(
        backend_pool, "eurostat", "dissemination", AccessResult(AccessState.AVAILABLE)
    )
    assert (
        await backend_pool.fetchval(
            "select state from public.provider_access where provider='eurostat'"
        )
        == "available"
    )


# ---------------------------------------------------------- B1 / B8: audit and classification


def test_quota_text_is_not_a_paid_tier_denial() -> None:
    daily = {
        "Information": "Our standard API rate limit is 25 requests per day. Please subscribe to "
        "any of the premium plans to instantly remove all daily rate limits."
    }
    assert classify_probe(200, daily).state == AccessState.QUOTA_EXHAUSTED
    assert classify_probe(200, {"Information": "This is a premium endpoint"}).state == (
        AccessState.PAID_ONLY
    )
    limit = {"message": "Limit reached. Upgrade your plan for more requests."}
    assert classify_probe(403, limit).state == AccessState.QUOTA_EXHAUSTED
    assert classify_probe(402, limit).state == AccessState.QUOTA_EXHAUSTED
    assert classify_probe(403, {"message": "upgrade to premium"}).state == AccessState.PAID_ONLY
    assert quota_denial_text("Daily credits exhausted") is True
    assert quota_denial_text("You have run out of credits") is True
    assert quota_denial_text("plan does not include this endpoint") is False
    assert quota_denial_text("Too Many Requests") is True
    # A bare 402 stays a paid-tier denial; only an explicit quota body softens it (B8).
    assert access_from_http(402).state == AccessState.PAID_ONLY
    assert access_from_http(402, quota_denial=True).state == AccessState.QUOTA_EXHAUSTED


def _audit_settings(key: str) -> Settings:
    return Settings(_env_file=None, alpha_vantage_api_key=SecretStr(key))


async def _run_audit(pool: asyncpg.Pool, handler: Any, key: str) -> list[str]:
    seen: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:

        async def job(ctx: JobContext) -> None:
            await run_source_audit(ctx, http, _audit_settings(key))

        await run_job(pool, "source_audit", job, trigger="manual")
    return seen


@pytest.mark.db
async def test_audit_lifts_a_hard_block_after_a_credential_change(
    backend_pool: asyncpg.Pool,
) -> None:
    old_hash = hashlib.sha256(b"old-key:").hexdigest()
    await backend_pool.execute(
        "update public.provider_access set state='paid_only', hard_blocked=true,"
        " configuration_hash=$1 where provider='alpha_vantage' and endpoint='company_overview'",
        old_hash,
    )

    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Symbol": "IBM", "Name": "IBM"})

    # Same credential: the block holds and no request is made for the endpoint.
    seen = await _run_audit(backend_pool, ok, "old-key")
    assert not any("OVERVIEW" in url for url in seen)
    # A new key: the block is lifted, the endpoint is probed and recovers.
    seen = await _run_audit(backend_pool, ok, "new-key")
    assert any("OVERVIEW" in url for url in seen)
    row = await backend_pool.fetchrow(
        "select state,hard_blocked from public.provider_access"
        " where provider='alpha_vantage' and endpoint='company_overview'"
    )
    assert row is not None and row["state"] == "available" and not row["hard_blocked"]


@pytest.mark.db
async def test_audit_reports_a_hard_block_as_a_block_not_an_exhausted_quota(
    backend_pool: asyncpg.Pool,
) -> None:
    # A legacy row (state overwritten by the old function) that is still hard-blocked.
    await backend_pool.execute(
        "update public.provider_access set state='temporarily_failed', hard_blocked=true,"
        " configuration_hash=$1 where provider='eodhd' and endpoint='eod'",
        hashlib.sha256(b":").hexdigest(),
    )
    await _run_audit(backend_pool, lambda r: httpx.Response(200, json={}), "k")
    states = await backend_pool.fetch(
        "select state from public.provider_fetch_audit where provider='eodhd' and endpoint='eod'"
    )
    assert "quota_exhausted" not in [r["state"] for r in states]


# ----------------------------------------------------------------------- B4: Retry-After


@pytest.mark.parametrize(
    "value",
    ["inf", "-inf", "nan", "1e400", "9" * 40, "Fri, 31 Dec 9999 23:59:59 GMT", "garbage", ""],
)
def test_retry_after_never_raises_and_is_capped(value: str) -> None:
    now = dt.datetime(2026, 10, 1, tzinfo=UTC)
    result = retry_after_at(value, now=now)
    assert result is None or now <= result <= now + dt.timedelta(days=7, seconds=4)
    assert retry_after_at("30", now=now) is not None


def test_rate_limit_wait_sleeps_short_waits_and_surfaces_long_ones() -> None:
    now = dt.datetime(2026, 10, 1, tzinfo=UTC)
    wait, at = rate_limit_wait("2", 0, now=now)
    assert wait is not None and 2 <= wait <= 5 and at is not None
    wait, at = rate_limit_wait("3600", 0, now=now)
    assert wait is None and at is not None
    wait, at = rate_limit_wait(None, 1, now=now)
    assert wait == 4.0 and at is None


async def test_gleif_and_esma_clients_wait_out_a_429_and_report_a_long_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(gleif, "_sleep", fake_sleep)
    monkeypatch.setattr(esma, "_sleep", fake_sleep)
    monkeypatch.setattr(wikidata, "_sleep", fake_sleep)
    record = fixture("lifecycle_gleif_before_rename.json")

    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json=record)

    async with httpx.AsyncClient(transport=httpx.MockTransport(flaky)) as http:
        entity = await gleif.GleifClient(http).record("5299000OLDCO00000001")
    assert entity is not None and entity.legal_name == "Oldco Holdings AG"
    assert len(slept) == 1 and 1 <= slept[0] <= 4

    def blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "900"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as http:
        with pytest.raises(gleif.GleifRateLimited) as g:
            await gleif.GleifClient(http).record("5299000OLDCO00000001")
        with pytest.raises(esma.EsmaRateLimited) as e:
            await esma.firds(http, "DE000OLDC001")
        with pytest.raises(wikidata.WikidataRateLimited) as w:
            await wikidata.WikidataClient(http, "ua").find("L", "I")
    for error in (g.value, e.value, w.value):
        assert error.retry_at is not None and error.retry_at > dt.datetime.now(UTC)
    # still caught by the clients' existing `except GleifError` / `EsmaError` handlers
    assert isinstance(g.value, gleif.GleifError) and isinstance(e.value, esma.EsmaError)


# --------------------------------------------------------------------------- A7: matching

ORD_ISIN, ADR_ISIN = "NL0010273215", "US0000000001"
ISSUER_LEI = "724500Y6DUVHQD6OXN27"


def test_lei_and_cik_confirm_only_the_issuer_never_a_security() -> None:
    ordinary = Candidate("ord", {"isin": ORD_ISIN, "lei": ISSUER_LEI, "cik": "0000937966"})
    adr = Candidate("adr", {"isin": ADR_ISIN, "lei": ISSUER_LEI, "cik": "0000937966"})
    # The ADR's query names its own ISIN: the ordinary share (same LEI, same CIK) is a rejected
    # candidate, not a 0.95 confirmation. This is the audit's Phase 1 exit-check failure.
    query = Candidate("q", {"isin": ADR_ISIN, "lei": ISSUER_LEI})
    result = match_identity(query, [ordinary, adr])
    assert (result.status, result.level, result.candidate) == ("confirmed", "security", adr)
    only_ordinary = match_identity(query, [ordinary])
    assert only_ordinary.status == "unresolved" and only_ordinary.candidate is None
    assert "ord:conflicting-isin" in only_ordinary.reasons

    # LEI alone, query without security ids: an issuer-level confirmation.
    issuer_query = Candidate("q", {"lei": ISSUER_LEI.lower()})
    issuer = match_identity(issuer_query, [adr, Candidate("other", {"lei": "X" * 20})])
    assert (issuer.status, issuer.level, issuer.issuer_confirmed) == ("confirmed", "issuer", True)
    # Both of the issuer's securities share the LEI: not ambiguous for an issuer question.
    both = match_identity(issuer_query, [ordinary, adr])
    assert both.status == "confirmed" and both.level == "issuer"

    # The query names a security the candidate lacks: the shared LEI only *suggests* it.
    lacking = Candidate("sec-less", {"lei": ISSUER_LEI})
    suggested = match_identity(Candidate("q", {"isin": ORD_ISIN, "lei": ISSUER_LEI}), [lacking])
    assert (suggested.status, suggested.level) == ("suggested", "issuer")
    assert suggested.issuer_confirmed


def test_conflicting_identifiers_reject_and_formatting_does_not_decide() -> None:
    query = Candidate("q", {"isin": " nl-0010273215 ", "figi": "bbg000bg7dy8"})
    assert match_identity(query, [Candidate("a", {"isin": ORD_ISIN})]).status == "confirmed"
    wrong_figi = Candidate("b", {"isin": ORD_ISIN, "figi": "BBG000000000"})
    assert match_identity(query, [wrong_figi]).status == "unresolved"
    assert (
        match_identity(
            Candidate("q", {"lei": ISSUER_LEI}),
            [Candidate("c", {"lei": "A" * 20, "isin": ORD_ISIN})],
        ).status
        == "unresolved"
    )
    assert normalize_identifier("cik", "320193") == normalize_identifier("cik", "0000320193")
    assert normalize_identifier("cik", "CIK 0000320193 ") == "0000320193"
    assert normalize_identifier("cik", "0") is None and normalize_identifier("isin", "  ") is None
    assert normalize_identifier("mic", " xetr ") == "XETR"
    # Names and domains still only suggest.
    assert match_identity(Candidate("q", name="Acme"), [Candidate("x", name="ACME")]).status == (
        "suggested"
    )


@pytest.mark.db
async def test_an_issuer_level_match_cannot_be_recorded_as_confirming_a_security(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer_match = match_identity(
        Candidate("q", {"lei": ISSUER_LEI}), [Candidate("c", {"lei": ISSUER_LEI})]
    )
    with pytest.raises(ValueError, match="issuer-level"):
        await record_match_decision(
            backend_pool, issuer_match, instrument_ticker="X_EQ", security_id=1
        )


# --------------------------------------------------------------- A8: observation identity


async def _seed_entities(pool: asyncpg.Pool) -> dict[str, int]:
    issuer = await pool.fetchval(
        "insert into public.issuer (legal_name, lei) values ('Oldco', $1) returning id",
        "5299000OLDCO00000001",
    )
    security = await pool.fetchval(
        "insert into public.security (issuer_id, isin, kind, name, currency)"
        " values ($1, 'DE000OLDC001', 'equity', 'Oldco share', 'EUR') returning id",
        issuer,
    )
    listing = await pool.fetchval(
        "insert into public.security_listing (security_id, mic, ticker, currency, valid_from)"
        " values ($1, 'XETR', 'OLD', 'EUR', '2024-01-10') returning id",
        security,
    )
    return {"issuer": issuer, "security": security, "listing": listing}


def obs(**kw: Any) -> SourceObservation:
    base: dict[str, Any] = {
        "domain": "fundamental",
        "metric": "revenue",
        "source": "sec",
        "period_start": dt.datetime(2026, 1, 1, tzinfo=UTC),
        "period_end": dt.datetime(2026, 3, 31, tzinfo=UTC),
        "value": Decimal(100),
        "unit": "USD",
        "currency": "USD",
        "source_url": "https://example.test/f",
        "retrieved_at": NOW,
    }
    return SourceObservation(**{**base, **kw})


AVAILABLE = AccessResult(AccessState.AVAILABLE)


@pytest.mark.db
async def test_observations_that_differ_in_period_unit_or_dimensions_are_all_kept(
    backend_pool: asyncpg.Pool,
) -> None:
    ytd = obs(period_start=dt.datetime(2026, 1, 1, tzinfo=UTC), value=Decimal(300),
              period_end=dt.datetime(2026, 9, 30, tzinfo=UTC))  # fmt: skip
    quarter = obs(period_start=dt.datetime(2026, 7, 1, tzinfo=UTC), value=Decimal(100),
                  period_end=dt.datetime(2026, 9, 30, tzinfo=UTC))  # fmt: skip
    segment = obs(period_start=dt.datetime(2026, 7, 1, tzinfo=UTC), value=Decimal(40),
                  period_end=dt.datetime(2026, 9, 30, tzinfo=UTC),
                  dimensions={"segment": "cloud"})  # fmt: skip
    eur = obs(
        period_start=dt.datetime(2026, 7, 1, tzinfo=UTC),
        value=Decimal(90),
        period_end=dt.datetime(2026, 9, 30, tzinfo=UTC),
        unit="EUR",
        currency="EUR",
    )
    ids = await publish_observations(
        backend_pool, [ytd, quarter, segment, eur], known_at=NOW, access=AVAILABLE
    )
    # Same period_end, source and metric: they used to collapse into one row.
    assert len(ids) == 4
    # An identical re-fetch stores nothing new.
    assert (
        await publish_observations(
            backend_pool, [ytd, quarter, segment, eur], known_at=NOW, access=AVAILABLE
        )
        == []
    )
    assert await backend_pool.fetchval("select count(*) from public.source_observation") == 4


@pytest.mark.db
async def test_a_restated_value_becomes_the_next_revision_and_supersedes_the_old(
    backend_pool: asyncpg.Pool,
) -> None:
    first = await publish_observations(backend_pool, [obs()], known_at=NOW, access=AVAILABLE)
    restated = await publish_observations(
        backend_pool, [obs(value=Decimal(104))], known_at=NOW + dt.timedelta(days=30),
        access=AVAILABLE,
    )  # fmt: skip
    again = await publish_observations(
        backend_pool, [obs(value=Decimal(104))], known_at=NOW + dt.timedelta(days=31),
        access=AVAILABLE,
    )  # fmt: skip
    assert len(first) == len(restated) == 1 and again == []
    rows = await backend_pool.fetch(
        "select id,revision,value,published from public.source_observation order by revision"
    )
    assert [(r["revision"], r["value"], r["published"]) for r in rows] == [
        (0, Decimal(100), False),  # superseded, kept for replay
        (1, Decimal(104), True),
    ]


@pytest.mark.db
async def test_a_displaced_preferred_row_is_unpublished_and_conflicts_are_recorded(
    backend_pool: asyncpg.Pool,
) -> None:
    priorities = {"vendor": 20, "sec": 5}
    await publish_observations(
        backend_pool, [obs(source="vendor", value=Decimal(110))], known_at=NOW, access=AVAILABLE,
        source_priorities=priorities,
    )  # fmt: skip
    vendor_id = await backend_pool.fetchval("select id from public.source_observation")
    assert await backend_pool.fetchval(
        "select published from public.source_observation where id=$1", vendor_id
    )
    await publish_observations(
        backend_pool, [obs(source="sec", value=Decimal(100))], known_at=NOW, access=AVAILABLE,
        source_priorities=priorities, tolerance_by_metric={"revenue": 1},
    )  # fmt: skip
    published = {
        r["source"]: r["published"]
        for r in await backend_pool.fetch("select source,published from public.source_observation")
    }
    assert published == {"sec": True, "vendor": False}
    conflict = await backend_pool.fetchrow(
        "select preferred_observation_id, alternative_observation_id, difference"
        " from public.source_conflict"
    )
    assert conflict is not None and conflict["alternative_observation_id"] == vendor_id
    assert conflict["difference"] == Decimal(10)
    # A lower-priority source arriving later does not displace the preferred row.
    await publish_observations(
        backend_pool, [obs(source="vendor", value=Decimal(111), vintage="v2")], known_at=NOW,
        access=AVAILABLE, source_priorities=priorities,
    )  # fmt: skip
    assert (
        await backend_pool.fetchval(
            "select published from public.source_observation where source='sec'"
        )
        is True
    )


@pytest.mark.db
async def test_entity_ids_are_stored_and_a_wider_id_is_derived_from_a_narrower_one(
    backend_pool: asyncpg.Pool,
) -> None:
    ids = await _seed_entities(backend_pool)
    await publish_observations(
        backend_pool, [obs(listing_id=ids["listing"])], known_at=NOW, access=AVAILABLE
    )
    await publish_observations(
        backend_pool, [obs(source="vendor", security_id=ids["security"])], known_at=NOW,
        access=AVAILABLE,
    )  # fmt: skip
    rows = await backend_pool.fetch(
        "select source,issuer_id,security_id,listing_id from public.source_observation"
        " order by source"
    )
    assert [tuple(r.values()) for r in rows] == [
        ("sec", ids["issuer"], ids["security"], ids["listing"]),
        ("vendor", ids["issuer"], ids["security"], None),
    ]
    # Identity includes the entity: the same metric for an unlinked subject is another row.
    assert (
        len(await publish_observations(backend_pool, [obs()], known_at=NOW, access=AVAILABLE)) == 1
    )


@pytest.mark.db
async def test_unavailable_access_publishes_nothing(backend_pool: asyncpg.Pool) -> None:
    denied = AccessResult(AccessState.PAID_ONLY)
    assert await publish_observations(backend_pool, [obs()], known_at=NOW, access=denied) == []


# ------------------------------------------------------------------- B6: nulls not distinct


@pytest.mark.db
async def test_unique_constraints_over_nullable_columns_deduplicate(
    backend_pool: asyncpg.Pool,
) -> None:
    insert = (
        "insert into public.listing_lifecycle_snapshot"
        " (isin,mic,publication_date,content_hash,source_url,known_at,raw)"
        " values ('DE000OLDC001','XETR',null,'h1','u',now(),'{}') on conflict do nothing"
    )
    await backend_pool.execute(insert)
    await backend_pool.execute(insert)
    assert (
        await backend_pool.fetchval("select count(*) from public.listing_lifecycle_snapshot") == 1
    )
    ids = await _seed_entities(backend_pool)
    for _ in range(2):
        await backend_pool.execute(
            "insert into public.security_listing (security_id,mic,ticker,currency)"
            " values ($1,'XFRA','OLD','EUR') on conflict (security_id,mic,ticker,valid_from)"
            " do nothing",
            ids["security"],
        )
    assert (
        await backend_pool.fetchval("select count(*) from public.security_listing where mic='XFRA'")
        == 1
    )
    for _ in range(2):  # a corporate action with no listing yet
        await backend_pool.execute(
            "insert into public.corporate_action (symbol,action_type,ex_date,source,source_url)"
            " values ('OLD','split','2026-01-02','s','u') on conflict do nothing"
        )
    assert await backend_pool.fetchval("select count(*) from public.corporate_action") == 1


# --------------------------------------------------- B2: FIRDS cancellations and listing_as_of


def test_firds_fixture_cancellation_and_delisting_are_the_current_state() -> None:
    body = fixture("lifecycle_firds_delisting.json")
    current = {r.mic: r for r in esma.parse_firds(body)}
    assert current["XFRA"].status == "CANC"  # not resurrected from the older NEWF record
    assert current["XETR"].status == "TERM" and current["XETR"].terminated_at is not None
    assert current["XETR"].terminated_at.date() == dt.date(2026, 1, 31)
    assert [r.status for r in esma.parse_firds_history(body)] == [
        "NEWF", "NEWF", "CANC", "MODF", "TERM"
    ]  # fmt: skip


@pytest.mark.db
async def test_an_as_of_screen_uses_the_symbol_valid_then_not_the_current_one(
    backend_pool: asyncpg.Pool,
) -> None:
    """D1.9 regression: a ticker change at the rename, then a delisting."""
    ids = await _seed_entities(backend_pool)
    sec = ids["security"]
    await backend_pool.execute("delete from public.security_listing")
    rows = [
        # (ticker, valid_from, valid_to, status, known_at)
        ("OLD", "2024-01-10", "2025-05-31", "terminated", "2025-06-02T00:00:00Z"),
        ("NEW", "2025-06-01", "2026-01-31", "terminated", "2025-06-05T00:00:00Z"),
        ("EXT", "2025-01-01", None, "terminated", "2026-02-02T00:00:00Z"),  # end not recorded
    ]
    for ticker, start, end, status, known in rows:
        await backend_pool.execute(
            "insert into public.security_listing"
            " (security_id,mic,ticker,currency,status,valid_from,valid_to,known_at)"
            " values ($1,'XETR',$2,'EUR',$3,$4,$5,$6)",
            sec,
            ticker,
            status,
            dt.date.fromisoformat(start),
            dt.date.fromisoformat(end) if end else None,
            dt.datetime.fromisoformat(known),
        )
    later = dt.datetime(2026, 6, 1, tzinfo=UTC)

    async def tickers(day: str, known_at: dt.datetime | None = later) -> list[str]:
        found = await listing_as_of(
            backend_pool, sec, dt.date.fromisoformat(day), known_at=known_at
        )
        return [str(r["ticker"]) for r in found]

    # Today every line is "terminated"; the screen dated 2024 still sees the OLD symbol.
    assert await tickers("2024-06-01") == ["OLD"]
    assert await tickers("2025-12-01") == ["EXT", "NEW"]
    assert await tickers("2026-03-01") == ["EXT"]  # NEW's window ended, EXT's end is unknown
    ext = await listing_as_of(backend_pool, sec, dt.date(2026, 3, 1), known_at=later)
    assert ext[0]["end_unknown"] is True
    # Knowledge cutoff: NEW was recorded on 2025-06-05, so a replay from 2025-06-02 cannot use it.
    assert await tickers("2025-06-10", dt.datetime(2025, 6, 6, tzinfo=UTC)) == ["NEW"]
    assert "NEW" not in await tickers("2025-06-10", dt.datetime(2025, 6, 2, tzinfo=UTC))


# ----------------------------------------------- B3 / D1.9: issuer aliases and relationships


def _entity(name: str) -> gleif.LegalEntity:
    return gleif.parse_record(fixture(name)["data"])


@pytest.mark.db
async def test_a_rename_is_recorded_as_a_dated_alias_window_and_readable_as_of(
    backend_pool: asyncpg.Pool,
) -> None:
    first_seen = dt.datetime(2024, 2, 1, 6, tzinfo=UTC)
    renamed_seen = dt.datetime(2025, 6, 2, 6, tzinfo=UTC)
    before = _entity("lifecycle_gleif_before_rename.json")
    after = _entity("lifecycle_gleif_after_rename.json")
    first = await sync_gleif_issuer(
        backend_pool, before, before_name=None, today=first_seen.date(), known_at=first_seen
    )
    replay = await sync_gleif_issuer(
        backend_pool, before, before_name="Oldco Holdings AG", today=first_seen.date(),
        known_at=first_seen,
    )  # fmt: skip
    assert replay.aliases_written == 0 and replay.issuer_id == first.issuer_id
    second = await sync_gleif_issuer(
        backend_pool, after, before_name="Oldco Holdings AG", today=renamed_seen.date(),
        known_at=renamed_seen,
    )  # fmt: skip
    assert second.issuer_id == first.issuer_id and second.renamed_from == "Oldco Holdings AG"
    issuer = first.issuer_id
    assert await backend_pool.fetchval(
        "select legal_name from public.issuer where id=$1", issuer
    ) == ("Newco Group SE")

    assert await legal_name_as_of(backend_pool, issuer, dt.date(2025, 1, 1)) == "Oldco Holdings AG"
    assert await legal_name_as_of(backend_pool, issuer, dt.date(2025, 6, 1)) == "Oldco Holdings AG"
    assert await legal_name_as_of(backend_pool, issuer, dt.date(2025, 6, 2)) == "Newco Group SE"
    # A replay dated before the rename was *seen* still sees the old name open-ended: the new
    # row is invisible and the close of the old window is not yet recorded.
    cutoff = dt.datetime(2025, 6, 1, tzinfo=UTC)
    assert (
        await legal_name_as_of(backend_pool, issuer, dt.date(2025, 7, 1), known_at=cutoff)
        == "Oldco Holdings AG"
    )
    names = {
        (a["name"], a["kind"])
        for a in await aliases_as_of(backend_pool, issuer, dt.date(2025, 7, 1))
    }
    assert ("Newco Group SE", "legal") in names and ("Newco", "trading") in names
    # The old name is a closed `legal` window, not an undated `former` that every date matches.
    assert all(name != "Oldco Holdings AG" for name, _ in names)


@pytest.mark.db
async def test_a_gleif_successor_becomes_a_dated_merged_into_link(
    backend_pool: asyncpg.Pool,
) -> None:
    merged = _entity("lifecycle_gleif_merged.json")
    successor = _entity("lifecycle_gleif_successor.json")
    seen = dt.datetime(2026, 4, 1, 6, tzinfo=UTC)
    sync = await sync_gleif_issuer(
        backend_pool, merged, before_name=None, today=seen.date(), known_at=seen,
        successor=successor,
    )  # fmt: skip
    assert sync.successor_issuer_id is not None and sync.successor_issuer_id != sync.issuer_id
    for _ in range(2):  # idempotent
        await sync_gleif_issuer(
            backend_pool, merged, before_name=None, today=seen.date(), known_at=seen,
            successor=successor,
        )  # fmt: skip
    assert await backend_pool.fetchval("select count(*) from public.issuer_relationship") == 1
    after = await relationships_as_of(backend_pool, sync.issuer_id, dt.date(2026, 5, 1))
    assert [(r["relationship"], r["successor_id"]) for r in after] == [
        ("merged_into", sync.successor_issuer_id)
    ]
    # An as-of screen dated before the merger, or replayed before it was known, has no link.
    assert await relationships_as_of(backend_pool, sync.issuer_id, dt.date(2026, 3, 1)) == []
    early = dt.datetime(2026, 3, 1, tzinfo=UTC)
    assert (
        await relationships_as_of(backend_pool, sync.issuer_id, dt.date(2026, 5, 1), known_at=early)
        == []
    )


@pytest.mark.db
async def test_firds_name_history_windows_and_entity_graph_snapshots(
    backend_pool: asyncpg.Pool,
) -> None:
    history = esma.parse_firds_history(fixture("lifecycle_firds_delisting.json"))
    assert firds_name_history(history) == [
        ("OLDCO HOLDINGS AG O.N.", None, dt.date(2025, 5, 31)),
        ("NEWCO GROUP SE O.N.", dt.date(2025, 6, 1), None),
    ]
    entity = _entity("lifecycle_gleif_before_rename.json")
    seen = dt.datetime(2024, 2, 1, tzinfo=UTC)
    issuer_id = (
        await sync_gleif_issuer(
            backend_pool, entity, before_name=None, today=seen.date(), known_at=seen
        )
    ).issuer_id
    assert await sync_firds_names(backend_pool, issuer_id, history, known_at=seen) == 2
    assert await sync_firds_names(backend_pool, issuer_id, history, known_at=seen) == 0
    trading = {
        a["name"]
        for a in await aliases_as_of(backend_pool, issuer_id, dt.date(2025, 1, 1), known_at=NOW)
        if a["kind"] == "trading"
    }
    assert trading == {"OLDCO HOLDINGS AG O.N."}
    # GLEIF Level-1 snapshots: the entity graph as it was known, not the current one.
    for name, when in (("before", "2024-02-01"), ("after", "2025-06-02")):
        record = fixture(f"lifecycle_gleif_{name}_rename.json")
        await backend_pool.execute(
            "insert into public.legal_entity_snapshot"
            " (lei,known_at,legal_name,source_url,content_hash,raw)"
            " values ('5299000OLDCO00000001',$1,$2,'u',$3,$4::jsonb)",
            dt.datetime.fromisoformat(when).replace(tzinfo=UTC),
            record["data"]["attributes"]["entity"]["legalName"]["name"],
            name,
            json.dumps(record),
        )
    snap = await issuer_snapshot_as_of(
        backend_pool, "5299000OLDCO00000001", dt.datetime(2025, 1, 1, tzinfo=UTC)
    )
    assert snap is not None and snap["legal_name"] == "Oldco Holdings AG"
    assert (
        await issuer_snapshot_as_of(
            backend_pool, "5299000OLDCO00000001", dt.datetime(2023, 1, 1, tzinfo=UTC)
        )
        is None
    )


# ---------------------------------------------- D1.8 / C-13: documents, evidence, immutability


async def _link(pool: asyncpg.Pool, content: bytes, known_at: dt.datetime, **kw: Any) -> int:
    return await link_public_document(
        pool,
        regulator="sec",
        external_id="0001-26-000001",
        stable_url="https://example.test/doc",
        document_type="8-K",
        content=content,
        known_at=known_at,
        **kw,
    )


@pytest.mark.db
async def test_document_versions_are_chained_and_readable_as_of(
    backend_pool: asyncpg.Pool,
) -> None:
    t1, t2, t3 = (dt.datetime(2026, m, 1, tzinfo=UTC) for m in (1, 2, 3))
    v1 = await _link(backend_pool, b"v1", t1, filed_at=t1)
    v3 = await _link(backend_pool, b"v3", t3, filed_at=t3)
    v2 = await _link(backend_pool, b"v2", t3 + dt.timedelta(days=5), filed_at=t2)  # late arrival
    chain = {
        r["id"]: r["superseded_by_id"]
        for r in await backend_pool.fetch(
            "select id, superseded_by_id from public.source_document_version"
        )
    }
    assert chain == {v1: v2, v2: v3, v3: None}

    def ids(rows: list[dict[str, object]]) -> list[object]:
        return [r["id"] for r in rows]

    cutoff = t2 + dt.timedelta(days=1)
    assert ids(await document_versions_as_of(backend_pool, "sec", "0001-26-000001", cutoff)) == [v1]
    current = await document_version_as_of(backend_pool, "sec", "0001-26-000001", cutoff)
    # At the cutoff v1 was current: v1's superseder (v2) arrived later, so it is not shown.
    assert current is not None and current["id"] == v1 and current["superseded_by_id"] is None
    assert (
        await document_version_as_of(backend_pool, "sec", "0001-26-000001", t1 - dt.timedelta(1))
        is None
    )
    latest = await document_version_as_of(backend_pool, "sec", "0001-26-000001")
    assert latest is not None and latest["id"] == v3


@pytest.mark.db
async def test_record_evidence_is_read_as_it_stood_at_the_cutoff(
    backend_pool: asyncpg.Pool,
) -> None:
    t1, t2 = dt.datetime(2026, 1, 1, tzinfo=UTC), dt.datetime(2026, 2, 1, tzinfo=UTC)
    await _link(backend_pool, b"a", t1, domain_table="fact", record_key=7, quote="net income 5")
    await _link(backend_pool, b"b", t2, domain_table="fact", record_key=7, quote="net income 6")
    early = await record_evidence_as_of(backend_pool, "fact", 7, t1 + dt.timedelta(days=1))
    assert [r["quote"] for r in early] == ["net income 5"]
    assert [r["quote"] for r in await record_evidence_as_of(backend_pool, "fact", 7)] == [
        "net income 5",
        "net income 6",
    ]
    assert await record_evidence_as_of(backend_pool, "fact", 8) == []


@pytest.mark.db
async def test_concurrent_identical_quotes_share_one_evidence_row(
    backend_pool: asyncpg.Pool,
) -> None:
    version = await _link(backend_pool, b"doc", NOW)
    quote = "Revenue grew 12% year over year."
    ids = await asyncio.gather(
        *[
            link_public_document(
                backend_pool,
                regulator="sec",
                external_id="0001-26-000001",
                stable_url="https://example.test/doc",
                document_type="8-K",
                content=b"doc",
                known_at=NOW,
                quote=quote,
                domain_table="fact",
                record_key=n,
            )
            for n in range(12)
        ]
    )
    assert set(ids) == {version}
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.source_evidence where document_version_id=$1", version
        )
        == 1
    )
    assert (
        await backend_pool.fetchval(
            "select count(distinct evidence_id) from public.source_record_evidence"
        )
        == 1
    )


@pytest.mark.db
async def test_recorded_evidence_is_append_only_unless_maintenance_says_so(
    backend_pool: asyncpg.Pool,
) -> None:
    version = await _link(
        backend_pool, b"doc", NOW, quote="a quote", domain_table="fact", record_key=1
    )
    evidence = await backend_pool.fetchval("select id from public.source_evidence")
    await backend_pool.execute(
        "update public.source_document_version set retrieved_at=now() where id=$1", version
    )  # bookkeeping columns stay writable
    for statement, args in (
        ("update public.source_document_version set content_hash='x' where id=$1", (version,)),
        ("delete from public.source_document_version where id=$1", (version,)),
        ("update public.source_evidence set quote='edited' where id=$1", (evidence,)),
        ("delete from public.source_evidence where id=$1", (evidence,)),
        ("delete from public.source_record_evidence", ()),
        ("update public.listing_lifecycle_snapshot set status='x'", ()),
    ):
        if "listing_lifecycle" in statement:
            await backend_pool.execute(
                "insert into public.listing_lifecycle_snapshot"
                " (isin,mic,content_hash,source_url,known_at,raw)"
                " values ('DE000OLDC001','XETR','h','u',now(),'{}')"
            )
        with pytest.raises(asyncpg.RestrictViolationError, match="append-only"):
            await backend_pool.execute(statement, *args)
    async with backend_pool.acquire() as conn, conn.transaction():
        await conn.execute("set local steward.allow_append_only_changes = 'on'")
        await conn.execute("update public.source_evidence set quote='edited' where id=$1", evidence)
    assert (
        await backend_pool.fetchval(
            "select quote from public.source_evidence where id=$1", evidence
        )
        == "edited"
    )


# --------------------------------------------------------------- B52: foreign-key indexes

LAST_CORE_MIGRATION = "20261005100999"


@pytest.mark.db
async def test_every_foreign_key_has_a_covering_index(backend_pool: asyncpg.Pool) -> None:
    later_tables: set[str] = set()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        if path.name.split("_")[0] > LAST_CORE_MIGRATION:
            later_tables |= set(
                re.findall(
                    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?(\w+)",
                    path.read_text(),
                    flags=re.I,
                )
            )
    missing = await backend_pool.fetch(
        """select c.conrelid::regclass::text as tbl, c.conname
             from pg_constraint c join pg_namespace n on n.oid = c.connamespace
            where c.contype = 'f' and n.nspname = 'public'
              and not exists (select 1 from pg_index i
                               where i.indrelid = c.conrelid and i.indisvalid and i.indpred is null
                                 and (i.indkey::int2[])[0:array_length(c.conkey, 1) - 1]
                                     = c.conkey)"""
    )
    # Tables created by later streams' migrations are theirs to index.
    offenders = [f"{r['tbl']}.{r['conname']}" for r in missing if r["tbl"] not in later_tables]
    assert offenders == []


# ------------------------------------------------------------------------- B5: override


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> Any:
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


@pytest.mark.db
async def test_identity_override_validates_ids_and_never_confirms_nothing(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    ids = await _seed_entities(backend_pool)
    await backend_pool.execute(
        "insert into public.instrument (t212_ticker, isin, name, currency)"
        " values ('OLD_EQ', 'DE000OLDC001', 'Oldco', 'EUR')"
    )
    url = "/reference/identity/OLD_EQ/override"
    reason = {"reason": "checked the KID"}

    nothing = await api.post(url, json={"decision": "confirmed", **reason})
    assert nothing.status_code == 422 and "security_id" in nothing.text
    issuer_only = await api.post(
        url, json={"decision": "confirmed", "issuer_id": ids["issuer"], **reason}
    )
    assert issuer_only.status_code == 422
    bad_fk = await api.post(url, json={"decision": "confirmed", "security_id": 9999, **reason})
    assert bad_fk.status_code == 422  # used to be a 500 from the foreign key
    bad_listing = await api.post(url, json={"decision": "confirmed", "listing_id": 9999, **reason})
    assert bad_listing.status_code == 422
    unknown = await api.post(
        "/reference/identity/NOPE_EQ/override",
        json={"decision": "confirmed", "security_id": ids["security"], **reason},
    )
    assert unknown.status_code == 404
    assert await backend_pool.fetchval("select count(*) from public.identity_match_decision") == 0

    ok = await api.post(url, json={"decision": "confirmed", "listing_id": ids["listing"], **reason})
    assert ok.status_code == 200
    link = await backend_pool.fetchrow(
        "select security_id, listing_id, status from public.instrument_security_link"
    )
    assert link is not None and tuple(link.values()) == (
        ids["security"],
        ids["listing"],
        "confirmed",
    )
    # Rejecting needs no ids.
    assert (await api.post(url, json={"decision": "rejected", **reason})).status_code == 200

    # The as-of reads are served too.
    sync_day = NOW.date()
    reading = await api.get(
        f"/reference/issuers/{ids['issuer']}/as-of", params={"date": sync_day.isoformat()}
    )
    assert reading.status_code == 200 and reading.json()["issuer_id"] == ids["issuer"]
    assert (
        await api.get("/reference/issuers/424242/as-of", params={"date": "2026-01-01"})
    ).status_code == 404
    version = await _link(backend_pool, b"doc", NOW, domain_table="fact", record_key=5, quote="q")
    versions = await api.get(
        "/reference/documents/versions",
        params={"regulator": "sec", "external_id": "0001-26-000001"},
    )
    assert versions.status_code == 200 and versions.json()[0]["id"] == version
    evidence = await api.get("/reference/records/fact/5/evidence")
    assert evidence.status_code == 200 and evidence.json()[0]["quote"] == "q"


@pytest.mark.db
async def test_identity_decisions_route_returns_reasons_as_json_not_a_string(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    """N2: no jsonb codec is registered, so `reasons` used to go out as the string '["isin"]'."""
    await backend_pool.execute(
        "insert into public.instrument (t212_ticker, isin, name, currency)"
        " values ('N2_EQ', 'DE000N2C0001', 'N2 Co', 'EUR')"
    )
    await backend_pool.execute(
        "insert into public.identity_match_decision (instrument_ticker, decision, score, reasons)"
        " values ('N2_EQ', 'suggested', 0.5, '[\"isin\", {\"lei\": \"x\"}]'::jsonb)"
    )
    body = (await api.get("/reference/identity-decisions")).json()
    assert body[0]["reasons"] == ["isin", {"lei": "x"}]
