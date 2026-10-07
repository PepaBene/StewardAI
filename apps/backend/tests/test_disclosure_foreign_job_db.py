"""P2.5 / P2.6 / P6.2: ESEF and EDINET documents are stored once with the adapters' dates, and the
deal collector walks submissions, skips non-deal filings and survives a failed fetch. Skipped
without STEWARD_TEST_DATABASE_URL."""

import datetime as dt
import json
from types import SimpleNamespace

import asyncpg
import pytest

from steward.disclosure import deal_job, foreign_job
from steward.disclosure.fetch import FetchFailure
from steward.disclosure.registry import seed_sources
from tests.test_disclosure_deals import PRESS
from tests.test_disclosure_esef_discovery import LEI_A, entry
from tests.test_disclosure_estimates_db import setup
from tests.test_disclosure_jurisdiction_adapters import EDINET

pytestmark = pytest.mark.db


def got(body: bytes) -> SimpleNamespace:
    return SimpleNamespace(content=body, modified=True, archive_path=None, retrieved_at=None)


async def lei_issuer(pool: asyncpg.Pool) -> int:
    await seed_sources(pool)
    issuer, _ = await setup(pool)
    await pool.execute("update public.issuer set lei = $2 where id = $1", issuer, LEI_A)
    return issuer


async def test_esef_reports_are_stored_once_and_known_when_processed(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer = await lei_issuer(backend_pool)
    index = json.dumps({"data": [entry("f1"), entry("f2", error_count=2)]}).encode()
    fetched: list[str] = []

    async def fetch(url: str) -> SimpleNamespace:
        fetched.append(url)
        return got(index if "/entities/" in url else b'{"facts": {}}')

    rows = [{"id": issuer, "lei": LEI_A}]
    first = await foreign_job.collect_esef(backend_pool, rows, fetch, fetch)
    assert first.stored == 1 and first.skipped == {"processing_errors": 1}
    doc = await backend_pool.fetchrow(
        """select d.issuer_id, v.known_at from public.source_document d
             join public.source_document_version v on v.document_id = d.id
            where d.regulator = 'ESEF' and d.external_id = 'f1'"""
    )
    assert doc["issuer_id"] == issuer
    assert doc["known_at"] == dt.datetime(2026, 3, 10, 9, 20, 1, tzinfo=dt.UTC)
    before = len(fetched)
    again = await foreign_job.collect_esef(backend_pool, rows, fetch, fetch)
    assert again.stored == 0 and again.unchanged == 1
    assert len(fetched) == before + 1  # the index only; the stored report is not fetched again


async def test_an_esef_index_failure_is_counted_and_stops_nothing(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer = await lei_issuer(backend_pool)

    async def down(url: str) -> SimpleNamespace:
        raise FetchFailure("retrieval_failed", "down")

    out = await foreign_job.collect_esef(backend_pool, [{"id": issuer, "lei": LEI_A}], down, down)
    assert out.failed == 1 and out.errors == [f"issuer {issuer}: FetchFailure"]


async def test_edinet_rows_are_stored_with_the_filing_time_and_matched_issuer(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer = await lei_issuer(backend_pool)
    codes: dict[str, int | None] = {"E00001": issuer}
    out = await foreign_job.collect_edinet_day(backend_pool, EDINET, codes)
    assert out.stored == 2  # the annual report and the large-holding report naming E00001
    rows = await backend_pool.fetch(
        """select d.external_id, d.issuer_id, v.known_at, v.filed_at
             from public.source_document d
             join public.source_document_version v on v.document_id = d.id
            where d.regulator = 'EDINET' order by d.external_id"""
    )
    assert [r["external_id"] for r in rows] == ["S100ABCD", "S100EFGH"]
    assert all(r["issuer_id"] == issuer for r in rows)
    assert all(r["known_at"] >= r["filed_at"] for r in rows)  # never before the filing
    again = await foreign_job.collect_edinet_day(backend_pool, EDINET, codes)
    assert again.stored == 0 and again.unchanged == 2


def submissions() -> bytes:
    accepted = [f"2026-10-0{d}T16:00:00.000Z" for d in (5, 6, 7, 8)]
    recent = {
        "form": ["8-K", "8-K", "10-K", "8-K"],
        "accessionNumber": [f"0000011111-26-00000{n}" for n in (1, 2, 3, 4)],
        "filingDate": [f"2026-10-0{d}" for d in (5, 6, 7, 8)],
        "acceptanceDateTime": accepted,
        "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm"],
        "items": ["1.01,9.01", "2.02,9.01", "", "1.01"],
        "reportDate": ["", "", "", ""],
    }
    return json.dumps({"filings": {"recent": recent}}).encode()


async def test_the_deal_collector_reads_deal_filings_only_and_survives_a_failed_fetch(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer = await lei_issuer(backend_pool)
    seen: list[str] = []

    async def fetch_json(url: str) -> SimpleNamespace:
        return got(submissions())

    async def fetch(url: str) -> SimpleNamespace:
        seen.append(url)
        if url.endswith("d.htm"):
            raise FetchFailure("retrieval_failed", "down")
        return got(f"<html><body><p>{PRESS}</p></body></html>".encode())

    out = await deal_job.collect_deals(
        backend_pool,
        [{"id": issuer, "cik": "0000011111", "ticker": "ACME"}],
        fetch_json,
        fetch,
        submissions_url="https://data.sec.gov/submissions/CIK{cik}.json",
        since=dt.date(2026, 9, 1),
    )
    # the earnings-only 8-K and the 10-K are never fetched
    assert [u.rsplit("/", 1)[-1] for u in seen] == ["a.htm", "d.htm"]
    assert out.filings == 2 and out.stored == 1 and out.failed == 1
    assert await backend_pool.fetchval("select count(*) from public.deal_event") == 1
