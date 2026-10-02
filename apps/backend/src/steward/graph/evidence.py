"""Store a fetched document and a cited span as `source_evidence`, returning its id.

Deal extraction, passage review and patent attribution all need a durable evidence row so that
a confirmed edge, event or ownership claim can be traced to the exact text it rests on. The
evidence's *class* later comes from the document's `regulator` (see `graph.keys`).
"""

from __future__ import annotations

import datetime as dt
import hashlib

import asyncpg


async def record_evidence(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    regulator: str,
    external_id: str,
    url: str,
    document_type: str | None,
    content: str | None,
    quote: str | None,
    filed_at: dt.datetime | None = None,
    published_at: dt.datetime | None = None,
    known_at: dt.datetime | None = None,
    span: tuple[int, int] | None = None,
    page: str | None = None,
) -> int:
    """Insert (or reuse) document, version and evidence rows; idempotent per content and span."""
    digest = hashlib.sha256(
        (content if content is not None else f"{url}\n{quote}").encode()
    ).hexdigest()
    document_id: int = await pool.fetchval(
        """insert into public.source_document(regulator,external_id,stable_url,document_type)
           values($1,$2,$3,$4)
           on conflict(regulator,external_id) do update set stable_url=excluded.stable_url
           returning id""",
        regulator,
        external_id,
        url,
        document_type,
    )
    version_id: int = await pool.fetchval(
        """insert into public.source_document_version
           (document_id,content_hash,filed_at,published_at,known_at)
           values($1,$2,$3,$4,coalesce($5,now()))
           on conflict(document_id,content_hash) do update set content_hash=excluded.content_hash
           returning id""",
        document_id,
        digest,
        filed_at,
        published_at,
        known_at,
    )
    existing = await pool.fetchval(
        """select id from public.source_evidence
           where document_version_id=$1 and coalesce(quote,'')=coalesce($2,'')
             and coalesce(span_start,-1)=coalesce($3,-1) limit 1""",
        version_id,
        quote,
        span[0] if span else None,
    )
    if existing is not None:
        return int(existing)
    evidence_id: int = await pool.fetchval(
        """insert into public.source_evidence
           (document_version_id,page,quote,span_start,span_end)
           values($1,$2,$3,$4,$5) returning id""",
        version_id,
        page,
        quote,
        span[0] if span else None,
        span[1] if span else None,
    )
    return evidence_id
