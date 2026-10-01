"""Version and attach evidence for public documents consumed by existing pipelines."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, time

import asyncpg


async def link_public_document(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    regulator: str,
    external_id: str,
    stable_url: str,
    document_type: str,
    content: bytes,
    known_at: datetime,
    filed_at: datetime | date | None = None,
    accepted_at: datetime | date | None = None,
    published_at: datetime | date | None = None,
    content_hash: str | None = None,
    raw_location: str | None = None,
    amends_version_id: int | None = None,
    domain_table: str | None = None,
    record_key: int | str | None = None,
    quote: str | None = None,
) -> int:
    """Upsert a document version, evidence excerpt and optional domain-record link.

    Callers should pass the fetched source bytes. For sources where only a structured
    row is retained (for example a news headline), pass a canonical serialization and
    a short quote so the hash precisely describes the retained evidence.
    """
    content_hash = content_hash or hashlib.sha256(content).hexdigest()

    def timestamp(value: datetime | date | None) -> datetime | None:
        if isinstance(value, date) and not isinstance(value, datetime):
            return datetime.combine(value, time.min, UTC)
        return value

    filed_at = timestamp(filed_at)
    accepted_at = timestamp(accepted_at)
    published_at = timestamp(published_at)
    document_id = await pool.fetchval(
        """insert into public.source_document(regulator,external_id,stable_url,document_type)
           values($1,$2,$3,$4) on conflict(regulator,external_id) do update
             set stable_url=excluded.stable_url,document_type=excluded.document_type
           returning id""",
        regulator,
        external_id,
        stable_url,
        document_type,
    )
    version_id: int = await pool.fetchval(
        """insert into public.source_document_version
             (document_id,content_hash,filed_at,accepted_at,published_at,retrieved_at,known_at,
              raw_location,amends_version_id)
           values($1,$2,$3,$4,$5,$6,$6,$7,$8)
           on conflict(document_id,content_hash) do update set
             retrieved_at=excluded.retrieved_at,
             amends_version_id=coalesce(source_document_version.amends_version_id,
                                        excluded.amends_version_id)
           returning id""",
        document_id,
        content_hash,
        filed_at,
        accepted_at,
        published_at,
        known_at,
        raw_location or stable_url,
        amends_version_id,
    )
    await mark_superseded(pool, document_id)
    if quote:
        # One atomic statement: the unique index source_evidence_version_quote makes a
        # concurrent duplicate resolve to the same row instead of racing a select-then-insert.
        evidence_id = await pool.fetchval(
            """insert into public.source_evidence(document_version_id,quote)
               values($1,$2)
               on conflict (document_version_id, md5(quote))
                 where quote is not null and observation_id is null and page is null
                   and table_name is null and span_start is null and span_end is null
               do update set quote=excluded.quote
               returning id""",
            version_id,
            quote,
        )
    else:
        evidence_id = None
    if domain_table is not None and record_key is not None:
        await pool.execute(
            """insert into public.source_record_evidence
                 (domain_table,record_key,document_version_id,evidence_id,known_at)
               values($1,$2,$3,$4,$5) on conflict(domain_table,record_key,document_version_id)
               do nothing""",
            domain_table,
            str(record_key),
            version_id,
            evidence_id,
            known_at,
        )
    return version_id


# Versions are ordered by the moment they became the document's content: the regulator's own
# acceptance / filing / publication time, else when they were first seen.
async def mark_superseded(pool: asyncpg.Pool | asyncpg.Connection, document_id: int) -> None:
    """Point every version of the document at the next one (`superseded_by_id`), newest last.

    Recomputed from the whole chain, so a late-arriving older version slots in correctly
    instead of being left as the latest. The newest version has no successor."""
    await pool.execute(
        """with ordered as (
              select id,
                     lead(id) over (
                       order by coalesce(accepted_at, filed_at, published_at, known_at),
                                known_at, id) as next_id
                from public.source_document_version where document_id=$1)
            update public.source_document_version v set superseded_by_id=o.next_id
              from ordered o
             where v.id=o.id and v.superseded_by_id is distinct from o.next_id""",
        document_id,
    )


async def document_versions_as_of(
    pool: asyncpg.Pool | asyncpg.Connection,
    regulator: str,
    external_id: str,
    known_at: datetime | None = None,
) -> list[dict[str, object]]:
    """Every version of a document that was known by `known_at`, oldest first.

    `superseded_by_id` is shown only when the superseding version was itself known by the
    cutoff: a replay never learns from the future that a version was later replaced.
    `is_current` marks the version in force at the cutoff."""
    cutoff = known_at or datetime.now(UTC)
    rows = await pool.fetch(
        """select v.id, v.document_id, v.content_hash, v.filed_at, v.accepted_at,
                   v.published_at, v.retrieved_at, v.known_at, v.amends_version_id,
                   case when s.known_at <= $3 then v.superseded_by_id end as superseded_by_id,
                   (s.id is null or s.known_at > $3) as is_current, v.raw_location
              from public.source_document d
              join public.source_document_version v on v.document_id=d.id
              left join public.source_document_version s on s.id=v.superseded_by_id
             where d.regulator=$1 and d.external_id=$2 and v.known_at <= $3
             order by coalesce(v.accepted_at, v.filed_at, v.published_at, v.known_at),
                      v.known_at, v.id""",
        regulator,
        external_id,
        cutoff,
    )
    return [dict(row) for row in rows]


async def document_version_as_of(
    pool: asyncpg.Pool | asyncpg.Connection,
    regulator: str,
    external_id: str,
    known_at: datetime | None = None,
) -> dict[str, object] | None:
    """The version of the document in force at `known_at`, or None before it was known."""
    versions = await document_versions_as_of(pool, regulator, external_id, known_at)
    current = [v for v in versions if v["is_current"]]
    return current[-1] if current else None


async def record_evidence_as_of(
    pool: asyncpg.Pool | asyncpg.Connection,
    domain_table: str,
    record_key: int | str,
    known_at: datetime | None = None,
) -> list[dict[str, object]]:
    """The evidence attached to a domain record as it stood at `known_at`: only links and
    document versions recorded by then, with the quote (if any) and the version's identity."""
    cutoff = known_at or datetime.now(UTC)
    rows = await pool.fetch(
        """select r.id, r.domain_table, r.record_key, r.known_at as linked_at,
                  v.id as document_version_id, v.content_hash, v.known_at as version_known_at,
                  v.accepted_at, v.filed_at, v.published_at, d.regulator, d.external_id,
                  d.stable_url, e.id as evidence_id, e.quote, e.page, e.table_name
             from public.source_record_evidence r
             join public.source_document_version v on v.id=r.document_version_id
             join public.source_document d on d.id=v.document_id
             left join public.source_evidence e on e.id=r.evidence_id
            where r.domain_table=$1 and r.record_key=$2 and r.known_at <= $3
              and v.known_at <= $3
            order by r.known_at, r.id""",
        domain_table,
        str(record_key),
        cutoff,
    )
    return [dict(row) for row in rows]
