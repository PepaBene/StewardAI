"""Persistence for the disclosure corpus: versioned documents, evidence spans, observations, claims.

Extends the existing `source_document` / `source_document_version` / `source_evidence` rows
(see `steward.sources.documents.link_public_document`, which older pipelines still use). Rules:
identical bytes never create a version; a document that cannot be read is stored *quarantined*
with the reason; observations and claims are never edited in place - a parser change inserts new
rows (the identity includes the parser version) and the old ones keep their lineage.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure.parse import ParsedDocument, section_for
from steward.sources.documents import mark_superseded

IMMUTABLE_REGULATORS = frozenset({"SEC"})


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    regulator: str
    external_id: str
    stable_url: str
    form: str
    known_at: dt.datetime
    issuer_id: int | None = None
    fiscal_period: str | None = None
    language: str | None = None
    source_key: str | None = None
    accepted_at: dt.datetime | None = None
    filed_at: dt.datetime | None = None
    published_at: dt.datetime | None = None
    date_notes: dict[str, str] = field(default_factory=dict)
    amends_external_id: str | None = None


@dataclass(frozen=True, slots=True)
class IngestResult:
    document_id: int
    version_id: int
    created: bool
    quarantined: bool


async def ingest_document_version(
    pool: asyncpg.Pool | asyncpg.Connection,
    record: DocumentRecord,
    content: bytes,
    *,
    archive_path: str | None = None,
    parser_version: str | None = None,
    quarantine_reason: str | None = None,
    now: dt.datetime | None = None,
) -> IngestResult:
    """Upsert the document, then add a version unless these exact bytes are already stored."""
    digest = hashlib.sha256(content).hexdigest()
    seen = now or dt.datetime.now(dt.UTC)
    document_id: int = await pool.fetchval(
        """insert into public.source_document
             (regulator, external_id, stable_url, document_type, issuer_id, form, fiscal_period,
              language, source_key, first_seen_at)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
           on conflict(regulator, external_id) do update set
             stable_url = excluded.stable_url,
             issuer_id = coalesce(source_document.issuer_id, excluded.issuer_id),
             fiscal_period = coalesce(source_document.fiscal_period, excluded.fiscal_period),
             language = coalesce(source_document.language, excluded.language),
             source_key = coalesce(source_document.source_key, excluded.source_key),
             form = coalesce(source_document.form, excluded.form)
           returning id""",
        record.regulator,
        record.external_id,
        record.stable_url,
        record.form,
        record.issuer_id,
        record.form,
        record.fiscal_period,
        record.language,
        record.source_key,
        seen,
    )
    existing = await pool.fetchrow(
        "select id, quarantined from public.source_document_version"
        " where document_id=$1 and content_hash=$2",
        document_id,
        digest,
    )
    if existing is None and parser_version and record.regulator in IMMUTABLE_REGULATORS:
        # A filed accession never changes (a change is a new accession that amends it), so other
        # bytes on a later fetch are the archive's own noise, not a new version. A new parser
        # still reads it again, and so does any parser for a version it could not read.
        existing = await pool.fetchrow(
            "select id, quarantined from public.source_document_version"
            " where document_id=$1 and not quarantined and parser_version=$2"
            " order by id desc limit 1",
            document_id,
            parser_version,
        )
    if existing is not None:
        await pool.execute(
            "update public.source_document_version set retrieved_at=$2 where id=$1",
            existing["id"],
            seen,
        )
        return IngestResult(document_id, existing["id"], False, existing["quarantined"])
    amends: int | None = None
    if record.amends_external_id:
        amends = await pool.fetchval(
            """select v.id from public.source_document d
                 join public.source_document_version v on v.document_id = d.id
                where d.regulator=$1 and d.external_id=$2 and v.known_at <= $3
                order by v.known_at desc, v.id desc limit 1""",
            record.regulator,
            record.amends_external_id,
            record.known_at,
        )
    notes = dict(record.date_notes)
    if record.amends_external_id and amends is None:
        # The original is not stored yet: remember which one it is, so it is linked on arrival.
        notes["amends_external_id"] = record.amends_external_id
    version_id: int = await pool.fetchval(
        """insert into public.source_document_version
             (document_id, content_hash, filed_at, accepted_at, published_at, retrieved_at,
              known_at, raw_location, amends_version_id, first_seen_at, byte_length, archive_path,
              date_notes, quarantined, quarantine_reason, parser_version)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$6,$10,$11,$12::jsonb,$13,$14,$15) returning id""",
        document_id,
        digest,
        record.filed_at,
        record.accepted_at,
        record.published_at,
        seen,
        record.known_at,
        archive_path or record.stable_url,
        amends,
        len(content),
        archive_path,
        json.dumps(notes),
        quarantine_reason is not None,
        quarantine_reason,
        parser_version,
    )
    await mark_superseded(pool, document_id)
    await pool.execute(  # amendments stored before this original arrived
        """update public.source_document_version c set amends_version_id = $1
            where c.amends_version_id is null and c.known_at >= $3
              and c.date_notes->>'amends_external_id' = $2
              and exists (select 1 from public.source_document x
                           where x.id = c.document_id and x.regulator = $4)""",
        version_id,
        record.external_id,
        record.known_at,
        record.regulator,
    )
    return IngestResult(document_id, version_id, True, quarantine_reason is not None)


async def store_span(
    pool: asyncpg.Pool | asyncpg.Connection,
    version_id: int,
    doc: ParsedDocument,
    start: int,
    end: int,
    *,
    method: str,
    confidence: float | None = None,
    table_name: str | None = None,
    row_ref: str | None = None,
    max_quote: int = 600,
) -> int:
    """An inspectable evidence span: character offsets into the parser's canonical text, the
    section it sits in and a bounded excerpt. Re-storing the same span returns the same row."""
    section = section_for(doc, start)
    existing = await pool.fetchval(
        """select id from public.source_evidence
            where document_version_id=$1 and span_start=$2 and span_end=$3
              and extraction_method=$4 and parser_version=$5""",
        version_id,
        start,
        end,
        method,
        doc.parser_version,
    )
    if existing is not None:
        return int(existing)
    return int(
        await pool.fetchval(
            """insert into public.source_evidence
                 (document_version_id, table_name, span_start, span_end, quote, section, row_ref,
                  extraction_method, parser_version, confidence)
               values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) returning id""",
            version_id,
            table_name,
            start,
            end,
            doc.span(start, end)[:max_quote],
            section.title if section else None,
            row_ref,
            method,
            doc.parser_version,
            confidence,
        )
    )


async def quarantine_review(
    pool: asyncpg.Pool | asyncpg.Connection, version_id: int, reason: str, candidate: dict[str, Any]
) -> None:
    """Open (once) a review for an unreadable document or a changed source format."""
    await pool.execute(
        """insert into public.quality_review(subject_type, subject_id, reason, candidate)
           values('document_version',$1,$2,$3::jsonb)
           on conflict (subject_type, subject_id, reason) where status in ('open','deferred')
           do nothing""",
        version_id,
        "parse_failure" if reason == "parse_failure" else "format_change",
        json.dumps(candidate),
    )


async def record_observation(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    metric: str,
    issuer_id: int,
    version_id: int,
    evidence_id: int,
    known_at: dt.datetime,
    period_end: dt.date,
    unit: str,
    parser_version: str,
    value: Decimal | None = None,
    text_value: str | None = None,
    value_state: str = "reported",
    period_start: dt.date | None = None,
    is_instant: bool = False,
    original_string: str | None = None,
    scale: int = 0,
    currency: str | None = None,
    basis: str | None = None,
    dimensions: dict[str, str] | None = None,
    segment: str | None = None,
    security_id: int | None = None,
    event_effective_at: dt.datetime | None = None,
    supersedes_id: int | None = None,
    source_precedence: int = 100,
) -> int | None:
    """Insert one observation; None when this exact (version, metric, period start and end, unit,
    currency, dimensions, parser version) already exists."""
    inserted: int | None = await pool.fetchval(
        """insert into public.disclosure_observation
             (metric, issuer_id, security_id, segment, period_start, period_end, is_instant,
              value_state, value, text_value, original_string, unit, scale, currency, basis,
              dimensions, document_version_id, evidence_id, source_precedence, parser_version,
              event_effective_at, known_at, supersedes_id)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb,$17,$18,$19,$20,
                  $21,$22,
                  coalesce($23::bigint,
                     (select o.id from public.disclosure_observation o
                        join public.source_document_version av
                          on av.id = (select amends_version_id from public.source_document_version
                                       where id = $17)
                       where o.document_version_id = av.id and o.metric = $1
                         and coalesce(o.segment,'') = coalesce($4,'')
                         and coalesce(o.period_start, date '0001-01-01')
                             = coalesce($5, date '0001-01-01')
                         and o.period_end = $6 and o.unit = $12
                         and coalesce(o.currency,'') = coalesce($14,'')
                         and o.dimensions = $16::jsonb
                       order by o.id desc limit 1)))
           on conflict (document_version_id, metric, coalesce(segment,''),
                        coalesce(period_start, date '0001-01-01'), period_end, unit,
                        coalesce(currency,''), md5(dimensions::text), parser_version) do nothing
           returning id""",
        metric,
        issuer_id,
        security_id,
        segment,
        period_start,
        period_end,
        is_instant,
        value_state,
        value,
        text_value,
        original_string,
        unit,
        scale,
        currency,
        basis,
        json.dumps(dimensions or {}, sort_keys=True),
        version_id,
        evidence_id,
        source_precedence,
        parser_version,
        event_effective_at,
        known_at,
        supersedes_id,
    )
    return inserted


async def record_claim(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    claim_type: str,
    evidence_id: int,
    version_id: int,
    known_at: dt.datetime,
    method_version: str,
    subject_issuer_id: int | None = None,
    subject_label: str | None = None,
    object_issuer_id: int | None = None,
    object_label: str | None = None,
    object_anonymous: bool = False,
    direction: str | None = None,
    polarity: str = "asserted",
    payload: dict[str, Any] | None = None,
    confidence: float | None = None,
    valid_from: dt.date | None = None,
    valid_to: dt.date | None = None,
    event_effective_at: dt.datetime | None = None,
    legacy_table: str | None = None,
    legacy_key: str | None = None,
    supersedes_claim_id: int | None = None,
) -> int:
    """A machine claim always starts `machine_suggested`; only a decision verifies it."""
    row = await pool.fetchrow(
        """insert into public.extracted_claim
             (claim_type, subject_issuer_id, subject_label, object_issuer_id, object_label,
              object_anonymous, direction, polarity, payload, evidence_id, document_version_id,
              confidence, method_version, valid_from, valid_to, event_effective_at, known_at,
              legacy_table, legacy_key, supersedes_claim_id)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
           on conflict (legacy_table, legacy_key) where legacy_table is not null
             do update set legacy_key = excluded.legacy_key
           returning id""",
        claim_type,
        subject_issuer_id,
        subject_label,
        object_issuer_id,
        object_label,
        object_anonymous,
        direction,
        polarity,
        json.dumps(payload or {}),
        evidence_id,
        version_id,
        confidence,
        method_version,
        valid_from,
        valid_to,
        event_effective_at,
        known_at,
        legacy_table,
        legacy_key,
        supersedes_claim_id,
    )
    assert row is not None
    return int(row["id"])


#: After this many failed reads by one parser version a filing is left alone until the parser
#: changes; an empty read is final for that parser at once.
MAX_FAILED_ATTEMPTS = 3

ATTEMPTS_DONE_SQL = """
  exists (select 1 from public.disclosure_backfill_attempt a
           where a.version_id = {version} and a.step = '{step}' and a.parser_version = {parser}
             and (a.outcome = 'empty' or a.attempts >= {max_failed}))
"""


def attempts_done_sql(version: str, step: str, parser: str) -> str:
    """SQL true when a step has finished with a version for this parser: it gave nothing, or it
    failed `MAX_FAILED_ATTEMPTS` times. `step` is a fixed name, never input."""
    return ATTEMPTS_DONE_SQL.format(
        version=version, step=step, parser=parser, max_failed=MAX_FAILED_ATTEMPTS
    )


async def record_attempt(
    pool: asyncpg.Pool | asyncpg.Connection,
    version_id: int,
    step: str,
    parser_version: str,
    outcome: str,
    detail: str | None = None,
) -> None:
    """Remember that a step read a version and got nothing (`empty`) or failed (`failed`)."""
    await pool.execute(
        """insert into public.disclosure_backfill_attempt
             (version_id, step, parser_version, outcome, detail)
           values ($1,$2,$3,$4,$5)
           on conflict (version_id, step, parser_version) do update set
             outcome = excluded.outcome, detail = excluded.detail, attempted_at = now(),
             attempts = public.disclosure_backfill_attempt.attempts + 1""",
        version_id,
        step,
        parser_version,
        outcome,
        detail,
    )
