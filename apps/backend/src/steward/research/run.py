"""`research` (on demand: params {ticker, profile?, name?}): a deep research report run as a
checkpointed chain of steps under a research profile.

1. gather (code): the source registry and the code sections (figures from stored records);
2. one step per model-written section (the profile's model, web search on allowed tiers);
3. the claim verifier over every model-written sentence;
4. store the report.

Each completed step's output is a `job_checkpoint`. If the worker is killed mid-run, the
queue redelivers the job and the run resumes: completed steps are read back, not redone
(and not paid for again). The profile's step budget (model calls plus searches) is spread
over the model sections; a section the budget does not reach says so.
"""

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from steward.ai import AINotConfigured
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.news_sources import NewsSourcesConfig
from steward.config.prompts import load_prompts
from steward.config.research import MODEL_SECTIONS, SENTENCES_BY_DEPTH, ResearchProfilesConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig
from steward.config.verification import VerificationConfig
from steward.jobs.runner import JobContext, JobFailed
from steward.llm.client import create_llm
from steward.llm.routing import routing_client
from steward.news.normalize import host_of
from steward.research.gather import Gathered, Valuation, gather
from steward.research.sources import CitedSentence, Registry, Source
from steward.research.writer import (
    SECTION_TITLES,
    LLMResearchWriter,
    ResearchWriter,
    SectionRequest,
)
from steward.research.writer import SYSTEM as WRITER_SYSTEM
from steward.settings import get_settings
from steward.verify.claims import SYSTEM as VERIFIER_SYSTEM
from steward.verify.claims import (
    Claim,
    ClaimCheckSummary,
    ClaimVerifier,
    LLMClaimVerifier,
    check_claims,
)


class ResearchSection(BaseModel):
    id: str
    title: str
    written_by: str = Field(description="code (figures from stored records) or model")
    sentences: list[CitedSentence]
    note: str | None = None


class ResearchBody(BaseModel):
    ticker: str
    name: str
    owned: bool
    headline: str
    profile_id: str
    profile_name: str
    depth: str
    model: str
    language: str
    sections: list[ResearchSection]
    valuation: list[Valuation]
    sources: list[Source]
    claims: ClaimCheckSummary | None = None
    steps_used: int = 0
    searches_used: int = 0
    dropped_sentences: int = Field(
        default=0, description="Model sentences removed for citing nothing checkable."
    )
    notes: list[str] = Field(default_factory=list)


@dataclass
class ResearchDeps:
    writer: ResearchWriter | None
    verifier: ClaimVerifier | None
    now: dt.datetime
    app_url: str | None = None


def _source_line(s: Source) -> str:
    text = (s.snippet or "")[:600]
    return f"[{s.id}] ({s.kind}, tier {s.tier or '?'}, {s.as_of or ''}) {s.title}: {text}"


def material_text(g: Gathered, reg: Registry) -> str:
    lines = [f"Company context: {g.context}", "", "Sources:"]
    lines += [_source_line(s) for s in reg.sources]
    lines += ["", "Already written from stored records (cite the same ids if you use them):"]
    for section in g.sections.values():
        for sentence in section.sentences:
            lines.append(f"- {sentence.text} [{', '.join(sentence.source_ids)}]")
    return "\n".join(lines)


def added_material_text(reg: Registry, start: int) -> str:
    """Sources earlier sections found by web search (registry entries from `start` on)."""
    added = reg.sources[start:]
    if not added:
        return ""
    return "\n".join(["More sources, found by web search:", *(_source_line(s) for s in added)])


def search_domains(
    profiles: HoldingProfilesConfig, registry: NewsSourcesConfig, ticker: str, tiers: list[int]
) -> tuple[tuple[str, ...], Any]:
    profile = profiles.for_ticker(ticker)
    ir = profile.allowed_domains() if profile else []
    domains = [
        *(ir if 1 in tiers else []),
        *(d.domain for d in registry.domain_tiers if d.tier in tiers),
    ]

    def tier_of(host: str | None) -> int | None:
        if host and any(host == d or host.endswith(f".{d}") for d in ir):
            return 1
        return registry.domain_tier(host)

    return tuple(dict.fromkeys(domains))[:60], tier_of


async def run_research(ctx: JobContext, deps: ResearchDeps) -> int:
    pool = ctx.pool
    ticker = str(ctx.params.get("ticker") or "").strip()
    if not ticker:
        raise JobFailed("research needs a ticker")
    async with ctx.step("load_config") as step:
        research_v = await load_active(pool, "research_profiles", ResearchProfilesConfig)
        theses_v = await load_active(pool, "theses", ThesesConfig)
        strategy_v = await load_active(pool, "strategy", StrategyConfig)
        profiles_v = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
        sources_v = await load_active(pool, "news_sources", NewsSourcesConfig)
        verification_v = await load_active(pool, "verification", VerificationConfig)
        for v in (research_v, theses_v, strategy_v, profiles_v, sources_v, verification_v):
            await ctx.record_config_version(v.id if v else None)
        research = research_v.body if research_v else ResearchProfilesConfig()
        profile = research.get(ctx.params.get("profile"))
        if profile is None:
            raise JobFailed(f"no research profile {ctx.params.get('profile')!r}")
        theses = theses_v.body if theses_v else ThesesConfig()
        thesis = next((t for t in theses.theses if t.ticker == ticker), None)
        strategy = strategy_v.body if strategy_v else None
        holding_profiles = profiles_v.body if profiles_v else HoldingProfilesConfig()
        news_sources = sources_v.body if sources_v else NewsSourcesConfig()
        verification = verification_v.body if verification_v else VerificationConfig()
        claim_settings = verification.claim_verifier
        step.detail.update(ticker=ticker, profile=profile.id, resumed=ctx.resumed)

    async def do_gather() -> dict[str, Any]:
        async with ctx.step("gather") as step:
            g = await gather(
                pool,
                ticker,
                profile,
                thesis,
                strategy,
                strategy_v.id if strategy_v else None,
                ctx.params.get("name"),
            )
            step.detail.update(sources=len(g.sources), code_sections=sorted(g.sections))
            return g.model_dump(mode="json")

    gathered = Gathered.model_validate(await ctx.checkpoint("gather", do_gather))
    reg = Registry.load(gathered.sources)
    domains, tier_of = search_domains(
        holding_profiles, news_sources, ticker, list(profile.source_tiers)
    )
    model_sections = [s for s in profile.sections if s in MODEL_SECTIONS]
    per_section_searches = (
        math.ceil(profile.max_searches / len(model_sections)) if model_sections else 0
    )
    budget = {"steps": 0, "searches": 0}
    written: dict[str, dict[str, Any]] = {}
    # The gathered material is identical for every section (the writer caches it); sources
    # added by search go in a separate, per-section part.
    material = material_text(gathered, reg)
    gathered_count = len(reg.sources)

    for index, section in enumerate(model_sections):

        async def do_section(section: str = section, index: int = index) -> dict[str, Any]:
            left = profile.max_steps - budget["steps"]
            out: dict[str, Any] = {"sentences": [], "new_sources": [], "steps": 0, "searches": 0}
            if deps.writer is None:
                out["note"] = "not written: no AI provider key is set"
                return out
            if left < 1:
                out["note"] = "not written: the profile's step budget is used up"
                return out
            async with ctx.step(f"section:{section}") as step:
                remaining_sections = len(model_sections) - index
                searches = max(
                    0,
                    min(
                        per_section_searches,
                        profile.max_searches - budget["searches"],
                        left - remaining_sections,
                    ),
                )
                result = await deps.writer.write(
                    SectionRequest(
                        section=section,
                        name=gathered.name,
                        ticker=ticker,
                        material=material,
                        added_material=added_material_text(reg, gathered_count),
                        language=profile.language,
                        max_sentences=SENTENCES_BY_DEPTH[profile.depth],
                        domains=domains,
                        max_searches=searches,
                        peers=gathered.peers,
                        today=deps.now.date(),
                    )
                )
                step.usage = result.usage.step_usage()
                out["steps"] = 1 + result.usage.searches
                out["searches"] = result.usage.searches
                if result.draft is None:
                    out["note"] = f"not written: {result.note}"
                    return out
                known = reg.ids()
                dropped = 0
                for d in result.draft.sentences[: SENTENCES_BY_DEPTH[profile.depth]]:
                    ids = [i for i in d.source_ids if i in known]
                    for url in d.urls:
                        tier = tier_of(host_of(url))
                        if (
                            url in result.titles
                            and tier is not None
                            and tier in profile.source_tiers
                        ):
                            before = len(reg.sources)
                            sid = reg.add("search", url, result.titles[url], url=url, tier=tier,
                                          snippet=result.titles[url])  # fmt: skip
                            if len(reg.sources) > before:
                                out["new_sources"].append(reg.sources[-1].model_dump())
                            ids.append(sid)
                    if not d.text.strip() or not ids:
                        dropped += 1
                        continue
                    out["sentences"].append(
                        CitedSentence(
                            text=d.text.strip(),
                            source_ids=list(dict.fromkeys(ids)),
                            written_by="model",
                        ).model_dump()
                    )
                out["dropped"] = dropped
                step.detail.update(
                    sentences=len(out["sentences"]), dropped=dropped, searches=searches
                )
            return out

        output = await ctx.checkpoint(f"section:{section}", do_section)
        for src in output.get("new_sources", []):
            source = Source.model_validate(src)
            reg.add(source.kind, source.ref, source.title, **source.model_dump(
                exclude={"id", "kind", "ref", "title"}))  # fmt: skip
        budget["steps"] += int(output.get("steps", 0))
        budget["searches"] += int(output.get("searches", 0))
        written[section] = output

    model_sentences = [
        (section, i, CitedSentence.model_validate(s))
        for section, out in written.items()
        for i, s in enumerate(out["sentences"])
    ]

    async def do_claims() -> dict[str, Any]:
        if not model_sentences or not claim_settings.enabled:
            return {"results": {}, "summary": None}
        async with ctx.step("verify_claims") as step:
            claims = [
                Claim(n, s.text, reg.evidence(s.source_ids))
                for n, (_, _, s) in enumerate(model_sentences)
            ]
            outcome = await check_claims(claims, claim_settings, deps.verifier, deps.now.date())
            if outcome.usage is not None:
                step.usage = outcome.usage.step_usage()
            removed = sum(
                1 for r in outcome.results.values()
                if r.status == "unsupported" and claim_settings.mode == "strip"
            )  # fmt: skip
            summary = outcome.summary(claim_settings.mode, removed)
            step.detail.update(summary.model_dump(exclude_none=True))
            return {
                "results": {str(k): [r.status, r.reason] for k, r in outcome.results.items()},
                "summary": summary.model_dump(),
            }

    checked = await ctx.checkpoint("claims", do_claims)
    for n, (written_in, i, _sentence) in enumerate(model_sentences):
        status = checked["results"].get(str(n))
        stored_sentence = written[written_in]["sentences"][i]
        if status:
            stored_sentence["check"], stored_sentence["check_reason"] = status

    async with ctx.step("store") as step:
        existing = await pool.fetchval(
            "select id from public.report where job_id = $1 and type = 'research'", ctx.job_id
        )
        if existing is not None:
            step.detail["report_id"] = existing
            return int(existing)
        sections: list[ResearchSection] = []
        notes: list[str] = []
        dropped = 0
        for section in profile.sections:
            if section in MODEL_SECTIONS:
                out = written.get(section, {"sentences": []})
                dropped += int(out.get("dropped", 0))
                kept = [
                    CitedSentence.model_validate(s)
                    for s in out["sentences"]
                    if not (s.get("check") == "unsupported" and claim_settings.mode == "strip")
                ]
                sections.append(
                    ResearchSection(
                        id=section,
                        title=SECTION_TITLES[section],
                        written_by="model",
                        sentences=kept,
                        note=out.get("note"),
                    )
                )
            else:
                code = gathered.sections.get(section)
                sections.append(
                    ResearchSection(
                        id=section,
                        title=SECTION_TITLES[section],
                        written_by="code",
                        sentences=code.sentences if code else [],
                        note=code.note if code else None,
                    )
                )
        if profile.language == "cs":
            notes.append("Sections written from stored records are in English.")
        body = ResearchBody(
            ticker=ticker,
            name=gathered.name,
            owned=gathered.owned,
            headline=f"{gathered.name}: {profile.name}, {sum(len(s.sentences) for s in sections)} "
            f"cited sentences from {len(reg.sources)} sources",
            profile_id=profile.id,
            profile_name=profile.name,
            depth=profile.depth,
            model=profile.model,
            language=profile.language,
            sections=sections,
            valuation=gathered.valuation,
            sources=reg.sources,
            claims=ClaimCheckSummary.model_validate(checked["summary"])
            if checked["summary"]
            else None,
            steps_used=budget["steps"],
            searches_used=budget["searches"],
            dropped_sentences=dropped,
            notes=notes,
        )
        report_id: int = await pool.fetchval(
            "insert into public.report (type, ticker, title, body, body_md, profile,"
            " config_version_ids, job_id, period_end) values ('research', $1, $2, $3::jsonb,"
            " $4, $5, $6, $7, $8) returning id",
            ticker,
            f"Research: {gathered.name} ({profile.name})"[:300],
            body.model_dump_json(),
            render_md(body),
            profile.id,
            ctx.config_version_ids,
            ctx.job_id,
            deps.now,
        )
        step.detail["report_id"] = report_id
    return report_id


def render_md(body: ResearchBody) -> str:
    lines = [f"# {body.name} ({body.ticker})", "", body.headline, ""]
    for section in body.sections:
        lines += [f"## {section.title}", ""]
        for s in section.sentences:
            mark = " [not supported by its sources]" if s.check == "unsupported" else ""
            lines.append(f"{s.text} [{', '.join(s.source_ids)}]{mark}")
        if section.note:
            lines.append(f"_{section.note}_")
        lines.append("")
    if body.valuation:
        lines += ["## Valuation figures (computed in code)", ""]
        lines += [f"- {v.method}: {v.display} ({v.formula})" for v in body.valuation]
        lines.append("")
    lines += ["## Sources", ""]
    for src in body.sources:
        where = src.url or src.app_path or src.ref
        lines.append(f"- [{src.id}] {src.title} (tier {src.tier or '?'}): {where}")
    return "\n".join(lines)


async def research_handler(ctx: JobContext) -> None:
    settings = get_settings()
    prompts = await load_prompts(ctx.pool)
    research = await load_active(ctx.pool, "research_profiles", ResearchProfilesConfig)
    verification = await load_active(ctx.pool, "verification", VerificationConfig)
    profile = (research.body if research else ResearchProfilesConfig()).get(
        ctx.params.get("profile")
    )
    writer: ResearchWriter | None
    verifier: ClaimVerifier | None
    try:
        client = routing_client(create_llm(settings))
    except AINotConfigured:
        writer = verifier = None
    else:
        writer = (
            LLMResearchWriter(
                client,
                profile.model,
                profile.effort,
                prompts.text("research_writer", WRITER_SYSTEM),
            )
            if profile
            else None
        )
        claim_settings = (
            verification.body if verification else VerificationConfig()
        ).claim_verifier
        verifier = LLMClaimVerifier(
            client,
            claim_settings.model,
            prompts.text("claim_verifier", VERIFIER_SYSTEM),
            effort=claim_settings.effort,
        )
    await run_research(
        ctx,
        ResearchDeps(
            writer=writer, verifier=verifier, now=dt.datetime.now(dt.UTC), app_url=settings.app_url
        ),
    )
