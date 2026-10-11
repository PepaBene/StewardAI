"""The article-text ladder: from a wire item to the best text the rules allow.

Rungs, tried in order per the source's `FetchPolicy`, stopping at the first that yields a real
article:

1. the feed's own text (`content:encoded`);
2. a polite fetch of the article page (robots.txt, per-host spacing, public addresses only)
   and extraction of its body with trafilatura;
3. the same URL rendered in a headless browser (JavaScript pages);
4. an official API for the source (a caller-supplied hook; EDINET, DART, Guardian, ...).

What it never does: log in, bypass or spoof a paywall, rotate addresses, or read an archive
mirror. A paywall stub (a teaser, "subscribe to continue", schema.org `isAccessibleForFree`
false) is detected and stored as truncated, so analysis never treats it as the article.

Every stored text keeps its provenance: canonical URL, rung, extractor and version, content
hash and fetch time. Bodies expire (`FetchPolicy.retention_days`) unless the story is linked to
a holding; the hash row stays as the audit trail.
"""

import datetime as dt
import hashlib
import importlib
import importlib.metadata
import re
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import Any

import asyncpg

from steward.config.news_sources import FetchPolicy, NewsSourcesConfig
from steward.documents.text import html_to_text
from steward.fundamentals.ir import IrError
from steward.research.polite import PoliteFetcher

MAX_PAGE_BYTES = 3_000_000
# An article shorter than this is a teaser, a listing or a consent page, not the story.
MIN_ARTICLE_CHARS = 400
MAX_BODY_CHARS = 200_000

PAYWALL_PHRASES = (
    "subscribe to continue",
    "subscribe to read",
    "subscribe now to read",
    "already a subscriber",
    "sign in to read",
    "log in to read",
    "this article is for subscribers",
    "this content is for subscribers",
    "to continue reading",
    "register to continue",
    "free articles remaining",
    "you have reached your limit",
    "become a member to read",
    "unlock this article",
    "premium content",
    # German, Czech: the outlets in the source plan.
    "weiterlesen mit",
    "nur für abonnenten",
    "jetzt abonnieren",
    "pro předplatitele",
    "pro odběratele",
    "dočtěte článek",
)
CONSENT_PHRASES = ("accept all cookies", "we value your privacy", "manage cookie preferences")
_ACCESSIBLE_FALSE = re.compile(r'"isAccessibleForFree"\s*:\s*(?:false|"false")', re.IGNORECASE)


@dataclass(frozen=True)
class Extracted:
    text: str
    extractor: str
    byline: str | None = None
    language: str | None = None
    authored_at: dt.datetime | None = None


@dataclass(frozen=True)
class FulltextResult:
    """What the ladder produced for one URL. `state` is the wire item's `fetch_state`."""

    url: str
    state: str  # full | stub | failed | skipped
    rung: int | None = None
    extracted: Extracted | None = None
    truncation_reason: str | None = None
    note: str | None = None

    @property
    def truncated(self) -> bool:
        return self.truncation_reason is not None


def _trafilatura_version() -> str:
    try:
        return importlib.metadata.version("trafilatura")
    except importlib.metadata.PackageNotFoundError:
        return "none"


def _parse_date(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def extract_article(html: bytes | str, url: str) -> Extracted | None:
    """The article body, byline, language and authored time of an HTML page.

    trafilatura first (best open-source F1 on the published benchmarks, but the plan requires
    checking it on real articles per outlet). If it is not installed or finds nothing, the
    page's visible text is used, flagged by extractor name `basic` so the caller can tell.
    """
    try:
        trafilatura = importlib.import_module("trafilatura")
    except ImportError:
        trafilatura = None
    if trafilatura is not None:
        document = trafilatura.bare_extraction(
            html, url=url, with_metadata=True, favor_precision=True, include_comments=False
        )

        # trafilatura 2 returns a Document object; 1.x returns a dict.
        def get(key: str) -> Any:
            if isinstance(document, dict):
                return document.get(key)
            return getattr(document, key, None)

        text = str(get("text") or "").strip() if document is not None else ""
        if text:
            return Extracted(
                text=text[:MAX_BODY_CHARS],
                extractor=f"trafilatura {_trafilatura_version()}",
                byline=str(get("author") or "") or None,
                language=str(get("language") or "")[:2].lower() or None,
                authored_at=_parse_date(get("date")),
            )
    markup = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    text = re.sub(r"\s+", " ", html_to_text(markup)).strip()
    return Extracted(text=text[:MAX_BODY_CHARS], extractor="basic") if text else None


def detect_truncation(
    text: str, html: str | bytes | None = None, summary: str | None = None
) -> str | None:
    """Why `text` is not the whole article (a reason code), or None when it looks complete."""
    lowered = text.lower()
    markup = html.decode("utf-8", errors="ignore") if isinstance(html, bytes) else (html or "")
    if markup and _ACCESSIBLE_FALSE.search(markup):
        # The page itself declares the body paywalled (schema.org NewsArticle markup).
        return "paywall_markup"
    if len(text) < MIN_ARTICLE_CHARS:
        if any(p in lowered or p in markup.lower() for p in CONSENT_PHRASES):
            return "consent_wall"
        return "too_short"
    # A paywall notice sits at the end of the free part. Only the tail counts, so an article
    # that merely discusses subscriptions is not flagged.
    tail = lowered[-600:]
    if any(p in tail for p in PAYWALL_PHRASES):
        return "paywall_notice"
    if summary and len(summary) > len(text):
        return "shorter_than_summary"
    return None


def content_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


# A rung-4 hook: given a URL, the text an official API holds for it (or None).
ApiFetch = Callable[[str], Awaitable[Extracted | None]]
Render = Callable[[str], Awaitable[str | None]]


def _judge(
    url: str, rung: int, extracted: Extracted, html: bytes | str | None, summary: str | None
) -> FulltextResult:
    reason = detect_truncation(extracted.text, html, summary)
    return FulltextResult(
        url=url,
        state="stub" if reason else "full",
        rung=rung,
        extracted=extracted,
        truncation_reason=reason,
    )


async def acquire_fulltext(
    url: str,
    policy: FetchPolicy,
    *,
    feed_text: str | None = None,
    summary: str | None = None,
    fetcher: PoliteFetcher | None = None,
    render: Render | None = None,
    api: ApiFetch | None = None,
) -> FulltextResult:
    """Run the ladder for one URL. The first rung that gives a complete article wins; if none
    does, the best stub is returned (so it is known the text is incomplete); if every rung
    errored, the state is `failed` with the last error as the note."""
    if not policy.rungs:
        return FulltextResult(url=url, state="skipped", note="policy allows no article fetch")
    best_stub: FulltextResult | None = None
    note: str | None = None
    for rung in policy.rungs:
        result: FulltextResult | None = None
        try:
            if rung == 1 and feed_text:
                result = _judge(url, 1, Extracted(feed_text, "feed"), None, summary)
            elif rung == 2 and fetcher is not None:
                page = await fetcher.get(url, MAX_PAGE_BYTES)
                extracted = extract_article(page.body, url)
                if extracted is not None:
                    result = _judge(url, 2, extracted, page.body, summary)
                else:
                    note = "no article text found in the page"
            elif rung == 3 and render is not None:
                html = await render(url)
                if html is None:
                    note = "browser could not render the page"
                else:
                    extracted = extract_article(html, url)
                    if extracted is not None:
                        result = _judge(url, 3, extracted, html, summary)
            elif rung == 4 and api is not None:
                api_text = await api(url)
                if api_text is not None:
                    result = _judge(url, 4, api_text, None, summary)
        except IrError as exc:
            # Refused, blocked, robots-disallowed: recorded, never worked around.
            note = str(exc)[:300]
            continue
        if result is None:
            continue
        if result.state == "full":
            return result
        if best_stub is None or len(result.extracted.text if result.extracted else "") > len(
            best_stub.extracted.text if best_stub.extracted else ""
        ):
            best_stub = result
    if best_stub is not None:
        return best_stub
    return FulltextResult(url=url, state="failed", note=note or "no rung produced text")


# ---------------------------------------------------------------------------
# Persistence and the funnel
# ---------------------------------------------------------------------------


def expiry(fetched_at: dt.datetime, policy: FetchPolicy, *, keep: bool) -> dt.datetime | None:
    """When the body is cleared: `retention_days` after the fetch, never for a kept story."""
    return None if keep else fetched_at + dt.timedelta(days=policy.retention_days)


async def store_result(
    pool: asyncpg.Pool,
    article_id: int,
    news_item_id: int | None,
    result: FulltextResult,
    policy: FetchPolicy,
    now: dt.datetime,
) -> None:
    """Record a ladder result: the text row (when there is text) and the wire item's state."""
    extracted = result.extracted
    if extracted is not None and result.rung is not None:
        text = extracted.text
        await pool.execute(
            "insert into public.news_fulltext (url, article_id, news_item_id, rung, extractor,"
            " body, byline, language, authored_at, content_hash, truncated, truncation_reason,"
            " char_count, fetched_at, expires_at) values ($1, $2, $3, $4, $5, $6, $7, $8, $9,"
            " $10, $11, $12, $13, $14, $15) on conflict (url) do update set rung = excluded.rung,"
            " extractor = excluded.extractor, body = excluded.body, byline = excluded.byline,"
            " language = excluded.language, authored_at = excluded.authored_at,"
            " content_hash = excluded.content_hash, truncated = excluded.truncated,"
            " truncation_reason = excluded.truncation_reason, char_count = excluded.char_count,"
            " fetched_at = excluded.fetched_at, expires_at = excluded.expires_at,"
            " news_item_id = coalesce(excluded.news_item_id, news_fulltext.news_item_id)",
            result.url,
            article_id,
            news_item_id,
            result.rung,
            extracted.extractor,
            text,
            extracted.byline,
            extracted.language,
            extracted.authored_at,
            content_hash(text),
            result.truncated,
            result.truncation_reason,
            len(text),
            now,
            expiry(now, policy, keep=news_item_id is not None),
        )
    await pool.execute(
        "update public.news_article set fetch_state = $2 where id = $1", article_id, result.state
    )


async def store_feed_text(
    db: asyncpg.Pool | asyncpg.Connection,
    url: str,
    text: str,
    summary: str | None,
    policy: FetchPolicy,
    now: dt.datetime,
    *,
    keep: bool,
) -> bool:
    """Rung 1: persist the article text the feed itself carried (`content:encoded`), so the
    ladder never fetches a page the feed already delivered. False when the text looks cut off
    (it is then left to the later rungs) or the wire row does not exist."""
    if detect_truncation(text, None, summary) is not None:
        return False
    stored = await db.fetchval(
        "with ins as (insert into public.news_fulltext (url, article_id, news_item_id, rung,"
        " extractor, body, content_hash, truncated, char_count, fetched_at, expires_at)"
        " select $1, a.id, a.news_item_id, 1, 'feed', $2, $3, false, $4, $5, $6"
        " from public.news_article a where a.url = $1"
        " on conflict (url) do nothing returning article_id),"
        " upd as (update public.news_article set fetch_state = 'full'"
        "  where id in (select article_id from ins) and fetch_state in ('headline', 'summary')"
        "  returning 1)"
        " select count(*) from ins",
        url,
        text,
        content_hash(text),
        len(text),
        now,
        expiry(now, policy, keep=keep),
    )
    return bool(stored)


async def purge_expired(pool: asyncpg.Pool, now: dt.datetime) -> int:
    """Clear the bodies past their expiry. The row, hash and provenance stay."""
    status: str = await pool.execute(
        "update public.news_fulltext set body = null where body is not null"
        " and expires_at is not null and expires_at < $1",
        now,
    )
    return int(status.rsplit(" ", 1)[-1])


async def fulltext_candidates(
    pool: asyncpg.Pool,
    limit: int,
    since: dt.datetime,
    unlinked_relevance: int | None = None,
    exclude_sources: Collection[str] = (),
) -> list[asyncpg.Record]:
    """Wire items worth article text, newest first: linked to a holding, or triaged at
    `unlinked_relevance` or above, and not yet fetched, leaving out `exclude_sources` (sources
    with no policy or no allowance left, whose rows would otherwise fill the limit and starve
    the rest). (The whole wire is never fetched: the funnel in section 8 of the plan.)"""
    rows: list[asyncpg.Record] = await pool.fetch(
        "select a.id, a.url, a.source_id, a.summary, a.news_item_id"
        " from public.news_article a"
        " where (a.linked or ($3::int is not null and (a.triage->>'relevance')::int >= $3))"
        " and a.fetch_state in ('headline', 'summary') and a.first_seen_at >= $2"
        " and a.source_id <> all($4::text[])"
        " order by a.published_at desc limit $1",
        limit,
        since,
        unlinked_relevance,
        list(exclude_sources),
    )
    return rows


async def hourly_budget_left(
    pool: asyncpg.Pool, source_id: str, policy: FetchPolicy, now: dt.datetime
) -> int:
    """Article fetches this source may still make this hour (rung 2 and 3 fetches only)."""
    used = await pool.fetchval(
        "select count(*) from public.news_fulltext f join public.news_article a on a.id ="
        " f.article_id where a.source_id = $1 and f.rung in (2, 3) and f.fetched_at >= $2",
        source_id,
        now - dt.timedelta(hours=1),
    )
    return max(0, policy.max_requests_per_hour - int(used or 0))


MAX_CANDIDATE_ROUNDS = 5


async def run_fulltext(
    pool: asyncpg.Pool,
    sources: NewsSourcesConfig,
    fetcher: PoliteFetcher | None,
    now: dt.datetime,
    *,
    limit: int = 40,
    render: Render | None = None,
    api: ApiFetch | None = None,
    unlinked_relevance: int | None = None,
) -> dict[str, Any]:
    """Acquire article text for the newest linked (or highly triaged) wire items that have none
    yet, within each source's hourly fetch allowance. Returns counts by outcome for the job
    step."""
    policies = {s.id: s.fetch for s in sources.sources}
    counts: dict[str, int] = {"candidates": 0, "full": 0, "stub": 0, "failed": 0, "skipped": 0}
    errors: list[str] = []
    allowance: dict[str, int] = {}
    since = now - dt.timedelta(hours=sources.freshness_hours)
    # Sources with no policy or no allowance left are left out of the next candidate query, so
    # their rows cannot fill the limit and starve the sources that can still be fetched.
    blocked: set[str] = set()
    seen: set[int] = set()
    processed = 0
    for _ in range(MAX_CANDIDATE_ROUNDS):
        rows = await fulltext_candidates(
            pool, limit - processed, since, unlinked_relevance, blocked
        )
        rows = [r for r in rows if r["id"] not in seen]
        if not rows:
            break
        counts["candidates"] += len(rows)
        seen.update(r["id"] for r in rows)
        blocked_before = len(blocked)
        for row in rows:
            policy = policies.get(row["source_id"])
            if policy is None:
                blocked.add(row["source_id"])
                continue
            if not policy.rungs:
                result = FulltextResult(url=row["url"], state="skipped", note="policy: no fetch")
            else:
                if row["source_id"] not in allowance:
                    allowance[row["source_id"]] = await hourly_budget_left(
                        pool, row["source_id"], policy, now
                    )
                if allowance[row["source_id"]] <= 0:
                    blocked.add(row["source_id"])
                    continue
                allowance[row["source_id"]] -= 1
                result = await acquire_fulltext(
                    row["url"],
                    policy,
                    summary=row["summary"],
                    fetcher=fetcher,
                    render=render,
                    api=api,
                )
            await store_result(pool, row["id"], row["news_item_id"], result, policy, now)
            counts[result.state] += 1
            processed += 1
            if result.state == "failed" and result.note and len(errors) < 10:
                errors.append(f"{row['url']}: {result.note}")
        if processed >= limit or len(blocked) == blocked_before:
            break
    if errors:
        counts_detail: dict[str, Any] = {**counts, "errors": errors}
        return counts_detail
    return dict(counts)
