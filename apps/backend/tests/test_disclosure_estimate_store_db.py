"""P7.2: an extracted consensus file becomes a snapshot once, is known when it was seen, and a
re-issued file supersedes the earlier one. Skipped without STEWARD_TEST_DATABASE_URL."""

import datetime as dt
from pathlib import Path

import asyncpg
import pytest

from steward.disclosure import estimate_extract as ex
from steward.disclosure import estimate_store
from tests.test_disclosure_estimates_db import setup

pytestmark = pytest.mark.db

FIXTURE = Path(__file__).parent / "fixtures" / "disclosure" / "consensus_acme_2026q3.htm"
SEEN = dt.datetime(2026, 7, 20, 9, tzinfo=dt.UTC)


def extraction(data: bytes | None = None) -> ex.Extraction:
    return ex.extract_html(data or FIXTURE.read_bytes())


async def test_a_file_is_stored_once_and_known_when_seen(backend_pool: asyncpg.Pool) -> None:
    issuer, source = await setup(backend_pool)
    first = await estimate_store.store_extraction(
        backend_pool, issuer_id=issuer, source_id=source, extraction=extraction(), fetched_at=SEEN
    )
    assert first.snapshot_id is not None and first.stored_values > 0 and not first.duplicate
    again = await estimate_store.store_extraction(
        backend_pool,
        issuer_id=issuer,
        source_id=source,
        extraction=extraction(),
        fetched_at=SEEN + dt.timedelta(days=1),
    )
    assert again.duplicate and again.snapshot_id == first.snapshot_id
    row = await backend_pool.fetchrow(
        "select known_at, published_at from public.public_estimate_snapshot where id = $1",
        first.snapshot_id,
    )
    assert row["known_at"] >= SEEN  # never earlier than the day we saw it
    assert row["known_at"] >= row["published_at"]
    n = await backend_pool.fetchval(
        "select count(*) from public.estimate_value where snapshot_id = $1", first.snapshot_id
    )
    assert n == first.stored_values


async def test_a_reissued_file_for_the_same_date_supersedes_the_first(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer, source = await setup(backend_pool)
    first = await estimate_store.store_extraction(
        backend_pool, issuer_id=issuer, source_id=source, extraction=extraction(), fetched_at=SEEN
    )
    corrected = FIXTURE.read_bytes().replace(b"1,250.5", b"1,251.5", 1)
    second = await estimate_store.store_extraction(
        backend_pool,
        issuer_id=issuer,
        source_id=source,
        extraction=extraction(corrected),
        fetched_at=SEEN + dt.timedelta(days=2),
    )
    assert second.superseded_id == first.snapshot_id and not second.duplicate
    link = await backend_pool.fetchrow(
        "select supersedes_id from public.public_estimate_snapshot where id = $1",
        second.snapshot_id,
    )
    assert link["supersedes_id"] == first.snapshot_id
    marked = await backend_pool.fetchval(
        "select corrected_by_id from public.public_estimate_snapshot where id = $1",
        first.snapshot_id,
    )
    assert marked == second.snapshot_id


class Got:
    def __init__(self, content: bytes, modified: bool = True) -> None:
        self.content = content
        self.modified = modified


async def test_the_collector_reads_enabled_sources_once_and_reports_failures(
    backend_pool: asyncpg.Pool,
) -> None:
    from steward.disclosure.fetch import FetchFailure

    _issuer, source = await setup(backend_pool)
    # not enabled: skipped entirely
    calls: list[str] = []

    async def fetch(url: str) -> Got:
        calls.append(url)
        return Got(FIXTURE.read_bytes())

    none = await estimate_store.collect_consensus(backend_pool, fetch, SEEN)
    assert none["sources"] == 0 and calls == []
    await backend_pool.execute(
        "update public.consensus_source set access_state='available', enabled=true where id=$1",
        source,
    )
    first = await estimate_store.collect_consensus(backend_pool, fetch, SEEN)
    assert first["stored"] == 1 and first["failed"] == 0
    second = await estimate_store.collect_consensus(backend_pool, fetch, SEEN)
    assert second["stored"] == 0 and second["duplicate"] == 1  # same bytes: no new version

    async def down(url: str) -> Got:
        raise FetchFailure("retrieval_failed", "down")

    failed = await estimate_store.collect_consensus(backend_pool, down, SEEN)
    assert failed["failed"] == 1 and failed["errors"] == ["acme.ir: FetchFailure"]


async def test_a_page_with_no_readable_lines_stores_nothing_and_says_why(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer, source = await setup(backend_pool)
    out = await estimate_store.store_extraction(
        backend_pool,
        issuer_id=issuer,
        source_id=source,
        extraction=extraction(b"<html><body><p>Coming soon</p></body></html>"),
        fetched_at=SEEN,
    )
    assert out.snapshot_id is None and out.stored_values == 0 and out.reasons
    assert await backend_pool.fetchval("select count(*) from public.public_estimate_snapshot") == 0
