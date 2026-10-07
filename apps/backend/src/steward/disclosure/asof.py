"""As-of reads over the disclosure corpus (plan P1.6).

The rule everywhere: a row is visible at `as_of` only if `known_at <= as_of`, and a restatement
shows the version known *at that moment*, not today's corrected value. A row is hidden once a
successor that supersedes it was itself known by `as_of`; the successor is never visible earlier.
`event_effective_at` is returned but never used to admit a row, so a backdated disclosure cannot
leak into a historical replay.

Every read returns the envelope the owner-only APIs promise: `source`, `published_at`,
`known_at`, `as_of`, `method_version`, `coverage` and a document link.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import asyncpg

type Pool = asyncpg.Pool | asyncpg.Connection

AUTHORITATIVE_STATES = ("verified",)


def _aware(value: dt.datetime | None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.UTC)
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def _envelope(row: dict[str, Any], as_of: dt.datetime, method: str | None) -> dict[str, Any]:
    return {
        "source": f"{row.get('regulator')} {row.get('form') or ''}".strip(),
        "published_at": row.get("published_at") or row.get("accepted_at") or row.get("filed_at"),
        "known_at": row["known_at"],
        "as_of": as_of,
        "method_version": method,
        "document_url": row.get("stable_url"),
    }


async def coverage_for(
    pool: Pool, issuer_id: int, dataset: str | None = None
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """select dataset, source_key, jurisdiction, metric, period, state, reason, checked_at
             from public.disclosure_coverage
            where issuer_id=$1 and ($2::text is null or dataset=$2)
            order by dataset, metric, period""",
        issuer_id,
        dataset,
    )
    return [dict(r) for r in rows]


async def observations_as_of(
    pool: Pool,
    issuer_id: int,
    as_of: dt.datetime | None = None,
    *,
    metric: str | None = None,
    verified_only: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cutoff = _aware(as_of)
    rows = await pool.fetch(
        """select * from (
             select distinct on (o.metric, coalesce(o.segment,''), o.period_end,
                                 md5(o.dimensions::text))
                    o.id, o.metric, o.segment, o.period_start, o.period_end, o.is_instant,
                    o.value_state, o.value, o.text_value, o.original_string, o.unit, o.scale,
                    o.currency, o.basis, o.dimensions, o.parser_version, o.known_at,
                    o.event_effective_at, o.review_state, o.supersedes_id, o.evidence_id,
                    o.document_version_id, v.accepted_at, v.filed_at, v.published_at,
                    d.regulator, d.external_id, d.stable_url, d.form,
                    e.quote, e.span_start, e.span_end, e.section
               from public.disclosure_observation o
               join public.source_document_version v on v.id = o.document_version_id
               join public.source_document d on d.id = v.document_id
               join public.source_evidence e on e.id = o.evidence_id
              where o.issuer_id = $1 and o.known_at <= $2
                and o.review_state not in ('rejected','superseded')
                and ($3::text is null or o.metric = $3)
                and (not $4 or o.review_state = 'verified')
                and not exists (select 1 from public.disclosure_observation s
                                 where s.supersedes_id = o.id and s.known_at <= $2
                                   and s.review_state <> 'rejected')
              order by o.metric, coalesce(o.segment,''), o.period_end, md5(o.dimensions::text),
                       o.known_at desc, o.source_precedence, o.id desc) latest
           order by metric, period_end desc, id
           limit $5 offset $6""",
        issuer_id,
        cutoff,
        metric,
        verified_only,
        limit,
        offset,
    )
    out = []
    for row in rows:
        item = dict(row)
        item["envelope"] = _envelope(item, cutoff, item["parser_version"])
        out.append(item)
    return out


async def claims_as_of(
    pool: Pool,
    issuer_id: int,
    as_of: dt.datetime | None = None,
    *,
    claim_type: str | None = None,
    verified_only: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Claims about (or involving) the issuer known by `as_of`. Machine suggestions are returned
    with their review state so a caller can never show them as verified fact."""
    cutoff = _aware(as_of)
    rows = await pool.fetch(
        """select c.id, c.claim_type, c.subject_issuer_id, c.subject_label, c.object_issuer_id,
                  c.object_label, c.object_anonymous, c.direction, c.polarity, c.payload,
                  c.review_state, c.confidence, c.method_version, c.valid_from, c.valid_to,
                  c.event_effective_at, c.known_at, c.supersedes_claim_id, c.evidence_id,
                  c.document_version_id, v.accepted_at, v.filed_at, v.published_at,
                  d.regulator, d.external_id, d.stable_url, d.form,
                  e.quote, e.span_start, e.span_end, e.section
             from public.extracted_claim c
             join public.source_document_version v on v.id = c.document_version_id
             join public.source_document d on d.id = v.document_id
             join public.source_evidence e on e.id = c.evidence_id
            where (c.subject_issuer_id = $1 or c.object_issuer_id = $1) and c.known_at <= $2
              and c.review_state not in ('rejected','superseded')
              and c.polarity <> 'superseded'
              and ($3::text is null or c.claim_type = $3)
              and (not $4 or c.review_state = 'verified')
              and not exists (select 1 from public.extracted_claim s
                               where s.supersedes_claim_id = c.id and s.known_at <= $2
                                 and s.review_state <> 'rejected')
            order by c.valid_from nulls last, c.id
            limit $5 offset $6""",
        issuer_id,
        cutoff,
        claim_type,
        verified_only,
        limit,
        offset,
    )
    out = []
    for row in rows:
        item = dict(row)
        item["envelope"] = _envelope(item, cutoff, item["method_version"])
        out.append(item)
    return out


async def filing_timeline(
    pool: Pool,
    issuer_id: int,
    as_of: dt.datetime | None = None,
    *,
    form: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Every document version of the issuer known by `as_of`, newest first. `superseded_by_id`
    and `amends_version_id` are shown only if the linked version was itself known by then, and
    `is_current` marks the version in force at the cutoff."""
    cutoff = _aware(as_of)
    rows = await pool.fetch(
        """select d.id as document_id, d.regulator, d.external_id, d.stable_url, d.form,
                  d.fiscal_period, d.language, d.source_key, v.id as version_id, v.content_hash,
                  v.filed_at, v.accepted_at, v.published_at, v.first_seen_at, v.retrieved_at,
                  v.known_at, v.date_notes, v.quarantined, v.quarantine_reason, v.byte_length,
                  v.parser_version,
                  case when a.known_at <= $2 then v.amends_version_id end as amends_version_id,
                  case when s.known_at <= $2 then v.superseded_by_id end as superseded_by_id,
                  (s.id is null or s.known_at > $2) as is_current
             from public.source_document d
             join public.source_document_version v on v.document_id = d.id
             left join public.source_document_version s on s.id = v.superseded_by_id
             left join public.source_document_version a on a.id = v.amends_version_id
            where d.issuer_id = $1 and v.known_at <= $2 and ($3::text is null or d.form = $3)
            order by coalesce(v.accepted_at, v.filed_at, v.published_at, v.known_at) desc, v.id desc
            limit $4 offset $5""",
        issuer_id,
        cutoff,
        form,
        limit,
        offset,
    )
    return [dict(r) for r in rows]


async def snapshot(pool: Pool, issuer_id: int, as_of: dt.datetime | None = None) -> dict[str, Any]:
    """Everything known about one issuer at one cutoff (the P1 exit check)."""
    cutoff = _aware(as_of)
    return {
        "issuer_id": issuer_id,
        "as_of": cutoff,
        "documents": await filing_timeline(pool, issuer_id, cutoff),
        "observations": await observations_as_of(pool, issuer_id, cutoff),
        "claims": await claims_as_of(pool, issuer_id, cutoff),
        "coverage": await coverage_for(pool, issuer_id),
    }


async def provenance(
    pool: Pool, kind: str, row_id: int, as_of: dt.datetime | None = None
) -> dict[str, Any] | None:
    """The source version, evidence span and version chain behind one observation or claim as
    known at `as_of`. None if the row does not exist or was not yet known."""
    cutoff = _aware(as_of)
    table = {"observation": "disclosure_observation", "claim": "extracted_claim"}[kind]
    method_col = "parser_version" if kind == "observation" else "method_version"
    row = await pool.fetchrow(
        f"""select r.id, r.known_at, r.review_state, r.{method_col} as method_version,
                   r.document_version_id, r.evidence_id, r.event_effective_at,
                   v.content_hash, v.accepted_at, v.filed_at, v.published_at, v.archive_path,
                   d.regulator, d.external_id, d.stable_url, d.form, d.fiscal_period,
                   e.quote, e.span_start, e.span_end, e.section, e.table_name, e.row_ref,
                   e.extraction_method, e.parser_version as span_parser_version, e.confidence
              from public.{table} r
              join public.source_document_version v on v.id = r.document_version_id
              join public.source_document d on d.id = v.document_id
              join public.source_evidence e on e.id = r.evidence_id
             where r.id = $1 and r.known_at <= $2""",  # noqa: S608 - table is from a fixed map
        row_id,
        cutoff,
    )
    if row is None:
        return None
    item = dict(row)
    versions = await pool.fetch(
        """select v.id, v.content_hash, v.accepted_at, v.filed_at, v.known_at,
                  case when s.known_at <= $3 then v.superseded_by_id end as superseded_by_id,
                  (s.id is null or s.known_at > $3) as is_current
             from public.source_document d
             join public.source_document_version v on v.document_id = d.id
             left join public.source_document_version s on s.id = v.superseded_by_id
            where d.regulator = $1 and d.external_id = $2 and v.known_at <= $3
            order by coalesce(v.accepted_at, v.filed_at, v.known_at), v.id""",
        item["regulator"],
        item["external_id"],
        cutoff,
    )
    item["version_chain"] = [dict(v) for v in versions]
    item["envelope"] = _envelope(item, cutoff, item["method_version"])
    return item
