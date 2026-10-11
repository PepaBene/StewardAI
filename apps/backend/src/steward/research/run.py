"""`research` (on demand: params {ticker, profile?, name?}): a deep research report run as a
checkpointed chain of steps under a research profile and a report template.

1. plan (code): the questions, tools, web intents, source tiers, counter-evidence search and
   stop conditions, written down before anything is searched;
2. gather (code): the source registry and the code sections (figures from stored records);
3. read_tools (code, no model): the plan's stored-data tools, the same typed read tools chat
   uses, called with bounded arguments; their sources join the registry and their results
   go to the sections that planned them as extra evidence, with a record of each call;
4. one step per model-written section (the profile's model, web search on allowed sources);
5. follow-up rounds (profiles that iterate): sections left thin are searched again, single-
   source claims are checked against a second source, until the sections have enough or the
   budget is used;
6. the claim verifier over every model-written sentence;
7. store the report, with the run snapshot and, by authoring mode, a thesis proposal or review.

Each completed step's output is a `job_checkpoint`. If the worker is killed mid-run, the
queue redelivers the job and the run resumes: completed steps are read back, not redone
(and not paid for again). The profile's step budget (model calls plus searches) is spread
over the model sections, less a reserve for follow-up rounds; a section the budget does not
reach says so.
"""

import datetime as dt
import math
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from steward.ai import AINotConfigured, Usage
from steward.chat.tools import ChatTools
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.news_sources import NewsSourcesConfig
from steward.config.prompts import load_prompts
from steward.config.research import SENTENCES_BY_DEPTH, ResearchProfilesConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig
from steward.config.verification import VerificationConfig
from steward.jobs.runner import JobContext, JobFailed
from steward.llm.client import create_llm
from steward.llm.routing import routing_client
from steward.news.normalize import host_of
from steward.research.gather import Gathered, Valuation, gather
from steward.research.layout import SectionSpec, investigation_profile, resolve_layout
from steward.research.ledger import ClaimLedger, SectionInput, build_ledger
from steward.research.model_plan import (
    LLMResearchPlanner,
    PlannerResult,
    ResearchPlanner,
    get_draft,
    make_plan,
    same_layout,
)
from steward.research.plan import ResearchPlan, build_plan, effective_searches, focus_for
from steward.research.read_tools import (
    ReadToolRun,
    ToolCallRecord,
    comparison,
    evidence_for,
    run_read_tools,
)
from steward.research.rules import load_compliance
from steward.research.snapshot import RunSnapshot, take_snapshot
from steward.research.source_policy import PRIORITY_TEXT, SourcePolicy
from steward.research.sources import CitedSentence, Registry, Source
from steward.research.trace import (
    ModelCallTrace,
    ResearchTrace,
    RetrievalTrace,
    ValidationTrace,
    clip,
    finish,
    web_trace,
)
from steward.research.untrusted import flag_sources, neutralise, strip_outbound
from steward.research.views import View, apply_view
from steward.research.web import DiscoveredUrl, SearchHit, discovered, merge_discovered
from steward.research.writer import SYSTEM as WRITER_SYSTEM
from steward.research.writer import (
    LLMResearchWriter,
    ResearchWriter,
    SectionRequest,
)
from steward.settings import get_settings
from steward.thesis.proposal import (
    ThesisProposal,
    ThesisReview,
    build_proposal,
    review_thesis,
)
from steward.verify.claims import SYSTEM as VERIFIER_SYSTEM
from steward.verify.claims import (
    Claim,
    ClaimCheckSummary,
    ClaimVerifier,
    LLMClaimVerifier,
    check_claims,
)

FOLLOW_UP_SENTENCES = 3
FOLLOW_UP_SEARCHES = 2
MAX_TARGETS_PER_ROUND = 3
MAX_CORROBORATE = 3


class ResearchSection(BaseModel):
    id: str
    title: str
    written_by: str = Field(description="code (figures from stored records) or model")
    sentences: list[CitedSentence]
    note: str | None = None
    detail_only: bool = Field(default=False, description="Left out of the concise view.")
    custom: bool = Field(default=False, description="A section the owner defined.")


class RoundRecord(BaseModel):
    round: int
    sections: list[str]
    reasons: dict[str, str]
    sentences_added: int = 0
    sources_added: int = 0
    urls_found: int = 0
    steps: int = 0


class SearchSummary(BaseModel):
    sources_after_first_pass: int = 0
    sources_added_by_follow_up: int = 0
    urls_found: int = 0
    urls_cited: int = 0
    urls_excluded: int = 0
    limitations: list[str] = Field(default_factory=list)


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
    # Phase 2: what the run planned and what its search found.
    mode: str = "standard"
    plan: ResearchPlan | None = None
    rounds: list[RoundRecord] = Field(default_factory=list)
    stopped_because: str | None = None
    search_summary: SearchSummary | None = None
    discovered_urls: list[DiscoveredUrl] = Field(default_factory=list)
    # Phase 3: how it is shown, what it was made from, and what it proposes for the thesis.
    view: str = "detailed"
    default_view: str = "detailed"
    template_id: str | None = None
    authoring: str = "research_only"
    snapshot: RunSnapshot | None = None
    thesis_proposal: ThesisProposal | None = None
    thesis_review: ThesisReview | None = None
    # Phase 4: every sentence as a typed, checked claim; Phase 5: what untrusted text did.
    ledger: ClaimLedger | None = None
    flagged_sources: list[str] = Field(
        default_factory=list, description="Sources with instruction-like text, treated as data."
    )
    links_removed: int = Field(
        default=0, description="Links or images in model text that were not report sources."
    )
    trace_purged_at: str | None = Field(
        default=None, description="When the owner deleted the run's working records."
    )
    # The plan's stored-data tools as the run called them (the same tools chat uses), one
    # compact record per call, so research and chat can be compared. Older reports have none.
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    # Per-call trace of the run (model calls, searches, retrieval, output validation): metadata
    # only, removed by the purge and the retention job. Older reports have none.
    trace: ResearchTrace | None = None


@dataclass
class ResearchDeps:
    writer: ResearchWriter | None
    verifier: ClaimVerifier | None
    now: dt.datetime
    app_url: str | None = None
    prompts: dict[str, str] = field(default_factory=dict)  # the system texts, for the snapshot
    planner: ResearchPlanner | None = None  # the model planning step; None = code plan


def _source_line(s: Source) -> str:
    text = neutralise(s.snippet)[:600]
    label = f"tier {s.tier or '?'}" if s.role is None else f"{s.role}, tier {s.tier or '?'}"
    caveat = f" [{s.caveat}]" if s.caveat else ""
    return f"[{s.id}] ({s.kind}, {label}, {s.as_of or ''}) {neutralise(s.title)}: {text}{caveat}"


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

    return tuple(dict.fromkeys(domains)), tier_of


def thin_sections(
    specs: list[SectionSpec], written: dict[str, dict[str, Any]], minimum: int
) -> dict[str, str]:
    """Model sections with fewer cited sentences than the profile needs, and why. A section
    the run could not write at all (no provider key, budget used up) is not a gap a follow-up
    could fill, so it is left out."""
    out: dict[str, str] = {}
    for spec in specs:
        got = written.get(spec.id)
        if got is None or str(got.get("note", "")).startswith("not written"):
            continue
        n = len(got.get("sentences", []))
        if n < minimum:
            out[spec.id] = f"only {n} cited sentence(s); the profile needs {minimum}"
    return out


def single_source_claims(
    specs: list[SectionSpec], written: dict[str, dict[str, Any]], reg: Registry
) -> dict[str, list[str]]:
    """Model sentences resting on one secondary source (a search result or a news item): the
    ones worth a second, independent source."""
    out: dict[str, list[str]] = {}
    for spec in specs:
        for s in written.get(spec.id, {}).get("sentences", []):
            found = [r for i in s.get("source_ids", []) if (r := reg.get(i)) is not None]
            # Two ids from one publisher are still one source.
            publishers = {(host_of(r.url) if r.url else None) or r.id for r in found}
            if len(publishers) == 1 and all(
                r.kind in ("search", "news_item") and r.tier != 1 and r.role != "primary"
                for r in found
            ):
                out.setdefault(spec.id, []).append(s["text"])
    return {k: v[:MAX_CORROBORATE] for k, v in out.items()}


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
        template = research.template_for(profile)
        theses = theses_v.body if theses_v else ThesesConfig()
        thesis = next((t for t in theses.theses if t.ticker == ticker), None)
        strategy = strategy_v.body if strategy_v else None
        holding_profiles = profiles_v.body if profiles_v else HoldingProfilesConfig()
        news_sources = sources_v.body if sources_v else NewsSourcesConfig()
        verification = verification_v.body if verification_v else VerificationConfig()
        claim_settings = verification.claim_verifier
        layout = resolve_layout(profile, template)
        step.detail.update(
            ticker=ticker,
            profile=profile.id,
            template=template.id if template else None,
            resumed=ctx.resumed,
        )

    async def do_gather() -> dict[str, Any]:
        async with ctx.step("gather") as step:
            g = await gather(
                pool,
                ticker,
                investigation_profile(profile, layout),
                thesis,
                strategy,
                strategy_v.id if strategy_v else None,
                ctx.params.get("name"),
            )
            step.detail.update(sources=len(g.sources), code_sections=sorted(g.sections))
            return g.model_dump(mode="json")

    gathered = Gathered.model_validate(await ctx.checkpoint("gather", do_gather))
    reg = Registry.load(gathered.sources)
    flagged = flag_sources(reg.sources)
    code_plan = build_plan(
        profile, layout, ticker=ticker, name=gathered.name, today=deps.now.date()
    )
    prices = await ctx.price_table()
    trace_parts: list[dict[str, Any]] = []

    def cost_of(usage: Usage) -> float | None:
        cost = prices.cost(
            usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            searches=usage.searches,
        )
        return float(cost) if cost is not None else None

    async def do_plan() -> dict[str, Any]:
        async with ctx.step("plan") as step:
            draft_id = ctx.params.get("plan_draft_id")
            result: PlannerResult | None = None
            if draft_id is not None:
                draft = await get_draft(pool, int(draft_id))
                if (
                    draft is None
                    or draft.ticker != ticker
                    or draft.status
                    not in (
                        "approved",
                        "started",
                    )
                ):
                    reason = "the approved plan draft was not found; the code-built plan is used"
                    chosen = code_plan.model_copy(update={"fallback_reason": reason})
                elif not same_layout(draft.plan, code_plan.planned_sections):
                    reason = "the profile or template changed after the draft; code plan used"
                    chosen = code_plan.model_copy(update={"fallback_reason": reason})
                else:
                    chosen = draft.plan.model_copy(
                        update={
                            "draft_id": draft.id,
                            "edited_by_owner": draft.edited or draft.plan.edited_by_owner,
                        }
                    )
            else:
                chosen, result = await make_plan(
                    code_plan,
                    profile,
                    deps.planner,
                    name=gathered.name,
                    use_model=profile.planning == "model",
                )
            part: dict[str, Any] | None = None
            if result is not None:
                step.usage = result.usage.step_usage()
                outcome = (
                    "ok"
                    if chosen.authored_by == "model"
                    else ("schema_failure" if result.schema_failure else "failed")
                )
                part = {
                    "model": ModelCallTrace(
                        step="plan",
                        model=result.usage.model,
                        input_tokens=result.usage.input_tokens,
                        output_tokens=result.usage.output_tokens,
                        cache_read_tokens=result.usage.cache_read_tokens,
                        cost_usd=cost_of(result.usage),
                        ms=result.ms,
                        outcome=outcome,  # type: ignore[arg-type]
                        note=clip(chosen.fallback_reason or "", 160) or None,
                    ).model_dump()
                }
            step.detail.update(
                mode=chosen.mode,
                questions=len(chosen.questions),
                tools=len(chosen.internal_tools),
                counter_search=bool(chosen.counter_evidence_intent),
                iterate=chosen.iterate,
                authored_by=chosen.authored_by,
                edited_by_owner=chosen.edited_by_owner,
                fallback=chosen.fallback_reason,
            )
            return {"plan": chosen.model_dump(mode="json"), "trace": part}

    planned = await ctx.checkpoint("plan", do_plan)
    plan = ResearchPlan.model_validate(planned["plan"])
    if planned.get("trace"):
        trace_parts.append(planned["trace"])

    async def do_read_tools() -> dict[str, Any]:
        async with ctx.step("read_tools") as step:
            tools = ChatTools(pool, reg, None, None)
            outcome = await run_read_tools(tools.call, reg, plan, ticker)
            step.detail.update(
                calls=len(outcome.records),
                by_status=comparison(outcome.records),
                sources_added=len(outcome.sources),
            )
            return outcome.model_dump(mode="json")

    registry_size = len(reg.sources)
    tool_run = ReadToolRun.model_validate(await ctx.checkpoint("read_tools", do_read_tools))
    if len(reg.sources) < registry_size + len(tool_run.sources):
        # Resumed: the tools did not run again, so put the sources they added back, in order.
        for src in tool_run.sources:
            reg.add(
                src.kind,
                src.ref,
                src.title,
                **src.model_dump(exclude={"id", "kind", "ref", "title"}),
            )
    snapshot = take_snapshot(
        now=deps.now,
        profile=profile.model_dump(mode="json"),
        template=template.model_dump(mode="json") if template else None,
        layout=[
            {"id": s.id, "title": s.title, "written_by": s.written_by, "custom": s.custom}
            for s in layout
        ],
        prompts=deps.prompts,
        versions={
            "strategy": strategy_v.id if strategy_v else None,
            "theses": theses_v.id if theses_v else None,
            "research_profiles": research_v.id if research_v else None,
            "holding_profiles": profiles_v.id if profiles_v else None,
            "news_sources": sources_v.id if sources_v else None,
            "verification": verification_v.id if verification_v else None,
        },
        thesis=thesis.model_dump(mode="json") if thesis else None,
        plan=plan.model_dump(mode="json"),
    )

    tiered, tier_of = search_domains(
        holding_profiles, news_sources, ticker, list(profile.source_tiers)
    )
    policy = SourcePolicy.for_profile(
        profile, tier_of, tuple(d.removeprefix("www.") for d in news_sources.blocked_domains)
    )
    domains = policy.search_domains(tiered)
    model_specs = [s for s in layout if s.written_by == "model"]
    total_searches = effective_searches(profile)
    reserve_steps = min(profile.max_steps // 3, profile.max_rounds * 3) if profile.iterate else 0
    reserve_searches = total_searches // 3 if profile.iterate else 0
    first_steps = profile.max_steps - reserve_steps
    first_searches = total_searches - reserve_searches
    per_section_searches = math.ceil(first_searches / len(model_specs)) if model_specs else 0
    budget = {"steps": 0, "searches": 0}
    written: dict[str, dict[str, Any]] = {}
    # The gathered material is identical for every section (the writer caches it); sources
    # added by search go in a separate, per-section part.
    material = material_text(gathered, reg)
    gathered_count = len(reg.sources)

    async def write_section(
        spec: SectionSpec,
        *,
        round_no: int,
        searches: int,
        max_sentences: int,
        focus: str,
        existing: str,
        step_name: str,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {"sentences": [], "new_sources": [], "steps": 0, "searches": 0}
        if deps.writer is None:
            out["note"] = "not written: no AI provider key is set"
            return out
        async with ctx.step(step_name) as step:
            offered = len(reg.sources)
            started = time.monotonic()
            result = await deps.writer.write(
                SectionRequest(
                    section=spec.id,
                    name=gathered.name,
                    ticker=ticker,
                    material=material,
                    added_material=added_material_text(reg, gathered_count),
                    language=profile.language,
                    max_sentences=max_sentences,
                    domains=domains,
                    max_searches=searches,
                    peers=gathered.peers,
                    today=deps.now.date(),
                    title=spec.title,
                    task=spec.task,
                    focus=focus,
                    policy=PRIORITY_TEXT if searches else "",
                    existing=existing,
                    horizon_days=profile.date_horizon_days,
                    tool_evidence=evidence_for(
                        tool_run, spec.id, first=spec.id == model_specs[0].id
                    ),
                )
            )
            ms = round((time.monotonic() - started) * 1000)
            step.usage = result.usage.step_usage()
            out["steps"] = 1 + result.usage.searches
            out["searches"] = result.usage.searches
            hits = result.hits or [SearchHit(u, t) for u, t in result.titles.items()]
            if result.search_errors:
                out["search_errors"] = result.search_errors
            call_outcome = (
                "ok"
                if result.draft is not None
                else (
                    "schema_failure"
                    if result.schema_failure
                    else ("refused" if "declined" in (result.note or "") else "failed")
                )
            )
            part: dict[str, Any] = {
                "offered": offered,
                "model": ModelCallTrace(
                    step=step_name,
                    section=spec.id,
                    round=round_no,
                    model=result.usage.model,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    cache_read_tokens=result.usage.cache_read_tokens,
                    searches=result.usage.searches,
                    cost_usd=cost_of(result.usage),
                    ms=ms,
                    outcome=call_outcome,  # type: ignore[arg-type]
                    note=clip(result.note or "", 160) or None,
                ).model_dump(),
            }
            out["trace"] = part
            if result.draft is None:
                out["note"] = f"not written: {result.note}"
                found = [discovered(h, policy, spec.id, round_no, set(), deps.now) for h in hits]
                out["discovered"] = [d.model_dump() for d in found]
                part["web"] = web_trace(
                    step_name, spec.id, round_no, result.queries, found, result.search_errors
                ).model_dump()
                part["validation"] = ValidationTrace(
                    step=step_name, section=spec.id, schema_failure=result.schema_failure
                ).model_dump()
                return out
            known = reg.ids()
            dropped = 0
            cited_urls: set[str] = set()
            for d in result.draft.sentences[:max_sentences]:
                ids = [i for i in d.source_ids if i in known]
                for url in d.urls:
                    if url not in result.titles:
                        continue
                    decision = policy.decide(host_of(url))
                    if not decision.allowed:
                        continue
                    before = len(reg.sources)
                    sid = reg.add(
                        "search",
                        url,
                        result.titles[url],
                        url=url,
                        tier=decision.tier,
                        snippet=result.titles[url],
                        role=decision.role,
                        caveat=decision.reason,
                        retrieved_at=deps.now.isoformat(timespec="seconds"),
                    )
                    if len(reg.sources) > before:
                        flag_sources(reg.sources[before:])
                        out["new_sources"].append(reg.sources[-1].model_dump())
                    cited_urls.add(url)
                    ids.append(sid)
                text, removed = strip_outbound(d.text, {s.url for s in reg.sources if s.url})
                out["links_removed"] = int(out.get("links_removed", 0)) + removed
                if not text or not ids:
                    dropped += 1
                    continue
                out["sentences"].append(
                    CitedSentence(
                        text=text,
                        source_ids=list(dict.fromkeys(ids)),
                        written_by="model",
                    ).model_dump()
                )
            out["dropped"] = dropped
            found = [discovered(h, policy, spec.id, round_no, cited_urls, deps.now) for h in hits]
            out["discovered"] = [d.model_dump() for d in found]
            part["web"] = web_trace(
                step_name, spec.id, round_no, result.queries, found, result.search_errors
            ).model_dump()
            part["validation"] = ValidationTrace(
                step=step_name,
                section=spec.id,
                sentences_returned=len(result.draft.sentences[:max_sentences]),
                dropped_uncited=dropped,
                links_removed=int(out.get("links_removed", 0)),
            ).model_dump()
            step.detail.update(
                sentences=len(out["sentences"]),
                dropped=dropped,
                searches=searches,
                urls_found=len(hits),
                round=round_no,
            )
        return out

    def absorb(output: dict[str, Any]) -> None:
        if output.get("trace"):
            trace_parts.append(output["trace"])
        for src in output.get("new_sources", []):
            source = Source.model_validate(src)
            reg.add(
                source.kind,
                source.ref,
                source.title,
                **source.model_dump(exclude={"id", "kind", "ref", "title"}),
            )
        budget["steps"] += int(output.get("steps", 0))
        budget["searches"] += int(output.get("searches", 0))

    for index, spec in enumerate(model_specs):

        async def do_section(spec: SectionSpec = spec, index: int = index) -> dict[str, Any]:
            left = first_steps - budget["steps"]
            if deps.writer is not None and left < 1:
                return {
                    "sentences": [],
                    "new_sources": [],
                    "steps": 0,
                    "searches": 0,
                    "note": "not written: the profile's step budget is used up",
                }
            searches = max(
                0,
                min(
                    per_section_searches,
                    first_searches - budget["searches"],
                    left - (len(model_specs) - index),
                ),
            )
            focus = focus_for(plan, spec.id)
            if index == 0 and plan.questions and plan.questions[0].id == "mode":
                focus = f"- {plan.questions[0].question}\n{focus}".strip()
            return await write_section(
                spec,
                round_no=0,
                searches=searches,
                max_sentences=spec.max_sentences or SENTENCES_BY_DEPTH[profile.depth],
                focus=focus,
                existing="",
                step_name=f"section:{spec.id}",
            )

        output = await ctx.checkpoint(f"section:{spec.id}", do_section)
        absorb(output)
        written[spec.id] = output

    # Follow-up rounds: only for profiles that iterate.
    rounds: list[RoundRecord] = []
    stopped: str | None = None
    first_pass_sources = len(reg.sources)
    if profile.iterate and model_specs:
        stopped = "round limit reached"
        for round_no in range(1, profile.max_rounds + 1):
            thin = thin_sections(model_specs, written, profile.min_sentences)
            single = single_source_claims(model_specs, written, reg)
            targets: dict[str, str] = dict(thin)
            for section_id in single:
                targets.setdefault(section_id, f"{len(single[section_id])} claim(s) on one source")
            if not targets:
                stopped = "every planned section has enough cited evidence"
                break
            steps_left = profile.max_steps - budget["steps"]
            if steps_left < 1:
                stopped = "the step budget is used up"
                break
            record = RoundRecord(round=round_no, sections=[], reasons={})
            before_sources = len(reg.sources)
            urls_before = sum(len(w.get("discovered", [])) for w in written.values())
            for spec in model_specs:
                if spec.id not in targets or len(record.sections) >= MAX_TARGETS_PER_ROUND:
                    continue
                steps_left = profile.max_steps - budget["steps"]
                if steps_left < 1:
                    break
                searches = max(
                    0, min(FOLLOW_UP_SEARCHES, total_searches - budget["searches"], steps_left - 1)
                )
                lines = [focus_for(plan, spec.id)] if spec.id in thin else []
                for text in single.get(spec.id, []):
                    lines.append("- Corroborate or contradict, with an independent source: " + text)
                if spec.id == "counter_case":
                    lines.append("- Look again for the strongest opposing evidence not yet given.")
                existing = " ".join(s["text"] for s in written[spec.id]["sentences"])[:1500]

                async def do_follow_up(
                    spec: SectionSpec = spec,
                    round_no: int = round_no,
                    searches: int = searches,
                    lines: list[str] = lines,
                    existing: str = existing,
                ) -> dict[str, Any]:
                    return await write_section(
                        spec,
                        round_no=round_no,
                        searches=searches,
                        max_sentences=FOLLOW_UP_SENTENCES,
                        focus="\n".join(x for x in lines if x),
                        existing=existing,
                        step_name=f"follow_up:{round_no}:{spec.id}",
                    )

                got = await ctx.checkpoint(f"follow_up:{round_no}:{spec.id}", do_follow_up)
                absorb(got)
                record.sections.append(spec.id)
                record.reasons[spec.id] = targets[spec.id]
                record.sentences_added += len(got["sentences"])
                record.steps += int(got.get("steps", 0))
                base = written[spec.id]
                base["sentences"] = [*base["sentences"], *got["sentences"]]
                base["discovered"] = [*base.get("discovered", []), *got.get("discovered", [])]
                base["dropped"] = int(base.get("dropped", 0)) + int(got.get("dropped", 0))
                base["links_removed"] = int(base.get("links_removed", 0)) + int(
                    got.get("links_removed", 0)
                )
                base["search_errors"] = [
                    *base.get("search_errors", []),
                    *got.get("search_errors", []),
                ]
            record.sources_added = len(reg.sources) - before_sources
            record.urls_found = (
                sum(len(w.get("discovered", [])) for w in written.values()) - urls_before
            )
            rounds.append(record)
            if not record.sections:
                stopped = "the step budget is used up"
                break

    all_discovered: list[DiscoveredUrl] = []
    for spec in model_specs:
        all_discovered = merge_discovered(
            all_discovered,
            [DiscoveredUrl.model_validate(d) for d in written[spec.id].get("discovered", [])],
        )
    limitations = sorted(
        {
            f"{n} URL(s): {text}"
            for text, n in _count([d.limitation for d in all_discovered if d.limitation]).items()
        }
    )
    errors = sorted({e for w in written.values() for e in w.get("search_errors", [])})
    if errors:
        limitations.append(f"search errors: {', '.join(errors)}")
    summary = SearchSummary(
        sources_after_first_pass=first_pass_sources,
        sources_added_by_follow_up=len(reg.sources) - first_pass_sources,
        urls_found=len(all_discovered),
        urls_cited=sum(1 for d in all_discovered if d.status == "cited"),
        urls_excluded=sum(1 for d in all_discovered if d.status == "excluded"),
        limitations=limitations,
    )

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
            claim_summary = outcome.summary(claim_settings.mode, removed)
            step.detail.update(claim_summary.model_dump(exclude_none=True))
            return {
                "results": {str(k): [r.status, r.reason] for k, r in outcome.results.items()},
                "summary": claim_summary.model_dump(),
            }

    checked = await ctx.checkpoint("claims", do_claims)
    check_drops: dict[str, list[str]] = {}
    for n, (written_in, i, _sentence) in enumerate(model_sentences):
        status = checked["results"].get(str(n))
        stored_sentence = written[written_in]["sentences"][i]
        if status:
            stored_sentence["check"], stored_sentence["check_reason"] = status
            if status[0] == "unsupported" and claim_settings.mode == "strip":
                check_drops.setdefault(written_in, []).append(str(status[1] or "unsupported"))

    async with ctx.step("store") as step:
        existing_report = await pool.fetchval(
            "select id from public.report where job_id = $1 and type = 'research'", ctx.job_id
        )
        if existing_report is not None:
            step.detail["report_id"] = existing_report
            return int(existing_report)
        sections: list[ResearchSection] = []
        notes: list[str] = []
        dropped = 0
        for spec in layout:
            if spec.written_by == "model":
                out = written.get(spec.id, {"sentences": []})
                dropped += int(out.get("dropped", 0))
                kept = [
                    CitedSentence.model_validate(s)
                    for s in out["sentences"]
                    if not (s.get("check") == "unsupported" and claim_settings.mode == "strip")
                ]
                sections.append(
                    ResearchSection(
                        id=spec.id,
                        title=spec.title,
                        written_by="model",
                        sentences=kept,
                        note=out.get("note"),
                        detail_only=spec.detail_only,
                        custom=spec.custom,
                    )
                )
            else:
                code = gathered.sections.get(spec.id)
                sections.append(
                    ResearchSection(
                        id=spec.id,
                        title=spec.title,
                        written_by="code",
                        sentences=(code.sentences if code else [])[: spec.max_sentences],
                        note=code.note if code else None,
                        detail_only=spec.detail_only,
                    )
                )
        if profile.language == "cs":
            notes.append("Sections written from stored records are in English.")
        notes.extend(tool_run.notices)
        proposal = build_proposal(
            profile.authoring,
            ticker,
            thesis,
            theses_v.id if theses_v else None,
            {s.id: s.sentences for s in sections if s.written_by == "model"},
        )
        kpi_review = (
            await review_thesis(pool, ticker, thesis, theses_v.id if theses_v else None)
            if thesis is not None
            else None
        )
        review = kpi_review if profile.authoring == "review_thesis" else None
        flagged = sorted(
            {*flagged, *(s.id for s in reg.sources if s.caveat and "instruction-like" in s.caveat)}
        )
        links_removed = sum(int(w.get("links_removed", 0)) for w in written.values())
        if flagged:
            notes.append(
                f"{len(flagged)} source(s) contained instruction-like text; it was treated as "
                "data and their claims are marked low confidence."
            )
        compliance = await load_compliance(pool, strategy, ticker)
        trace = finish(trace_parts, check_drops)
        offered = {
            p["model"]["section"]: int(p["offered"])
            for p in trace_parts
            if "offered" in p and p["model"].get("round") == 0
        }
        for s in sections:
            if s.id not in offered:
                continue
            cited_ids = {i for x in s.sentences for i in x.source_ids}
            trace.retrieval.append(
                RetrievalTrace(
                    section=s.id,
                    sources_offered=offered[s.id],
                    sources_cited=len(cited_ids),
                    sources_uncited=max(0, offered[s.id] - len(cited_ids)),
                    tools_planned=list(
                        dict.fromkeys(
                            t for q in plan.questions if q.section == s.id for t in q.internal_tools
                        )
                    ),
                )
            )
        trace.plan_source = plan.authored_by + (
            f" (fallback: {plan.fallback_reason})" if plan.fallback_reason else ""
        )
        ledger = build_ledger(
            [
                SectionInput(
                    id=s.id,
                    written_by="model" if s.written_by == "model" else "code",
                    sentences=s.sentences,
                    note=s.note,
                )
                for s in sections
            ],
            reg,
            gathered.valuation,
            deps.now,
            kpis=[
                (k.kpi_id, k.rule, k.status, k.rationale)
                for k in (kpi_review.kpis if kpi_review else [])
            ],
            rules=[
                (r.id, f"{r.type} {r.operator} {getattr(r, 'value', '')}".strip())
                for r in (strategy.enabled_rules if strategy else [])
            ],
            stale_days=profile.stale_days,
            kpi_version=theses_v.id if theses_v else None,
            strategy_version=strategy_v.id if strategy_v else None,
            notes=[n for n in notes if "Sections written" not in n],
            rule_compliance=compliance,
        )
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
            mode=profile.mode,
            plan=plan,
            rounds=rounds,
            stopped_because=stopped,
            search_summary=summary,
            discovered_urls=all_discovered,
            default_view=template.default_view if template else "detailed",
            template_id=template.id if template else None,
            authoring=profile.authoring,
            snapshot=snapshot,
            thesis_proposal=proposal,
            thesis_review=review,
            ledger=ledger,
            flagged_sources=flagged,
            links_removed=links_removed,
            tool_calls=tool_run.records,
            trace=trace,
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


def _count(items: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return counts


def render_md(body: ResearchBody, view: View | None = None) -> str:
    """Markdown for a view of the report; the detailed view when none is given. The stored
    `body_md` is the detailed one, so a template's `default_view` only steers readers."""
    shown = apply_view(body, view or "detailed") if (view or "detailed") != "detailed" else body
    lines = [f"# {shown.name} ({shown.ticker})", "", shown.headline, ""]
    ledger = shown.ledger
    if ledger:
        span_cov = ledger.coverage
        span = (
            f", data from {span_cov.data_from} to {span_cov.data_to}" if span_cov.data_from else ""
        )
        lines += [
            f"_As of {span_cov.as_of[:10]}{span}. Evidence confidence: {ledger.confidence} "
            f"({ledger.confidence_why})._",
            *[f"_{n}_" for n in span_cov.notices],
            "",
        ]
    by_position = {(c.section_id, c.index): c for c in ledger.claims} if ledger else {}
    for section in shown.sections:
        lines += [f"## {section.title}", ""]
        for i, s in enumerate(section.sentences):
            mark = " [not supported by its sources]" if s.check == "unsupported" else ""
            claim = by_position.get((section.id, i))
            tag = f" ({claim.type.replace('_', ' ')}, {claim.confidence})" if claim else ""
            lines.append(f"{s.text} [{', '.join(s.source_ids)}]{tag}{mark}")
        if section.note:
            lines.append(f"_{section.note}_")
        lines.append("")
    if shown.valuation:
        lines += ["## Valuation figures (computed in code)", ""]
        lines += [f"- {v.method}: {v.display} ({v.formula})" for v in shown.valuation]
        lines.append("")
    if shown.thesis_proposal and shown.thesis_proposal.changes:
        lines += ["## Proposed thesis changes (not applied)", ""]
        for change in shown.thesis_proposal.changes:
            lines.append(f"- {change.field} ({change.kind}):")
            lines += [f"  - {line.text} [{', '.join(line.evidence_ids)}]" for line in change.after]
        lines.append("")
    if shown.view == "detailed" and shown.search_summary:
        cover = shown.search_summary
        lines += [
            "## Search coverage",
            "",
            f"{cover.urls_found} URLs found, {cover.urls_cited} cited, "
            f"{cover.urls_excluded} excluded; {cover.sources_added_by_follow_up} sources "
            "added by follow-up rounds."
            + (f" Stopped: {shown.stopped_because}." if shown.stopped_because else ""),
            *[f"- {m}" for m in cover.limitations],
            "",
        ]
    lines += ["## Sources", ""]
    for src in shown.sources:
        where = src.url or src.app_path or src.ref
        label = (
            f"tier {src.tier or '?'}" if src.role is None else f"{src.role}, tier {src.tier or '?'}"
        )
        lines.append(f"- [{src.id}] {src.title} ({label}): {where}")
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
    planner: ResearchPlanner | None = None
    writer_system = prompts.text("research_writer", WRITER_SYSTEM)
    verifier_system = prompts.text("claim_verifier", VERIFIER_SYSTEM)
    try:
        client = routing_client(create_llm(settings))
    except AINotConfigured:
        writer = verifier = None
    else:
        cheap = (verification.body if verification else VerificationConfig()).claim_verifier.model
        if profile and profile.planning == "model":
            planner = LLMResearchPlanner(client, profile.planner_model or cheap)
        writer = (
            LLMResearchWriter(client, profile.model, profile.effort, writer_system)
            if profile
            else None
        )
        claim_settings = (
            verification.body if verification else VerificationConfig()
        ).claim_verifier
        verifier = LLMClaimVerifier(
            client,
            claim_settings.model,
            verifier_system,
            effort=claim_settings.effort,
        )
    await run_research(
        ctx,
        ResearchDeps(
            writer=writer,
            verifier=verifier,
            now=dt.datetime.now(dt.UTC),
            app_url=settings.app_url,
            prompts={"research_writer": writer_system, "claim_verifier": verifier_system},
            planner=planner,
        ),
    )
