"""Public-disclosure evidence reads: filing timeline, as-of snapshot, provenance, span viewer,
source registry, coverage and the reviewer queue (plan P1.6 / P1.7). Owner-only.

Every read takes an optional `as_of`; rows known after it are never returned. A machine claim is
returned with its review state and is never presented as verified.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import AwareDatetime, BaseModel, Field

from api.auth import Owner
from api.db import IdPath, Pool
from api.routes import Storage
from steward.disclosure import asof, review
from steward.disclosure.parse import ParseError, parse_html
from steward.storage import StorageError

router = APIRouter(prefix="/disclosures", tags=["disclosures"])

AsOf = Annotated[AwareDatetime | None, Query(description="Show only what was known by this time.")]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0, le=100000)]


class Envelope(BaseModel):
    source: str
    published_at: dt.datetime | None
    known_at: dt.datetime
    as_of: dt.datetime
    method_version: str | None
    document_url: str | None


class DocumentVersionRow(BaseModel):
    document_id: int
    version_id: int
    regulator: str
    external_id: str
    stable_url: str
    form: str | None
    fiscal_period: str | None
    language: str | None
    source_key: str | None
    content_hash: str
    filed_at: dt.datetime | None
    accepted_at: dt.datetime | None
    published_at: dt.datetime | None
    first_seen_at: dt.datetime
    retrieved_at: dt.datetime
    known_at: dt.datetime
    date_notes: dict[str, str]
    quarantined: bool
    quarantine_reason: str | None
    byte_length: int | None
    parser_version: str | None
    amends_version_id: int | None
    superseded_by_id: int | None
    is_current: bool


class EvidenceSpan(BaseModel):
    evidence_id: int
    quote: str | None
    span_start: int | None
    span_end: int | None
    section: str | None


class ObservationRow(BaseModel):
    id: int
    metric: str
    segment: str | None
    period_start: dt.date | None
    period_end: dt.date
    is_instant: bool
    value_state: Literal["reported", "not_disclosed", "null"]
    value: str | None
    text_value: str | None
    original_string: str | None
    unit: str
    scale: int
    currency: str | None
    basis: str | None
    dimensions: dict[str, Any]
    review_state: Literal["machine", "under_review", "verified", "rejected", "superseded"]
    known_at: dt.datetime
    event_effective_at: dt.datetime | None
    supersedes_id: int | None
    document_version_id: int
    evidence: EvidenceSpan
    envelope: Envelope


class ClaimRow(BaseModel):
    id: int
    claim_type: str
    subject_issuer_id: int | None
    subject_label: str | None
    object_issuer_id: int | None
    object_label: str | None
    object_anonymous: bool
    direction: str | None
    polarity: Literal["asserted", "denied", "superseded"]
    payload: dict[str, Any]
    review_state: Literal["machine_suggested", "under_review", "verified", "rejected", "superseded"]
    confidence: float | None
    valid_from: dt.date | None
    valid_to: dt.date | None
    known_at: dt.datetime
    event_effective_at: dt.datetime | None
    supersedes_claim_id: int | None
    document_version_id: int
    evidence: EvidenceSpan
    envelope: Envelope


class CoverageRow(BaseModel):
    dataset: str
    source_key: str | None
    jurisdiction: str | None
    metric: str
    period: str
    state: str
    reason: str | None
    checked_at: dt.datetime


class Snapshot(BaseModel):
    issuer_id: int
    as_of: dt.datetime
    documents: list[DocumentVersionRow]
    observations: list[ObservationRow]
    claims: list[ClaimRow]
    coverage: list[CoverageRow]


class SourceRow(BaseModel):
    source_key: str
    provider: str
    endpoint: str
    jurisdiction: str
    regulator: str
    access_method: str
    access_state: str
    enabled: bool
    tested_on: dt.date | None
    access_detail: str | None
    stop_condition: str
    document_formats: list[str]
    last_checked_at: dt.datetime | None


class VersionRef(BaseModel):
    id: int
    content_hash: str
    accepted_at: dt.datetime | None
    filed_at: dt.datetime | None
    known_at: dt.datetime
    superseded_by_id: int | None
    is_current: bool


class Provenance(BaseModel):
    id: int
    kind: Literal["observation", "claim"]
    review_state: str
    document_version_id: int
    regulator: str
    external_id: str
    stable_url: str
    form: str | None
    fiscal_period: str | None
    content_hash: str
    evidence: EvidenceSpan
    table_name: str | None
    row_ref: str | None
    extraction_method: str | None
    span_parser_version: str | None
    confidence: float | None
    version_chain: list[VersionRef]
    envelope: Envelope


class SpanText(BaseModel):
    version_id: int
    parser_version: str
    stale_parser: bool
    before: str
    span: str
    after: str
    signed_url: str | None


class ReviewRow(BaseModel):
    id: int
    subject_type: str
    subject_id: int
    reason: str
    status: str
    candidate: dict[str, Any]
    assignee: str | None
    decision_reason: str | None
    decided_by: str | None
    decided_at: dt.datetime | None
    created_at: dt.datetime


class ReviewDecision(BaseModel):
    decision: Literal["approved", "rejected", "superseded", "deferred"]
    reason: str = Field(min_length=3, max_length=500)


def _span(row: dict[str, Any]) -> EvidenceSpan:
    return EvidenceSpan(
        evidence_id=row["evidence_id"],
        quote=row["quote"],
        span_start=row["span_start"],
        span_end=row["span_end"],
        section=row["section"],
    )


def _observation(row: dict[str, Any]) -> ObservationRow:
    value = row["value"]
    return ObservationRow(
        **{
            k: row[k]
            for k in (
                "id",
                "metric",
                "segment",
                "period_start",
                "period_end",
                "is_instant",
                "value_state",
                "text_value",
                "original_string",
                "unit",
                "scale",
                "currency",
                "basis",
                "review_state",
                "known_at",
                "event_effective_at",
                "supersedes_id",
                "document_version_id",
            )
        },
        value=None if value is None else format(value, "f"),
        dimensions=_json(row["dimensions"]),
        evidence=_span(row),
        envelope=Envelope(**row["envelope"]),
    )


def _json(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else json.loads(value or "{}")


def _claim(row: dict[str, Any]) -> ClaimRow:
    return ClaimRow(
        **{
            k: row[k]
            for k in (
                "id",
                "claim_type",
                "subject_issuer_id",
                "subject_label",
                "object_issuer_id",
                "object_label",
                "object_anonymous",
                "direction",
                "polarity",
                "review_state",
                "valid_from",
                "valid_to",
                "known_at",
                "event_effective_at",
                "supersedes_claim_id",
                "document_version_id",
            )
        },
        confidence=None if row["confidence"] is None else float(row["confidence"]),
        payload=_json(row["payload"]),
        evidence=_span(row),
        envelope=Envelope(**row["envelope"]),
    )


def _document(row: dict[str, Any]) -> DocumentVersionRow:
    return DocumentVersionRow(**{**row, "date_notes": _json(row["date_notes"])})


async def _snapshot_rows(pool: Pool, issuer_id: int, as_of: dt.datetime | None) -> Snapshot:
    data = await asof.snapshot(pool, issuer_id, as_of)
    return Snapshot(
        issuer_id=issuer_id,
        as_of=data["as_of"],
        documents=[_document(r) for r in data["documents"]],
        observations=[_observation(r) for r in data["observations"]],
        claims=[_claim(r) for r in data["claims"]],
        coverage=[CoverageRow(**r) for r in data["coverage"]],
    )


class IssuerRow(BaseModel):
    issuer_id: int
    legal_name: str
    cik: str | None
    document_count: int
    latest_known_at: dt.datetime | None
    open_reviews: int


@router.get("/issuers")
async def issuers(pool: Pool, _owner: Owner) -> list[IssuerRow]:
    """Issuers that have at least one disclosure document, with how many and the newest one."""
    rows = await pool.fetch(
        """select i.id as issuer_id, i.legal_name, i.cik,
                  count(distinct d.id)::int as document_count,
                  max(v.known_at) as latest_known_at,
                  (select count(*)::int from public.quality_review q
                    join public.extracted_claim c
                      on q.subject_type = 'claim' and q.subject_id = c.id
                   where q.status in ('open','deferred')
                     and c.subject_issuer_id = i.id) as open_reviews
             from public.issuer i
             join public.source_document d on d.issuer_id = i.id
             join public.source_document_version v on v.document_id = d.id
            group by i.id order by max(v.known_at) desc, i.legal_name limit 500"""
    )
    return [IssuerRow(**dict(r)) for r in rows]


@router.get("/issuers/{issuer_id}/snapshot")
async def issuer_snapshot(
    issuer_id: IdPath, pool: Pool, _owner: Owner, as_of: AsOf = None
) -> Snapshot:
    """Everything known about the issuer at `as_of` (default now): documents, observations,
    claims and coverage. Nothing known later is included."""
    return await _snapshot_rows(pool, issuer_id, as_of)


@router.get("/issuers/{issuer_id}/timeline")
async def issuer_timeline(
    issuer_id: IdPath,
    pool: Pool,
    _owner: Owner,
    as_of: AsOf = None,
    form: Annotated[str | None, Query(max_length=20)] = None,
    limit: Limit = 200,
    offset: Offset = 0,
) -> list[DocumentVersionRow]:
    rows = await asof.filing_timeline(pool, issuer_id, as_of, form=form, limit=limit, offset=offset)
    return [_document(r) for r in rows]


@router.get("/issuers/{issuer_id}/observations")
async def issuer_observations(
    issuer_id: IdPath,
    pool: Pool,
    _owner: Owner,
    as_of: AsOf = None,
    metric: Annotated[str | None, Query(max_length=120)] = None,
    verified_only: bool = False,
    limit: Limit = 200,
    offset: Offset = 0,
) -> list[ObservationRow]:
    rows = await asof.observations_as_of(
        pool,
        issuer_id,
        as_of,
        metric=metric,
        verified_only=verified_only,
        limit=limit,
        offset=offset,
    )
    return [_observation(r) for r in rows]


@router.get("/issuers/{issuer_id}/claims")
async def issuer_claims(
    issuer_id: IdPath,
    pool: Pool,
    _owner: Owner,
    as_of: AsOf = None,
    claim_type: Annotated[str | None, Query(max_length=80)] = None,
    verified_only: bool = False,
    limit: Limit = 200,
    offset: Offset = 0,
) -> list[ClaimRow]:
    rows = await asof.claims_as_of(
        pool,
        issuer_id,
        as_of,
        claim_type=claim_type,
        verified_only=verified_only,
        limit=limit,
        offset=offset,
    )
    return [_claim(r) for r in rows]


@router.get("/issuers/{issuer_id}/coverage")
async def issuer_coverage(issuer_id: IdPath, pool: Pool, _owner: Owner) -> list[CoverageRow]:
    return [CoverageRow(**r) for r in await asof.coverage_for(pool, issuer_id)]


@router.get("/provenance/{kind}/{row_id}")
async def provenance(
    kind: Literal["observation", "claim"],
    row_id: IdPath,
    pool: Pool,
    _owner: Owner,
    as_of: AsOf = None,
) -> Provenance:
    """The source version, evidence span and version chain behind one value, as known at
    `as_of`. 404 if the row does not exist or was not yet known then."""
    item = await asof.provenance(pool, kind, row_id, as_of)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No {kind} {row_id} known at that time")
    return Provenance(
        id=item["id"],
        kind=kind,
        review_state=item["review_state"],
        document_version_id=item["document_version_id"],
        regulator=item["regulator"],
        external_id=item["external_id"],
        stable_url=item["stable_url"],
        form=item["form"],
        fiscal_period=item["fiscal_period"],
        content_hash=item["content_hash"],
        evidence=_span(item),
        table_name=item["table_name"],
        row_ref=item["row_ref"],
        extraction_method=item["extraction_method"],
        span_parser_version=item["span_parser_version"],
        confidence=None if item["confidence"] is None else float(item["confidence"]),
        version_chain=[VersionRef(**v) for v in item["version_chain"]],
        envelope=Envelope(**item["envelope"]),
    )


@router.get("/versions/{version_id}/span")
async def version_span(
    version_id: IdPath,
    start: Annotated[int, Query(ge=0)],
    end: Annotated[int, Query(ge=0)],
    pool: Pool,
    storage: Storage,
    _owner: Owner,
    context: Annotated[int, Query(ge=0, le=2000)] = 400,
) -> SpanText:
    """The archived document text around a cited span. The archived bytes are re-parsed with the
    parser version that produced the offsets; `stale_parser` flags a version mismatch."""
    if end < start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "end must not precede start")
    row = await pool.fetchrow(
        "select archive_path, parser_version from public.source_document_version where id=$1",
        version_id,
    )
    if row is None or not row["archive_path"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No archived copy of that version")
    try:
        data = await storage.get(row["archive_path"])
        url = await storage.signed_url(row["archive_path"], 600)
    except StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    try:
        doc = parse_html(data)
    except ParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unreadable: {exc}") from exc
    if end > len(doc.text):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "span is outside the document")
    return SpanText(
        version_id=version_id,
        parser_version=doc.parser_version,
        stale_parser=row["parser_version"] not in (None, doc.parser_version),
        before=doc.text[max(0, start - context) : start],
        span=doc.text[start:end],
        after=doc.text[end : end + context],
        signed_url=url,
    )


@router.get("/sources")
async def sources(pool: Pool, _owner: Owner) -> list[SourceRow]:
    """The source registry: access method, tested state and the stop condition per source."""
    rows = await pool.fetch(
        """select source_key, provider, endpoint, jurisdiction, regulator, access_method,
                  access_state, enabled, tested_on, access_detail, stop_condition,
                  document_formats, last_checked_at
             from public.disclosure_source order by jurisdiction, source_key"""
    )
    return [SourceRow(**dict(r)) for r in rows]


@router.get("/review")
async def review_queue(
    pool: Pool,
    _owner: Owner,
    queue_status: Annotated[
        Literal["open", "deferred", "approved", "rejected", "superseded"], Query(alias="status")
    ] = "open",
    reason: Annotated[str | None, Query(max_length=40)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[ReviewRow]:
    rows = await review.queue(pool, status=queue_status, reason=reason, limit=limit, offset=offset)
    return [ReviewRow(**{**r, "candidate": _json(r["candidate"])}) for r in rows]


@router.post("/review/{review_id}/decision")
async def decide_review(
    review_id: IdPath, body: ReviewDecision, pool: Pool, owner: Owner
) -> ReviewRow:
    """Approve, reject, supersede or defer a review item. The reason is mandatory and the
    original candidate is preserved."""
    try:
        await review.decide(pool, review_id, body.decision, f"owner:{owner.user_id}", body.reason)
    except review.ReviewError as exc:
        code = (
            status.HTTP_404_NOT_FOUND if "does not exist" in str(exc) else status.HTTP_409_CONFLICT
        )
        raise HTTPException(code, str(exc)) from exc
    rows = await pool.fetch(
        "select id, subject_type, subject_id, reason, status, candidate, assignee,"
        " decision_reason, decided_by, decided_at, created_at"
        " from public.quality_review where id=$1",
        review_id,
    )
    row = dict(rows[0])
    return ReviewRow(**{**row, "candidate": _json(row["candidate"])})
