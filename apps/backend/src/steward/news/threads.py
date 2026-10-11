"""N6.4: story threads and "what changed".

A thread groups the stories of one developing situation (a merger, a strike, a tariff dispute) on
a holding, so ten articles do not read as ten events. Each story that joins a thread is diffed
against the thread's earlier stories into what is new, what changed, what was corrected, and
what is still unknown.

This is code, not a model call (rules before AI): membership is the embedding centroid's
similarity to the thread's stories (headline overlap with the same event type when there is no
embedding); the diff compares the claims already extracted by 6.1 by token overlap and
number changes. A model is not asked to decide what changed.
"""

import datetime as dt
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from steward.news.analysis import StoryMaterial
from steward.news.embeddings import cosine
from steward.news.normalize import headline_tokens
from steward.news.novelty import centroid, story_vectors
from steward.news.translate import numbers_in

SAME_CLAIM = 0.8
RELATED_CLAIM = 0.4
HEADLINE_JOIN = 0.5


@dataclass
class ThreadDiff:
    new: list[str] = field(default_factory=list)
    changed: list[dict[str, str]] = field(default_factory=list)
    retracted: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "new": self.new,
            "changed": self.changed,
            "retracted": self.retracted,
            "unknown": self.unknown,
        }

    @property
    def empty(self) -> bool:
        return not (self.new or self.changed or self.retracted)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"\w+", text.lower()))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def claim_diff(
    prior: Sequence[str],
    now: Sequence[str],
    corrections: Sequence[str] = (),
    unknowns: Sequence[str] = (),
) -> ThreadDiff:
    """Compare the thread's earlier claims with this story's."""
    diff = ThreadDiff(retracted=list(dict.fromkeys(corrections)), unknown=list(unknowns))
    prior_tokens = [(p, _tokens(p)) for p in prior]
    for claim in now:
        tokens = _tokens(claim)
        best: tuple[float, str] = (0.0, "")
        for text, ptokens in prior_tokens:
            score = jaccard(tokens, ptokens)
            if score > best[0]:
                best = (score, text)
        same_numbers = numbers_in(claim) == numbers_in(best[1])
        if best[0] >= SAME_CLAIM and same_numbers:
            continue
        if best[0] >= RELATED_CLAIM and not same_numbers:
            diff.changed.append({"was": best[1], "now": claim})
        else:
            diff.new.append(claim)
    return diff


def best_thread(
    own: Sequence[float],
    threads: Mapping[int, Sequence[float]],
    threshold: float,
) -> int | None:
    """The thread whose centroid is most similar to `own`, if at or above `threshold`."""
    best_id: int | None = None
    best = threshold
    for thread_id, vector in sorted(threads.items()):
        if len(vector) != len(own):
            continue
        sim = cosine(own, vector)
        if sim >= best:
            best, best_id = sim, thread_id
    return best_id


async def _thread_members(pool: asyncpg.Pool, ticker: str, since: dt.datetime) -> list[Any]:
    rows = await pool.fetch(
        "select t.id as thread_id, c.id as cluster_id, c.headline, c.event_type"
        " from public.story_thread t join public.story_cluster c on c.thread_id = t.id"
        " where t.ticker = $1 and t.state <> 'closed' and t.last_update_at >= $2",
        ticker,
        since,
    )
    return list(rows)


async def assign_thread(
    pool: asyncpg.Pool,
    story: StoryMaterial,
    own: Sequence[float],
    window_days: int,
    threshold: float,
) -> tuple[int, bool]:
    """The thread the story belongs to (created when none fits) and whether it was created.
    A story that already has a thread keeps it.

    Matching and creating run under a transaction-scoped advisory lock per holding, and a new
    thread is attached to the story before the lock is released, so two concurrent runs cannot
    each create a thread for the same developing situation."""
    if story.thread_id is not None:
        return story.thread_id, False
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "select pg_advisory_xact_lock(hashtext($1))", f"story_thread:{story.ticker or ''}"
        )
        # Another run may have attached a thread to this very story while this one waited.
        current = await conn.fetchval(
            "select thread_id from public.story_cluster where id = $1", story.cluster_id
        )
        if current is not None:
            return int(current), False
        return await _assign_locked(pool, conn, story, own, window_days, threshold)


async def _assign_locked(
    pool: asyncpg.Pool,
    conn: asyncpg.Connection,
    story: StoryMaterial,
    own: Sequence[float],
    window_days: int,
    threshold: float,
) -> tuple[int, bool]:
    if story.ticker is not None:
        rows = await _thread_members(
            pool, story.ticker, story.last_seen - dt.timedelta(days=window_days)
        )
        by_thread: dict[int, list[Any]] = {}
        for r in rows:
            if r["cluster_id"] != story.cluster_id:
                by_thread.setdefault(r["thread_id"], []).append(r)
        chosen: int | None = None
        if own and by_thread:
            vectors = await story_vectors(
                pool, [m["cluster_id"] for ms in by_thread.values() for m in ms]
            )
            centroids = {
                tid: centroid([vectors[m["cluster_id"]] for m in ms if m["cluster_id"] in vectors])
                for tid, ms in by_thread.items()
            }
            chosen = best_thread(own, {t: v for t, v in centroids.items() if v}, threshold)
        if chosen is None:
            mine = frozenset(headline_tokens(story.headline))
            for tid, ms in sorted(by_thread.items()):
                if any(
                    m["event_type"] == story.event_type
                    and jaccard(mine, frozenset(headline_tokens(m["headline"]))) >= HEADLINE_JOIN
                    for m in ms
                ):
                    chosen = tid
                    break
        if chosen is not None:
            return chosen, False
    created = await conn.fetchval(
        "insert into public.story_thread (ticker, title, opened_at, last_update_at)"
        " values ($1, $2, $3, $3) returning id",
        story.ticker,
        story.headline[:200],
        story.first_seen,
    )
    await conn.execute(
        "update public.story_cluster set thread_id = $1 where id = $2 and thread_id is null",
        created,
        story.cluster_id,
    )
    return int(created), True


async def prior_claims(pool: asyncpg.Pool, thread_id: int, cluster_id: int) -> list[str]:
    """Claims of the thread's other stories: their latest extraction, else their headlines."""
    rows = await pool.fetch(
        "select c.id, c.headline,"
        " (select a.payload from public.story_analysis a where a.cluster_id = c.id"
        "  and a.kind = 'extraction' order by a.created_at desc, a.id desc limit 1) as extraction"
        " from public.story_cluster c where c.thread_id = $1 and c.id <> $2"
        " and c.first_seen <= (select first_seen from public.story_cluster where id = $2)",
        thread_id,
        cluster_id,
    )
    claims: list[str] = []
    for r in rows:
        payload = r["extraction"]
        if payload is not None:
            doc = json.loads(payload) if isinstance(payload, str) else payload
            texts = [c["text"] for c in doc.get("key_claims", [])]
            claims.extend(texts or [r["headline"]])
        else:
            claims.append(r["headline"])
    return claims


async def record_update(
    pool: asyncpg.Pool, thread_id: int, story: StoryMaterial, diff: ThreadDiff
) -> None:
    await pool.execute(
        "update public.story_cluster set thread_id = $1 where id = $2", thread_id, story.cluster_id
    )
    await pool.execute(
        "insert into public.story_thread_update (thread_id, cluster_id, at, diff)"
        " values ($1, $2, $3, $4::jsonb)"
        " on conflict (thread_id, cluster_id) do update set diff = excluded.diff",
        thread_id,
        story.cluster_id,
        story.last_seen,
        json.dumps(diff.as_json(), ensure_ascii=False),
    )
    await pool.execute(
        "update public.story_thread set last_update_at = greatest(last_update_at, $2),"
        " state = case when state = 'closed' then state else 'open' end where id = $1",
        thread_id,
        story.last_seen,
    )


async def quiet_old_threads(pool: asyncpg.Pool, now: dt.datetime, window_days: int) -> int:
    done = await pool.execute(
        "update public.story_thread set state = 'quiet'"
        " where state = 'open' and last_update_at < $1",
        now - dt.timedelta(days=window_days),
    )
    return int(done.split()[-1])
