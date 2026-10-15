"""Hybrid passage search: keyword and embedding rankings fused with Reciprocal Rank Fusion.

The keyword side is Postgres full text over the child passages. The vector side is a pgvector
index scan when the extension is installed, else a bounded scan in Python over the newest
embedded passages (fine for the corpus this app holds). Each side yields its own ranking;
a passage's score is the sum of 1 / (K + rank) over the rankings it appears in, so a passage
both sides like beats one that only one side loves, and neither side's raw scores (which are
not comparable) are ever mixed. The reader gets the child passage and its parent section.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import asyncpg

from steward.news.embeddings import Embedder, cosine

RRF_K = 60
CANDIDATES = 50  # per side, before fusion
PYTHON_SCAN = 4000  # newest embedded passages the Python vector scan compares
VECTOR_MIN_COSINE = 0.5  # a neighbour further than this adds noise, not recall
PER_DOCUMENT = 2  # passages shown from one document
MAX_LIMIT = 20
PARENT_CHARS = 3000

Mode = Literal["hybrid", "keyword", "vector"]


@dataclass(frozen=True)
class Filters:
    tickers: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    since: dt.date | None = None
    until: dt.date | None = None

    def sql(self, first_param: int) -> tuple[str, list[Any]]:
        """A where-clause fragment (starting with ' and') and its parameters, numbered from
        `first_param`."""
        parts: list[str] = []
        params: list[Any] = []
        n = first_param
        if self.tickers:
            parts.append(f" and tickers && ${n}::text[]")
            params.append(list(self.tickers))
            n += 1
        if self.kinds:
            parts.append(f" and kind = any(${n}::text[])")
            params.append(list(self.kinds))
            n += 1
        if self.since:
            parts.append(f" and as_of >= ${n}")
            params.append(self.since)
            n += 1
        if self.until:
            parts.append(f" and as_of <= ${n}")
            params.append(self.until)
        return "".join(parts), params


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    kind: str
    ref: str
    title: str
    section_path: str | None
    text: str
    context: str | None
    tickers: tuple[str, ...]
    as_of: dt.date | None
    source_url: str | None
    app_path: str | None
    tier: int | None
    score: float
    keyword_rank: int | None = None
    vector_rank: int | None = None
    cosine: float | None = None


@dataclass
class SearchResult:
    mode: str
    hits: list[Hit]
    vector_backend: str | None = None  # "pgvector", "python" or None when no vector side ran
    notes: list[str] = field(default_factory=list)


async def vector_schema(pool: asyncpg.Pool) -> str | None:
    """The quoted schema of the pgvector type when `document_chunk` has its vector column,
    else None (a catalog lookup, cheap next to the search itself)."""
    found = await pool.fetchval(
        "select format('%I', n.nspname) from pg_extension e"
        " join pg_namespace n on n.oid = e.extnamespace where e.extname = 'vector'"
        " and exists (select 1 from information_schema.columns"
        " where table_schema = 'public' and table_name = 'document_chunk'"
        " and column_name = 'embedding_vec')"
    )
    return str(found) if found else None


def rrf(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion: ids ranked best first in each list, scored by 1 / (k + rank)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


async def keyword_ranking(pool: asyncpg.Pool, query: str, filters: Filters) -> list[int]:
    """Passage ids best first by full-text rank (empty when the query has no searchable word).

    A question in plain words rarely has every word in one passage, so when the strict (all
    words) match finds nothing the terms are OR-ed and ts_rank_cd puts the passages with the
    most of them first."""
    clause, params = filters.sql(3)
    for any_word in (False, True):
        tsq = "websearch_to_tsquery('english', $1::text)"
        if any_word:
            tsq = f"to_tsquery('english', replace({tsq}::text, ' & ', ' | '))"
        rows = await pool.fetch(
            f"with q as (select {tsq} as tsq)"  # noqa: S608
            " select c.id from public.document_chunk c, q"
            f" where not c.is_parent and numnode(q.tsq) > 0 and c.search @@ q.tsq{clause}"
            " order by ts_rank_cd(c.search, q.tsq) desc, c.id desc limit $2",
            query,
            CANDIDATES,
            *params,
        )
        if rows:
            return [int(r["id"]) for r in rows]
    return []


async def vector_ranking(
    pool: asyncpg.Pool, vector: Sequence[float], model: str, filters: Filters
) -> tuple[list[tuple[int, float]], str]:
    """(passage id, cosine) best first and the backend used."""
    schema = await vector_schema(pool)
    if schema is not None and len(vector) == 768:
        literal = "[" + ",".join(f"{x:.7g}" for x in vector) + "]"
        rows = await _pgvector_rows(pool, schema, literal, model, filters)
        return [(int(r["id"]), float(r["cos"])) for r in rows], "pgvector"
    clause, params = filters.sql(4)
    rows = await pool.fetch(
        "select id, embedding from public.document_chunk where not is_parent"  # noqa: S608
        " and embedding is not null and embedding_model = $1 and embedding_dim = $2"
        f"{clause} order by as_of desc nulls last, id desc limit $3",
        model,
        len(vector),
        PYTHON_SCAN,
        *params,
    )
    scored = sorted(
        ((int(r["id"]), cosine(vector, r["embedding"])) for r in rows),
        key=lambda x: x[1],
        reverse=True,
    )
    return scored[:CANDIDATES], "python"


async def _pgvector_rows(
    pool: asyncpg.Pool, schema: str, literal: str, model: str, filters: Filters
) -> list[asyncpg.Record]:
    clause, params = filters.sql(4)
    op = f"operator({schema}.<=>)"
    cast = f"$1::text::{schema}.vector"
    rows: list[asyncpg.Record] = await pool.fetch(
        f"select id, 1 - (embedding_vec {op} {cast}) as cos from public.document_chunk"  # noqa: S608
        " where not is_parent and embedding_vec is not null and embedding_model = $3"
        f"{clause} order by embedding_vec {op} {cast} limit $2",
        literal,
        CANDIDATES,
        model,
        *params,
    )
    return rows


async def _load(pool: asyncpg.Pool, ids: Sequence[int]) -> dict[int, asyncpg.Record]:
    rows = await pool.fetch(
        "select c.id, c.kind, c.ref, c.title, c.section_path, c.text, c.tickers, c.as_of,"
        " c.source_url, c.app_path, c.tier, p.text as parent_text"
        " from public.document_chunk c left join public.document_chunk p on p.id = c.parent_id"
        " where c.id = any($1::bigint[])",
        list(ids),
    )
    return {int(r["id"]): r for r in rows}


async def search(
    pool: asyncpg.Pool,
    embedder: Embedder | None,
    query: str,
    *,
    filters: Filters = Filters(),  # noqa: B008 - frozen
    limit: int = 8,
    mode: Mode = "hybrid",
) -> SearchResult:
    """Best passages for `query`. Without an embedder (or when it fails) the result is
    keyword-only and says so in `notes`."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    notes: list[str] = []
    keyword: list[int] = []
    vector: list[tuple[int, float]] = []
    backend: str | None = None
    if mode in ("hybrid", "keyword"):
        keyword = await keyword_ranking(pool, query, filters)
    if mode in ("hybrid", "vector"):
        if embedder is None:
            notes.append("no embedder configured: keyword matches only")
        else:
            try:
                vec = (await embedder.embed([query]))[0]
                vector, backend = await vector_ranking(pool, vec, embedder.model, filters)
                vector = [(i, c) for i, c in vector if c >= VECTOR_MIN_COSINE]
            except Exception as exc:  # the embedding call failed: keep the keyword side
                notes.append(f"embedding unavailable ({type(exc).__name__}): keyword matches only")
    cos = dict(vector)
    scores = rrf([keyword, [i for i, _ in vector]])
    ranked = sorted(scores, key=lambda i: scores[i], reverse=True)
    rows = await _load(pool, ranked)
    kw_rank = {i: n for n, i in enumerate(keyword, start=1)}
    vec_rank = {i: n for n, (i, _) in enumerate(vector, start=1)}
    hits: list[Hit] = []
    per_doc: dict[tuple[str, str], int] = {}
    for chunk_id in ranked:
        row = rows.get(chunk_id)
        if row is None:
            continue
        key = (row["kind"], row["ref"])
        if per_doc.get(key, 0) >= PER_DOCUMENT:
            continue
        per_doc[key] = per_doc.get(key, 0) + 1
        hits.append(
            Hit(
                chunk_id=chunk_id,
                kind=row["kind"],
                ref=row["ref"],
                title=row["title"],
                section_path=row["section_path"],
                text=row["text"],
                context=(row["parent_text"] or "")[:PARENT_CHARS] or None,
                tickers=tuple(row["tickers"]),
                as_of=row["as_of"],
                source_url=row["source_url"],
                app_path=row["app_path"],
                tier=row["tier"],
                score=round(scores[chunk_id], 5),
                keyword_rank=kw_rank.get(chunk_id),
                vector_rank=vec_rank.get(chunk_id),
                cosine=round(cos[chunk_id], 4) if chunk_id in cos else None,
            )
        )
        if len(hits) >= limit:
            break
    return SearchResult(mode=mode, hits=hits, vector_backend=backend, notes=notes)
