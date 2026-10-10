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
import logging
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from steward.ai import AINotConfigured, Usage, create_client
from steward.budget_guard import BudgetExceeded
from steward.config.jobs import JobsConfig, NewsSettings
from steward.config.prompts import PromptsConfig, load_prompts
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig, ThesisConfig
from steward.jobs.runner import JobContext
from steward.llm.cache import ResultCache
from steward.llm.client import LLM, create_llm
from steward.news import brief as briefs
from steward.news import claims as claim_checks
from steward.news import extract as extraction
from steward.news import framing as framings
from steward.news import impact as impacts
from steward.news import market as markets
from steward.news import novelty as novelties
from steward.news import themes as theme_logic
from steward.news import thesis_link as thesis_links
from steward.news import threads as thread_logic
from steward.news.analysis import (
    AnalysisError,
    StoryMaterial,
    input_hash,
    latest_analysis,
    load_story,
    save_analysis,
)
from steward.settings import get_settings
from steward.xray.supply_chain import SupplyChain

logger = logging.getLogger(__name__)

SELECT_DAYS = 3
MAX_STORIES = 500


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
    """(story id, severity) of the stories seen recently, most severe and newest first."""
    rows = await pool.fetch(
        "select id, coalesce(severity, 0) as severity from public.story_cluster"
        " where last_seen >= $1 and duplicate_of is null"
        " order by severity desc nulls last, last_seen desc limit $2",
        now - dt.timedelta(days=SELECT_DAYS),
        MAX_STORIES,
    )
    return [(r["id"], r["severity"]) for r in rows]


def make_llm() -> LLM:
    settings = get_settings()
    try:
        client = create_client(settings)
    except AINotConfigured:
        client = None
    return create_llm(settings, client)


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

    async def extract(self, story: StoryMaterial, usage: Usage) -> None:
        model = self.news.extraction_model
        system = self.prompts.text("news_extract", extraction.SYSTEM_PROMPT, model)
        hash_ = extraction.extraction_hash(story, model, system)
        if await analysis_exists(self.pool, story.cluster_id, "extraction", hash_):
            return
        try:
            result = await extraction.extract_story(
                self.llm,
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

    async def market(self, story: StoryMaterial, benchmarks: list[str]) -> None:
        ctx = await markets.market_context(self.pool, story, benchmarks)
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
    ) -> None:
        ex = await self.extraction_of(story)
        result, paths = impacts.graph_chains(graph, story, ex, indicators)
        model = self.news.analysis_model
        explain = bool(paths) and self.llm_ready(model)
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
            except BudgetExceeded as exc:
                self.stopped = True
                self.counts.failed("budget", exc)
            except AnalysisError as exc:
                self.counts.failed(f"impact:{story.cluster_id}", exc)
        await self.save(
            story, "impact", hash_, model if explain else None, result.model_dump(mode="json")
        )

    async def framing(self, story: StoryMaterial, usage: Usage) -> None:
        model = self.news.analysis_model
        if not framings.eligible(story, self.news.framing_min_outlets) or not self.llm_ready(model):
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
        hash_ = input_hash(
            "claims",
            model,
            system,
            [c.text for c in ex.key_claims],
            [(i.id, i.text) for i in claim_checks.primaries(story)],
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

    async def thesis(self, story: StoryMaterial, theses: ThesesConfig | None, usage: Usage) -> None:
        model = self.news.analysis_model
        ex = await self.extraction_of(story)
        thesis = thesis_for(theses, story.ticker)
        if thesis is None or ex is None or not self.llm_ready(model):
            return
        system = self.prompts.text("news_thesis", thesis_links.SYSTEM_PROMPT, model)
        hash_ = input_hash(
            "thesis",
            model,
            system,
            story.lifecycle,
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
    model_ids = [sid for sid, severity in picked if severity >= news.analysis_min_severity][
        : news.analysis_per_run
    ]
    stories: dict[int, StoryMaterial] = {}
    for sid, _ in picked:
        story = await load_story(ctx.pool, sid)
        if story is not None and story.items:
            stories[sid] = story

    if "extraction" in run.techniques and news.analysis_per_run:
        usage = Usage(model=news.extraction_model)
        async with ctx.step("extract") as step:
            if run.llm_ready(news.extraction_model):
                for sid in model_ids:
                    if sid in stories and not run.stopped:
                        await run.extract(stories[sid], usage)
            else:
                step.detail.update(skipped=f"no API key for {news.extraction_model}")
            step.detail.update(run.counts.detail())
            step.usage = usage.step_usage()

    async with ctx.step("code") as step:
        benchmarks = [str(s) for s in config.prices.benchmark_symbols]
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
                        await run.market(story, benchmarks)
                except (asyncpg.PostgresError, ValueError) as exc:
                    run.counts.failed(f"{technique}:{story.cluster_id}", exc)
        if "thread" in run.techniques:
            await thread_logic.quiet_old_threads(ctx.pool, now, news.thread_window_days)
        if "themes" in run.techniques:
            step.detail.update(theme_rows=await theme_logic.update_themes(ctx.pool, now.date()))
        step.detail.update(run.counts.detail())

    wants_model = run.techniques & {"impact", "framing", "claims", "thesis"}
    if wants_model and news.analysis_per_run:
        usage = Usage(model=news.analysis_model)
        async with ctx.step("analyse") as step:
            graph, indicators, graph_note = (
                await load_graph(ctx.pool) if "impact" in run.techniques else (None, {}, None)
            )
            if graph_note:
                step.detail.update(graph=graph_note)
            theses_stored = await load_active(ctx.pool, "theses", ThesesConfig)
            theses = theses_stored.body if theses_stored else None
            for sid in model_ids:
                story = stories.get(sid)
                if story is None:
                    continue
                if "impact" in run.techniques and graph is not None:
                    await run.impact(story, graph, indicators, usage)
                if "framing" in run.techniques:
                    await run.framing(story, usage)
                if "claims" in run.techniques:
                    await run.claims(story, usage)
                if "thesis" in run.techniques:
                    await run.thesis(story, theses, usage)
            step.detail.update(run.counts.detail())
            step.usage = usage.step_usage()


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
        )
        lines = sum(len(s.lines) for s in body.sections)
        if lines == 0:
            return 0
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
        step.usage = usage[0].step_usage()
    if now.weekday() == 0:
        week = day - dt.timedelta(days=7)
        usage[0] = Usage(model=model)
        async with ctx.step("weekly") as step:
            written["weekly"] = await store("weekly", "", week, now)
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
            step.detail.update(lines=written["weekly"], holdings=holdings)
            step.usage = usage[0].step_usage()
