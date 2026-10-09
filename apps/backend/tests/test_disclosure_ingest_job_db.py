"""`mirror_fact` and the filing ingestion job against a real database."""

from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from steward.disclosure.fact_evidence import mirror_fact
from steward.disclosure.fetch import FetchOutcome
from steward.disclosure.ingest import DocumentRecord, ingest_document_version
from steward.disclosure.ingest_job import FilingStatus, ingest_filings
from steward.disclosure.parse import parse_html
from steward.disclosure.registry import (
    SourceAccess,
    SourceNotEnabled,
    record_access_test,
    seed_sources,
    set_enabled,
)
from steward.disclosure.sec_discovery import discover_filings

pytestmark = pytest.mark.db

UTC = dt.UTC
KNOWN = dt.datetime(2026, 2, 20, 21, 0, tzinfo=UTC)
ACCESSION = "0000000001-26-000001"
HEADER = (
    "<ix:header><ix:resources>"
    '<xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="s">1</xbrli:identifier>'
    "</xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate>"
    "<xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>"
    '<xbrli:unit id="u1"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
    "</ix:resources></ix:header>"
)


def filing_html(*revenues: str) -> bytes:
    facts = "".join(
        f'<p>Revenue <ix:nonFraction name="us-gaap:Revenues" contextRef="c1" unitRef="u1" '
        f'decimals="-6" scale="6">{r}</ix:nonFraction> million.</p>'
        for r in revenues
    )
    return f"<html><body>{HEADER}<h1>Item 8</h1>{facts}</body></html>".encode()


async def make_issuer(pool: asyncpg.Pool) -> int:
    value = await pool.fetchval(
        "insert into public.issuer(legal_name, cik, country) values('Acme Corp','0000000001','US')"
        " returning id"
    )
    return int(value)


async def make_fact(
    pool: asyncpg.Pool, issuer_id: int, value: str = "1234000000"
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """insert into public.financial_statement_fact
             (issuer_id, ticker, accession, form, concept, statement, period_start, period_end,
              basis, value, unit, currency, source, source_url, known_at)
           values($1,'ACME',$2,'10-K','us-gaap:Revenues','income','2025-01-01','2025-12-31',
                  'reported',$3,'USD','USD','sec-xbrl','https://sec.example/a.htm',$4)
           returning id, issuer_id, concept, period_start, period_end, value, unit, currency,
                     basis, dimensions, known_at, null::text as value_text""",
        issuer_id,
        ACCESSION,
        Decimal(value),
        KNOWN,
    )
    assert row is not None
    out = dict(row)
    out["dimensions"] = {}  # jsonb comes back as text; the job decodes it the same way
    return out


async def stored_version(pool: asyncpg.Pool, issuer_id: int, content: bytes) -> int:
    result = await ingest_document_version(
        pool,
        DocumentRecord("SEC", ACCESSION, "https://sec.example/a.htm", "10-K", KNOWN, issuer_id),
        content,
    )
    return result.version_id


async def test_mirror_fact_writes_the_span_and_the_observation_with_the_legacy_link(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer_id = await make_issuer(backend_pool)
    content = filing_html("1,234")
    version_id = await stored_version(backend_pool, issuer_id, content)
    doc = parse_html(content)
    fact = await make_fact(backend_pool, issuer_id)

    observation_id = await mirror_fact(backend_pool, version_id, doc, fact)
    assert observation_id is not None
    evidence = await backend_pool.fetchrow(
        "select e.* from public.source_evidence e"
        " join public.disclosure_observation o on o.evidence_id = e.id where o.id=$1",
        observation_id,
    )
    assert evidence is not None
    assert evidence["document_version_id"] == version_id
    assert evidence["table_name"] == "financial_statement_fact"
    assert evidence["row_ref"] == str(fact["id"])
    assert evidence["extraction_method"] == "ixbrl"
    assert doc.text[evidence["span_start"] : evidence["span_end"]] == "1,234"
    obs = await backend_pool.fetchrow(
        "select * from public.disclosure_observation where id=$1", observation_id
    )
    assert obs is not None
    assert obs["value"] == Decimal("1234000000") and obs["value_state"] == "reported"
    assert obs["original_string"] == "1,234" and obs["issuer_id"] == issuer_id
    assert obs["known_at"] == KNOWN and obs["document_version_id"] == version_id
    assert obs["period_end"] == dt.date(2025, 12, 31) and obs["unit"] == "USD"

    # the same fact again adds nothing: one span, one observation
    assert await mirror_fact(backend_pool, version_id, doc, fact) is None
    assert await backend_pool.fetchval("select count(*) from public.disclosure_observation") == 1
    assert await backend_pool.fetchval("select count(*) from public.source_evidence") == 1


async def test_mirror_fact_stores_nothing_when_the_fact_does_not_match_exactly(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer_id = await make_issuer(backend_pool)
    content = filing_html("1,234")
    version_id = await stored_version(backend_pool, issuer_id, content)
    doc = parse_html(content)

    wrong_value = await make_fact(backend_pool, issuer_id, "999")
    assert await mirror_fact(backend_pool, version_id, doc, wrong_value) is None
    wrong_period = {**await make_fact(backend_pool, issuer_id), "period_end": dt.date(2024, 12, 31)}
    assert await mirror_fact(backend_pool, version_id, doc, wrong_period) is None
    assert await backend_pool.fetchval("select count(*) from public.disclosure_observation") == 0
    assert await backend_pool.fetchval("select count(*) from public.source_evidence") == 0


async def test_mirror_fact_refuses_an_ambiguous_span(backend_pool: asyncpg.Pool) -> None:
    issuer_id = await make_issuer(backend_pool)
    content = filing_html("1,234", "1,234")  # the same fact twice: which span is the evidence?
    version_id = await stored_version(backend_pool, issuer_id, content)
    fact = await make_fact(backend_pool, issuer_id)
    assert await mirror_fact(backend_pool, version_id, parse_html(content), fact) is None
    assert await backend_pool.fetchval("select count(*) from public.source_evidence") == 0


def fetcher(content: bytes) -> Any:
    async def fetch(url: str) -> FetchOutcome:
        return FetchOutcome(
            url, 200, True, content, hashlib.sha256(content).hexdigest(), "text/html"
        )

    return fetch


def discovered() -> list[Any]:
    return discover_filings(
        "1",
        {
            "filings": {
                "recent": {
                    "accessionNumber": [ACCESSION],
                    "form": ["10-K"],
                    "filingDate": ["2026-02-20"],
                    "acceptanceDateTime": ["2026-02-20T21:00:00.000Z"],
                    "reportDate": ["2025-12-31"],
                    "primaryDocument": ["a.htm"],
                    "items": [""],
                }
            }
        },
    )


async def test_the_job_refuses_until_the_source_has_a_recorded_access_test(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_sources(backend_pool)  # seeded: disabled and untested
    issuer_id = await make_issuer(backend_pool)
    with pytest.raises(SourceNotEnabled):
        await ingest_filings(
            backend_pool,
            issuer_id=issuer_id,
            ticker="ACME",
            filings=discovered(),
            fetch=fetcher(filing_html("1,234")),
        )
    assert await backend_pool.fetchval("select count(*) from public.source_document") == 0


async def test_the_job_stores_parses_mirrors_and_skips_identical_bytes_on_a_rerun(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_sources(backend_pool)
    await record_access_test(backend_pool, "sec.archive", SourceAccess.AVAILABLE, "tested")
    await set_enabled(backend_pool, "sec.archive", True)
    issuer_id = await make_issuer(backend_pool)
    await make_fact(backend_pool, issuer_id)
    content = filing_html("1,234")

    first = await ingest_filings(
        backend_pool,
        issuer_id=issuer_id,
        ticker="ACME",
        filings=discovered(),
        fetch=fetcher(content),
    )
    [one] = first.results
    assert one.status is FilingStatus.INGESTED and one.facts_mirrored == 1
    version = await backend_pool.fetchrow(
        "select v.*, d.regulator, d.external_id, d.issuer_id, d.source_key"
        " from public.source_document_version v join public.source_document d on d.id=v.document_id"
    )
    assert version is not None and version["id"] == one.version_id
    assert (version["regulator"], version["external_id"]) == ("SEC", ACCESSION)
    assert version["known_at"] == KNOWN and version["accepted_at"] == KNOWN
    assert version["issuer_id"] == issuer_id and version["source_key"] == "sec.archive"
    assert version["content_hash"] == hashlib.sha256(content).hexdigest()
    assert not version["quarantined"] and version["parser_version"]
    assert await backend_pool.fetchval("select count(*) from public.disclosure_observation") == 1

    again = await ingest_filings(
        backend_pool,
        issuer_id=issuer_id,
        ticker="ACME",
        filings=discovered(),
        fetch=fetcher(content),
    )
    assert again.results[0].status is FilingStatus.UNCHANGED
    assert await backend_pool.fetchval("select count(*) from public.source_document_version") == 1
    assert await backend_pool.fetchval("select count(*) from public.disclosure_observation") == 1


async def test_an_unreadable_filing_is_stored_quarantined_and_the_next_one_still_runs(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_sources(backend_pool)
    await record_access_test(backend_pool, "sec.archive", SourceAccess.AVAILABLE, "tested")
    await set_enabled(backend_pool, "sec.archive", True)
    issuer_id = await make_issuer(backend_pool)
    filings = discover_filings(
        "1",
        {
            "filings": {
                "recent": {
                    "accessionNumber": [ACCESSION, "0000000001-26-000002"],
                    "form": ["10-K", "10-Q"],
                    "filingDate": ["2026-02-20", "2026-05-01"],
                    "acceptanceDateTime": ["2026-02-20T21:00:00.000Z", "2026-05-01T20:00:00.000Z"],
                    "reportDate": ["2025-12-31", "2026-03-31"],
                    "primaryDocument": ["a.htm", "b.htm"],
                    "items": ["", ""],
                }
            }
        },
    )

    async def fetch(url: str) -> FetchOutcome:
        body = b"<html><body>  </body></html>" if url.endswith("a.htm") else filing_html("1,234")
        return FetchOutcome(url, 200, True, body, hashlib.sha256(body).hexdigest(), "text/html")

    result = await ingest_filings(
        backend_pool, issuer_id=issuer_id, ticker="ACME", filings=filings, fetch=fetch
    )
    bad, good = result.results
    assert bad.quarantined and bad.status is FilingStatus.INGESTED and bad.reason
    assert good.status is FilingStatus.INGESTED and not good.quarantined
    review = await backend_pool.fetchrow("select * from public.quality_review")
    assert review is not None and review["subject_id"] == bad.version_id
    assert review["reason"] == "parse_failure"
