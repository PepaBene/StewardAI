"""The news pipeline job (`news_ingest`): ingest -> normalise -> dedupe and cluster -> link ->
classify -> corroborate -> route.

1. Every enabled source in the news_sources config is read (a source added to the config is
   read on the next run; the `source` table mirrors the registry).
2. Each item gets a canonical URL, its outlet's tier, and a freshness check; blocked outlets,
   stale items, excluded tier-3 items and items that match no holding are dropped, as is any
   URL already stored.
3. The item joins the story cluster of a syndicated copy, or starts a new one.
4. New items are classified (EDGAR filings by rule, the rest by the model, 50 per call).
5. Each touched story, and each unconfirmed story still in its re-check window, is assessed:
   event type and severity (event rules config), then status under the corroboration rules.
   Unconfirmed stories at severity 2 or above get a corroboration search.
6. Confirmed stories that a notification rule matches become alerts; the due ones are sent.
"""

import datetime as dt
import json
import logging
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import httpx

from steward import __version__, budget_guard
from steward.ai import AINotConfigured, Usage, create_client
from steward.config.event_rules import EVENT_DESCRIPTIONS, EventRulesConfig, with_default_services
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import EscalationSettings, JobsConfig, NewsSettings
from steward.config.news_sources import (
    AGGREGATOR_TYPES,
    PRIMARY_COUNTRY,
    REGULATORY_COUNTRY,
    GoogleNewsLocale,
    NewsSource,
    NewsSourcesConfig,
)
from steward.config.notifications import NotificationsConfig
from steward.config.prompts import load_prompts
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig
from steward.config.verification import TrustSettings, VerificationConfig
from steward.documents.fetch import FetchError, NotModified
from steward.jobs import queue
from steward.jobs.runner import JobContext, create_job
from steward.llm.cache import ResultCache
from steward.llm.client import create_llm
from steward.llm.routing import routing_client
from steward.news.classify import SYSTEM_PROMPT as CLASSIFICATION_PROMPT
from steward.news.classify import (
    Candidate,
    ClassificationError,
    Classifier,
    ClassifyItem,
    ClaudeClassifier,
    HoldingContext,
    Label,
)
from steward.news.cluster import ClusterMember, Fingerprint, best_cluster, simhash_cluster
from steward.news.corroborate import (
    SEARCH_SYSTEM,
    ClaudeCorroborator,
    Corroborator,
    OtherStory,
    SearchFinding,
    SearchRequest,
    decide,
)
from steward.news.dedupe import (
    DedupeResult,
    group_by_embedding,
    group_by_simhash,
    merge_story_clusters,
    pending_embedding,
    recent_vectors,
    store_embeddings,
)
from steward.news.embeddings import (
    Embedder,
    EmbeddingError,
    GeminiEmbedder,
    embed_text,
    estimate_tokens,
)
from steward.news.escalation import escalation_due, queue_escalation
from steward.news.evidence import (
    Basis,
    evidence_kind,
    indirect_only,
    is_correction,
    lifecycle_for,
    matching_topics,
)
from steward.news.feeds import (
    HoldingFeedInfo,
    RawItem,
    collect_alpha_vantage_news,
    collect_edgar,
    collect_finnhub,
    collect_google_news,
    collect_page,
    collect_rss_conditional,
)
from steward.news.fulltext import purge_expired, run_fulltext, store_feed_text
from steward.news.language import resolve_language
from steward.news.link import HoldingTerms, Linker, holding_terms, short_name
from steward.news.normalize import canonical_url, host_of
from steward.news.primary_sources import (
    collect_cninfo,
    collect_dart,
    collect_gazette,
    collect_hkex,
    collect_stored_filings,
    collect_tdnet,
)
from steward.news.provenance import backfill_provenance
from steward.news.regulatory import MAX_HITS as FTS_MAX_HITS
from steward.news.regulatory import FtsResult, collect_edgar_fts, collect_regulatory
from steward.news.simhash import item_hash, to_signed, to_unsigned
from steward.news.translate import (
    BATCH_SIZE,
    LlmTranslator,
    TranslateItem,
    TranslationError,
    usable_model,
)
from steward.news.translate import SYSTEM_PROMPT as TRANSLATION_PROMPT
from steward.news.wire_sources import collect_gdelt, collect_news_sitemap
from steward.notify.alerts import create_alerts, dispatch_due
from steward.notify.channels import Channel, channels_from_settings
from steward.notify.route import AlertRequest
from steward.research.polite import PoliteFetcher, build_fetcher
from steward.settings import get_settings
from steward.sources.documents import link_public_document
from steward.sources.edgar import EdgarClient
from steward.verify.trust import TrustTable, load_trust

logger = logging.getLogger(__name__)


@dataclass
class NewsDeps:
    http: httpx.AsyncClient
    edgar: EdgarClient | None = None
    finnhub_key: str | None = None
    alpha_vantage_key: str | None = None
    dart_key: str | None = None
    classifier: Classifier | None = None
    corroborator: Corroborator | None = None
    channels: dict[str, Channel] = field(default_factory=dict)
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC)
    app_url: str | None = None
    # Fetches article pages for the full-text step; None skips the step.
    fetcher: PoliteFetcher | None = None
    # Translation of non-English wire items and their embeddings; None skips the step.
    translator: LlmTranslator | None = None
    embedder: Embedder | None = None


@dataclass(frozen=True)
class NewsConfig:
    sources: NewsSourcesConfig
    event_rules: EventRulesConfig
    notifications: NotificationsConfig
    notifications_version_id: int | None
    profiles: HoldingProfilesConfig
    theses: ThesesConfig
    news: NewsSettings
    version_ids: tuple[int, ...]
    escalation: EscalationSettings = field(default_factory=EscalationSettings)
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    trust: TrustTable = field(default_factory=lambda: TrustTable({}, TrustSettings()))


async def load_news_config(ctx: JobContext) -> NewsConfig:
    pool = ctx.pool
    sources = await load_active(pool, "news_sources", NewsSourcesConfig)
    rules = await load_active(pool, "event_rules", EventRulesConfig)
    notifications = await load_active(pool, "notifications", NotificationsConfig)
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    theses = await load_active(pool, "theses", ThesesConfig)
    jobs = await load_active(pool, "jobs", JobsConfig)
    verification = await load_active(pool, "verification", VerificationConfig)
    stored = (sources, rules, notifications, profiles, theses, jobs, verification)
    for s in stored:
        await ctx.record_config_version(s.id if s else None)
    verification_body = verification.body if verification else VerificationConfig()
    return NewsConfig(
        sources=sources.body if sources else NewsSourcesConfig(),
        event_rules=with_default_services(rules.body) if rules else EventRulesConfig(),
        notifications=notifications.body if notifications else NotificationsConfig(),
        notifications_version_id=notifications.id if notifications else None,
        profiles=profiles.body if profiles else HoldingProfilesConfig(),
        theses=theses.body if theses else ThesesConfig(),
        news=(jobs.body if jobs else JobsConfig()).news,
        escalation=(jobs.body if jobs else JobsConfig()).escalation,
        version_ids=tuple(s.id for s in stored if s is not None),
        verification=verification_body,
        trust=await load_trust(pool, verification_body.trust),
    )


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------


@dataclass
class Holdings:
    terms: list[HoldingTerms]
    feed_info: list[HoldingFeedInfo]
    context: list[HoldingContext]
    ir_domains: set[str]

    def name(self, ticker: str | None) -> str:
        return next((t.display_name() for t in self.terms if t.ticker == ticker), ticker or "")


FUND_CONSTITUENTS = 10


async def fund_constituents(pool: asyncpg.Pool, isins: Sequence[str]) -> dict[str, list[str]]:
    """The names of the largest equity constituents of each held fund, from its newest holdings
    snapshot, so news about a top constituent reaches the fund indirectly."""
    rows = await pool.fetch(
        "select s.fund_isin, h.name from public.etf_snapshot s"
        " join lateral (select name from public.etf_holding"
        "   where snapshot_id = s.id and asset_class = 'equity' and name is not null"
        "   order by weight desc limit $2) h on true"
        " where s.fund_isin = any($1::text[]) and s.as_of = (select max(as_of)"
        "   from public.etf_snapshot where fund_isin = s.fund_isin)",
        list(isins),
        FUND_CONSTITUENTS,
    )
    found: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        name = short_name(r["name"])
        if len(name) >= 3 and name not in found[r["fund_isin"]]:
            found[r["fund_isin"]].append(name)
    return found


async def load_holdings(pool: asyncpg.Pool, config: NewsConfig) -> Holdings:
    rows = await pool.fetch(
        "select ticker from public.position_snapshot where snapshot_date = coalesce("
        " (select max(snapshot_date) from public.account_snapshot),"
        " (select max(snapshot_date) from public.position_snapshot))"
    )
    tickers = sorted(
        {r["ticker"] for r in rows}
        | {t.ticker for t in config.theses.theses}
        | {p.ticker for p in config.profiles.profiles}
    )
    instruments = {
        r["t212_ticker"]: r
        for r in await pool.fetch(
            "select i.t212_ticker, i.name, i.isin, i.cik, i.home_country,"
            " (select listing_ticker from public.instrument_identifier x"
            "   where x.ticker = i.t212_ticker and x.exch_code = 'LN'"
            "   order by (x.figi = x.composite_figi) desc limit 1) as tidm"
            " from public.instrument i where i.t212_ticker = any($1::text[])",
            tickers,
        )
    }
    # Names from reference data: GLEIF legal and other names, subsidiaries, Wikidata
    # aliases, and every listing's ticker.
    known_aliases: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in await pool.fetch(
        "select ticker, alias, kind from public.entity_alias where ticker = any($1::text[])"
        " order by ticker, kind, alias",
        tickers,
    ):
        known_aliases[row["ticker"]].append((row["alias"], row["kind"]))
    constituents = await fund_constituents(
        pool, [r["isin"] for r in instruments.values() if r["isin"]]
    )
    terms, feed_info, context = [], [], []
    ir_domains: set[str] = set()
    for ticker in tickers:
        profile = config.profiles.for_ticker(ticker)
        instrument = instruments.get(ticker)
        t = holding_terms(
            ticker,
            profile,
            instrument["name"] if instrument else None,
            instrument["isin"] if instrument else None,
        )
        for alias, kind in known_aliases.get(ticker, []):
            target = t.symbols if kind == "listing_ticker" else t.aliases
            if alias not in target and alias not in t.names:
                target.append(alias)
        if instrument and instrument["isin"]:
            t.relations.extend(("constituent", n) for n in constituents.get(instrument["isin"], []))
        terms.append(t)
        cik = (profile.sec_cik if profile else None) or (instrument["cik"] if instrument else None)
        names = tuple(dict.fromkeys([*t.names[1:2], *t.names[:1], *t.aliases]))
        feed_info.append(
            HoldingFeedInfo(
                ticker,
                names,
                cik,
                t.symbols[0] if t.symbols else None,
                isin=instrument["isin"] if instrument else None,
                home_country=instrument["home_country"] if instrument else None,
                tidm=instrument["tidm"] if instrument else None,
                regulatory_urls=tuple(str(u) for u in profile.regulatory_news_urls)
                if profile
                else (),
                topics=tuple(profile.watch_topics) if profile else (),
                alpha_symbol=profile.alpha_vantage_symbol if profile else None,
                listing_codes=tuple(dict.fromkeys([ticker.split("_")[0], *t.symbols])),
            )
        )
        thesis = next((th for th in config.theses.theses if th.ticker == ticker), None)
        context.append(
            HoldingContext(
                ticker=ticker,
                name=t.display_name(),
                why_owned=thesis.why_owned if thesis else "",
                kpis=tuple((k.id, k.description or k.metric.replace("_", " ")) for k in thesis.kpis)
                if thesis
                else (),
                exit_conditions=tuple(thesis.exit_conditions) if thesis else (),
                relations=tuple(term for _, term in t.relations),
                watch_topics=tuple(t.topics),
            )
        )
        if profile:
            ir_domains |= {d.removeprefix("www.") for d in profile.allowed_domains()}
    return Holdings(terms, feed_info, context, ir_domains)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


async def sync_sources(pool: asyncpg.Pool, sources: Sequence[NewsSource]) -> None:
    for s in sources:
        await pool.execute(
            "insert into public.source (id, name, tier, type, url, enabled, tickers, region,"
            " language, paywall, state_media) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,"
            " $11) on conflict (id) do update set name = excluded.name, tier = excluded.tier,"
            " type = excluded.type, url = excluded.url, enabled = excluded.enabled,"
            " tickers = excluded.tickers, region = excluded.region,"
            " language = excluded.language, paywall = excluded.paywall,"
            " state_media = excluded.state_media,"
            # A source the owner switches back on starts with a clean breaker.
            " consecutive_failures = case when excluded.enabled and not source.enabled"
            " then 0 else source.consecutive_failures end,"
            " backoff_until = case when excluded.enabled and not source.enabled"
            " then null else source.backoff_until end",
            s.id,
            s.name,
            s.tier,
            s.type,
            str(s.url) if s.url else None,
            s.enabled,
            list(s.tickers),
            s.region,
            s.language,
            s.paywall,
            s.state_media,
        )
    await pool.execute(
        "update public.source set enabled = false where not (id = any($1::text[]))",
        [s.id for s in sources],
    )


async def run_dedupe(
    ctx: JobContext,
    embedder: Embedder | None,
    settings: NewsSettings,
    now: dt.datetime,
) -> tuple[DedupeResult, set[int]]:
    """SimHash groups, then (with an embedder) embedding groups and safe cluster merges.
    Returns the result and the cluster ids that gained an item."""
    pool = ctx.pool
    since = now - dt.timedelta(hours=72)
    result = DedupeResult()
    result.simhash_groups = await group_by_simhash(pool, ctx.job_id, since)
    kept: set[int] = set()
    if embedder is None or settings.embedding_per_run == 0:
        result.skipped = "no embedder" if embedder is None else "embedding_per_run is 0"
        return result, kept
    rows = await pending_embedding(pool, settings.embedding_per_run, since)
    if not rows:
        return result, kept
    texts = [embed_text(r["headline"], r["summary"], r["language"]) for r in rows]
    try:
        await budget_guard.before_request(ctx, embedder.model, can_switch_provider=True)
        vectors = await embedder.embed(texts)
    except (EmbeddingError, budget_guard.BudgetExceeded) as exc:
        # Degrade: the wire is still stored, grouped by SimHash and searchable.
        result.skipped = f"{type(exc).__name__}: {exc}"[:300]
        return result, kept
    ids = [r["id"] for r in rows]
    await store_embeddings(pool, embedder.model, ids, vectors)
    result.embedded = len(ids)
    result.embedding_tokens = estimate_tokens(texts)
    window = settings.duplicate_window_hours
    all_vectors = await recent_vectors(pool, embedder.model, now - dt.timedelta(hours=window * 2))
    groups = await group_by_embedding(
        pool, set(ids), all_vectors, settings.embedding_duplicate_threshold, window
    )
    result.embedding_groups = len(groups)
    result.merged_clusters = await merge_story_clusters(pool, groups, set(ids))
    kept = {keep for _, keep in result.merged_clusters}
    return result, kept


async def translate_pending(
    ctx: JobContext, translator: LlmTranslator, settings: NewsSettings
) -> tuple[dict[str, Any], Usage]:
    """Translate the newest non-English wire items (linked first) that have no translation."""
    rows = await ctx.pool.fetch(
        "select id, headline, summary, language from public.news_article"
        " where translation_state is null order by linked desc, published_at desc limit $1",
        settings.translation_per_run,
    )
    detail: dict[str, Any] = {"pending": len(rows), "done": 0, "failed": 0}
    usage = Usage(model=translator.model)
    for start in range(0, len(rows), BATCH_SIZE):
        chunk = rows[start : start + BATCH_SIZE]
        items = [
            TranslateItem(i, r["headline"], r["summary"], r["language"])
            for i, r in enumerate(chunk)
        ]
        try:
            done, used = await translator.translate(items)
        except (TranslationError, budget_guard.BudgetExceeded) as exc:
            # Degrade: the originals stay searchable and the rest wait for the next run.
            detail["error"] = str(exc)[:300]
            failed_usage = getattr(exc, "usage", None)
            if failed_usage is not None:
                usage.input_tokens += failed_usage.input_tokens
                usage.output_tokens += failed_usage.output_tokens
            break
        usage.input_tokens += used.input_tokens
        usage.output_tokens += used.output_tokens
        for i, r in enumerate(chunk):
            got = done.get(i)
            if got is None:
                await ctx.pool.execute(
                    "update public.news_article set translation_state = 'failed' where id = $1",
                    r["id"],
                )
                detail["failed"] += 1
                continue
            await ctx.pool.execute(
                "update public.news_article set title_en = $2, summary_en = $3,"
                " translation_state = 'done' where id = $1",
                r["id"],
                got.headline_en,
                got.summary_en,
            )
            detail["done"] += 1
    return detail, usage


BREAKER_AFTER = 3
BACKOFF_BASE = dt.timedelta(minutes=10)
BACKOFF_MAX = dt.timedelta(hours=6)


def next_backoff(failures: int, now: dt.datetime) -> dt.datetime | None:
    """When a source with `failures` consecutive failures is read again: not at all delayed for
    the first two, then 10 minutes doubling per further failure up to 6 hours."""
    if failures < BREAKER_AFTER:
        return None
    doublings = min(failures - BREAKER_AFTER, 10)
    return now + min(BACKOFF_BASE * (1 << doublings), BACKOFF_MAX)


def breaker_open(backoff_until: dt.datetime | None, now: dt.datetime) -> bool:
    return backoff_until is not None and backoff_until > now


@dataclass
class SourceRun:
    """What one source's read did in this run (the non-item half of a `news_source_run` row)."""

    latency_ms: int | None = None
    error: str | None = None
    not_modified: bool = False
    skipped: bool = False


async def record_source_runs(
    pool: asyncpg.Pool,
    job_id: int,
    now: dt.datetime,
    runs: dict[str, SourceRun],
    stats: "IngestStats",
) -> None:
    """One `news_source_run` row per source read (or skipped by its breaker) in this run."""
    rows = []
    for source_id, run in runs.items():
        tally = stats.per_source.get(source_id) or SourceTally()
        drops = dict(tally.drops)
        if run.not_modified:
            drops["not_modified"] = 1
        if run.skipped:
            drops["skipped_backoff"] = 1
        rows.append(
            (
                job_id,
                source_id,
                now,
                run.latency_ms,
                tally.fetched,
                tally.stored_linked,
                tally.stored_wire,
                json.dumps(drops),
                run.error,
            )
        )
    if rows:
        await pool.executemany(
            "insert into public.news_source_run (job_id, source_id, started_at, latency_ms,"
            " fetched, stored_linked, stored_wire, drops, error)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)"
            " on conflict (job_id, source_id) do nothing",
            rows,
        )


async def undated_baseline(
    pool: asyncpg.Pool, source_id: str, items: list[RawItem]
) -> tuple[list[RawItem], list[str]]:
    """Undated feed items are stored as first seen now, so they need the baseline a page
    source has: the undated links on a feed's first read are recorded, never stored as news
    (a new feed would otherwise flood alerts with old articles), and later reads keep only
    undated links not seen before. Dated items pass through; the freshness window judges them."""
    key = f"news_rss_undated:{source_id}"
    stored = await pool.fetchval("select value from public.sync_state where key = $1", key)
    previous = set(json.loads(stored)["links"]) if stored else None
    undated = [canonical_url(i.url) for i in items if i.published_at is None]
    if previous is None or undated:
        await pool.execute(
            "insert into public.sync_state (key, value) values ($1, $2::jsonb)"
            " on conflict (key) do update set value = excluded.value",
            key,
            json.dumps({"links": list(dict.fromkeys(undated))[:1000]}),
        )
    kept = [
        i
        for i in items
        if i.published_at is not None
        or (previous is not None and canonical_url(i.url) not in previous)
    ]
    notes = (
        [f"first read: {len(undated)} undated links recorded as baseline"]
        if previous is None and undated
        else []
    )
    return kept, notes


async def collect_source(
    source: NewsSource,
    deps: NewsDeps,
    holdings: Holdings,
    since: dt.datetime,
    pool: asyncpg.Pool,
    freshness_days: int,
    rules: EventRulesConfig | None = None,
    google_news_locale: GoogleNewsLocale | None = None,
    fts_max_hits: int = FTS_MAX_HITS,
) -> tuple[list[RawItem], list[str]]:
    rules = rules or EventRulesConfig()
    scoped = tuple(source.tickers)
    feeds = [h for h in holdings.feed_info if not scoped or h.ticker in scoped]
    if source.type == "rss":
        validators = await pool.fetchrow(
            "select etag, http_last_modified from public.source where id = $1", source.id
        )
        rss_items, etag, modified = await collect_rss_conditional(
            deps.http,
            source.id,
            str(source.url),
            scoped,
            validators["etag"] if validators else None,
            validators["http_last_modified"] if validators else None,
        )
        await pool.execute(
            "update public.source set etag = $2, http_last_modified = $3 where id = $1",
            source.id,
            etag,
            modified,
        )
        return await undated_baseline(pool, source.id, rss_items)
    if source.type == "news_sitemap":
        return (
            await collect_news_sitemap(
                deps.http,
                source.id,
                str(source.url),
                region=source.region,
                default_language=source.language,
            ),
            [],
        )
    if source.type == "gdelt":
        gdelt = await collect_gdelt(deps.http, source.id, feeds, freshness_days)
        return gdelt.items, gdelt.errors
    if source.type == "page":
        key = f"news_page:{source.id}"
        stored = await pool.fetchval("select value from public.sync_state where key = $1", key)
        previous = set(json.loads(stored)["links"]) if stored else None
        items, current = await collect_page(
            deps.http, source.id, str(source.url), scoped, previous, deps.now()
        )
        await pool.execute(
            "insert into public.sync_state (key, value) values ($1, $2::jsonb)"
            " on conflict (key) do update set value = excluded.value",
            key,
            json.dumps({"links": current[:1000]}),
        )
        return items, [] if previous is not None else ["first read: links recorded as baseline"]
    if source.type == "edgar":
        if deps.edgar is None:
            return [], ["SEC_USER_AGENT is not set"]
        result = await collect_edgar(deps.edgar, source.id, feeds, since)
        return result.items, result.errors
    if source.type == "finnhub":
        if not deps.finnhub_key:
            return [], ["FINNHUB_API_KEY is not set"]
        result = await collect_finnhub(
            deps.http, deps.finnhub_key, source.id, feeds, since, deps.now().date()
        )
        return result.items, result.errors
    if source.type == "alpha_vantage":
        if not deps.alpha_vantage_key:
            return [], ["ALPHA_VANTAGE_API_KEY is not set"]
        return await collect_alpha_vantage_news(
            deps.http, deps.alpha_vantage_key, source.id, feeds, pool
        )
    if source.type in PRIMARY_COUNTRY or source.type == "filings":
        today = deps.now().date()
        if source.type == "tdnet":
            primary = await collect_tdnet(deps.http, source.id, feeds, rules, today)
        elif source.type == "hkex":
            primary = await collect_hkex(deps.http, source.id, feeds, rules, today)
        elif source.type == "cninfo":
            primary = await collect_cninfo(deps.http, source.id, feeds, rules, today)
        elif source.type == "gazette":
            primary = await collect_gazette(deps.http, source.id, feeds, rules, today)
        elif source.type == "dart":
            if not deps.dart_key:
                return [], ["DART_API_KEY is not set"]
            primary = await collect_dart(deps.http, deps.dart_key, source.id, feeds, rules, today)
        else:
            primary = await collect_stored_filings(pool, source.id, feeds, rules, since)
        return primary.items, primary.errors
    if source.type in REGULATORY_COUNTRY:
        result = await collect_regulatory(
            deps.http,
            source.type,
            source.id,
            feeds,
            rules,
            REGULATORY_COUNTRY[source.type],
        )
        return result.items, result.errors
    if source.type == "edgar_fts":
        if deps.edgar is None:
            return [], ["SEC_USER_AGENT is not set"]
        fts = await collect_edgar_fts(
            deps.http,
            deps.edgar.headers,
            source.id,
            feeds,
            since.date(),
            deps.now().date(),
            fts_max_hits,
        )
        await record_filing_hits(pool, fts)
        return fts.items, fts.errors
    result = await collect_google_news(
        deps.http, source.id, feeds, freshness_days, google_news_locale
    )
    return result.items, result.errors


async def record_filing_hits(pool: asyncpg.Pool, fts: FtsResult) -> None:
    """Store topic hits and filing-review candidates without inferring counterparties.

    EDGAR full-text results do not include the cited passage. A filer and a holding appearing
    near "customer" or "supplier" cannot establish direction or even a relationship, so those
    hits remain unlinked review candidates until a fetched passage is resolved.
    """
    for ticker, topic, hit in fts.topic_hits:
        await pool.execute(
            "insert into public.filing_hit (ticker, kind, query, matched_term, accession, cik,"
            " filer_name, form, filed_at, file, url) values ($1, 'topic', $2, $2, $3, $4, $5,"
            " $6, $7, $8, $9) on conflict do nothing",
            ticker,
            topic,
            hit.accession,
            hit.cik,
            hit.filer_name,
            hit.form,
            hit.filed_at,
            hit.file,
            hit.url,
        )
    for ticker, kind, keyword, hit in fts.relations:
        await pool.execute(
            "insert into public.filing_hit (ticker, kind, query, matched_term, accession,"
            " cik, filer_name, form, filed_at, file, url) values ($1, $2, $3, $4, $5, $6,"
            " $7, $8, $9, $10, $11) on conflict do nothing",
            ticker,
            kind,  # always 'relationship_candidate': no role is inferred from the keyword
            f"name + {keyword}",
            keyword,
            hit.accession,
            hit.cik,
            hit.filer_name,
            hit.form,
            hit.filed_at,
            hit.file,
            hit.url,
        )


def item_tier(
    item: RawItem, source: NewsSource, config: NewsSourcesConfig, ir_domains: set[str]
) -> int:
    host = item.outlet_host
    if host and any(host == d or host.endswith(f".{d}") for d in ir_domains):
        return 1
    configured = config.domain_tier(host)
    if configured is not None:
        return configured
    if source.type == "ai_search":
        return 3
    if source.type in AGGREGATOR_TYPES:
        return config.unknown_outlet_tier
    return source.tier


@dataclass
class SourceTally:
    """One source's items in one run, by outcome (the `news_source_run` row)."""

    fetched: int = 0
    stored_linked: int = 0
    stored_wire: int = 0
    drops: Counter[str] = field(default_factory=Counter)


@dataclass
class IngestStats:
    fetched: int = 0
    stored: int = 0
    duplicate: int = 0
    stale: int = 0
    blocked: int = 0
    tier3_dropped: int = 0
    unlinked: int = 0
    new_clusters: int = 0
    wire_only: int = 0
    touched_clusters: set[int] = field(default_factory=set)
    per_source: dict[str, SourceTally] = field(default_factory=lambda: defaultdict(SourceTally))

    def detail(self) -> dict[str, int]:
        return {
            "fetched": self.fetched,
            "stored": self.stored,
            "duplicate_url": self.duplicate,
            "stale": self.stale,
            "blocked": self.blocked,
            "tier3_dropped": self.tier3_dropped,
            "unlinked": self.unlinked,
            "new_clusters": self.new_clusters,
            "wire_only": self.wire_only,
        }


WIRE_UPSERT = (
    "insert into public.news_article (url, source_id, outlet, outlet_name, region, tier,"
    " published_at, first_seen_at, headline, summary, language, linked, news_item_id, job_id,"
    " fetch_state, simhash, translation_state, topics) values ($1, $2, $3, $4, $5, $6, $7, $8,"
    " $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)"
    " on conflict (url) do update set linked = news_article.linked or excluded.linked,"
    " news_item_id = coalesce(news_article.news_item_id, excluded.news_item_id),"
    " topics = array(select distinct t from unnest(news_article.topics || excluded.topics) t)"
)


def wire_args(
    url: str,
    item: RawItem,
    source: NewsSource,
    tier: int,
    published: dt.datetime,
    now: dt.datetime,
    job_id: int,
    *,
    news_item_id: int | None = None,
    topics: Sequence[str] = (),
) -> tuple[Any, ...]:
    """The `WIRE_UPSERT` arguments of one kept item."""
    language = item_language(item, source)
    digest = item_hash(item.headline, item.summary)
    return (
        url,
        source.id,
        item.outlet_host,
        item.outlet_name,
        item.region or source.region,
        tier,
        published,
        now,
        item.headline,
        item.summary,
        language,
        news_item_id is not None,
        news_item_id,
        job_id,
        "summary" if item.summary else "headline",
        to_signed(digest) if digest is not None else None,
        "not_needed" if language in (None, "en") else None,
        list(topics),
    )


def item_language(item: RawItem, source: NewsSource) -> str | None:
    """The language stored for an item: detected from its text when the detector is confident
    (a feed's tag is often only its site's default), else the feed's or the source's."""
    return resolve_language(item.language or source.language, item.headline, item.summary)


async def recent_members(pool: asyncpg.Pool, since: dt.datetime) -> list[ClusterMember]:
    rows = await pool.fetch(
        "select i.cluster_id, i.headline, i.summary, i.outlet_name, a.simhash"
        " from public.news_item i left join public.news_article a on a.news_item_id = i.id"
        " where i.cluster_id is not null and i.first_seen_at >= $1",
        since,
    )
    return [
        ClusterMember(
            r["cluster_id"],
            Fingerprint.of(r["headline"], r["summary"], (r["outlet_name"] or "",)),
            to_unsigned(r["simhash"]) if r["simhash"] is not None else None,
        )
        for r in rows
    ]


async def store_items(
    pool: asyncpg.Pool,
    items: Sequence[tuple[RawItem, NewsSource]],
    config: NewsConfig,
    holdings: Holdings,
    now: dt.datetime,
    job_id: int,
) -> IngestStats:
    stats = IngestStats(fetched=len(items))
    since = now - dt.timedelta(hours=config.sources.freshness_hours)
    linker = Linker(holdings.terms)
    by_url: dict[str, tuple[RawItem, NewsSource]] = {}
    for item, source in items:
        stats.per_source[source.id].fetched += 1
        canonical = canonical_url(item.url)
        if canonical in by_url:
            stats.per_source[source.id].drops["duplicate_url"] += 1
        else:
            by_url[canonical] = (item, source)
    stats.duplicate += len(items) - len(by_url)
    existing = {
        r["url"]
        for r in await pool.fetch(
            "select url from public.news_item where url = any($1::text[])", list(by_url)
        )
    }
    on_wire = {
        r["url"]
        for r in await pool.fetch(
            "select url from public.news_article where url = any($1::text[])", list(by_url)
        )
    }
    members = await recent_members(pool, since)
    wire_rows: list[tuple[Any, ...]] = []
    # (url, item, source, keep) of kept items whose feed carried the article text (rung 1).
    feed_texts: list[tuple[str, RawItem, NewsSource, bool]] = []
    # Oldest first, so the original of a syndicated story starts the cluster.
    ordered = sorted(by_url.items(), key=lambda kv: kv[1][0].published_at or now)
    for url, (item, source) in ordered:
        tally = stats.per_source[source.id]
        if url in existing:
            stats.duplicate += 1
            tally.drops["duplicate_url"] += 1
            continue
        published = item.published_at or now
        if published < since or published > now + dt.timedelta(hours=6):
            stats.stale += 1
            tally.drops["stale"] += 1
            continue
        if config.sources.blocked(item.outlet_host) or config.sources.blocked(host_of(url)):
            stats.blocked += 1
            tally.drops["blocked"] += 1
            continue
        if source.type in AGGREGATOR_TYPES and not config.sources.allowed_aggregated(
            item.outlet_host
        ):
            stats.blocked += 1
            tally.drops["aggregator_not_allowed"] += 1
            continue
        tier = item_tier(item, source, config.sources, holdings.ir_domains)
        text = f"{item.headline}\n{item.summary or ''}"
        topics = matching_topics(text, config.sources.portfolio_topics)
        # Everything not dropped above stays on the wire; only linked items of a tier that may
        # raise stories go on to clustering, classification and alerts.
        if tier == 3 and not config.sources.include_tier3:
            stats.tier3_dropped += 1
            stats.wire_only += 1
            tally.drops["tier3_wire_only"] += 1
            if url not in on_wire:
                wire_rows.append(
                    wire_args(url, item, source, tier, published, now, job_id, topics=topics)
                )
                tally.stored_wire += 1
                feed_texts.append((url, item, source, False))
            continue
        links = linker.link(text, item.tickers)
        if not links and not source.macro:
            stats.unlinked += 1
            stats.wire_only += 1
            tally.drops["unmatched_wire_only"] += 1
            if url not in on_wire:
                wire_rows.append(
                    wire_args(url, item, source, tier, published, now, job_id, topics=topics)
                )
                tally.stored_wire += 1
                feed_texts.append((url, item, source, False))
            continue
        outlet_host = item.outlet_host or host_of(url)
        kind = evidence_kind(source, tier, outlet_host, holdings.ir_domains, config.sources)
        fingerprint = Fingerprint.of(item.headline, item.summary, (item.outlet_name or "",))
        async with pool.acquire() as conn, conn.transaction():
            digest = item_hash(item.headline, item.summary)
            cluster_id = best_cluster(fingerprint, members) or simhash_cluster(digest, members)
            if cluster_id is None:
                cluster_id = await conn.fetchval(
                    "insert into public.story_cluster (headline, headline_key, best_tier,"
                    " first_seen, last_seen, config_version_ids)"
                    " values ($1, $2, $3, $4, $4, $5) returning id",
                    item.headline,
                    " ".join(sorted(fingerprint.headline)),
                    tier,
                    now,
                    list(config.version_ids),
                )
                stats.new_clusters += 1
            item_id = await conn.fetchval(
                "insert into public.news_item (url, source_id, outlet, outlet_name, tier,"
                " published_at, first_seen_at, headline, summary, language, cluster_id,"
                " tickers, links, event_type, affected_ticker, rumour, relevant,"
                " classified_at, classified_by, job_id, sentiment_score, sentiment_relevance,"
                " evidence_kind, independence_key, is_correction)"
                " values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14,"
                " $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25)"
                " on conflict (url) do nothing returning id",
                url,
                source.id,
                item.outlet_host,
                item.outlet_name,
                tier,
                published,
                now,
                item.headline,
                item.summary,
                item_language(item, source),
                cluster_id,
                [link.ticker for link in links],
                json.dumps([link.as_json() for link in links]),
                item.event_type,
                links[0].ticker if item.event_type and links else None,
                False if item.event_type else None,
                True if item.event_type else None,
                now if item.event_type else None,
                f"rules:{source.type}" if item.event_type else None,
                job_id,
                item.sentiment_score,
                item.sentiment_relevance,
                kind,
                config.sources.independence_key(outlet_host) or None,
                is_correction(item.headline),
            )
            if item_id is None:
                stats.duplicate += 1
                tally.drops["duplicate_url"] += 1
                continue
            await conn.execute(
                WIRE_UPSERT,
                *wire_args(
                    url,
                    item,
                    source,
                    tier,
                    published,
                    now,
                    job_id,
                    news_item_id=item_id,
                    topics=topics,
                ),
            )
            evidence_text = json.dumps(
                {"headline": item.headline, "summary": item.summary},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
            await link_public_document(
                conn,
                regulator=item.outlet_host or host_of(url) or "publisher",
                external_id=url,
                stable_url=url,
                document_type="news_item",
                content=evidence_text,
                known_at=now,
                published_at=published,
                domain_table="news_item",
                record_key=item_id,
                quote=item.headline,
            )
            await refresh_cluster_sources(conn, cluster_id, now, config.sources.independence_key)
        members.append(ClusterMember(cluster_id, fingerprint, digest))
        stats.stored += 1
        tally.stored_linked += 1
        stats.touched_clusters.add(cluster_id)
        feed_texts.append((url, item, source, True))
    if wire_rows:
        await pool.executemany(WIRE_UPSERT, wire_rows)
    for url, item, source, keep in feed_texts:
        if item.content:
            await store_feed_text(
                pool, url, item.content, item.summary, source.fetch, now, keep=keep
            )
    return stats


async def refresh_cluster_sources(
    conn: asyncpg.Connection,
    cluster_id: int,
    now: dt.datetime,
    key_of: Callable[[str | None], str] | None = None,
) -> None:
    """Recompute a story's last-seen time, best tier and number of independent sources.
    `key_of` maps an outlet to its independence group (outlets that republish one wire count
    once); without it every outlet counts."""
    rows = await conn.fetch(
        "select outlet, url, tier from public.news_item where cluster_id = $1", cluster_id
    )
    sources = {
        (key_of(r["outlet"]) if key_of else r["outlet"]) if r["outlet"] else r["url"] for r in rows
    }
    await conn.execute(
        "update public.story_cluster set last_seen = greatest(last_seen, $2),"
        " best_tier = $3, source_count = $4 where id = $1",
        cluster_id,
        now,
        min((r["tier"] for r in rows), default=3),
        len(sources),
    )


async def classify_pending(
    ctx: JobContext,
    classifier: Classifier,
    holdings: Holdings,
    settings: NewsSettings,
    now: dt.datetime,
) -> tuple[set[int], dict[str, Any]]:
    """Classify stored items that have no label yet. Returns the clusters touched."""
    rows = await ctx.pool.fetch(
        "select id, headline, summary, outlet, tier, published_at, links, cluster_id"
        " from public.news_item where classified_at is null"
        " order by first_seen_at limit $1",
        settings.max_items_per_run,
    )
    touched: set[int] = set()
    detail: dict[str, Any] = {"pending": len(rows), "labelled": 0, "missing": 0, "errors": []}
    size = settings.classification_batch_size
    prepared = []
    for start in range(0, len(rows), size):
        chunk = rows[start : start + size]
        items = [
            ClassifyItem(
                index=i,
                headline=r["headline"],
                summary=r["summary"],
                outlet=r["outlet"],
                tier=r["tier"],
                published_at=r["published_at"],
                candidates=tuple(
                    Candidate(link["ticker"], link["via"], link["term"])
                    for link in json.loads(r["links"])
                ),
            )
            for i, r in enumerate(chunk)
        ]
        candidates = {c.ticker for item in items for c in item.candidates}
        context = [h for h in holdings.context if h.ticker in candidates]
        prepared.append((start, chunk, items, context))
    # With `classification_use_batch` and a non-Claude model, the whole backlog goes to the
    # provider's batch API in one request set; a group it did not answer is classified directly.
    answers: list[Any] = [None] * len(prepared)
    many = getattr(classifier, "classify_many", None)
    if settings.classification_use_batch and many is not None and len(prepared) > 0:
        async with ctx.step("classify_batch") as batch_step:
            answers = await many(
                [(items, context) for _, _, items, context in prepared],
                now.date(),
                wait_minutes=settings.classification_batch_wait_minutes,
            )
            batch_step.detail["groups"] = len(prepared)
            batch_step.detail["answered"] = sum(a is not None for a in answers)
            batch_step.usage = None
    for (start, chunk, items, context), answer in zip(prepared, answers, strict=True):
        async with ctx.step(f"classify:{start // size + 1}") as step:
            try:
                if isinstance(answer, ClassificationError):
                    raise answer
                if answer is not None:
                    result, usage = answer
                else:
                    result, usage = await classifier.classify(items, context, now.date())
            except ClassificationError as exc:
                detail["errors"].append(str(exc))
                step.detail["error"] = str(exc)
                if exc.usage is not None:
                    step.usage = exc.usage.step_usage()
                continue
            step.usage = usage.step_usage()
            step.detail.update(items=len(items), missing=len(result.missing))
            for index, label in result.labels.items():
                row = chunk[index]
                await save_label(ctx.pool, row["id"], label, classifier_name(classifier), now)
                touched.add(row["cluster_id"])
            detail["labelled"] += len(result.labels)
            detail["missing"] += len(result.missing)
    return touched, detail


def classifier_name(classifier: Classifier) -> str:
    model = getattr(classifier, "_model", None)
    return str(model) if model else type(classifier).__name__


async def save_label(
    pool: asyncpg.Pool, item_id: int, label: Label, by: str, now: dt.datetime
) -> None:
    await pool.execute(
        "update public.news_item set event_type = $2, affected_ticker = $3, relevant = $4,"
        " thesis_link = $5, rumour = $6, event_date = $7, classified_at = $8,"
        " classified_by = $9 where id = $1",
        item_id,
        label.event_type,
        label.affected_ticker,
        label.relevant,
        label.thesis_link,
        label.rumour,
        label.event_date,
        now,
        by,
    )


# ---------------------------------------------------------------------------
# Assessment: severity and corroboration status per story
# ---------------------------------------------------------------------------


@dataclass
class StoryState:
    id: int
    headline: str
    summary: str | None
    ticker: str | None
    event_type: str | None
    severity: int
    rumour: bool
    recycled: bool
    relevant: bool
    thesis_link: str | None
    event_date: dt.date | None
    best_tier: int
    outlets: frozenset[str]
    published_at: dt.datetime
    first_seen: dt.datetime
    status: str
    search_rounds: int
    last_searched_at: dt.datetime | None
    finding: SearchFinding | None
    url: str
    classified: bool
    resolution: str | None = None
    duplicate_of: int | None = None
    # Evidence model (N5): kinds of the story's items, whether one is a correction notice,
    # whether the owner closed the story, whether every link to the holding is indirect, the
    # stored lifecycle, and the items behind it (for the lifecycle event).
    kinds: frozenset[str] = frozenset()
    corrected: bool = False
    closed: bool = False
    indirect_only: bool = False
    lifecycle: str = "reported"
    evidence: list[dict[str, Any]] = field(default_factory=list)


def _finding_from_json(data: Any) -> SearchFinding | None:
    if not data:
        return None
    doc = json.loads(data) if isinstance(data, str) else data
    return SearchFinding(
        supporting=tuple((u, int(t)) for u, t in doc.get("supporting", [])),
        contradicting=tuple((u, int(t)) for u, t in doc.get("contradicting", [])),
        note=doc.get("note", ""),
    )


async def load_story(
    pool: asyncpg.Pool,
    cluster_id: int,
    rules: EventRulesConfig,
    trust: TrustTable | None = None,
    key_of: Callable[[str | None], str] | None = None,
) -> StoryState | None:
    cluster = await pool.fetchrow(
        "select id, status, search_rounds, last_searched_at, corroboration, first_seen,"
        " resolution, duplicate_of, lifecycle, lifecycle_closed_at"
        " from public.story_cluster where id = $1",
        cluster_id,
    )
    items = await pool.fetch(
        "select url, headline, summary, outlet, tier, published_at, event_type,"
        " affected_ticker, relevant, thesis_link, rumour, event_date, classified_at, tickers,"
        " evidence_kind, is_correction, links"
        " from public.news_item where cluster_id = $1 order by tier, published_at, id",
        cluster_id,
    )
    if cluster is None or not items:
        return None
    labelled = [i for i in items if i["classified_at"] is not None]
    lead = labelled[0] if labelled else items[0]
    ticker = lead["affected_ticker"]
    recycled = False
    if lead["event_date"] is not None:
        age = lead["published_at"].date() - lead["event_date"]
        recycled = age > dt.timedelta(days=rules.recycled_after_days)
    return StoryState(
        id=cluster_id,
        headline=lead["headline"],
        summary=lead["summary"],
        ticker=ticker,
        event_type=lead["event_type"],
        severity=0,
        rumour=bool(lead["rumour"]),
        recycled=recycled,
        relevant=bool(lead["relevant"]) if labelled else False,
        thesis_link=lead["thesis_link"],
        event_date=lead["event_date"],
        best_tier=min(
            trust.effective_tier(i["tier"], i["outlet"]) if trust else i["tier"] for i in items
        ),
        outlets=frozenset(
            (key_of(i["outlet"]) if key_of else i["outlet"]) for i in items if i["outlet"]
        ),
        published_at=min(i["published_at"] for i in items),
        first_seen=cluster["first_seen"],
        status=cluster["status"],
        search_rounds=cluster["search_rounds"],
        last_searched_at=cluster["last_searched_at"],
        finding=_finding_from_json(cluster["corroboration"]),
        url=lead["url"],
        classified=bool(labelled),
        resolution=cluster["resolution"],
        duplicate_of=cluster["duplicate_of"],
        kinds=frozenset(i["evidence_kind"] for i in items if i["evidence_kind"]),
        corrected=any(i["is_correction"] for i in items),
        closed=cluster["lifecycle_closed_at"] is not None,
        indirect_only=ticker is not None
        and indirect_only(
            link["via"]
            for i in items
            for link in json.loads(i["links"])
            if link["ticker"] == ticker
        ),
        lifecycle=cluster["lifecycle"],
        evidence=[
            {
                "url": i["url"],
                "kind": i["evidence_kind"],
                "tier": i["tier"],
                "outlet": i["outlet"],
            }
            for i in items[:20]
        ],
    )


async def insider_cluster(
    pool: asyncpg.Pool, ticker: str, rules: EventRulesConfig, now: dt.datetime
) -> bool:
    count = await pool.fetchval(
        "select count(distinct cluster_id) from public.news_item"
        " where affected_ticker = $1 and event_type = 'insider_transaction'"
        " and published_at >= $2",
        ticker,
        now - dt.timedelta(days=rules.insider_cluster_days),
    )
    return int(count or 0) >= rules.insider_cluster_count


async def other_stories(
    pool: asyncpg.Pool,
    story: StoryState,
    window: dt.timedelta,
    trust: TrustTable | None = None,
    key_of: Callable[[str | None], str] | None = None,
) -> list[OtherStory]:
    if story.ticker is None or story.event_type is None:
        return []
    rows = await pool.fetch(
        "select c.id, array_agg(i.tier) as tiers, array_agg(i.outlet) as outlets"
        " from public.story_cluster c join public.news_item i on i.cluster_id = c.id"
        " where c.id <> $1 and c.ticker = $2 and c.event_type = $3 and not c.rumour"
        # A story the owner marked false, one sources dispute, or a known event's copy
        # never counts as an independent source.
        " and c.status <> 'disputed' and c.resolution is distinct from 'false'"
        " and c.duplicate_of is null"
        " and c.last_seen >= $4 and c.first_seen <= $5"
        " group by c.id",
        story.id,
        story.ticker,
        story.event_type,
        story.first_seen - window,
        story.first_seen + window,
    )
    return [
        OtherStory(
            r["id"],
            min(
                trust.effective_tier(t, o) if trust else t
                for t, o in zip(r["tiers"], r["outlets"], strict=True)
            ),
            frozenset((key_of(o) if key_of else o) for o in r["outlets"] if o),
        )
        for r in rows
    ]


async def assess(
    pool: asyncpg.Pool, story: StoryState, config: NewsConfig, now: dt.datetime
) -> StoryState:
    rules = config.event_rules
    if story.classified and story.event_type:
        story.severity = rules.severity(
            story.event_type,
            story.ticker,
            thesis_linked=story.thesis_link is not None,
            insider_cluster=story.event_type == "insider_transaction"
            and story.ticker is not None
            and await insider_cluster(pool, story.ticker, rules, now),
        )
        if not story.relevant:
            story.severity = 0
    window = dt.timedelta(hours=config.news.recheck_hours)
    if not story.classified:
        status, confidence, reason = "pending", "unconfirmed", "not yet classified"
        if story.best_tier == 1:
            confidence = "verified"
    else:
        others = await other_stories(
            pool, story, window, config.trust, config.sources.independence_key
        )
        rules_v = config.verification.corroboration
        decision = decide(
            best_tier=story.best_tier,
            outlets=story.outlets,
            rumour=story.rumour,
            others=others,
            finding=story.finding,
            independent_needed=rules_v.independent_sources_to_confirm,
            tier1_alone=rules_v.tier1_confirms_alone,
        )
        status, confidence, reason = decision.status, decision.confidence, decision.reason
    if story.resolution == "confirmed":
        # The owner resolved a disputed story; that decision wins over the rules.
        status, confidence, reason = "confirmed", "corroborated", "resolved by the owner"
    elif story.resolution == "false":
        status, confidence, reason = "disputed", "disputed", "marked false by the owner"
    story.status = status
    recheck = story.first_seen + window if status in ("unconfirmed", "sentiment") else None
    lifecycle, lifecycle_reason = lifecycle_for(
        Basis(
            status=status,
            confidence=confidence,
            kinds=story.kinds,
            tier1_support=any(
                t == 1 for _, t in (story.finding.supporting if story.finding else ())
            ),
            corrected=story.corrected,
            closed=story.closed,
        )
    )
    if lifecycle != story.lifecycle:
        support = [
            {"url": u, "kind": "search", "tier": t, "outlet": host_of(u)}
            for u, t in (story.finding.supporting if story.finding else ())
        ]
        await pool.execute(
            "insert into public.story_lifecycle_event (cluster_id, at, from_state, to_state,"
            " reason, evidence) values ($1, $2, $3, $4, $5, $6::jsonb)",
            story.id,
            now,
            story.lifecycle,
            lifecycle,
            lifecycle_reason,
            json.dumps([*story.evidence, *support]),
        )
        story.lifecycle = lifecycle
    await pool.execute(
        "update public.story_cluster set ticker = $2, event_type = $3, severity = $4,"
        " status = $5, confidence = $6, status_reason = $7, rumour = $8, recycled = $9,"
        " event_date = $10, kpi_id = $11, headline = $12, best_tier = $13,"
        " recheck_until = $14, lifecycle = $15,"
        " lifecycle_reason = $16, lifecycle_at = case when lifecycle is distinct from $15"
        " then $17 else lifecycle_at end where id = $1",
        story.id,
        story.ticker,
        story.event_type,
        story.severity if story.classified else None,
        status,
        confidence,
        reason,
        story.rumour,
        story.recycled,
        story.event_date,
        story.thesis_link,
        story.headline,
        story.best_tier,
        recheck,
        lifecycle,
        lifecycle_reason,
        now,
    )
    return story


def search_due(
    story: StoryState, settings: NewsSettings, now: dt.datetime, tier1_alone: bool = True
) -> bool:
    if story.status not in ("unconfirmed", "sentiment") or story.severity < 2:
        return False
    if (story.best_tier == 1 and tier1_alone) or story.recycled:
        return False
    if story.search_rounds >= settings.corroboration_rounds:
        return False
    if now > story.first_seen + dt.timedelta(hours=settings.recheck_hours):
        return False
    if story.last_searched_at is None:
        return True
    spacing = dt.timedelta(hours=settings.recheck_hours / max(1, settings.corroboration_rounds))
    return now - story.last_searched_at >= spacing


def search_domains(config: NewsConfig, holdings: Holdings) -> tuple[str, ...]:
    trusted = [d.domain for d in config.sources.domain_tiers if d.tier <= 2]
    return tuple(dict.fromkeys([*sorted(holdings.ir_domains), *trusted]))[:60]


def merge_findings(old: SearchFinding | None, new: SearchFinding) -> SearchFinding:
    if old is None:
        return new
    return SearchFinding(
        supporting=tuple(dict.fromkeys(old.supporting + new.supporting)),
        contradicting=tuple(dict.fromkeys(old.contradicting + new.contradicting)),
        note=new.note or old.note,
    )


async def corroborate_story(
    ctx: JobContext,
    story: StoryState,
    corroborator: Corroborator,
    config: NewsConfig,
    holdings: Holdings,
    now: dt.datetime,
) -> StoryState:
    async with ctx.step(f"corroborate:{story.id}") as step:
        request = SearchRequest(
            headline=story.headline,
            summary=story.summary,
            company=holdings.name(story.ticker),
            published_at=story.published_at,
            own_outlets=config.sources.group_members(story.outlets),
            domains=search_domains(config, holdings),
        )

        def tier_of(host: str | None) -> int | None:
            if host and any(host == d or host.endswith(f".{d}") for d in holdings.ir_domains):
                return 1
            return config.sources.domain_tier(host)

        outcome, usage = await corroborator.search(request, tier_of)
        step.usage = usage.step_usage()
        finding = merge_findings(story.finding, outcome.finding)
        step.detail.update(
            supporting=[u for u, _ in outcome.finding.supporting],
            contradicting=[u for u, _ in outcome.finding.contradicting],
            rejected=outcome.rejected[:10],
            note=outcome.finding.note,
        )
        await ctx.pool.execute(
            "update public.story_cluster set search_rounds = search_rounds + 1,"
            " last_searched_at = $2, corroboration = $3::jsonb where id = $1",
            story.id,
            now,
            json.dumps(
                {
                    "supporting": [list(x) for x in finding.supporting],
                    "contradicting": [list(x) for x in finding.contradicting],
                    "note": finding.note,
                    "rejected": outcome.rejected[:20],
                }
            ),
        )
        story.search_rounds += 1
        story.last_searched_at = now
        story.finding = finding
    return await assess(ctx.pool, story, config, now)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def alert_message(story: StoryState, holdings: Holdings, app_url: str | None) -> str:
    event = EVENT_DESCRIPTIONS.get(story.event_type or "", story.event_type or "news")
    event = event.split(":")[0]
    name = holdings.name(story.ticker)
    label = {1: "tier 1"}.get(story.best_tier, f"tier {story.best_tier}")
    lines = [
        f"[Severity {story.severity}] {name}: {event}",
        story.headline,
        f"Source ({label}): {story.url}",
    ]
    if story.finding and story.finding.supporting:
        lines.append(f"Corroborated by: {', '.join(u for u, _ in story.finding.supporting[:2])}")
    if app_url:
        lines.append(f"{app_url.rstrip('/')}/news?story={story.id}")
    return "\n".join(lines)


async def route_story(
    ctx: JobContext,
    story: StoryState,
    config: NewsConfig,
    holdings: Holdings,
    now: dt.datetime,
    app_url: str | None,
    available_channels: Collection[str] | None = None,
) -> str | None:
    if story.status != "confirmed" or story.rumour or story.recycled or story.severity < 1:
        return None
    if story.duplicate_of is not None:
        return None
    if story.indirect_only and story.severity < config.news.indirect_alert_min_severity:
        # Reached the holding only through a competitor, supplier, customer or topic.
        return (
            f"story {story.id}: indirect link only, severity {story.severity} is below "
            f"{config.news.indirect_alert_min_severity}"
        )
    known = await known_event(ctx.pool, story)
    if known is not None:
        await ctx.pool.execute(
            "update public.story_cluster set duplicate_of = $2, status_reason ="
            " concat_ws('; ', status_reason, $3::text) where id = $1",
            story.id,
            known,
            f"a known event (story #{known}), not alerted again",
        )
        story.duplicate_of = known
        return f"story {story.id}: known event (story #{known}), not alerted again"
    exists = await ctx.pool.fetchval(
        "select exists (select 1 from public.alert where cluster_id = $1 and kind = 'news')",
        story.id,
    )
    if exists:
        return None
    created = await create_alerts(
        ctx.pool,
        AlertRequest("news", story.severity, story.ticker, story.event_type),
        alert_message(story, holdings, app_url),
        config.notifications,
        now=now,
        config_version_id=config.notifications_version_id,
        cluster_id=story.id,
        job_id=ctx.job_id,
        available_channels=available_channels,
    )
    return f"story {story.id}: {created.explanation}" if created.ids else None


async def known_event(pool: asyncpg.Pool, story: StoryState) -> int | None:
    """An earlier confirmed story about the same holding and event: the same event date
    (+-1 day) when both are known, else first seen within 72 hours. Another outlet's copy
    of a known event that did not cluster with it therefore never alerts twice.

    Only a story that was itself alert-worthy counts as the known event: not a rumour (a
    tier-1 outlet's rumour is still "confirmed" by the source rules), not recycled, relevant
    (severity 1+), and not marked false by the owner. Otherwise an early rumour would swallow
    the real announcement."""
    if story.ticker is None or story.event_type is None:
        return None
    found: int | None = await pool.fetchval(
        "select id from public.story_cluster where id <> $1 and ticker = $2"
        " and event_type = $3 and status = 'confirmed' and duplicate_of is null"
        " and not rumour and not recycled and coalesce(severity, 0) >= 1"
        " and resolution is distinct from 'false'"
        " and first_seen <= $4 and ("
        "   ($5::date is not null and event_date is not null"
        "    and abs(event_date - $5::date) <= 1)"
        "   or (($5::date is null or event_date is null)"
        "    and first_seen >= $4 - interval '72 hours'))"
        " order by first_seen, id limit 1",
        story.id,
        story.ticker,
        story.event_type,
        story.first_seen,
        story.event_date,
    )
    return found


async def queue_earnings_check(
    ctx: JobContext, story: StoryState, config: NewsConfig, now: dt.datetime
) -> str | None:
    if (
        story.status != "confirmed"
        or story.ticker is None
        or story.event_type not in config.event_rules.earnings_check_on
    ):
        return None
    recent = await ctx.pool.fetchval(
        "select exists (select 1 from public.job where job_type = 'earnings_check'"
        " and params ->> 'ticker' = $1 and created_at >= $2"
        " and status in ('succeeded', 'queued', 'waiting', 'running'))",
        story.ticker,
        now - dt.timedelta(days=config.event_rules.earnings_check_cooldown_days),
    )
    if recent:
        return None
    report_date = story.published_at.date().isoformat()
    async with ctx.pool.acquire() as conn, conn.transaction():
        job_id = await create_job(
            conn,
            "earnings_check",
            "event",
            params={"ticker": story.ticker, "report_date": report_date, "story_id": story.id},
        )
        await queue.enqueue(conn, job_id, "earnings_check")
    return f"{story.ticker} ({report_date}, job {job_id})"


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


async def run_news_pipeline(ctx: JobContext, deps: NewsDeps) -> None:
    pool = ctx.pool
    now = deps.now()
    async with ctx.step("load_config") as step:
        config = await load_news_config(ctx)
        holdings = await load_holdings(pool, config)
        await sync_sources(pool, config.sources.sources)
        enabled = [s for s in config.sources.sources if s.enabled and s.type != "ai_search"]
        backfilled = await backfill_provenance(
            pool,
            {s.id: s for s in config.sources.sources},
            config.sources,
            holdings.ir_domains,
        )
        step.detail.update(
            holdings=[t.ticker for t in holdings.terms],
            sources=[s.id for s in enabled],
            provenance_backfilled=backfilled,
        )

    since = now - dt.timedelta(hours=config.sources.freshness_hours)
    freshness_days = max(1, config.sources.freshness_hours // 24)
    collected: list[tuple[RawItem, NewsSource]] = []
    runs: dict[str, SourceRun] = {}
    for source in enabled:
        async with ctx.step(f"collect:{source.id}") as step:
            health = await pool.fetchrow(
                "select consecutive_failures, backoff_until, last_item_count"
                " from public.source where id = $1",
                source.id,
            )
            failures = health["consecutive_failures"] if health else 0
            if health and breaker_open(health["backoff_until"], now):
                runs[source.id] = SourceRun(skipped=True)
                step.detail["skipped"] = f"circuit breaker open until {health['backoff_until']}"
                continue
            error: str | None = None
            not_modified = False
            started = time.monotonic()
            try:
                items, notes = await collect_source(
                    source,
                    deps,
                    holdings,
                    since,
                    pool,
                    freshness_days,
                    config.event_rules,
                    config.sources.google_news_locale,
                    config.news.edgar_fts_max_hits,
                )
            except NotModified:
                items, notes, not_modified = [], [], True
            except (FetchError, httpx.HTTPError, ValueError) as exc:
                items, notes, error = [], [], str(exc)[:500]
            latency_ms = round((time.monotonic() - started) * 1000)
            previous_count = health["last_item_count"] if health else None
            if not error and not not_modified and not items and previous_count:
                # A feed that answers but yields nothing after yielding items: the layout or
                # the URL may have changed. Reported, and counted against the breaker.
                notes = [*notes, f"parser drift: 0 items (previous run {previous_count})"]
            drift = bool(notes) and notes[-1].startswith("parser drift")
            failed = error is not None or drift
            collected.extend((item, source) for item in items)
            runs[source.id] = SourceRun(
                latency_ms=latency_ms,
                error=error or (notes[-1] if drift else None),
                not_modified=not_modified,
            )
            step.detail.update(items=len(items), latency_ms=latency_ms)
            if not_modified:
                step.detail["not_modified"] = True
            if notes:
                step.detail["notes"] = notes[:20]
            if error:
                step.detail["error"] = error
            await pool.execute(
                "update public.source set last_fetched_at = $2,"
                " last_item_count = case when $8::boolean then last_item_count else $3 end,"
                " last_error = $4, consecutive_failures = $5, backoff_until = $6,"
                " last_success_at = case when $7::boolean then last_success_at else $2 end,"
                " last_item_at = case when $3::integer > 0 then $2 else last_item_at end"
                " where id = $1",
                source.id,
                now,
                len(items),
                error or ("; ".join(notes)[:500] if notes and not items else None),
                0 if not failed else failures + 1,
                None if not failed else next_backoff(failures + 1, now),
                failed,
                not_modified,
            )

    async with ctx.step("normalise_dedupe_link") as step:
        stats = await store_items(pool, collected, config, holdings, now, ctx.job_id)
        step.detail.update(stats.detail())
        await record_source_runs(pool, ctx.job_id, now, runs, stats)
        await pool.execute(
            "update public.filing_hit h set news_item_id = n.id from public.news_item n"
            " where h.news_item_id is null and h.kind = 'topic' and n.url = h.url"
        )
    touched = set(stats.touched_clusters)

    async with ctx.step("dedupe") as step:
        deduped, kept = await run_dedupe(ctx, deps.embedder, config.news, now)
        if deps.embedder is not None and deduped.embedding_tokens:
            step.usage = Usage(
                model=deps.embedder.model, input_tokens=deduped.embedding_tokens
            ).step_usage()
            step.detail["embedding_tokens_estimated"] = deduped.embedding_tokens
        touched = (touched - {gone for gone, _ in deduped.merged_clusters}) | kept
        step.detail.update(
            simhash_grouped=deduped.simhash_groups,
            embedded=deduped.embedded,
            embedding_grouped=deduped.embedding_groups,
            clusters_merged=[list(m) for m in deduped.merged_clusters],
        )
        if deduped.skipped:
            step.detail["skipped"] = deduped.skipped

    if deps.classifier is not None:
        classified, _ = await classify_pending(ctx, deps.classifier, holdings, config.news, now)
        touched |= classified
    else:
        async with ctx.step("classify") as step:
            step.detail["skipped"] = "ANTHROPIC_API_KEY is not set; items stay unclassified"
    # EDGAR items are labelled by rule when stored; their stories need assessing too.

    async with ctx.step("assess") as step:
        rechecks = await pool.fetch(
            "select id from public.story_cluster where status in ('unconfirmed', 'sentiment',"
            " 'pending') and (recheck_until is null or recheck_until >= $1)"
            " and last_seen >= $2",
            now,
            since,
        )
        ids = sorted(touched | {r["id"] for r in rechecks})
        stories: list[StoryState] = []
        # Two passes: the first stores every story's holding and event type, so the second
        # can count other stories of the same event as independent sources, whatever order
        # they were first seen in.
        for _ in range(2):
            stories = []
            for cluster_id in ids:
                story = await load_story(
                    pool,
                    cluster_id,
                    config.event_rules,
                    config.trust,
                    config.sources.independence_key,
                )
                if story is not None:
                    stories.append(await assess(pool, story, config, now))
        counts: dict[str, int] = defaultdict(int)
        for s in stories:
            counts[s.status] += 1
        step.detail.update(assessed=len(stories), **counts)

    if deps.corroborator is not None:
        for story in stories:
            if search_due(
                story,
                config.news,
                now,
                config.verification.corroboration.tier1_confirms_alone,
            ):
                await corroborate_story(ctx, story, deps.corroborator, config, holdings, now)

    async with ctx.step("route") as step:
        routed: list[str] = []
        checks: list[str] = []
        escalations: list[str] = []
        for story in stories:
            outcome = await route_story(
                ctx, story, config, holdings, now, deps.app_url, set(deps.channels)
            )
            if outcome:
                routed.append(outcome)
            check = await queue_earnings_check(ctx, story, config, now)
            if check:
                checks.append(check)
            if escalation_due(
                story.status,
                story.severity,
                story.rumour,
                story.recycled,
                story.duplicate_of is not None,
                story.ticker,
                config.escalation,
            ):
                job = await queue_escalation(
                    pool,
                    {"cluster_id": story.id, "ticker": story.ticker},
                    ("cluster_id", str(story.id)),
                )
                if job is not None:
                    escalations.append(f"story {story.id} (job {job})")
        step.detail.update(alerts=routed, earnings_checks=checks, escalations=escalations)

    async with ctx.step("dispatch") as step:
        result = await dispatch_due(pool, deps.channels, now=now)
        step.detail.update(sent=result.sent, failed=result.failed, retrying=result.retrying)

    if deps.translator is not None and config.news.translation_per_run > 0:
        async with ctx.step("translate") as step:
            detail, used = await translate_pending(ctx, deps.translator, config.news)
            step.detail.update(detail)
            step.usage = used.step_usage()

    # Last, so page fetches never delay an alert.
    if deps.fetcher is not None and config.news.fulltext_per_run > 0:
        async with ctx.step("fulltext") as step:
            step.detail.update(
                await run_fulltext(
                    pool,
                    config.sources,
                    deps.fetcher,
                    now,
                    limit=config.news.fulltext_per_run,
                    unlinked_relevance=config.news.triage_fulltext_relevance,
                )
            )
            step.detail["expired_bodies_cleared"] = await purge_expired(pool, now)


async def news_ingest_handler(ctx: JobContext) -> None:
    settings = get_settings()
    classifier: Classifier | None = None
    corroborator: Corroborator | None = None
    stored_jobs = await load_active(ctx.pool, "jobs", JobsConfig)
    news = (stored_jobs.body if stored_jobs else JobsConfig()).news
    try:
        client = create_client(settings)
    except AINotConfigured:
        client = None
    llm = create_llm(settings, client)
    prompts = await load_prompts(ctx.pool)
    if llm.configured(news.classification_model):
        classifier = ClaudeClassifier(
            client,
            news.classification_model,
            prompts.text("news_classification", CLASSIFICATION_PROMPT, news.classification_model),
            effort=news.classification_effort,
            llm=llm,
            cache=ResultCache(ctx.pool, "news_classification"),
        )
    if client is not None:
        corroborator = ClaudeCorroborator(
            routing_client(client, llm),
            news.corroboration_model,
            news.corroboration_max_searches,
            prompts.text("news_corroboration", SEARCH_SYSTEM, news.corroboration_model),
            effort=news.corroboration_effort,
        )
    translator: LlmTranslator | None = None
    if usable_model(llm, news.translation_model):
        translator = LlmTranslator(
            llm,
            news.translation_model,
            prompts.text("news_translation", TRANSLATION_PROMPT, news.translation_model),
            effort=news.translation_effort,
            cache=ResultCache(ctx.pool, "news_translation"),
        )
    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": f"StewardAI/{__version__}"}
    ) as http:
        embedder: Embedder | None = (
            GeminiEmbedder(
                http,
                settings.google_api_key.get_secret_value(),
                news.embedding_model,
                news.embedding_dimensions,
            )
            if settings.google_api_key
            else None
        )
        deps = NewsDeps(
            translator=translator,
            embedder=embedder,
            http=http,
            edgar=EdgarClient(http, settings.sec_user_agent) if settings.sec_user_agent else None,
            finnhub_key=(
                settings.finnhub_api_key.get_secret_value() if settings.finnhub_api_key else None
            ),
            alpha_vantage_key=(
                settings.alpha_vantage_api_key.get_secret_value()
                if settings.alpha_vantage_api_key
                else None
            ),
            dart_key=settings.dart_api_key.get_secret_value() if settings.dart_api_key else None,
            classifier=classifier,
            corroborator=corroborator,
            channels=channels_from_settings(http, settings),
            app_url=settings.app_url,
            fetcher=build_fetcher(
                http,
                news.fulltext_browser_fallback,
                f"{settings.sec_user_agent or 'StewardAI'} StewardAI/{__version__}",
            ),
        )
        try:
            await run_news_pipeline(ctx, deps)
        finally:
            if deps.fetcher is not None:
                await deps.fetcher.aclose()
