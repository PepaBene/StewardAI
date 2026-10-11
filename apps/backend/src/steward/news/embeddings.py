"""Multilingual embeddings of wire items and the grouping built on them.

`gemini-embedding-2` maps a Japanese, Chinese, German or English report of one event close
together, so the same event in different languages groups without translating first. The
embedder is a thin client over Gemini's `batchEmbedContents`; vectors are L2-normalised here
(truncated embeddings are not unit length), so cosine similarity is a dot product.

Grouping is conservative on purpose. Two items join one group only above a high cosine
threshold and within a time window, and a merge of *story clusters* additionally needs a
shared holding. A wrong merge hides a story (it would count as one source and could swallow
an alert); a missed merge only costs a duplicate in the wire.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
MAX_BATCH = 100
REQUEST_TIMEOUT = 60.0
# Input text per item: a headline and the lead of the summary carry the event.
MAX_CHARS = 600


class EmbeddingError(Exception):
    pass


def embed_text(headline: str, summary: str | None, language: str | None = None) -> str:
    """What is embedded for an item: its headline and the lead of its summary, in the original
    language."""
    return f"{headline}. {(summary or '')[: MAX_CHARS - len(headline)]}".strip()[:MAX_CHARS]


def estimate_tokens(texts: Sequence[str]) -> int:
    """The embedding API reports no usage, so cost is estimated: about 4 characters a token
    for Latin text, about 1.5 per CJK character. An estimate, labelled as one on the step."""
    total = 0
    for text in texts:
        cjk = sum("぀" <= ch <= "鿿" or "가" <= ch <= "힯" for ch in text)
        total += math.ceil((len(text) - cjk) / 4 + cjk / 1.5)
    return total


def normalise(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        raise EmbeddingError("the embedding is the zero vector")
    return [x / norm for x in vector]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two L2-normalised vectors of the same length."""
    return sum(x * y for x, y in zip(a, b, strict=True))


class Embedder(Protocol):
    model: str
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class GeminiEmbedder:
    def __init__(
        self, http: httpx.AsyncClient, api_key: str, model: str, dimensions: int = 768
    ) -> None:
        self._http = http
        self._key = api_key
        self.model = model
        self.dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One normalised vector per text, in order."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH):
            chunk = texts[start : start + MAX_BATCH]
            body = {
                "requests": [
                    {
                        "model": f"models/{self.model}",
                        "content": {"parts": [{"text": t}]},
                        "taskType": "SEMANTIC_SIMILARITY",
                        "outputDimensionality": self.dimensions,
                    }
                    for t in chunk
                ]
            }
            try:
                response = await self._http.post(
                    EMBED_URL.format(model=self.model),
                    json=body,
                    headers={"x-goog-api-key": self._key},
                    timeout=REQUEST_TIMEOUT,
                )
            except httpx.HTTPError as exc:
                raise EmbeddingError(f"embedding call failed: {type(exc).__name__}") from exc
            if response.status_code != 200:
                raise EmbeddingError(f"embedding call failed: HTTP {response.status_code}")
            try:
                rows = response.json()["embeddings"]
                got = [normalise(r["values"]) for r in rows]
            except (ValueError, KeyError, TypeError) as exc:
                raise EmbeddingError("embedding answer was not understood") from exc
            if len(got) != len(chunk) or any(len(v) != self.dimensions for v in got):
                raise EmbeddingError("embedding answer had the wrong count or dimension")
            vectors.extend(got)
        return vectors


@dataclass(frozen=True)
class Vec:
    """An item's vector with what grouping needs to know about it."""

    article_id: int
    vector: tuple[float, ...]
    published_ts: float  # seconds since epoch
    group_id: int | None = None


def best_match(
    target: Vec,
    pool: Sequence[Vec],
    threshold: float,
    window_hours: float,
) -> tuple[Vec, float] | None:
    """The most similar earlier item in `pool` at or above `threshold` and within the time
    window, or None. Items published after `target` are not matches (a group's id is its
    earliest member's, so matching only backwards keeps ids stable across runs)."""
    best: tuple[Vec, float] | None = None
    limit = window_hours * 3600
    for other in pool:
        if other.article_id == target.article_id or other.published_ts > target.published_ts:
            continue
        if target.published_ts - other.published_ts > limit:
            continue
        score = cosine(target.vector, other.vector)
        if score >= threshold and (best is None or score > best[1]):
            best = (other, score)
    return best


def assign_groups(
    new: Sequence[Vec], existing: Sequence[Vec], threshold: float, window_hours: float
) -> dict[int, int]:
    """Group id for each new item that belongs to a duplicate group: the group id of its best
    match (or that match's own id when it had none). Items that match nothing are absent.
    New items are handled oldest first, so a chain of reports forms one group."""
    pool = list(existing)
    groups: dict[int, int] = {}
    for item in sorted(new, key=lambda v: (v.published_ts, v.article_id)):
        match = best_match(item, pool, threshold, window_hours)
        if match is not None:
            other = match[0]
            gid = groups.get(other.article_id) or other.group_id or other.article_id
            groups[item.article_id] = gid
            pool.append(Vec(item.article_id, item.vector, item.published_ts, gid))
        else:
            pool.append(item)
    return groups
