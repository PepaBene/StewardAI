"""SEC filing ingestion job (plan P1.3, P2.2, P2.4): discovered filings -> stored, parsed documents.

Discovery (`sec_discovery`) only lists filings. This job fetches each one, stores the bytes as a
document version, parses it, extracts its note tables and mirrors the stored statement facts it
can match into the evidence tables. Rules:

  * the source must be enabled with a recorded passing access test (`check_enable`), or the whole
    job refuses to run before it fetches anything;
  * identical bytes already stored for the document are never stored or parsed again;
  * one failed filing never stops the rest; every filing ends as ingested, unchanged or failed,
    with the reason;
  * a document that cannot be parsed is stored quarantined with a review, not dropped;
  * a failure after the bytes were stored (notes, fact mirroring) is a named gap on the result,
    not a silent skip. The identical bytes are skipped on the next run, so such a gap needs an
    explicit re-run of the note job or of `mirror_fact`;
  * the fetcher is injected (`DisclosureFetcher.fetch` bound to a source key in production), and
    an archive is written only when a store is passed in.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import asyncpg

from steward.disclosure.fact_evidence import mirror_document_facts, mirror_fact
from steward.disclosure.fetch import FetchFailure, FetchOutcome, extension_for
from steward.disclosure.financials import concept_choice, metric_for
from steward.disclosure.ingest import DocumentRecord, ingest_document_version, quarantine_review
from steward.disclosure.note_job import extract_notes_for_document
from steward.disclosure.parse import ParsedDocument, ParseError, parse_html
from steward.disclosure.registry import SourceNotEnabled, check_enable
from steward.disclosure.sec_discovery import DiscoveredFiling
from steward.storage import EvidenceStore, evidence_path

type Pool = asyncpg.Pool | asyncpg.Connection
type Fetcher = Callable[[str], Awaitable[FetchOutcome]]

SOURCE_KEY = "sec.archive"
REGULATOR = "SEC"


class FilingStatus(StrEnum):
    INGESTED = "ingested"
    UNCHANGED = "unchanged"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FilingResult:
    accession: str
    status: FilingStatus
    reason: str | None = None
    version_id: int | None = None
    quarantined: bool = False
    facts_mirrored: int = 0
    note_rows: int = 0
    gaps: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IngestJobResult:
    results: tuple[FilingResult, ...]

    def count(self, status: FilingStatus) -> int:
        return sum(1 for r in self.results if r.status is status)

    @property
    def ingested(self) -> int:
        return self.count(FilingStatus.INGESTED)

    @property
    def unchanged(self) -> int:
        return self.count(FilingStatus.UNCHANGED)

    @property
    def failed(self) -> int:
        return self.count(FilingStatus.FAILED)


async def require_source(pool: Pool, source_key: str) -> None:
    """Refuse unless the source row exists, is enabled and has a passing access test."""
    row = await pool.fetchrow(
        "select enabled, access_state from public.disclosure_source where source_key=$1",
        source_key,
    )
    if row is None:
        raise SourceNotEnabled(f"unknown disclosure source {source_key!r}")
    if not row["enabled"]:
        raise SourceNotEnabled(f"{source_key} is disabled; enable it after its access test")
    check_enable(row["access_state"], True)


def record_for(filing: DiscoveredFiling, issuer_id: int, source_key: str) -> DocumentRecord:
    """The document row for a discovered filing; its dates are the discovery's, unmodified."""
    return DocumentRecord(
        regulator=REGULATOR,
        external_id=filing.accession,
        stable_url=filing.primary_url,
        form=filing.form,
        known_at=filing.known_at,
        issuer_id=issuer_id,
        source_key=source_key,
        accepted_at=filing.accepted_at,
        filed_at=dt.datetime.combine(filing.filed_at, dt.time.min, dt.UTC),
        date_notes=dict(filing.date_notes),
        amends_external_id=filing.amends_accession,
    )


CURRENT_REPORT_FORMS = {"8-K"}


async def index_filing_items(
    pool: Pool, issuer_id: int, filings: Sequence[DiscoveredFiling]
) -> int:
    """Remember the items each 8-K of an issuer declares (one row per accession; a row is never
    changed). The earnings release (Item 2.02) is read from here. Returns the rows added."""
    added = 0
    for f in filings:
        if f.base_form not in CURRENT_REPORT_FORMS or not f.items:
            continue
        status = await pool.execute(
            """insert into public.sec_filing_item
                 (accession, issuer_id, form, items, report_date, filed_at, known_at)
               values ($1,$2,$3,$4,$5,$6,$7) on conflict (accession) do nothing""",
            f.accession,
            issuer_id,
            f.form,
            f.items,
            f.report_date,
            f.filed_at,
            f.known_at,
        )
        added += status.endswith(" 1")
    return added


async def _already_stored(pool: Pool, accession: str, digest: str) -> bool:
    found = await pool.fetchval(
        """select 1 from public.source_document d
             join public.source_document_version v on v.document_id = d.id
            where d.regulator=$1 and d.external_id=$2 and v.content_hash=$3""",
        REGULATOR,
        accession,
        digest,
    )
    return found is not None


def _fact_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """A statement fact row with its jsonb column decoded (asyncpg returns it as text)."""
    out = dict(row)
    if isinstance(out.get("dimensions"), str):
        out["dimensions"] = json.loads(out["dimensions"])
    return out


async def mirror_stored_facts(
    pool: Pool, issuer_id: int, accession: str, version_id: int, doc: ParsedDocument
) -> int:
    """Mirror the stored statement facts of this filing that match a parsed iXBRL fact exactly.
    A fact with no unique match stays unmirrored: an ambiguous span is not evidence."""
    rows = await pool.fetch(
        """select id, issuer_id, concept, period_start, period_end, value, unit, currency, basis,
                  dimensions, known_at, null::text as value_text
             from public.financial_statement_fact
            where issuer_id=$1 and accession=$2 and statement <> 'note' and value is not null
            order by id""",
        issuer_id,
        accession,
    )
    facts = [_fact_row(r) for r in rows]

    def slot_of(row: Mapping[str, Any]) -> tuple[Any, ...] | None:
        metric = metric_for(str(row["concept"]))
        if metric is None:
            return None
        dims = json.dumps(row.get("dimensions") or {}, sort_keys=True)
        return (metric.key, row["period_start"], row["period_end"], row["unit"], dims)

    # One metric from several concepts in the same period: the observation key has no concept,
    # so the first row would win. Keep the metric's preferred concept, or none for lease parts.
    choice = concept_choice(
        (str(f["concept"]), slot) for f in facts if (slot := slot_of(f)) is not None
    )
    mirrored = 0
    for row in facts:
        if choice.get(slot_of(row), str(row["concept"])) != str(row["concept"]):
            continue
        if await mirror_fact(pool, version_id, doc, row) is not None:
            mirrored += 1
    known_at = await pool.fetchval(
        "select known_at from public.source_document_version where id=$1", version_id
    )
    # The filing's own facts too, so older filings (never copied into the statement table) get
    # evidence as well. Observations already written above are reused, never duplicated.
    return mirrored + await mirror_document_facts(pool, version_id, issuer_id, known_at, doc)


async def _fetch(fetch: Fetcher, url: str) -> FetchOutcome | str:
    """The outcome, or the reason it could not be had. A disabled source ends the whole job."""
    try:
        return await fetch(url)
    except SourceNotEnabled:
        raise
    except FetchFailure as exc:
        return f"{exc.state}: {exc}"
    except Exception as exc:  # one filing's failure must not stop the others
        return f"fetch failed ({type(exc).__name__})"


async def ingest_filing(
    pool: Pool,
    filing: DiscoveredFiling,
    *,
    issuer_id: int,
    ticker: str | None,
    fetch: Fetcher,
    store: EvidenceStore | None = None,
    source_key: str = SOURCE_KEY,
) -> FilingResult:
    acc = filing.accession
    outcome = await _fetch(fetch, filing.primary_url)
    if isinstance(outcome, str):
        return FilingResult(acc, FilingStatus.FAILED, outcome)
    if not outcome.modified:
        return FilingResult(acc, FilingStatus.UNCHANGED, "the source reported no change")
    content = outcome.content
    if not content:
        return FilingResult(acc, FilingStatus.FAILED, "the source returned an empty body")
    digest = outcome.sha256 or hashlib.sha256(content).hexdigest()
    if await _already_stored(pool, acc, digest):
        return FilingResult(acc, FilingStatus.UNCHANGED, "identical bytes already stored")
    archive = outcome.archive_path
    if archive is None and store is not None:
        archive = evidence_path(digest, extension_for(outcome.content_type, filing.primary_url))
        await store.put(archive, content, outcome.content_type or "application/octet-stream")
    doc: ParsedDocument | None = None
    parse_error: str | None = None
    try:
        doc = parse_html(content)
    except ParseError as exc:
        parse_error = str(exc)
    stored = await ingest_document_version(
        pool,
        record_for(filing, issuer_id, source_key),
        content,
        archive_path=archive,
        parser_version=doc.parser_version if doc else None,
        quarantine_reason=parse_error,
        now=outcome.retrieved_at,
    )
    if not stored.created:
        return FilingResult(acc, FilingStatus.UNCHANGED, "identical bytes already stored")
    if doc is None:
        await quarantine_review(
            pool, stored.version_id, "parse_failure", {"accession": acc, "error": parse_error}
        )
        return FilingResult(
            acc, FilingStatus.INGESTED, parse_error, stored.version_id, quarantined=True
        )
    gaps: list[str] = []
    note_rows = mirrored = 0
    try:
        notes = await extract_notes_for_document(
            pool,
            issuer_id=issuer_id,
            ticker=ticker,
            accession=acc,
            form=filing.form,
            known_at=filing.known_at,
            source_url=filing.primary_url,
            doc=doc,
        )
        note_rows = notes.stored
        gaps.extend(notes.skipped)
    except Exception as exc:
        gaps.append(f"notes not extracted ({type(exc).__name__})")
    try:
        mirrored = await mirror_stored_facts(pool, issuer_id, acc, stored.version_id, doc)
    except Exception as exc:
        gaps.append(f"facts not mirrored ({type(exc).__name__})")
    return FilingResult(
        acc,
        FilingStatus.INGESTED,
        None,
        stored.version_id,
        facts_mirrored=mirrored,
        note_rows=note_rows,
        gaps=tuple(gaps),
    )


async def ingest_filings(
    pool: Pool,
    *,
    issuer_id: int,
    ticker: str | None,
    filings: Sequence[DiscoveredFiling],
    fetch: Fetcher,
    store: EvidenceStore | None = None,
    source_key: str = SOURCE_KEY,
) -> IngestJobResult:
    """Ingest the filings oldest first (so an amendment finds the filing it amends)."""
    await require_source(pool, source_key)
    results: list[FilingResult] = []
    for filing in sorted(filings, key=lambda f: (f.known_at, f.accession)):
        try:
            results.append(
                await ingest_filing(
                    pool,
                    filing,
                    issuer_id=issuer_id,
                    ticker=ticker,
                    fetch=fetch,
                    store=store,
                    source_key=source_key,
                )
            )
        except SourceNotEnabled:
            raise
        except Exception as exc:  # database or storage failure on this filing only
            results.append(
                FilingResult(filing.accession, FilingStatus.FAILED, f"{type(exc).__name__}")
            )
    return IngestJobResult(tuple(results))
