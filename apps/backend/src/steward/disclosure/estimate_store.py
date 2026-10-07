"""P7.2: store an extracted consensus file as an estimate snapshot and its value lines.

A snapshot is known when we first saw the file, never at its stated publication date: a file
fetched today cannot have been known last month. The same bytes are stored once. A file that
re-issues an earlier publication date supersedes that earlier snapshot (a corrected file); a file
for a new date is a new snapshot, so revisions between dates stay visible. A line the extractor
could not read cleanly is counted and left out; it is never stored half-read.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import httpx

from steward.disclosure import estimate_extract as ex
from steward.disclosure.fetch import FetchFailure

type Pool = asyncpg.Pool | asyncpg.Connection


@dataclass(frozen=True)
class StoreOutcome:
    snapshot_id: int | None
    stored_values: int
    skipped_values: int
    duplicate: bool
    superseded_id: int | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)


async def store_extraction(
    pool: Pool,
    *,
    issuer_id: int,
    source_id: int,
    extraction: ex.Extraction,
    fetched_at: dt.datetime,
    document_version_id: int | None = None,
) -> StoreOutcome:
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=dt.UTC)
    seen = await pool.fetchval(
        """select id from public.public_estimate_snapshot
            where source_id = $1 and content_hash = $2 limit 1""",
        source_id,
        extraction.content_hash,
    )
    if seen is not None:
        return StoreOutcome(int(seen), 0, 0, True)
    storable = [v for v in extraction.values if v.storable and v.fiscal_period]
    skipped = [v for v in extraction.values if v not in storable]
    reasons = tuple(sorted({i for v in skipped for i in v.issues} | set(extraction.issues)))
    if not storable:
        return StoreOutcome(None, 0, len(skipped), False, reasons=reasons)
    published = (
        dt.datetime.combine(extraction.published_on, dt.time(), tzinfo=dt.UTC)
        if extraction.published_on
        else fetched_at
    )
    # A file cannot be known before it was seen, whatever date it states.
    known = max(fetched_at, published)
    earlier = await pool.fetchrow(
        """select id from public.public_estimate_snapshot
            where source_id = $1 and series = 'published_analyst_consensus'
              and published_at = $2 and corrected_by_id is null
            order by id desc limit 1""",
        source_id,
        published,
    )
    async with _transaction(pool) as conn:
        snap = int(
            await conn.fetchval(
                """insert into public.public_estimate_snapshot
                     (issuer_id, source_id, document_version_id, content_hash, series, published_at,
                      known_at, panel_description, analyst_count, panel_method_version,
                      supersedes_id)
                   values ($1,$2,$3,$4,'published_analyst_consensus',$5,$6,$7,$8,$9,$10)
                   returning id""",
                issuer_id,
                source_id,
                document_version_id,
                extraction.content_hash,
                published,
                known,
                extraction.panel_description,
                extraction.panel_size,
                extraction.panel_method,
                None if earlier is None else int(earlier["id"]),
            )
        )
        for v in storable:
            await conn.execute(
                """insert into public.estimate_value
                     (snapshot_id, metric, segment, segment_taxonomy, non_additive, restated_prior,
                      fiscal_period, fiscal_calendar, period_start, period_end, currency, unit,
                      scale, basis, basis_definition, mean, median, high, low, analyst_count,
                      original_string)
                   values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
                           $21)""",
                snap,
                v.metric,
                v.segment,
                v.segment_taxonomy,
                v.non_additive,
                v.restated_prior,
                v.fiscal_period,
                v.fiscal_calendar,
                v.period_start,
                v.period_end,
                v.currency,
                v.unit,
                v.scale,
                v.basis,
                v.basis_definition,
                v.mean,
                v.median,
                v.high,
                v.low,
                v.analyst_count,
                v.original_string,
            )
        if earlier is not None:
            await conn.execute(
                "update public.public_estimate_snapshot set corrected_by_id = $2 where id = $1",
                int(earlier["id"]),
                snap,
            )
    return StoreOutcome(
        snap,
        len(storable),
        len(skipped),
        False,
        None if earlier is None else int(earlier["id"]),
        reasons,
    )


async def collect_consensus(
    pool: Pool,
    fetch: Callable[[str], Awaitable[Any]],
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Fetch every enabled consensus source once and store what changed. HTML is read; a PDF or
    spreadsheet source is counted as unsupported rather than skipped silently. A failed fetch is
    reported against its source and never stops the others."""
    when = now or dt.datetime.now(dt.UTC)
    totals: dict[str, Any] = {
        "sources": 0,
        "stored": 0,
        "duplicate": 0,
        "nothing_readable": 0,
        "unsupported_format": 0,
        "failed": 0,
        "errors": [],
    }
    rows = await pool.fetch(
        """select id, issuer_id, source_key, url, format from public.consensus_source
            where enabled and access_state = 'available' order by id"""
    )
    for r in rows:
        totals["sources"] += 1
        if r["format"] != "html":
            totals["unsupported_format"] += 1
            continue
        try:
            got = await fetch(r["url"])
        except (FetchFailure, httpx.HTTPError) as exc:
            totals["failed"] += 1
            totals["errors"].append(f"{r['source_key']}: {type(exc).__name__}")
            continue
        if not getattr(got, "modified", True) or not got.content:
            totals["duplicate"] += 1
            continue
        outcome = await store_extraction(
            pool,
            issuer_id=int(r["issuer_id"]),
            source_id=int(r["id"]),
            extraction=ex.extract_html(got.content),
            fetched_at=when,
        )
        if outcome.duplicate:
            totals["duplicate"] += 1
        elif outcome.snapshot_id is None:
            totals["nothing_readable"] += 1
            totals["errors"].append(f"{r['source_key']}: {'; '.join(outcome.reasons[:2])}")
        else:
            totals["stored"] += 1
    return totals


class _transaction:
    """A transaction on a connection, or on one connection taken from a pool."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool
        self._conn: asyncpg.Connection | None = None
        self._tx: asyncpg.connection.transaction.Transaction | None = None
        self._acquired = False

    async def __aenter__(self) -> asyncpg.Connection:
        if isinstance(self._pool, asyncpg.Pool):
            self._conn = await self._pool.acquire()
            self._acquired = True
        else:
            self._conn = self._pool
        self._tx = self._conn.transaction()
        await self._tx.start()
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        assert self._tx is not None and self._conn is not None
        if exc_type is None:
            await self._tx.commit()
        else:
            await self._tx.rollback()
        if self._acquired and isinstance(self._pool, asyncpg.Pool):
            await self._pool.release(self._conn)
