"""Shared plumbing of the N6 analysis techniques.

Every technique reads a story's stored text (`StoryMaterial`), computes a result in code or
through one `LLM.complete` call (`structured`), and appends it to `story_analysis` keyed by a hash
of its inputs, so an unchanged story is never analysed twice and a changed one adds a row.

The rule for every model-written fact is the one in section 6 of the plan: it cites a span of the
stored text and the span is verified here by string match (`verify_span`) before anything is
saved. What fails the check is dropped; a result left with nothing verified is not saved.
"""

import datetime as dt
import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg
from pydantic import BaseModel

from steward.ai import Usage
from steward.llm.cache import ResultCache, request_key
from steward.llm.client import LLM
from steward.llm.types import Completion, LLMError, Message, Request

MAX_BODY_CHARS = 6000
REQUEST_TIMEOUT = 120.0

# Evidence kinds that count as a primary document (see steward.news.evidence).
PRIMARY_KINDS = ("primary_filing", "regulator", "company_release", "central_bank")


class AnalysisError(Exception):
    """A technique's model call failed or returned nothing usable."""


@dataclass(frozen=True)
class ItemText:
    id: int
    url: str
    outlet: str | None
    outlet_name: str | None
    tier: int
    region: str | None
    state_media: bool
    evidence_kind: str | None
    independence_key: str | None
    is_correction: bool
    published_at: dt.datetime
    language: str | None
    headline: str
    summary: str | None
    body: str | None

    @property
    def text(self) -> str:
        """Everything stored of the item, the text a cited span is checked against."""
        return "\n".join(p for p in (self.headline, self.summary, self.body) if p)

    @property
    def label(self) -> str:
        return self.outlet_name or self.outlet or f"item {self.id}"


@dataclass(frozen=True)
class StoryMaterial:
    cluster_id: int
    ticker: str | None
    headline: str
    event_type: str | None
    severity: int
    lifecycle: str
    recycled: bool
    event_date: dt.date | None
    first_seen: dt.datetime
    last_seen: dt.datetime
    thread_id: int | None
    items: tuple[ItemText, ...]

    def item(self, item_id: int) -> ItemText | None:
        return next((i for i in self.items if i.id == item_id), None)

    @property
    def independent_outlets(self) -> int:
        return len({i.independence_key or i.outlet or i.id for i in self.items})


# Typographic quotes, primes and dashes folded to their ASCII forms (by code point).
_UNIFY = {
    **dict.fromkeys((0x2018, 0x2019, 0x201A, 0x201B, 0x60, 0xB4), "'"),
    **dict.fromkeys((0x201C, 0x201D, 0x201E, 0x201F), '"'),
    **dict.fromkeys((*range(0x2010, 0x2016), 0x2212), "-"),
}


def fold(text: str) -> str:
    """Text as compared for a cited span: NFKC, quotes and dashes unified, lower case, one
    space between words."""
    t = unicodedata.normalize("NFKC", text).lower().translate(_UNIFY)
    return re.sub(r"\s+", " ", t).strip()


def verify_span(span: str, texts: Sequence[str]) -> bool:
    """Whether `span` occurs, after folding, in one of `texts`. Too short a span (under 12
    characters) proves nothing and fails."""
    needle = fold(span).strip(" .…\"'")
    if len(needle) < 12:
        return False
    return any(needle in fold(t) for t in texts)


def input_hash(*parts: object) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


ITEM_SELECT = (
    "select i.id, i.url, i.outlet, i.outlet_name, i.tier, i.evidence_kind, i.independence_key,"
    " i.is_correction, i.published_at, i.language, i.headline, i.summary,"
    " a.region, coalesce(s.state_media, false) as state_media,"
    " (select f.body from public.news_fulltext f where f.news_item_id = i.id"
    "  and f.body is not null and not f.truncated order by f.fetched_at desc limit 1) as body"
    " from public.news_item i left join public.news_article a on a.news_item_id = i.id"
    " left join public.source s on s.id = i.source_id"
)


def item_from_row(r: asyncpg.Record) -> ItemText:
    return ItemText(
        id=r["id"],
        url=r["url"],
        outlet=r["outlet"],
        outlet_name=r["outlet_name"],
        tier=r["tier"],
        region=r["region"],
        state_media=r["state_media"],
        evidence_kind=r["evidence_kind"],
        independence_key=r["independence_key"],
        is_correction=r["is_correction"],
        published_at=r["published_at"],
        language=r["language"],
        headline=r["headline"],
        summary=r["summary"],
        body=(r["body"] or "")[:MAX_BODY_CHARS] or None,
    )


async def related_primaries(
    pool: asyncpg.Pool, story: StoryMaterial, days: int = 3, limit: int = 4
) -> list[ItemText]:
    """Primary documents about the story's holding that are stored in other stories: filings,
    regulator notices, company releases and central-bank items from a few days around it. A
    claim can be checked against them even when none of the story's own items is primary."""
    if story.ticker is None:
        return []
    window = dt.timedelta(days=days)
    rows = await pool.fetch(
        ITEM_SELECT + " join public.story_cluster c on c.id = i.cluster_id"
        " where i.cluster_id <> $1 and c.ticker = $2 and i.evidence_kind = any($3::text[])"
        " and i.published_at between $4 and $5 order by i.published_at desc limit $6",
        story.cluster_id,
        story.ticker,
        list(PRIMARY_KINDS),
        story.first_seen - window,
        story.last_seen + window,
        limit,
    )
    return [item_from_row(r) for r in rows]


async def load_story(pool: asyncpg.Pool, cluster_id: int) -> StoryMaterial | None:
    c = await pool.fetchrow(
        "select id, ticker, headline, event_type, severity, lifecycle, recycled, event_date,"
        " first_seen, last_seen, thread_id from public.story_cluster where id = $1",
        cluster_id,
    )
    if c is None:
        return None
    rows = await pool.fetch(
        ITEM_SELECT + " where i.cluster_id = $1 order by i.tier, i.published_at", cluster_id
    )
    items = tuple(item_from_row(r) for r in rows)
    return StoryMaterial(
        cluster_id=c["id"],
        ticker=c["ticker"],
        headline=c["headline"],
        event_type=c["event_type"],
        severity=c["severity"] or 0,
        lifecycle=c["lifecycle"],
        recycled=c["recycled"],
        event_date=c["event_date"],
        first_seen=c["first_seen"],
        last_seen=c["last_seen"],
        thread_id=c["thread_id"],
        items=items,
    )


def items_block(items: Sequence[ItemText], chars: int = 1800) -> str:
    """The story's items as the model sees them: id, outlet metadata, and text."""
    rows = []
    for i in items:
        rows.append(
            json.dumps(
                {
                    "item_id": i.id,
                    "outlet": i.label,
                    "tier": i.tier,
                    "region": i.region,
                    "state_media": i.state_media,
                    "kind": i.evidence_kind,
                    "published": i.published_at.date().isoformat(),
                    "text": i.text[:chars],
                },
                ensure_ascii=False,
            )
        )
    return "[\n" + ",\n".join(rows) + "\n]"


async def structured[T: BaseModel](
    llm: LLM,
    model: str,
    system: str,
    user: str,
    schema: type[T],
    usage: Usage,
    *,
    effort: str | None = None,
    cache: ResultCache | None = None,
    cache_key: str,
    max_tokens: int = 4000,
) -> T:
    """One structured model call with the result cache; the cost is added to `usage`."""
    request = Request(
        model=model,
        system=system,
        messages=(Message("user", user),),
        schema=schema,
        effort=effort,
        max_tokens=max_tokens,
        timeout=REQUEST_TIMEOUT,
        cache_key=cache_key,
    )
    key = request_key(model, system, [user], schema, effort)
    if cache is not None:
        hit = await cache.get(key)
        if hit is not None:
            try:
                return schema.model_validate(hit)
            except ValueError:
                pass
    try:
        done: Completion = await llm.complete(request)
    except LLMError as exc:
        raise AnalysisError(f"{cache_key} call failed: {exc}") from exc
    usage.add(done.usage)
    if done.stop_reason in ("refusal", "max_tokens"):
        raise AnalysisError(f"{cache_key} stopped early ({done.stop_reason})")
    if not isinstance(done.parsed, schema):
        raise AnalysisError(f"{cache_key} returned no parseable JSON")
    if cache is not None:
        await cache.put(key, model, done.parsed.model_dump(mode="json"))
    return done.parsed


async def save_analysis(
    pool: asyncpg.Pool,
    cluster_id: int,
    kind: str,
    hash_: str,
    model: str | None,
    payload: dict[str, Any],
    job_id: int | None,
) -> bool:
    """Append a result; False when the same inputs were already analysed."""
    got = await pool.fetchval(
        "insert into public.story_analysis (cluster_id, kind, input_hash, model, payload, job_id)"
        " values ($1, $2, $3, $4, $5::jsonb, $6)"
        " on conflict (cluster_id, kind, input_hash) do nothing returning id",
        cluster_id,
        kind,
        hash_,
        model,
        json.dumps(payload, default=str, ensure_ascii=False),
        job_id,
    )
    return got is not None


async def latest_analysis(pool: asyncpg.Pool, cluster_id: int, kind: str) -> dict[str, Any] | None:
    raw = await pool.fetchval(
        "select payload from public.story_analysis where cluster_id = $1 and kind = $2"
        " order by created_at desc, id desc limit 1",
        cluster_id,
        kind,
    )
    if raw is None:
        return None
    return json.loads(raw) if isinstance(raw, str) else dict(raw)
