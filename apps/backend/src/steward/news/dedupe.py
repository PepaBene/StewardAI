"""Wire deduplication after storage: SimHash groups, embedding groups, and story-cluster merges.

Stage 1 of dedupe (canonical URL) happens when an item is stored. Here, per run:

1. SimHash: items whose text hashes differ in at most 3 bits are copies of one text and join
   one wire group (`news_article.group_id`, the id of the earliest member).
2. Embeddings: items not grouped yet are embedded (multilingual), and an item that matches an
   earlier one above the threshold within the window joins its group, whatever the language.
3. Story clusters: when a *linked* item matches a linked item of another story cluster, shares
   a holding with it, and its own cluster is only that one new item with no alert, the item
   moves into the older cluster. Anything less certain stays a separate story: a wrong merge
   hides an alert, a missed one only shows a duplicate.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

import asyncpg

from steward.news.embeddings import Vec, assign_groups
from steward.news.simhash import SimhashIndex, to_unsigned


@dataclass
class DedupeResult:
    simhash_groups: int = 0
    embedded: int = 0
    embedding_groups: int = 0
    merged_clusters: list[tuple[int, int]] = field(default_factory=list)  # (removed, kept)
    embedding_tokens: int = 0
    skipped: str | None = None


async def group_by_simhash(pool: asyncpg.Pool, job_id: int, since: dt.datetime) -> int:
    """Group this run's items with earlier near-identical ones. Returns items newly grouped."""
    rows = await pool.fetch(
        "select id, simhash, group_id, job_id from public.news_article"
        " where simhash is not null and first_seen_at >= $1 order by published_at, id",
        since,
    )
    index = SimhashIndex()
    group_of: dict[int, int] = {}
    changed: dict[int, int] = {}
    for r in rows:
        value = to_unsigned(r["simhash"])
        article_id = r["id"]
        group_of[article_id] = r["group_id"] or article_id
        # Any ungrouped item in the window, not only this run's: a late-arriving older article
        # is indexed before the newer copy stored in an earlier run, which must still join it.
        if r["group_id"] is None:
            matches = index.matches(value)
            if matches:
                # Nearest earlier item; join its group (the earliest member's id).
                gid = group_of[matches[0][0]]
                group_of[article_id] = gid
                changed[article_id] = gid
        index.add(article_id, value)
    for article_id, gid in changed.items():
        await pool.execute(
            "update public.news_article set group_id = $2, group_method = 'simhash'"
            " where id = $1 and group_id is null",
            article_id,
            gid,
        )
        # The group's first member points at itself so the group is queryable by group_id.
        await pool.execute(
            "update public.news_article set group_id = id, group_method = coalesce(group_method,"
            " 'simhash') where id = $1 and group_id is null",
            gid,
        )
    return len(changed)


async def pending_embedding(
    pool: asyncpg.Pool, limit: int, since: dt.datetime
) -> list[asyncpg.Record]:
    rows: list[asyncpg.Record] = await pool.fetch(
        "select a.id, a.headline, a.summary, a.language, a.published_at, a.group_id"
        " from public.news_article a left join public.news_embedding e on e.article_id = a.id"
        " where e.article_id is null and a.first_seen_at >= $2"
        " order by a.linked desc, a.published_at desc limit $1",
        limit,
        since,
    )
    return rows


async def store_embeddings(
    pool: asyncpg.Pool, model: str, ids: Sequence[int], vectors: Sequence[Sequence[float]]
) -> None:
    await pool.executemany(
        "insert into public.news_embedding (article_id, model, dim, vector)"
        " values ($1, $2, $3, $4) on conflict (article_id) do nothing",
        [(i, model, len(v), list(v)) for i, v in zip(ids, vectors, strict=True)],
    )


MAX_RECENT_VECTORS = 5000


async def recent_vectors(
    pool: asyncpg.Pool, model: str, since: dt.datetime, limit: int = MAX_RECENT_VECTORS
) -> list[Vec]:
    """Vectors of articles first seen since `since`, newest first and at most `limit`: the
    matching is O(new x all) in Python, so an old backlog must not grow it without bound."""
    rows = await pool.fetch(
        "select a.id, a.published_at, a.group_id, e.vector from public.news_article a"
        " join public.news_embedding e on e.article_id = a.id"
        " where e.model = $1 and a.first_seen_at >= $2"
        " order by a.first_seen_at desc, a.id desc limit $3",
        model,
        since,
        limit,
    )
    return [
        Vec(r["id"], tuple(r["vector"]), r["published_at"].timestamp(), r["group_id"]) for r in rows
    ]


async def group_by_embedding(
    pool: asyncpg.Pool,
    new_ids: set[int],
    vectors: Sequence[Vec],
    threshold: float,
    window_hours: float,
) -> dict[int, int]:
    """Assign and store embedding groups for `new_ids`; returns {article id: group id}."""
    new = [v for v in vectors if v.article_id in new_ids]
    existing = [v for v in vectors if v.article_id not in new_ids]
    groups = assign_groups(new, existing, threshold, window_hours)
    for article_id, gid in groups.items():
        await pool.execute(
            "update public.news_article set group_id = coalesce(group_id, $2),"
            " group_method = coalesce(group_method, 'embedding') where id = $1",
            article_id,
            gid,
        )
        await pool.execute(
            "update public.news_article set group_id = id, group_method = coalesce(group_method,"
            " 'embedding') where id = $1 and group_id is null",
            gid,
        )
    return groups


async def merge_story_clusters(
    pool: asyncpg.Pool, groups: dict[int, int], new_ids: set[int]
) -> list[tuple[int, int]]:
    """Move a new linked item into the older story cluster of the item it was grouped with,
    when that is safe (see the module docstring). Returns (removed cluster, kept cluster)."""
    merged: list[tuple[int, int]] = []
    for article_id in sorted(groups):
        if article_id not in new_ids:
            continue
        gid = groups[article_id]
        new = await pool.fetchrow(
            "select i.id as item_id, i.cluster_id, i.tickers from public.news_article a"
            " join public.news_item i on i.id = a.news_item_id where a.id = $1",
            article_id,
        )
        if new is None or new["cluster_id"] is None:
            continue
        older = await pool.fetchrow(
            "select i.cluster_id from public.news_article a"
            " join public.news_item i on i.id = a.news_item_id"
            " where a.group_id = $1 and a.id <> $2 and i.cluster_id is not null"
            " and i.cluster_id <> $3 and i.tickers && $4::text[]"
            " order by a.published_at, a.id limit 1",
            gid,
            article_id,
            new["cluster_id"],
            list(new["tickers"]),
        )
        if older is None:
            continue
        async with pool.acquire() as conn, conn.transaction():
            alone = await conn.fetchval(
                "select count(*) = 1 and not exists (select 1 from public.alert"
                " where cluster_id = $1) and not exists (select 1 from public.story_cluster"
                " where duplicate_of = $1) from public.news_item where cluster_id = $1",
                new["cluster_id"],
            )
            if not alone:
                continue
            await conn.execute(
                "update public.news_item set cluster_id = $2 where id = $1",
                new["item_id"],
                older["cluster_id"],
            )
            await conn.execute("delete from public.story_cluster where id = $1", new["cluster_id"])
            await conn.execute(
                "update public.story_cluster c set last_seen = greatest(c.last_seen, now()),"
                " best_tier = s.best_tier, source_count = s.outlets from (select min(tier) as"
                " best_tier, count(distinct coalesce(nullif(independence_key, ''), outlet, url))"
                " as outlets from"
                " public.news_item where cluster_id = $1) s where c.id = $1",
                older["cluster_id"],
            )
        merged.append((new["cluster_id"], older["cluster_id"]))
    return merged
