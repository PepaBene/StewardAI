"""N6.2: is this story new, an update, a repeat or recycled, and does it beat expectations.

Computed in code, no model: the story's embedding centroid is compared with the stories on the
same holding in the preceding window. Repeats are suppressed in rankings and briefs, never deleted.

- recycled: the pipeline already marked the story as older news, or its event date is more than
  a week before the first report of it;
- repeat: cosine similarity to an earlier story at or above `novelty_repeat_similarity`;
- update: at or above `novelty_update_similarity`;
- new: otherwise. With no embedding for the story the answer is `new` with basis
  `no_embedding`, not a guess.

Surprise comes from the extraction (6.1): the main number's stated position against what was
expected. No expectation in the text means `unknown`, never a guess.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import asyncpg

from steward.news.embeddings import EmbeddingError, cosine, normalise
from steward.news.extract import StoryExtraction

Novelty = Literal["new", "update", "repeat", "recycled"]
Surprise = Literal["none", "in_line", "above", "below", "unknown"]

RECYCLED_AFTER_DAYS = 7


@dataclass(frozen=True)
class NoveltyResult:
    novelty: Novelty
    similarity: float | None
    similar_cluster_id: int | None
    basis: str


def centroid(vectors: Sequence[Sequence[float]]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    total = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            total[i] += x
    try:
        return normalise([x / len(vectors) for x in total])
    except EmbeddingError:
        return []


def classify_novelty(
    own: Sequence[float],
    earlier: Mapping[int, Sequence[float]],
    *,
    recycled: bool,
    event_date: dt.date | None,
    first_seen: dt.datetime,
    repeat_at: float,
    update_at: float,
) -> NoveltyResult:
    if recycled or (
        event_date is not None and (first_seen.date() - event_date).days > RECYCLED_AFTER_DAYS
    ):
        return NoveltyResult("recycled", None, None, "event older than its first report")
    if not own:
        return NoveltyResult("new", None, None, "no_embedding")
    best_id: int | None = None
    best = -1.0
    for cluster_id, vector in sorted(earlier.items()):
        if len(vector) != len(own):
            continue
        sim = cosine(own, vector)
        if sim > best:
            best, best_id = sim, cluster_id
    if best_id is None:
        return NoveltyResult("new", None, None, "no earlier story on the holding")
    if best >= repeat_at:
        return NoveltyResult("repeat", best, best_id, "similar to an earlier story")
    if best >= update_at:
        return NoveltyResult("update", best, best_id, "related to an earlier story")
    return NoveltyResult("new", best, best_id, "nothing similar earlier")


def surprise_from(extraction: StoryExtraction | None) -> Surprise:
    if extraction is None or extraction.magnitude is None:
        return "unknown"
    return extraction.magnitude.vs_expectation


async def story_vectors(pool: asyncpg.Pool, cluster_ids: Sequence[int]) -> dict[int, list[float]]:
    """Each story's embedding centroid (stories with no embedded item are absent)."""
    if not cluster_ids:
        return {}
    rows = await pool.fetch(
        "select i.cluster_id, e.vector from public.news_embedding e"
        " join public.news_article a on a.id = e.article_id"
        " join public.news_item i on i.id = a.news_item_id"
        " where i.cluster_id = any($1::bigint[])",
        list(cluster_ids),
    )
    by: dict[int, list[list[float]]] = {}
    for r in rows:
        by.setdefault(r["cluster_id"], []).append(list(r["vector"]))
    return {cid: centroid(vs) for cid, vs in by.items() if vs}


async def earlier_stories(
    pool: asyncpg.Pool,
    cluster_id: int,
    ticker: str | None,
    first_seen: dt.datetime,
    days: int,
    event_type: str | None = None,
) -> dict[int, list[float]]:
    """Centroids of the other stories in the `days` before this one: those on the same holding,
    plus, when the story has an event type, those of the same type on any holding. A theme that
    reaches the owner through two holdings (an export rule, a rate decision) is then one
    development, not two new ones."""
    if ticker is None and event_type is None:
        return {}
    rows = await pool.fetch(
        "select id from public.story_cluster where id <> $1"
        " and first_seen < $2 and first_seen >= $3 and duplicate_of is null"
        " and (ticker = $4 or (event_type = $5 and $5 is not null))",
        cluster_id,
        first_seen,
        first_seen - dt.timedelta(days=days),
        ticker,
        event_type,
    )
    return await story_vectors(pool, [r["id"] for r in rows])
