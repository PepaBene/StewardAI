"""Backfill of typed provenance on news items stored before migration 20261029110000.

Those items have no `evidence_kind`, so they count as no primary evidence and a story made of
them could never reach `primary_confirmed`. The same rules that classify new items are applied
to the stored row (its source, tier and outlet host). Items whose source has since been
removed from the config are given the plain `outlet` kind only when they are not tier 1 or 3;
otherwise they are left alone, because guessing a primary kind would overstate the evidence.
"""

import json
from collections.abc import Callable, Collection, Mapping

import asyncpg

from steward.config.news_sources import NewsSource, NewsSourcesConfig
from steward.news.evidence import evidence_kind, is_correction
from steward.news.normalize import host_of

BATCH = 500
CURSOR_KEY = "news_provenance_backfill_cursor"


async def backfill_provenance(
    pool: asyncpg.Pool,
    sources: Mapping[str, NewsSource],
    sources_config: NewsSourcesConfig,
    ir_domains: Collection[str],
    limit: int = BATCH,
) -> int:
    """Classify up to `limit` items that have no evidence kind; the number updated."""
    # Rows left alone below stay null, so a plain "newest 500 without a kind" would fetch them
    # forever and starve the rest: walk by id with a cursor kept between runs instead.
    stored = await pool.fetchval("select value from public.sync_state where key = $1", CURSOR_KEY)
    cursor = int(json.loads(stored).get("after", 0)) if stored else 0
    rows = await pool.fetch(
        "select id, source_id, tier, url, outlet, headline from public.news_item"
        " where evidence_kind is null and id > $2 order by id limit $1",
        limit,
        cursor,
    )
    next_cursor = rows[-1]["id"] if len(rows) >= limit else 0  # a short page ends the sweep
    await pool.execute(
        "insert into public.sync_state (key, value) values ($1, $2::jsonb)"
        " on conflict (key) do update set value = excluded.value",
        CURSOR_KEY,
        json.dumps({"after": next_cursor}),
    )
    key: Callable[[str | None], str] = sources_config.independence_key
    done = 0
    for r in rows:
        source = sources.get(r["source_id"])
        # The same host as a new item gets (`outlet_host or host_of(url)`), so the
        # independence key does not differ between stored and backfilled rows.
        host = r["outlet"] or host_of(r["url"])
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
