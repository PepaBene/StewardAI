"""Filing ingestion job with an injected fetcher: no network, no database."""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

import pytest

from steward.disclosure import ingest_job
from steward.disclosure.fetch import FetchFailure, FetchOutcome
from steward.disclosure.ingest import DocumentRecord, IngestResult
from steward.disclosure.ingest_job import FilingStatus, ingest_filings
from steward.disclosure.note_job import NoteJobResult
from steward.disclosure.registry import SourceNotEnabled
from steward.disclosure.sec_discovery import DiscoveredFiling, discover_filings

HTML = b"<html><body><h1>Item 7</h1><p>Revenue grew.</p></body></html>"


def submissions() -> dict[str, Any]:
    forms = ["10-K", "10-Q", "10-K/A"]
    return {
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0000000001-26-000001",
                    "0000000001-26-000002",
                    "0000000001-26-000003",
                ],
                "form": forms,
                "filingDate": ["2026-02-20", "2026-05-01", "2026-06-01"],
                "acceptanceDateTime": [
                    "2026-02-20T21:00:00.000Z",
                    "2026-05-01T20:15:00.000Z",
                    "2026-06-01T20:00:00.000Z",
                ],
                "reportDate": ["2025-12-31", "2026-03-31", "2025-12-31"],
                "primaryDocument": ["a.htm", "b.htm", "c.htm"],
                "items": ["", "", ""],
            }
        }
    }


FILINGS = discover_filings("1", submissions())


class Pool:
    def __init__(
        self, source: dict[str, Any] | None = None, stored: set[str] | None = None
    ) -> None:
        self.source = (
            source if source is not None else {"enabled": True, "access_state": "available"}
        )
        self.stored = stored or set()  # accession numbers whose bytes are already stored

    async def fetchrow(self, _sql: str, _key: str) -> dict[str, Any] | None:
        return self.source

    async def fetchval(
        self, _sql: str, _regulator: str, accession: str, _digest: str
    ) -> int | None:
        return 1 if accession in self.stored else None

    async def fetch(self, _sql: str, *_args: Any) -> list[dict[str, Any]]:
        return []


class Store:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        self.puts.append((path, data, content_type))

    async def get(self, path: str) -> bytes:
        raise AssertionError

    async def signed_url(self, path: str, expires_in: int) -> str:
        raise AssertionError


def outcome(content: bytes = HTML, **kw: Any) -> FetchOutcome:
    return FetchOutcome(
        "https://x",
        200,
        kw.pop("modified", True),
        content,
        hashlib.sha256(content).hexdigest(),
        "text/html",
        **kw,
    )


class Recorder:
    """Replaces the database-facing steps and records what the job passed to them."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.ingested: list[tuple[DocumentRecord, dict[str, Any]]] = []
        self.reviews: list[tuple[int, str]] = []
        self.notes: list[str] = []
        self.note_error: Exception | None = None

        async def ingest(
            _pool: Any, record: DocumentRecord, _content: bytes, **kw: Any
        ) -> IngestResult:
            self.ingested.append((record, kw))
            return IngestResult(
                100 + len(self.ingested),
                200 + len(self.ingested),
                True,
                bool(kw["quarantine_reason"]),
            )

        async def review(
            _pool: Any, version_id: int, reason: str, _candidate: dict[str, Any]
        ) -> None:
            self.reviews.append((version_id, reason))

        async def notes(_pool: Any, **kw: Any) -> NoteJobResult:
            if self.note_error:
                raise self.note_error
            self.notes.append(kw["accession"])
            return NoteJobResult(1, 2, ("no table in one block",))

        async def mirror(_pool: Any, _issuer: int, _acc: str, _version: int, _doc: Any) -> int:
            return 3

        monkeypatch.setattr(ingest_job, "ingest_document_version", ingest)
        monkeypatch.setattr(ingest_job, "quarantine_review", review)
        monkeypatch.setattr(ingest_job, "extract_notes_for_document", notes)
        monkeypatch.setattr(ingest_job, "mirror_stored_facts", mirror)


async def run(pool: Pool, fetch: Any, store: Store | None = None, filings: Any = FILINGS) -> Any:
    return await ingest_filings(
        pool,  # type: ignore[arg-type]
        issuer_id=7,
        ticker="ACME",
        filings=filings,
        fetch=fetch,
        store=store,
    )


async def test_the_job_refuses_when_the_source_has_no_passing_access_test() -> None:
    calls: list[str] = []

    async def fetch(url: str) -> FetchOutcome:
        calls.append(url)
        return outcome()

    for source in (
        None,
        {"enabled": False, "access_state": "available"},
        {"enabled": True, "access_state": "untested"},
        {"enabled": True, "access_state": "retrieval_failed"},
    ):
        pool = Pool(source)
        pool.source = source  # type: ignore[assignment]
        with pytest.raises(SourceNotEnabled):
            await run(pool, fetch)
    assert calls == []


async def test_each_filing_is_stored_with_its_own_dates_parsed_and_mirrored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = Recorder(monkeypatch)
    urls: list[str] = []

    async def fetch(url: str) -> FetchOutcome:
        urls.append(url)
        return outcome()

    result = await run(Pool(), fetch)
    assert (result.ingested, result.unchanged, result.failed) == (3, 0, 0)
    assert urls == [f.primary_url for f in FILINGS]  # oldest first
    first, amendment = rec.ingested[0][0], rec.ingested[2][0]
    assert first.regulator == "SEC" and first.external_id == "0000000001-26-000001"
    assert first.known_at == dt.datetime(2026, 2, 20, 21, 0, tzinfo=dt.UTC)
    assert first.accepted_at == first.known_at and first.source_key == "sec.archive"
    assert first.issuer_id == 7 and first.amends_external_id is None
    assert "published_at" in first.date_notes
    assert amendment.amends_external_id == "0000000001-26-000001"
    assert rec.ingested[0][1]["archive_path"] is None  # no store passed in
    assert rec.ingested[0][1]["parser_version"]
    assert rec.notes == [f.accession for f in FILINGS]
    one = result.results[0]
    assert (one.facts_mirrored, one.note_rows, one.gaps) == (3, 2, ("no table in one block",))


async def test_bytes_are_archived_only_when_a_store_is_passed_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = Recorder(monkeypatch)
    store = Store()

    async def fetch(_url: str) -> FetchOutcome:
        return outcome()

    await run(Pool(), fetch, store, FILINGS[:1])
    digest = hashlib.sha256(HTML).hexdigest()
    assert store.puts == [(f"{digest[:2]}/{digest}.html", HTML, "text/html")]
    assert rec.ingested[0][1]["archive_path"] == f"{digest[:2]}/{digest}.html"


async def test_identical_bytes_already_stored_are_skipped_without_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = Recorder(monkeypatch)
    store = Store()

    async def fetch(_url: str) -> FetchOutcome:
        return outcome()

    pool = Pool(stored={"0000000001-26-000002"})
    result = await run(pool, fetch, store)
    assert [r.status for r in result.results] == [
        FilingStatus.INGESTED,
        FilingStatus.UNCHANGED,
        FilingStatus.INGESTED,
    ]
    assert "0000000001-26-000002" not in rec.notes
    assert len(store.puts) == 2 and len(rec.ingested) == 2


async def test_a_304_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    Recorder(monkeypatch)

    async def fetch(_url: str) -> FetchOutcome:
        return FetchOutcome("https://x", 304, False)

    result = await run(Pool(), fetch)
    assert result.unchanged == 3


async def test_one_failed_filing_does_not_stop_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch)

    async def fetch(url: str) -> FetchOutcome:
        if url.endswith("a.htm"):
            raise FetchFailure("not_covered", "no such document", status_code=404)
        if url.endswith("b.htm"):
            raise ConnectionError("boom")
        return outcome()

    result = await run(Pool(), fetch)
    assert [r.status for r in result.results] == [
        FilingStatus.FAILED,
        FilingStatus.FAILED,
        FilingStatus.INGESTED,
    ]
    assert result.results[0].reason == "not_covered: no such document"
    assert result.results[1].reason == "fetch failed (ConnectionError)"
    assert [r.external_id for r, _ in rec.ingested] == ["0000000001-26-000003"]


async def test_an_unreadable_document_is_stored_quarantined_with_a_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = Recorder(monkeypatch)

    async def fetch(_url: str) -> FetchOutcome:
        return outcome(b"<html><body>   </body></html>")

    result = await run(Pool(), fetch, filings=FILINGS[:1])
    [one] = result.results
    assert one.status is FilingStatus.INGESTED and one.quarantined
    assert one.reason == "document has no readable text"
    assert rec.ingested[0][1]["quarantine_reason"] == one.reason
    assert rec.reviews == [(one.version_id, "parse_failure")]
    assert rec.notes == []  # nothing was extracted from an unreadable document


async def test_a_failure_after_storing_is_a_named_gap_not_a_lost_filing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = Recorder(monkeypatch)
    rec.note_error = RuntimeError("db")

    async def fetch(_url: str) -> FetchOutcome:
        return outcome()

    [one] = (await run(Pool(), fetch, filings=FILINGS[:1])).results
    assert one.status is FilingStatus.INGESTED and one.version_id is not None
    assert one.gaps == ("notes not extracted (RuntimeError)",) and one.facts_mirrored == 3


async def test_a_source_disabled_mid_run_ends_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    Recorder(monkeypatch)

    async def fetch(_url: str) -> FetchOutcome:
        raise SourceNotEnabled("sec.archive is disabled")

    with pytest.raises(SourceNotEnabled):
        await run(Pool(), fetch)


async def test_an_empty_body_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    Recorder(monkeypatch)

    async def fetch(_url: str) -> FetchOutcome:
        return outcome(b"")

    result = await run(Pool(), fetch, filings=FILINGS[:1])
    assert result.results[0].reason == "the source returned an empty body"


def test_discovery_fixture_is_what_the_job_expects() -> None:
    assert all(isinstance(f, DiscoveredFiling) for f in FILINGS) and len(FILINGS) == 3
