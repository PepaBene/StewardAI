"""The `news_analysis` and `news_brief` jobs: run the N6 techniques over stored stories.

`news_analysis` picks the stories seen in the last days. Code techniques (novelty, threads, market
context, themes) run for all of them. Model techniques (extraction, impact explanations, framing,
claim checks, thesis links) run for the `analysis_per_run` most severe and newest, each only when
its technique is listed in `analysis_techniques`, its model has a key, and the inputs changed since
it last ran (the result is keyed by a hash of its inputs). A budget stop ends the model techniques
for the run; code techniques and everything already saved stay.

Results are appended to `story_analysis`; nothing here changes a story's severity, status or
alerts.
"""

import datetime as dt
import json
import logging
import re
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any, cast

import asyncpg
import httpx

from steward.ai import AINotConfigured, Usage
from steward.budget_guard import BudgetExceeded
from steward.config.jobs import JobsConfig, NewsSettings
from steward.config.news_sources import NewsSourcesConfig
from steward.config.notifications import NotificationsConfig
from steward.config.prompts import PromptsConfig, load_prompts
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig, ThesisConfig
from steward.jobs.runner import JobContext
from steward.llm.batching import BatchCollector
from steward.llm.cache import ResultCache
from steward.llm.client import LLM, create_llm
from steward.llm.types import Completion, Request
from steward.news import brief as briefs
from steward.news import claims as claim_checks
from steward.news import extract as extraction
from steward.news import framing as framings
from steward.news import gdelt_theme, ops
from steward.news import impact as impacts
from steward.news import market as markets
from steward.news import novelty as novelties
from steward.news import sentiment as sentiments
from steward.news import theme_narrative as theme_narratives
from steward.news import themes as theme_logic
from steward.news import thesis_link as thesis_links
from steward.news import threads as thread_logic
from steward.news.analysis import (
    AnalysisError,
    StoryMaterial,
    input_hash,
    latest_analysis,
    load_story,
    related_primaries,
    save_analysis,
)
from steward.notify.alerts import create_alerts
from steward.notify.route import AlertRequest
from steward.settings import get_settings
from steward.xray.supply_chain import SupplyChain

logger = logging.getLogger(__name__)

# Long enough for a story's 3- and 5-trading-day market windows to complete (5 trading days
# is up to 9 calendar days with a weekend), so the event study is not starved.
SELECT_DAYS = 10
BATCH_WAIT_MINUTES = 60.0


class BatchedLLM:
    """The part of `LLM` that `structured` uses, sending each completion through a batch
    collector so many extractions share one batch request."""

    def __init__(self, llm: LLM, collector: BatchCollector) -> None:
        self._llm = llm
        self._collector = collector

    def configured(self, model: str) -> bool:
        return self._llm.configured(model)

    async def complete(self, request: Request) -> Completion:
        return await self._collector.submit(request)


MAX_STORIES = 500
SEVERE = 3  # stories at or above this severity are analysed directly and never take a reuse


@dataclass
class Counts:
    saved: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def saved_one(self, kind: str) -> None:
        self.saved[kind] = self.saved.get(kind, 0) + 1

    def failed(self, key: str, exc: Exception) -> None:
        self.errors[key] = str(exc)[:200]

    def detail(self) -> dict[str, Any]:
        return {"saved": self.saved, "errors": self.errors}


async def analysis_exists(pool: asyncpg.Pool, cluster_id: int, kind: str, hash_: str) -> bool:
    return bool(
        await pool.fetchval(
            "select exists (select 1 from public.story_analysis where cluster_id = $1"
            " and kind = $2 and input_hash = $3)",
            cluster_id,
            kind,
            hash_,
        )
    )


async def pick_stories(pool: asyncpg.Pool, now: dt.datetime) -> list[tuple[int, int]]:
    """(story id, severity) of the stories seen recently that have at least one item, highest
    exposure x materiality first (then newest). Exposure is the holding weight the story's latest
    impact analysis found, so a story the portfolio does not hold ranks below one it does at the
    same severity; a story not yet analysed counts at the ranking floor."""
    rows = await pool.fetch(
        "select c.id, coalesce(c.severity, 0) as severity, c.last_seen,"
        " coalesce((select p.payload from public.story_analysis p where p.cluster_id = c.id"
        "  and p.kind = 'impact' order by p.created_at desc, p.id desc limit 1), '{}'::jsonb)"
        "  as impact"
        " from public.story_cluster c where c.last_seen >= $1 and c.duplicate_of is null"
        " and exists (select 1 from public.news_item i where i.cluster_id = c.id)",
        now - dt.timedelta(days=SELECT_DAYS),
    )

    def rank(r: asyncpg.Record) -> tuple[float, dt.datetime]:
        raw = r["impact"]
        impact: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else dict(raw)
        exposure = (
            float(impact.get("direct_weight") or 0)
            + 0.5 * float(impact.get("reached_weight") or 0)
            + impacts.ATTRIBUTE_RANK_FACTOR * float(impact.get("attribute_weight") or 0)
        )
        materiality = (r["severity"] + 1) / 4
        return (max(exposure, briefs.EXPOSURE_FLOOR) * materiality, r["last_seen"])

    ranked = sorted(rows, key=rank, reverse=True)[:MAX_STORIES]
    return [(r["id"], r["severity"]) for r in ranked]


async def fetch_gdelt(pool: asyncpg.Pool, limit: int, now: dt.datetime) -> dict[str, Any]:
    """GDELT tone and attention for the themes whose data is more than a day old."""
    stored = await load_active(pool, "news_sources", NewsSourcesConfig)
    topics = {
        t.id: t.keywords
        for t in (stored.body if stored else NewsSourcesConfig()).portfolio_topics
        if t.enabled
    }
    due = (await gdelt_theme.stale_themes(pool, sorted(topics), now.date()))[:limit]
    done: list[str] = []
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": "stewardai/1.0"}) as client:

        async def get(url: str) -> dict[str, Any] | None:
            response = await client.get(url)
            if response.status_code != 200 or not response.text.lstrip().startswith("{"):
                raise httpx.HTTPError(f"GDELT answered {response.status_code}")
            body: dict[str, Any] = response.json()
            return body

        for theme in due:
            try:
                tone, attention = await gdelt_theme.theme_series(get, topics[theme])
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{theme}: {str(exc)[:80]}")
                break
            await gdelt_theme.store_series(pool, theme, tone, attention)
            done.append(theme)
    return {"themes": done, "errors": errors}


async def resolve_quiet_stories(pool: asyncpg.Pool, now: dt.datetime, days: int) -> int:
    """Mark confirmed or corroborated stories with no new item for `days` days as resolved.

    This is not the owner closing a story: `lifecycle_closed_at` stays empty, so a new item
    reaching the story makes its next assessment recompute the state from the evidence and
    the story reopens by itself. Disputed and corrected stories stay as they are."""
    if days <= 0:
        return 0
    async with pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            "update public.story_cluster c set lifecycle = 'resolved', lifecycle_at = $1,"
            " lifecycle_reason = $3 from (select id, lifecycle from public.story_cluster"
            "  where lifecycle in ('corroborated', 'primary_confirmed') and last_seen < $2"
            "  and lifecycle_closed_at is null and duplicate_of is null for update) old"
            " where c.id = old.id returning c.id, old.lifecycle as before",
            now,
            now - dt.timedelta(days=days),
            f"no new item for {days} days",
        )
        for r in rows:
            await conn.execute(
                "insert into public.story_lifecycle_event (cluster_id, from_state, to_state,"
                " reason) values ($1, $2, 'resolved', $3)",
                r["id"],
                r["before"],
                f"no new item for {days} days",
            )
    return len(rows)


def make_llm() -> LLM:
    try:
        return create_llm(get_settings())
    except AINotConfigured:
        return LLM()  # no key: every `llm_ready` check is false and the steps are skipped


def thesis_for(theses: ThesesConfig | None, ticker: str | None) -> ThesisConfig | None:
    if theses is None or ticker is None:
        return None
    return next((t for t in theses.theses if t.ticker == ticker), None)


async def load_graph(
    pool: asyncpg.Pool,
) -> tuple[SupplyChain | None, dict[str, list[str]], str | None]:
    """The owner's supply-chain graph and thesis indicators, or why there is none."""
    from steward.events import feed as events_feed

    owner = get_settings().owner_user_id
    if not owner:
        return None, {}, "no owner is configured, so there is no exposure graph"
    try:
        chain, indicators = await events_feed.chain_and_indicators(pool, str(owner))
    except Exception as exc:
        return None, {}, f"the exposure graph could not be built: {str(exc)[:150]}"
    return chain, indicators, None


class AnalysisRun:
    def __init__(
        self, ctx: JobContext, news: NewsSettings, prompts: PromptsConfig, llm: LLM
    ) -> None:
        self.ctx = ctx
        self.pool = ctx.pool
        self.news = news
        self.prompts = prompts
        self.llm = llm
        self.techniques = set(news.analysis_techniques)
        self.counts = Counts()
        self.stopped = False
        self.extractions: dict[int, extraction.StoryExtraction] = {}

    def llm_ready(self, model: str) -> bool:
        return not self.stopped and self.llm.configured(model)

    def cache(self, name: str) -> ResultCache:
        return ResultCache(self.pool, name)

    async def save(
        self, story: StoryMaterial, kind: str, hash_: str, model: str | None, payload: Any
    ) -> None:
        if await save_analysis(
            self.pool, story.cluster_id, kind, hash_, model, payload, self.ctx.job_id
        ):
            self.counts.saved_one(kind)

    async def extraction_of(self, story: StoryMaterial) -> extraction.StoryExtraction | None:
        if story.cluster_id in self.extractions:
            return self.extractions[story.cluster_id]
        doc = await latest_analysis(self.pool, story.cluster_id, "extraction")
        if doc is None:
            return None
        parsed = extraction.StoryExtraction.model_validate(doc)
        self.extractions[story.cluster_id] = parsed
        return parsed

    async def extract_batched(self, stories: list[StoryMaterial], usage: Usage) -> None:
        """Extract many stories through the provider's batch endpoint (half price): every
        story's request goes into one batch and the run waits for the answers. A request the
        batch does not answer is asked directly, as `BatchCollector` does."""
        collector = BatchCollector(self.llm, wait_minutes=BATCH_WAIT_MINUTES)
        adapter = cast(LLM, BatchedLLM(self.llm, collector))
        await collector.run([self.extract(s, usage, adapter) for s in stories])

    async def analyse_batched(self, work: list[Coroutine[Any, Any, None]]) -> None:
        """Run the model stages of many stories through one batch: every stage reads
        `self.llm`, so it is swapped for the batch adapter while the stories run together."""
        collector = BatchCollector(self.llm, wait_minutes=BATCH_WAIT_MINUTES)
        direct = self.llm
        self.llm = cast(LLM, BatchedLLM(direct, collector))
        try:
            await collector.run(work)
        finally:
            self.llm = direct

    async def reuse_from_repeat(self, story: StoryMaterial, kind: str) -> bool:
        """Cost lever N5: a quiet story the novelty step marked as a repeat of an earlier story
        of the same holding takes that story's `kind` analysis instead of a new model call.
        True when the story now has one (stored under a hash of the source, so a later run
        finds it and asks nothing)."""
        if not self.news.reuse_repeat_analysis or story.severity >= SEVERE:
            return False
        novelty = await latest_analysis(self.pool, story.cluster_id, "novelty")
        source_id = novelty.get("similar_story_id") if novelty else None
        if not novelty or novelty.get("novelty") != "repeat" or not source_id:
            return False
        source = await self.pool.fetchrow(
            "select a.payload, a.model from public.story_analysis a"
            " join public.story_cluster c on c.id = a.cluster_id"
            " where a.cluster_id = $1 and a.kind = $2 and c.ticker is not distinct from $3"
            " order by a.created_at desc, a.id desc limit 1",
            int(source_id),
            kind,
            story.ticker,
        )
        if source is None:
            return False
        raw = source["payload"]
        payload: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else dict(raw)
        hash_ = input_hash("reused", kind, int(source_id), payload)
        if not await analysis_exists(self.pool, story.cluster_id, kind, hash_):
            await self.save(story, kind, hash_, source["model"], payload)
            self.counts.saved_one(f"{kind}_reused")
        return True

    async def extract(self, story: StoryMaterial, usage: Usage, llm: LLM | None = None) -> None:
        model = self.news.extraction_model
        system = self.prompts.text("news_extract", extraction.SYSTEM_PROMPT, model)
        hash_ = extraction.extraction_hash(story, model, system)
        if await analysis_exists(self.pool, story.cluster_id, "extraction", hash_):
            return
        try:
            result = await extraction.extract_story(
                llm or self.llm,
                model,
                story,
                usage,
                system=system,
                effort=self.news.extraction_effort,
                cache=self.cache("news_extract"),
            )
        except BudgetExceeded as exc:
            self.stopped = True
            self.counts.failed("budget", exc)
            return
        except AnalysisError as exc:
            self.counts.failed(f"extract:{story.cluster_id}", exc)
            return
        if result is None:
            self.counts.failed(f"extract:{story.cluster_id}", AnalysisError("no verified claim"))
            return
        self.extractions[story.cluster_id] = result
        await self.save(story, "extraction", hash_, model, result.model_dump(mode="json"))

    async def novelty(self, story: StoryMaterial) -> None:
        ex = await self.extraction_of(story)
        vectors = await novelties.story_vectors(self.pool, [story.cluster_id])
        own = vectors.get(story.cluster_id, [])
        earlier = await novelties.earlier_stories(
            self.pool,
            story.cluster_id,
            story.ticker,
            story.first_seen,
            self.news.novelty_window_days,
            story.event_type,
        )
        result = novelties.classify_novelty(
            own,
            earlier,
            recycled=story.recycled,
            event_date=story.event_date,
            first_seen=story.first_seen,
            repeat_at=self.news.novelty_repeat_similarity,
            update_at=self.news.novelty_update_similarity,
        )
        surprise = novelties.surprise_from(ex)
        payload = {
            "novelty": result.novelty,
            "surprise": surprise,
            "similarity": result.similarity,
            "similar_story_id": result.similar_cluster_id,
            "basis": result.basis,
        }
        hash_ = input_hash("novelty", payload, [i.id for i in story.items])
        await self.save(story, "novelty", hash_, None, payload)

    async def thread(self, story: StoryMaterial) -> None:
        vectors = await novelties.story_vectors(self.pool, [story.cluster_id])
        thread_id, created = await thread_logic.assign_thread(
            self.pool,
            story,
            vectors.get(story.cluster_id, []),
            self.news.thread_window_days,
            self.news.novelty_update_similarity,
        )
        ex = await self.extraction_of(story)
        prior = await thread_logic.prior_claims(self.pool, thread_id, story.cluster_id)
        now_claims = [c.text for c in ex.key_claims] if ex else [story.headline]
        corrections = [i.headline for i in story.items if i.is_correction]
        diff = thread_logic.claim_diff(prior, now_claims, corrections, ex.unknowns if ex else [])
        await thread_logic.record_update(self.pool, thread_id, story, diff)
        payload = {"thread_id": thread_id, "opened": created, "diff": diff.as_json()}
        await self.save(
            story,
            "thread",
            input_hash("thread", thread_id, now_claims, prior, corrections),
            None,
            payload,
        )

    async def market(
        self, story: StoryMaterial, benchmarks: list[str], sectors: dict[str, str]
    ) -> None:
        ctx = await markets.market_context(
            self.pool,
            story,
            benchmarks,
            sectors.get(story.ticker or ""),
            self.news.sector_benchmarks,
        )
        if ctx is None:
            return
        payload = ctx.model_dump(mode="json")
        hash_ = input_hash("market", payload["symbol"], payload["base_date"], payload["windows"])
        await self.save(story, "market", hash_, None, payload)

    async def impact(
        self,
        story: StoryMaterial,
        graph: SupplyChain,
        indicators: dict[str, list[str]],
        usage: Usage,
        attributes: dict[str, dict[str, str]],
    ) -> None:
        ex = await self.extraction_of(story)
        result, paths = impacts.graph_chains(graph, story, ex, indicators, attributes=attributes)
        model = self.news.analysis_model
        explain = bool(paths) and self.llm_ready(model)
        if explain and await self.reuse_from_repeat(story, "impact"):
            return
        system = self.prompts.text("news_impact", impacts.SYSTEM_PROMPT, model)
        hash_ = input_hash(
            "impact",
            result.model_dump(mode="json"),
            model if explain else None,
            system if explain else None,
        )
        if await analysis_exists(self.pool, story.cluster_id, "impact", hash_):
            return
        if explain:
            # The code-only result is kept under its own hash when the model step fails, so the
            # "explained" hash stays unsaved and the next run asks the model again.
            code_only_hash = input_hash("impact", result.model_dump(mode="json"), None, None)
            try:
                result = await impacts.explain_chains(
                    self.llm,
                    model,
                    story,
                    ex,
                    result,
                    paths,
                    graph,
                    usage,
                    system=system,
                    effort=self.news.analysis_effort,
                    cache=self.cache("news_impact"),
                )
            except (BudgetExceeded, AnalysisError) as exc:
                if isinstance(exc, BudgetExceeded):
                    self.stopped = True
                    self.counts.failed("budget", exc)
                else:
                    self.counts.failed(f"impact:{story.cluster_id}", exc)
                await self.save(
                    story, "impact", code_only_hash, None, result.model_dump(mode="json")
                )
                return
        await self.save(
            story, "impact", hash_, model if explain else None, result.model_dump(mode="json")
        )

    async def framing(self, story: StoryMaterial, usage: Usage) -> None:
        model = self.news.analysis_model
        if not framings.eligible(story, self.news.framing_min_outlets) or not self.llm_ready(model):
            return
        if await self.reuse_from_repeat(story, "framing"):
            return
        system = self.prompts.text("news_framing", framings.SYSTEM_PROMPT, model)
        hash_ = input_hash("framing", model, system, [(i.id, i.text) for i in story.items])
        if await analysis_exists(self.pool, story.cluster_id, "framing", hash_):
            return
        try:
            result = await framings.compare_framing(
                self.llm,
                model,
                story,
                usage,
                system=system,
                effort=self.news.analysis_effort,
                cache=self.cache("news_framing"),
            )
        except BudgetExceeded as exc:
            self.stopped = True
            self.counts.failed("budget", exc)
            return
        except AnalysisError as exc:
            self.counts.failed(f"framing:{story.cluster_id}", exc)
            return
        if result is not None:
            await self.save(story, "framing", hash_, model, result.model_dump(mode="json"))

    async def claims(self, story: StoryMaterial, usage: Usage) -> None:
        model = self.news.analysis_model
        ex = await self.extraction_of(story)
        if story.severity < 3 or ex is None or not self.llm_ready(model):
            return
        system = self.prompts.text("news_claims", claim_checks.SYSTEM_PROMPT, model)
        extra = await related_primaries(self.pool, story)
        hash_ = input_hash(
            "claims",
            model,
            system,
            [c.text for c in ex.key_claims],
            [(i.id, i.text) for i in [*claim_checks.primaries(story), *extra]],
        )
        if await analysis_exists(self.pool, story.cluster_id, "claims", hash_):
            return
        try:
            result = await claim_checks.check_claims(
                self.llm,
                model,
                story,
                ex,
                usage,
                system=system,
                effort=self.news.analysis_effort,
                cache=self.cache("news_claims"),
                extra=extra,
            )
        except BudgetExceeded as exc:
            self.stopped = True
            self.counts.failed("budget", exc)
            return
        except AnalysisError as exc:
            self.counts.failed(f"claims:{story.cluster_id}", exc)
            return
        await self.save(
            story, "claims", hash_, model, {"claims": [c.model_dump(mode="json") for c in result]}
        )

    async def sentiment(self, story: StoryMaterial, usage: Usage) -> None:
        model = self.news.analysis_model
        if not self.llm_ready(model):
            return
        if await self.reuse_from_repeat(story, "sentiment"):
            return
        system = self.prompts.text("news_sentiment", sentiments.SYSTEM_PROMPT, model)
        hash_ = input_hash("sentiment", model, system, [(i.id, i.text) for i in story.items])
        if await analysis_exists(self.pool, story.cluster_id, "sentiment", hash_):
            return
        try:
            result = await sentiments.read_sentiment(
                self.llm,
                model,
                story,
                usage,
                system=system,
                effort=self.news.analysis_effort,
                cache=self.cache("news_sentiment"),
            )
        except BudgetExceeded as exc:
            self.stopped = True
            self.counts.failed("budget", exc)
            return
        except AnalysisError as exc:
            self.counts.failed(f"sentiment:{story.cluster_id}", exc)
            return
        if result is not None:
            await self.save(story, "sentiment", hash_, model, result.model_dump(mode="json"))

    async def thesis(self, story: StoryMaterial, theses: ThesesConfig | None, usage: Usage) -> None:
        model = self.news.analysis_model
        ex = await self.extraction_of(story)
        thesis = thesis_for(theses, story.ticker)
        if thesis is None or ex is None or not self.llm_ready(model):
            return
        system = self.prompts.text("news_thesis", thesis_links.SYSTEM_PROMPT, model)
        confirmed = bool(
            await self.pool.fetchval(
                "select exists (select 1 from public.news_feedback where target = 'story'"
                " and target_id = $1 and kind = 'confirmed')",
                str(story.cluster_id),
            )
        )
        hash_ = input_hash(
            "thesis",
            model,
            system,
            story.lifecycle,
            confirmed,
            [c.text for c in ex.key_claims],
            thesis.model_dump(mode="json"),
        )
        if await analysis_exists(self.pool, story.cluster_id, "thesis", hash_):
            return
        try:
            result = await thesis_links.link_thesis(
                self.llm,
                model,
                story,
                ex,
                thesis,
                usage,
                system=system,
                effort=self.news.analysis_effort,
                cache=self.cache("news_thesis"),
                owner_confirmed=confirmed,
            )
        except BudgetExceeded as exc:
            self.stopped = True
            self.counts.failed("budget", exc)
            return
        except AnalysisError as exc:
            self.counts.failed(f"thesis:{story.cluster_id}", exc)
            return
        await self.save(
            story, "thesis", hash_, model, {"links": [a.model_dump(mode="json") for a in result]}
        )


async def news_analysis_handler(ctx: JobContext) -> None:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    news = config.news
    now = dt.datetime.now(dt.UTC)
    prompts = await load_prompts(ctx.pool)
    run = AnalysisRun(ctx, news, prompts, make_llm())
    picked = await pick_stories(ctx.pool, now)
    stories: dict[int, StoryMaterial] = {}
    for sid, _ in picked:
        story = await load_story(ctx.pool, sid)
        if story is not None and story.items:
            stories[sid] = story
    # A story with no items takes no `analysis_per_run` slot.
    model_ids = [
        sid for sid, severity in picked if sid in stories and severity >= news.analysis_min_severity
    ][: news.analysis_per_run]

    strategy = await load_active(ctx.pool, "strategy", StrategyConfig)
    classifications: dict[str, dict[str, str]] = (
        {t: dict(d) for t, d in strategy.body.classifications.items()} if strategy else {}
    )

    if "extraction" in run.techniques and news.analysis_per_run:
        usage = Usage(model=news.extraction_model)
        async with ctx.step("extract") as step:
            if run.llm_ready(news.extraction_model):
                chosen = [stories[sid] for sid in model_ids if sid in stories]
                if news.batch_extraction and len(chosen) > 1:
                    await run.extract_batched(chosen, usage)
                else:
                    for story in chosen:
                        if not run.stopped:
                            await run.extract(story, usage)
            else:
                step.detail.update(skipped=f"no API key for {news.extraction_model}")
            step.detail.update(run.counts.detail())
            step.usage = usage.step_usage()

    async with ctx.step("code") as step:
        benchmarks = [str(s) for s in config.prices.benchmark_symbols]
        sectors = {t: dims["sector"] for t, dims in classifications.items() if "sector" in dims}
        for story in stories.values():
            for technique in ("novelty", "thread", "market"):
                if technique not in run.techniques:
                    continue
                try:
                    if technique == "novelty":
                        await run.novelty(story)
                    elif technique == "thread":
                        await run.thread(story)
                    else:
                        await run.market(story, benchmarks, sectors)
                except (asyncpg.PostgresError, ValueError, IndexError) as exc:
                    run.counts.failed(f"{technique}:{story.cluster_id}", exc)
        if "thread" in run.techniques:
            await thread_logic.quiet_old_threads(ctx.pool, now, news.thread_window_days)
        if "themes" in run.techniques:
            step.detail.update(theme_rows=await theme_logic.update_themes(ctx.pool, now.date()))
            if news.gdelt_themes:
                step.detail.update(gdelt=await fetch_gdelt(ctx.pool, news.gdelt_max_themes, now))
        step.detail.update(
            auto_resolved=await resolve_quiet_stories(ctx.pool, now, news.auto_resolve_days)
        )
        step.detail.update(run.counts.detail())

    wants_model = run.techniques & {"impact", "framing", "claims", "thesis", "sentiment"}
    if wants_model and news.analysis_per_run:
        usage = Usage(model=news.analysis_model)
        async with ctx.step("analyse") as step:
            graph, indicators, graph_note = (
                await load_graph(ctx.pool) if "impact" in run.techniques else (None, {}, None)
            )
            if graph_note:
                step.detail.update(graph=graph_note)
            attributes = (
                await impacts.holding_attributes(ctx.pool, classifications)
                if graph is not None
                else {}
            )
            theses_stored = await load_active(ctx.pool, "theses", ThesesConfig)
            theses = theses_stored.body if theses_stored else None

            async def analyse_story(story: StoryMaterial) -> None:
                if "impact" in run.techniques and graph is not None:
                    await run.impact(story, graph, indicators, usage, attributes)
                if "framing" in run.techniques:
                    await run.framing(story, usage)
                if "claims" in run.techniques:
                    await run.claims(story, usage)
                if "sentiment" in run.techniques:
                    await run.sentiment(story, usage)
                if "thesis" in run.techniques:
                    await run.thesis(story, theses, usage)

            chosen = [stories[sid] for sid in model_ids if sid in stories]
            # Cost lever N1: nobody waits for a quiet story's analysis, so those go to the batch
            # endpoint together; a severe story is analysed directly and at once.
            waiting = [s for s in chosen if s.severity < SEVERE] if news.batch_analysis else []
            for story in chosen:
                if story not in waiting:
                    await analyse_story(story)
            if len(waiting) > 1:
                await run.analyse_batched([analyse_story(s) for s in waiting])
            else:
                for story in waiting:
                    await analyse_story(story)
            step.detail.update(batched=len(waiting) if len(waiting) > 1 else 0)
            step.detail.update(run.counts.detail())
            step.usage = usage.step_usage()
    # The scheduled ops report rides this job: the first run of each ISO week sends it. A failure
    # here never fails the analysis.
    async with ctx.step("ops_report") as step:
        try:
            step.detail.update(sent=await ops.send_weekly_report(ctx.pool, now, ctx.job_id))
        except Exception as exc:
            step.detail.update(error=str(exc)[:200])


async def write_theme_briefs(
    ctx: JobContext,
    news: NewsSettings,
    llm: LLM,
    prompts: PromptsConfig,
    usage: Usage,
    start: dt.datetime,
    end: dt.datetime,
) -> dict[str, Any]:
    """One cited paragraph per watch theme with enough items in the week."""
    model = news.brief_model
    stored = await load_active(ctx.pool, "news_sources", NewsSourcesConfig)
    topics = [
        t for t in (stored.body if stored else NewsSourcesConfig()).portfolio_topics if t.enabled
    ]
    system = prompts.text("news_theme", theme_narratives.SYSTEM_PROMPT, model)
    written: list[str] = []
    rejected: list[str] = []
    errors: list[str] = []
    for topic in topics:
        items = await theme_narratives.theme_items(ctx.pool, topic.id, start, end)
        if len(items) < theme_narratives.MIN_ITEMS:
            continue
        try:
            narrative = await theme_narratives.write_narrative(
                llm,
                model,
                topic.label,
                items,
                usage,
                system=system,
                effort=news.brief_effort,
                cache=ResultCache(ctx.pool, "news_theme"),
            )
        except BudgetExceeded as exc:
            errors.append(f"budget: {str(exc)[:100]}")
            break
        except AnalysisError as exc:
            errors.append(f"{topic.id}: {str(exc)[:100]}")
            continue
        if narrative is None:
            rejected.append(topic.id)
            continue
        await ctx.pool.execute(
            "insert into public.news_brief (kind, ticker, period_start, period_end, body, model,"
            " job_id) values ('theme', $1, $2, $3, $4::jsonb, $5, $6)"
            " on conflict (kind, ticker, period_start) do update set"
            " period_end = excluded.period_end, body = excluded.body, model = excluded.model,"
            " job_id = excluded.job_id, created_at = now()",
            topic.id,
            start,
            end,
            theme_narratives.as_body(topic.label, narrative).model_dump_json(),
            model,
            ctx.job_id,
        )
        written.append(topic.id)
    return {"written": written, "rejected": rejected, "errors": errors}


BRIEF_ALERT_LINES = 3
ALERT_LINE_CHARS = 240


def notification_text(text: str, limit: int = ALERT_LINE_CHARS) -> str:
    """Feed-controlled text for a notification: control characters and markup-ish angle
    brackets removed, whitespace collapsed to single spaces, bounded to `limit` characters."""
    cleaned = re.sub(r"[\x00-\x1f\x7f<>]+", " ", text)
    cleaned = " ".join(cleaned.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


async def alert_brief(ctx: JobContext, body: briefs.BriefBody, now: dt.datetime) -> str:
    """Send the morning brief's top lines through the alert routing, once a day. What the
    notification rules decide (channel, quiet hours, daily cap) applies as for any alert."""
    # Ranked without the sentiment nudge: sentiment never decides what is sent.
    lines = sorted(
        (ln for s in body.sections for ln in s.lines),
        key=lambda ln: (-(ln.alert_score if ln.alert_score is not None else ln.score), ln.story_id),
    )
    top = lines[:BRIEF_ALERT_LINES]
    if not top:
        return "no lines"
    key = f"morning-brief:{now.date().isoformat()}"
    if await ctx.pool.fetchval(
        "select exists (select 1 from public.alert where dedupe_key = $1)", key
    ):
        return "already sent today"
    stored = await load_active(ctx.pool, "notifications", NotificationsConfig)
    config = stored.body if stored else NotificationsConfig()
    message = "Morning brief:\n" + "\n".join(
        f"- {notification_text(ln.ticker) + ': ' if ln.ticker else ''}{notification_text(ln.text)}"
        for ln in top
    )
    created = await create_alerts(
        ctx.pool,
        AlertRequest("news", max(ln.severity for ln in top), None, "brief"),
        message,
        config,
        now=now,
        job_id=ctx.job_id,
        dedupe_key=key,
    )
    return created.explanation


async def news_brief_handler(ctx: JobContext) -> None:
    """The morning brief every day, and on Mondays the weekly brief and one brief per holding
    with stories in the week."""
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    news = (stored.body if stored else JobsConfig()).news
    now = dt.datetime.now(dt.UTC)
    prompts = await load_prompts(ctx.pool)
    llm = make_llm()
    model = news.brief_model
    # Each step records its own spend: a fresh Usage per step, shared with the writer.
    usage = [Usage(model=model)]
    system = prompts.text("news_brief", briefs.SYSTEM_PROMPT, model)
    state = {"stopped": False}

    async def writer(chosen: list[briefs.Candidate]) -> dict[int, briefs.WrittenLine]:
        if state["stopped"] or not llm.configured(model):
            return {}
        try:
            return await briefs.write_lines(
                llm,
                model,
                chosen,
                usage[0],
                system=system,
                effort=news.brief_effort,
                cache=ResultCache(ctx.pool, "news_brief"),
            )
        except BudgetExceeded:
            state["stopped"] = True
            return {}
        except AnalysisError:
            return {}

    last_body: list[briefs.BriefBody] = []

    async def store(kind: str, ticker: str, start: dt.datetime, end: dt.datetime) -> int:
        body, by_model = await briefs.build_brief(
            ctx.pool,
            kind,
            start,
            end,
            min_severity=news.analysis_min_severity,
            max_lines=news.brief_max_lines,
            ticker=ticker or None,
            writer=writer,
            sentiment_weight=news.sentiment_weight,
        )
        lines = sum(len(s.lines) for s in body.sections)
        if lines == 0:
            return 0
        last_body[:] = [body]
        await ctx.pool.execute(
            "insert into public.news_brief (kind, ticker, period_start, period_end, body, model,"
            " job_id) values ($1, $2, $3, $4, $5::jsonb, $6, $7)"
            " on conflict (kind, ticker, period_start) do update set"
            " period_end = excluded.period_end,"
            " body = excluded.body, model = excluded.model, job_id = excluded.job_id,"
            " created_at = now()",
            kind,
            ticker,
            start,
            end,
            body.model_dump_json(),
            model if by_model else None,
            ctx.job_id,
        )
        return lines

    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    written: dict[str, int] = {}
    async with ctx.step("morning") as step:
        written["morning"] = await store("morning", "", day - dt.timedelta(days=1), now)
        step.detail.update(lines=written["morning"])
        if news.brief_alert and written["morning"] and last_body:
            step.detail.update(alert=await alert_brief(ctx, last_body[0], now))
        step.usage = usage[0].step_usage()
    if now.weekday() == 0:
        week = day - dt.timedelta(days=7)
        usage[0] = Usage(model=model)
        async with ctx.step("weekly") as step:
            written["weekly"] = await store("weekly", "", week, now)
            written["region"] = await store("region", "", week, now)
            rows = await ctx.pool.fetch(
                "select distinct ticker from public.story_cluster where last_seen >= $1"
                " and ticker is not null and severity >= $2 and duplicate_of is null",
                week,
                news.analysis_min_severity,
            )
            holdings = 0
            for r in rows:
                if await store("holding", r["ticker"], week, now):
                    holdings += 1
            step.detail.update(
                lines=written["weekly"], region_lines=written["region"], holdings=holdings
            )
            step.usage = usage[0].step_usage()
        if news.theme_narratives and llm.configured(model) and not state["stopped"]:
            usage[0] = Usage(model=model)
            async with ctx.step("themes") as step:
                step.detail.update(
                    await write_theme_briefs(ctx, news, llm, prompts, usage[0], week, now)
                )
                step.usage = usage[0].step_usage()
