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
    if quote:
        evidence_id = await pool.fetchval(
            """select id from public.source_evidence
               where document_version_id=$1 and quote=$2 order by id limit 1""",
            version_id,
            quote,
        )
        if evidence_id is None:
            evidence_id = await pool.fetchval(
                """insert into public.source_evidence(document_version_id,quote)
                   values($1,$2) returning id""",
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
