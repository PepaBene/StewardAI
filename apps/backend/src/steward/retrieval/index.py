"""Writes documents into `document_chunk` and embeds the passages that lack a vector.

Two separate steps, so the cheap one never waits on the costly one: `index_documents` chunks
and stores text (keyword search works as soon as it returns), `embed_pending` adds vectors to
passages that have none, or whose vector came from another model or dimension. A document
whose text is unchanged is skipped by its hash; a changed one replaces its chunks whole.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import asyncpg

from steward.news.embeddings import Embedder, estimate_tokens
from steward.retrieval import chunking

Kind = Literal["filing", "report", "thesis", "note", "news", "disclosure", "agent_note"]
KINDS: tuple[str, ...] = ("filing", "report", "thesis", "note", "news")
# Longest text sent to the embedder for one passage (the model takes 8,192 tokens).
EMBED_CHARS = 6000


@dataclass(frozen=True)
class SourceDoc:
    kind: Kind
    ref: str
    title: str
    text: str
    tickers: tuple[str, ...] = ()
    as_of: dt.date | None = None
    source_url: str | None = None
    app_path: str | None = None
    tier: int | None = None
    # False for short single-passage records (a news item, an owner note).
    structured: bool = True

    @property
    def version(self) -> str:
        return chunking.content_hash(f"{self.title}\n{self.text}")


@dataclass
class IndexStats:
    documents: int = 0
    skipped: int = 0
    parents: int = 0
    children: int = 0


def embed_input(title: str, section_path: str | None, text: str) -> str:
    """What is embedded for a passage: where it sits, then the passage itself."""
    head = f"{title} | {section_path}" if section_path and section_path != title else title
    return f"{head}\n{text}"[:EMBED_CHARS]


async def is_indexed(pool: asyncpg.Pool | asyncpg.Connection, doc: SourceDoc) -> bool:
    return bool(
        await pool.fetchval(
            "select exists (select 1 from public.document_chunk"
            " where kind = $1 and ref = $2 and version = $3)",
            doc.kind,
            doc.ref,
            doc.version,
        )
    )


async def index_document(pool: asyncpg.Pool, doc: SourceDoc) -> tuple[int, int] | None:
    """Stores one document's chunks, replacing any older version of the same record. Returns
    (parents, children) written, or None when this exact version is already indexed."""
    if await is_indexed(pool, doc):
        return None
    chunked = chunking.chunk(doc.text, doc.title, structured=doc.structured)
    if not chunked.children:
        return 0, 0
    common = (doc.kind, doc.ref, doc.version)
    shared = (list(doc.tickers), doc.as_of, doc.source_url, doc.app_path, doc.tier)
    async with pool.acquire() as con, con.transaction():
        await con.execute(
            "delete from public.document_chunk where kind = $1 and ref = $2", doc.kind, doc.ref
        )
        parent_ids: list[int] = []
        for i, parent in enumerate(chunked.parents):
            parent_ids.append(
                await con.fetchval(
                    "insert into public.document_chunk (kind, ref, version, chunk_index,"
                    " is_parent, section_path, title, text, tickers, as_of, source_url,"
                    " app_path, tier) values ($1,$2,$3,$4,true,$5,$6,$7,$8,$9,$10,$11,$12)"
                    " returning id",
                    *common,
                    i,
                    parent.section_path,
                    doc.title,
                    parent.text,
                    *shared,
                )
            )
        await con.executemany(
            "insert into public.document_chunk (kind, ref, version, chunk_index, is_parent,"
            " parent_id, section_path, title, text, tickers, as_of, source_url, app_path, tier)"
            " values ($1,$2,$3,$4,false,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
            [
                (
                    *common,
                    i,
                    parent_ids[c.parent] if c.parent >= 0 else None,
                    c.section_path,
                    doc.title,
                    c.text,
                    *shared,
                )
                for i, c in enumerate(chunked.children)
            ],
        )
    return len(chunked.parents), len(chunked.children)


async def index_documents(pool: asyncpg.Pool, docs: Sequence[SourceDoc]) -> IndexStats:
    stats = IndexStats()
    for doc in docs:
        written = await index_document(pool, doc)
        if written is None:
            stats.skipped += 1
            continue
        stats.documents += 1
        stats.parents += written[0]
        stats.children += written[1]
    return stats


async def prune(pool: asyncpg.Pool, kind: Kind, keep_refs: Sequence[str]) -> int:
    """Removes the chunks of records of `kind` that are no longer in the source (a deleted
    report, an archived note). Returns the number of chunk rows removed."""
    tag = await pool.execute(
        "delete from public.document_chunk where kind = $1 and not (ref = any($2::text[]))",
        kind,
        list(keep_refs),
    )
    return int(tag.split()[-1])


@dataclass
class EmbedStats:
    embedded: int = 0
    tokens_estimated: int = 0


async def pending_children(
    pool: asyncpg.Pool, model: str, dimensions: int, limit: int
) -> list[asyncpg.Record]:
    """Passages with no vector, or a vector from another model or dimension, newest first."""
    rows: list[asyncpg.Record] = await pool.fetch(
        "select id, title, section_path, text from public.document_chunk"
        " where not is_parent and (embedding is null or embedding_model <> $1"
        " or embedding_dim <> $2) order by as_of desc nulls last, id desc limit $3",
        model,
        dimensions,
        limit,
    )
    return rows


async def embed_pending(
    pool: asyncpg.Pool, embedder: Embedder, limit: int, batch: int = 100
) -> EmbedStats:
    """Embeds up to `limit` passages. A failed batch raises (nothing from it is stored); the
    batches before it are kept, and the next run continues where this one stopped."""
    stats = EmbedStats()
    rows = await pending_children(pool, embedder.model, embedder.dimensions, limit)
    for start in range(0, len(rows), batch):
        part = rows[start : start + batch]
        texts = [embed_input(r["title"], r["section_path"], r["text"]) for r in part]
        vectors = await embedder.embed(texts)
        await pool.executemany(
            "update public.document_chunk set embedding = $2, embedding_model = $3,"
            " embedding_dim = $4 where id = $1",
            [
                (r["id"], v, embedder.model, embedder.dimensions)
                for r, v in zip(part, vectors, strict=True)
            ],
        )
        stats.embedded += len(part)
        stats.tokens_estimated += estimate_tokens(texts)
    return stats
