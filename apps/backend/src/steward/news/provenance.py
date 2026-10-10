"""Backfill of typed provenance on news items stored before migration 20261029110000.

Those items have no `evidence_kind`, so they count as no primary evidence and a story made of
them could never reach `primary_confirmed`. The same rules that classify new items are applied
to the stored row (its source, tier and outlet host). Items whose source has since been
removed from the config are given the plain `outlet` kind only when they are not tier 1 or 3;
otherwise they are left alone, because guessing a primary kind would overstate the evidence.
"""

from collections.abc import Callable, Collection, Mapping

import asyncpg

from steward.config.news_sources import NewsSource, NewsSourcesConfig
from steward.news.evidence import evidence_kind, is_correction
from steward.news.normalize import host_of

BATCH = 500


async def backfill_provenance(
    pool: asyncpg.Pool,
    sources: Mapping[str, NewsSource],
    sources_config: NewsSourcesConfig,
    ir_domains: Collection[str],
    limit: int = BATCH,
) -> int:
    """Classify up to `limit` items that have no evidence kind; the number updated."""
    rows = await pool.fetch(
        "select id, source_id, tier, url, outlet, headline from public.news_item"
        " where evidence_kind is null order by id desc limit $1",
        limit,
    )
    key: Callable[[str | None], str] = sources_config.independence_key
    done = 0
    for r in rows:
        source = sources.get(r["source_id"])
        host = host_of(r["url"]) or r["outlet"]
        if source is None:
            # Unknown source: only an ordinary outlet can be said without it.
            if r["tier"] != 2:
                continue
            kind: str = "outlet"
        else:
            kind = evidence_kind(source, r["tier"], host, ir_domains, sources_config)
        await pool.execute(
            "update public.news_item set evidence_kind = $2, independence_key = $3,"
            " is_correction = $4 where id = $1",
            r["id"],
            kind,
            key(host) or None,
            is_correction(r["headline"]),
        )
        done += 1
    return done
