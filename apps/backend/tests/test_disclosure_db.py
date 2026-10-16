"""Public-disclosure P1 against a real database: the point-in-time exit check (a filing, a later
amendment and a restatement at three historical cutoffs), versioning, append-only guards, the
reviewer queue, identity, the fetch governor and the owner API."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.disclosure import asof, review
from steward.disclosure.fetch import DisclosureFetcher, FetchFailure, Validators
from steward.disclosure.ingest import (
    DocumentRecord,
    ingest_document_version,
    quarantine_review,
    record_claim,
    record_observation,
    store_span,
)
from steward.disclosure.parse import PARSER_VERSION, parse_html
from steward.disclosure.registry import (
    SourceAccess,
    SourceNotEnabled,
    record_access_test,
    seed_sources,
    set_enabled,
)
from steward.reference import disclosure_identity as ident
from steward.sources.governor import QuotaExceeded

pytestmark = pytest.mark.db

OWNER = uuid.UUID("00000000-0000-0000-0000-00000000000a")
UTC = dt.UTC
T1 = dt.datetime(2026, 2, 20, 21, 0, tzinfo=UTC)  # original 10-K accepted
T2 = dt.datetime(2026, 5, 2, 20, 15, tzinfo=UTC)  # 10-K/A accepted
BEFORE = dt.datetime(2026, 1, 1, tzinfo=UTC)
BETWEEN = dt.datetime(2026, 3, 15, tzinfo=UTC)
AFTER = dt.datetime(2026, 6, 1, tzinfo=UTC)


def filing_html(revenue: str) -> bytes:
    return (
        "<html><body><p>Item 7. Management's Discussion</p>"
        f'<p>Revenue was <ix:nonFraction name="us-gaap:Revenues" contextRef="c" unitRef="u" '
        f'scale="6">{revenue}</ix:nonFraction> million.</p></body></html>'
    ).encode()


async def issuer(pool: asyncpg.Pool, name: str = "Acme Corp", **cols: str) -> int:
    row = await pool.fetchrow(
        "insert into public.issuer(legal_name, cik, lei, country) values($1,$2,$3,'US')"
        " returning id",
        name,
        cols.get("cik"),
        cols.get("lei"),
    )
    assert row is not None
    return int(row["id"])


async def file_revenue(
    pool: asyncpg.Pool,
    issuer_id: int,
    accession: str,
    known_at: dt.datetime,
    revenue: str,
    *,
    amends: str | None = None,
    supersedes_id: int | None = None,
) -> tuple[int, int, int]:
    """Ingest a filing and the one revenue observation read from it: (version, evidence, obs)."""
    await seed_sources(pool)
    content = filing_html(revenue)
    result = await ingest_document_version(
        pool,
        DocumentRecord(
            regulator="sec",
            external_id=accession,
            stable_url=f"https://www.sec.gov/Archives/edgar/data/1234/{accession}/a.htm",
            form="10-K/A" if amends else "10-K",
            known_at=known_at,
            issuer_id=issuer_id,
            fiscal_period="FY2025",
            accepted_at=known_at,
            filed_at=known_at,
            amends_external_id=amends,
            source_key="sec.archive",
        ),
        content,
        parser_version=PARSER_VERSION,
    )
    doc = parse_html(content)
    fact = doc.facts[0]
    assert fact.start is not None and fact.end is not None and fact.value is not None
    evidence_id = await store_span(
        pool, result.version_id, doc, fact.start, fact.end, method="ix:nonFraction", confidence=1.0
    )
    obs_id = await record_observation(
        pool,
        metric="revenue",
        issuer_id=issuer_id,
        version_id=result.version_id,
        evidence_id=evidence_id,
        known_at=known_at,
        period_start=dt.date(2025, 1, 1),
        period_end=dt.date(2025, 12, 31),
        value=fact.value,
        original_string=fact.raw_text,
        unit="USD",
        scale=6,
        currency="USD",
        parser_version=PARSER_VERSION,
        supersedes_id=supersedes_id,
    )
    assert obs_id is not None
    return result.version_id, evidence_id, obs_id


# ------------------------------------------------------------ P1 exit: three cutoffs
async def test_filing_amendment_and_restatement_at_three_cutoffs(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    acme = await issuer(pool)
    v1, _e1, o1 = await file_revenue(pool, acme, "0000001234-26-000003", T1, "1,000")
    v2, _e2, o2 = await file_revenue(
        pool,
        acme,
        "0000001234-26-000012",
        T2,
        "950",
        amends="0000001234-26-000003",
        supersedes_id=o1,
    )

    before = await asof.snapshot(pool, acme, BEFORE)
    assert before["documents"] == [] and before["observations"] == []

    between = await asof.snapshot(pool, acme, BETWEEN)
    assert [d["version_id"] for d in between["documents"]] == [v1]  # the amendment is unseen
    assert [o["id"] for o in between["observations"]] == [o1]
    assert between["observations"][0]["value"] == Decimal("1000000000")

    after = await asof.snapshot(pool, acme, AFTER)
    assert {d["version_id"] for d in after["documents"]} == {v1, v2}
    by_version = {d["version_id"]: d for d in after["documents"]}
    assert by_version[v2]["amends_version_id"] == v1  # amendment link, known by this cutoff
    assert by_version[v1]["amends_version_id"] is None
    assert [o["id"] for o in after["observations"]] == [o2]  # restated value, original hidden
    assert after["observations"][0]["value"] == Decimal("950000000")
    assert after["observations"][0]["envelope"]["known_at"] == T2

    # a full amendment replaces the original once known; before that the original is in force
    assert by_version[v1]["is_current"] is False
    assert by_version[v2]["is_current"] is True
    assert between["documents"][0]["is_current"] is True


async def test_event_effective_date_never_admits_a_row(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    acme = await issuer(pool)
    _v, evidence, _o = await file_revenue(pool, acme, "0000001234-26-000003", T2, "10")
    backdated = await pool.fetchval(
        "select count(*) from public.disclosure_observation where issuer_id=$1", acme
    )
    assert backdated == 1
    # effective long before it became known: still invisible until known_at
    await pool.execute(
        "insert into public.disclosure_observation(metric,issuer_id,period_end,unit,value,"
        "document_version_id,evidence_id,parser_version,known_at,event_effective_at,segment)"
        " select 'backdated',$1,period_end,'USD',1,document_version_id,evidence_id,"
        "'t',$2,'2020-01-01','x' from public.disclosure_observation where issuer_id=$1 limit 1",
        acme,
        T2,
    )
    assert [o["metric"] for o in await asof.observations_as_of(pool, acme, T1)] == []
    assert {o["metric"] for o in await asof.observations_as_of(pool, acme, T2)} == {
        "revenue",
        "backdated",
    }
    assert evidence > 0


async def test_provenance_resolves_source_span_and_version_chain(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    acme = await issuer(pool)
    v1, _e, o1 = await file_revenue(pool, acme, "0000001234-26-000003", T1, "1,000")
    info = await asof.provenance(pool, "observation", o1, BETWEEN)
    assert info is not None
    assert info["external_id"] == "0000001234-26-000003" and info["document_version_id"] == v1
    assert info["quote"].strip() == "1,000"
    assert info["extraction_method"] == "ix:nonFraction"
    assert info["envelope"]["method_version"] == PARSER_VERSION
    assert info["envelope"]["document_url"].endswith("a.htm")
    assert await asof.provenance(pool, "observation", o1, BEFORE) is None  # not yet known


# ------------------------------------------------------------------ versions and quarantine
async def test_identical_bytes_do_not_create_a_version_and_changed_bytes_do(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    acme = await issuer(pool)
    record = DocumentRecord(
        regulator="sec",
        external_id="acc-1",
        stable_url="https://x/acc-1",
        form="8-K",
        known_at=T1,
        issuer_id=acme,
        date_notes={"published_at": "n/a"},
    )
    first = await ingest_document_version(pool, record, b"<p>one</p>")
    again = await ingest_document_version(pool, record, b"<p>one</p>")
    changed = await ingest_document_version(pool, record, b"<p>two</p>")
    assert first.created and not again.created and changed.created
    assert first.version_id == again.version_id != changed.version_id
    assert first.document_id == changed.document_id
    superseded = await pool.fetchval(
        "select superseded_by_id from public.source_document_version where id=$1",
        first.version_id,
    )
    assert superseded == changed.version_id  # chain recomputed from the whole history


async def test_a_refetched_sec_accession_with_other_bytes_is_not_a_new_version(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    record = DocumentRecord(
        regulator="SEC",
        external_id="0000000001-26-000099",
        stable_url="https://x/acc-99",
        form="8-K",
        known_at=T1,
        issuer_id=await issuer(pool),
    )
    unreadable = await ingest_document_version(
        pool, record, b"<p>?</p>", parser_version="p1", quarantine_reason="no body"
    )
    read = await ingest_document_version(pool, record, b"<p>one</p>", parser_version="p1")
    refetched = await ingest_document_version(pool, record, b"<p>one </p>", parser_version="p1")
    reparsed = await ingest_document_version(pool, record, b"<p>one  </p>", parser_version="p2")
    assert unreadable.created and read.created  # a parser may read what it once could not
    assert not refetched.created and refetched.version_id == read.version_id
    assert reparsed.created  # a new parser reads the filing again


async def test_unreadable_document_is_quarantined_with_one_review(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    acme = await issuer(pool)
    result = await ingest_document_version(
        pool,
        DocumentRecord("sec", "bad-1", "https://x/bad", "10-K", T1, issuer_id=acme),
        b"   ",
        quarantine_reason="document has no readable text",
    )
    assert result.quarantined
    await quarantine_review(pool, result.version_id, "parse_failure", {"error": "empty"})
    await quarantine_review(pool, result.version_id, "parse_failure", {"error": "empty"})
    items = await review.queue(pool, subject_type="document_version")
    assert len(items) == 1 and items[0]["reason"] == "parse_failure"
    timeline = await asof.filing_timeline(pool, acme, AFTER)
    assert timeline[0]["quarantined"] and timeline[0]["quarantine_reason"]


# ------------------------------------------------------------------------ append-only
async def test_observations_and_claims_cannot_be_edited_in_place(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    acme = await issuer(pool)
    v1, e1, o1 = await file_revenue(pool, acme, "0000001234-26-000003", T1, "1,000")
    claim = await record_claim(
        pool,
        claim_type="relationship.customer",
        evidence_id=e1,
        version_id=v1,
        known_at=T1,
        method_version="m1",
        subject_issuer_id=acme,
        object_label="Customer A",
        object_anonymous=True,
        direction="supplier_to_customer",
    )
    for sql, args in (
        ("update public.disclosure_observation set value=1 where id=$1", (o1,)),
        ("update public.extracted_claim set polarity='denied' where id=$1", (claim,)),
        ("update public.extracted_claim set valid_to='2030-01-01' where id=$1", (claim,)),
        ("delete from public.disclosure_observation where id=$1", (o1,)),
    ):
        with pytest.raises(asyncpg.RestrictViolationError):
            await pool.execute(sql, *args)
    # review bookkeeping is the only thing that may change
    await pool.execute(
        "update public.disclosure_observation set review_state='under_review' where id=$1", o1
    )


async def test_verified_state_needs_a_decider_and_names_need_evidence(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    acme = await issuer(pool)
    v1, e1, _o = await file_revenue(pool, acme, "0000001234-26-000003", T1, "1,000")
    claim = await record_claim(
        pool,
        claim_type="relationship.customer",
        evidence_id=e1,
        version_id=v1,
        known_at=T1,
        method_version="m1",
        subject_issuer_id=acme,
        object_label="Globex",
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "update public.extracted_claim set review_state='verified' where id=$1", claim
        )
    state = await pool.fetchval(
        "select review_state from public.extracted_claim where id=$1", claim
    )
    assert state == "machine_suggested"
    with pytest.raises(asyncpg.CheckViolationError):  # an anonymous object has no company id
        await pool.execute(
            "insert into public.extracted_claim(claim_type, subject_issuer_id, object_issuer_id,"
            " object_anonymous, evidence_id, document_version_id, method_version, known_at)"
            " values('r',$1,$1,true,$2,$3,'m',$4)",
            acme,
            e1,
            v1,
            T1,
        )


# --------------------------------------------------------------------------- review queue
async def test_review_decision_is_audited_and_excludes_rejected_claims(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    acme = await issuer(pool)
    v1, e1, _o = await file_revenue(pool, acme, "0000001234-26-000003", T1, "1,000")
    named = await record_claim(
        pool,
        claim_type="relationship.customer",
        evidence_id=e1,
        version_id=v1,
        known_at=T1,
        method_version="m1",
        subject_issuer_id=acme,
        object_label="Globex Corp",
        confidence=0.4,
    )
    anon = await record_claim(
        pool,
        claim_type="relationship.customer",
        evidence_id=e1,
        version_id=v1,
        known_at=T1,
        method_version="m1",
        subject_issuer_id=acme,
        object_label="Customer A",
        object_anonymous=True,
        confidence=0.9,
    )
    candidate = {"object": "Globex Corp", "score": 0.4}
    r_named = await review.enqueue(pool, "claim", named, "entity_ambiguity", candidate)
    assert await review.enqueue(pool, "claim", named, "entity_ambiguity", candidate) is None
    r_anon = await review.enqueue(pool, "claim", anon, "low_confidence", {"object": "Customer A"})
    assert r_named is not None and r_anon is not None

    with pytest.raises(review.ReviewError, match="reason"):
        await review.decide(pool, r_named, "approved", "owner:x", "  ")
    with pytest.raises(review.ReviewError, match="unresolved counterparty"):
        await review.decide(pool, r_named, "approved", "owner:x", "looks right")
    still = await pool.fetchval(
        "select review_state from public.extracted_claim where id=$1", named
    )
    assert still == "machine_suggested"  # the refused approval changed nothing

    approved = await review.decide(
        pool, r_anon, "approved", "owner:x", "table shows Customer A 23%"
    )
    assert approved["status"] == "approved"
    decided = dt.datetime.now(UTC)  # decisions are stamped with the wall clock
    verified = {c["id"] for c in await asof.claims_as_of(pool, acme, decided, verified_only=True)}
    assert verified == {anon}
    # a decision made after the cutoff never rewrites what was known at the cutoff (audit 4.1)
    assert await asof.claims_as_of(pool, acme, AFTER, verified_only=True) == []

    await review.decide(pool, r_named, "rejected", "owner:x", "different Globex entity")
    later = dt.datetime.now(UTC)
    visible = {c["id"] for c in await asof.claims_as_of(pool, acme, later)}
    assert visible == {anon}  # rejected is excluded from authoritative reads but still stored
    before = {c["id"] for c in await asof.claims_as_of(pool, acme, AFTER)}
    assert before == {named, anon}  # at AFTER neither had been decided
    assert await pool.fetchval("select count(*) from public.extracted_claim") == 2
    with pytest.raises(review.ReviewError, match="already decided"):
        await review.decide(pool, r_named, "approved", "owner:x", "again")

    log = await review.history(pool, r_named)
    assert [e["action"] for e in log] == ["rejected"] and log[0]["actor"] == "owner:x"
    original = (await review.queue(pool, status="rejected"))[0]
    assert original["candidate"] == {"object": "Globex Corp", "score": 0.4}  # candidate intact
    assert original["decided_by"] == "owner:x" and original["decision_reason"]


async def test_a_claim_ended_by_a_successor_is_only_ended_after_the_successor_is_known(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    acme = await issuer(pool)
    v1, e1, _o = await file_revenue(pool, acme, "0000001234-26-000003", T1, "1,000")
    first = await record_claim(
        pool,
        claim_type="relationship.customer",
        evidence_id=e1,
        version_id=v1,
        known_at=T1,
        method_version="m1",
        subject_issuer_id=acme,
        object_label="Customer A",
        object_anonymous=True,
    )
    v2, e2, _o2 = await file_revenue(
        pool, acme, "0000001234-26-000012", T2, "950", amends="0000001234-26-000003"
    )
    await record_claim(
        pool,
        claim_type="relationship.customer",
        evidence_id=e2,
        version_id=v2,
        known_at=T2,
        method_version="m1",
        subject_issuer_id=acme,
        object_label="Customer A",
        object_anonymous=True,
        polarity="denied",
        supersedes_claim_id=first,
    )
    mid = await asof.claims_as_of(pool, acme, BETWEEN)
    assert [c["id"] for c in mid] == [first] and mid[0]["polarity"] == "asserted"
    late = await asof.claims_as_of(pool, acme, AFTER)
    assert [c["polarity"] for c in late] == ["denied"]


# ------------------------------------------------------------------------------- identity
async def test_identity_confirms_by_identifier_only_and_never_invents_ids(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    globex = await issuer(pool, "Globex Corporation", cik="0000005678", lei="5493001KJTIIGC8Y1R12")
    await issuer(pool, "Globex Holdings Ltd")

    by_lei = await ident.resolve_mention(pool, "Globex", lei="5493001KJTIIGC8Y1R12")
    assert by_lei["status"] == "confirmed" and by_lei["issuer_id"] == globex
    by_cik = await ident.resolve_mention(pool, "G", cik="5678", mention_kind="issuer")
    assert by_cik["status"] == "confirmed"

    by_name = await ident.resolve_mention(pool, "Globex Corp")
    assert by_name["status"] != "confirmed" and by_name["issuer_id"] is None
    nobody = await ident.resolve_mention(pool, "Zzyzx Industries")
    assert nobody["status"] == "unresolved" and nobody["issuer_id"] is None

    anon = await ident.resolve_mention(pool, "Customer A")
    assert anon["anonymous"] and anon["issuer_id"] is None
    row = await pool.fetchrow(
        "select issuer_id, security_id, anonymous_label from public.disclosure_entity_mapping"
        " where id=$1",
        anon["id"],
    )
    assert row is not None and row["anonymous_label"] == "Customer A" and row["issuer_id"] is None
    with pytest.raises(asyncpg.CheckViolationError):  # name-only confirmation needs a decider
        await pool.execute(
            "insert into public.disclosure_entity_mapping(mention, mention_kind, issuer_id, status)"
            " values('Globex','issuer',$1,'confirmed')",
            globex,
        )


async def test_succession_is_followed_only_when_known(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    old, new = await issuer(pool, "OldCo"), await issuer(pool, "NewCo")
    await pool.execute(
        "insert into public.issuer_relationship(predecessor_id, successor_id, relationship,"
        " valid_from, known_at) values($1,$2,'renamed_to','2026-04-01',$3)",
        old,
        new,
        dt.datetime(2026, 4, 2, tzinfo=UTC),
    )
    assert await ident.successors(pool, old, dt.datetime(2026, 3, 1, tzinfo=UTC)) == []
    assert (
        await ident.successors(pool, old, dt.datetime(2026, 4, 1, 12, tzinfo=UTC)) == []
    )  # not yet known
    assert await ident.successors(pool, old, AFTER) == [new]


async def test_disclosures_join_portfolio_only_through_a_confirmed_link(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    acme = await issuer(pool)
    sec = await pool.fetchval(
        "insert into public.security(issuer_id, kind, name)"
        " values($1,'common','Acme') returning id",
        acme,
    )
    for ticker, isin in (("ACME_US_EQ", "US0000000011"), ("ACMX_US_EQ", "US0000000029")):
        await pool.execute(
            "insert into public.instrument(t212_ticker, isin, name, currency)"
            " values($1,$2,'Acme','USD')",
            ticker,
            isin,
        )
    await pool.execute(
        "insert into public.instrument_security_link(ticker, security_id, status) values"
        "('ACME_US_EQ',$1,'confirmed'),('ACMX_US_EQ',$1,'suggested')",
        sec,
    )
    assert await ident.portfolio_tickers(pool, acme) == ["ACME_US_EQ"]


# --------------------------------------------------------------------------- fetch governor
async def enabled_fetcher(
    pool: asyncpg.Pool,
    handler,
    *,
    key: str = "sec.archive",
    ua: str | None = "Steward test@example.com",
) -> DisclosureFetcher:
    await seed_sources(pool)
    await record_access_test(pool, key, SourceAccess.AVAILABLE, "recorded by the test probe")
    await set_enabled(pool, key, True)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DisclosureFetcher(pool, http, user_agent=ua)


async def test_source_registry_refuses_untested_sources(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    assert await seed_sources(pool) > 0 and await seed_sources(pool) == 0  # idempotent
    with pytest.raises(SourceNotEnabled):
        await set_enabled(pool, "sedarplus.portal", True)
    with pytest.raises(asyncpg.CheckViolationError):  # the database enforces it too
        await pool.execute(
            "update public.disclosure_source set enabled=true where source_key='ted.search'"
        )
    fetcher = DisclosureFetcher(
        pool, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    )
    with pytest.raises(SourceNotEnabled):
        await fetcher.fetch("ted.search", "https://ted.example/x")
    # a failed test disables a source that was enabled
    await record_access_test(pool, "sec.archive", SourceAccess.AVAILABLE, "ok")
    await set_enabled(pool, "sec.archive", True)
    await record_access_test(pool, "sec.archive", SourceAccess.RETRIEVAL_FAILED, "timeout")
    assert (
        await pool.fetchval(
            "select enabled from public.disclosure_source where source_key='sec.archive'"
        )
        is False
    )


async def test_fetch_sends_declared_user_agent_validators_and_dedupes_by_hash(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(
            200, content=b"<html>x</html>", headers={"ETag": '"v1"', "Content-Type": "text/html"}
        )

    stored: dict[str, bytes] = {}

    class Store:
        async def put(self, path: str, data: bytes, content_type: str) -> None:
            stored[path] = data

        async def get(self, path: str) -> bytes:
            return stored[path]

        async def signed_url(self, path: str, expires_in: int) -> str:
            return "https://signed/" + path

    fetcher = await enabled_fetcher(pool, handler)
    fetcher._store = Store()
    first = await fetcher.fetch("sec.archive", "https://www.sec.gov/Archives/x.htm?token=SECRET")
    assert first.modified and first.url == "https://www.sec.gov/Archives/x.htm"
    assert first.archive_path and stored[first.archive_path] == b"<html>x</html>"
    assert seen[0].headers["User-Agent"].endswith("test@example.com")
    again = await fetcher.fetch(
        "sec.archive", "https://www.sec.gov/Archives/x.htm", validators=first.validators
    )
    assert not again.modified and again.status_code == 304
    assert Validators().headers() == {}


async def test_fetch_classifies_failures_and_requires_a_contact_user_agent(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    fetcher = await enabled_fetcher(pool, lambda r: httpx.Response(404))
    with pytest.raises(FetchFailure) as missing:
        await fetcher.fetch("sec.archive", "https://www.sec.gov/Archives/none.htm")
    assert missing.value.state == "not_covered"

    nobody = await enabled_fetcher(pool, lambda r: httpx.Response(200), ua=None)
    with pytest.raises(FetchFailure) as no_ua:
        await nobody.fetch("sec.archive", "https://www.sec.gov/Archives/x.htm")
    assert no_ua.value.state == "authentication_failed"

    limited = await enabled_fetcher(
        pool, lambda r: httpx.Response(429, headers={"Retry-After": "120"})
    )
    with pytest.raises(FetchFailure) as slow:
        await limited.fetch("sec.archive", "https://www.sec.gov/Archives/x.htm")
    assert slow.value.state == "retrieval_failed" and slow.value.retry_at is not None
    # the shared governor now refuses further requests until the server's wait elapses
    with pytest.raises((QuotaExceeded, RuntimeError)):
        await limited.fetch("sec.archive", "https://www.sec.gov/Archives/x.htm")


async def test_oversized_download_is_refused(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    fetcher = await enabled_fetcher(pool, lambda r: httpx.Response(200, content=b"x" * 5000))
    fetcher._max_bytes = 1000
    with pytest.raises(FetchFailure, match="larger"):
        await fetcher.fetch("sec.archive", "https://www.sec.gov/Archives/big.htm")


# ------------------------------------------------------------------------------------ API
@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(str(OWNER), None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_api_snapshot_provenance_and_review(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    pool = backend_pool
    acme = await issuer(pool)
    v1, e1, o1 = await file_revenue(pool, acme, "0000001234-26-000003", T1, "1,000")
    claim = await record_claim(
        pool,
        claim_type="relationship.customer",
        evidence_id=e1,
        version_id=v1,
        known_at=T1,
        method_version="m1",
        subject_issuer_id=acme,
        object_label="Customer A",
        object_anonymous=True,
        confidence=0.3,
    )
    review_id = await review.enqueue(
        pool, "claim", claim, "low_confidence", {"object": "Customer A"}
    )

    early = await api.get(
        f"/disclosures/issuers/{acme}/snapshot", params={"as_of": BEFORE.isoformat()}
    )
    assert early.status_code == 200 and early.json()["documents"] == []
    snap = (
        await api.get(f"/disclosures/issuers/{acme}/snapshot", params={"as_of": AFTER.isoformat()})
    ).json()
    assert Decimal(snap["observations"][0]["value"]) == Decimal(1_000_000_000)
    assert snap["claims"][0]["review_state"] == "machine_suggested"  # never shown as verified
    assert snap["documents"][0]["date_notes"] == {}

    prov = await api.get(
        f"/disclosures/provenance/observation/{o1}", params={"as_of": AFTER.isoformat()}
    )
    assert prov.status_code == 200 and prov.json()["evidence"]["quote"].strip() == "1,000"
    assert (
        await api.get(
            f"/disclosures/provenance/observation/{o1}", params={"as_of": BEFORE.isoformat()}
        )
    ).status_code == 404

    queue = (await api.get("/disclosures/review")).json()
    assert [q["id"] for q in queue] == [review_id]
    short = await api.post(
        f"/disclosures/review/{review_id}/decision", json={"decision": "approved", "reason": "x"}
    )
    assert short.status_code == 422
    done = await api.post(
        f"/disclosures/review/{review_id}/decision",
        json={"decision": "approved", "reason": "Customer A 23% in the notes table"},
    )
    assert done.status_code == 200 and done.json()["decided_by"] == f"owner:{OWNER}"
    again = await api.post(
        f"/disclosures/review/{review_id}/decision",
        json={"decision": "rejected", "reason": "second thoughts"},
    )
    assert again.status_code == 409

    sources = (await api.get("/disclosures/sources")).json()
    assert len(sources) >= 13
    assert {s["access_state"] for s in sources} == {"untested"}  # seeded, none tested
    assert not any(s["enabled"] for s in sources)

    listed = (await api.get("/disclosures/issuers")).json()
    assert [(i["issuer_id"], i["document_count"]) for i in listed] == [(acme, 1)]
