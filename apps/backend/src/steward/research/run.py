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

import asyncio
import datetime as dt
import hashlib
import json
import math
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, Field

from steward.ai import AINotConfigured, Usage
from steward.chat.owner import owner_of
from steward.chat.tools import ChatTools
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.news_sources import NewsSourcesConfig
from steward.config.prompts import load_prompts
from steward.config.research import SENTENCES_BY_DEPTH, ResearchProfilesConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig
from steward.config.verification import VerificationConfig
from steward.documents.fetch import FetchError, default_headers, fetch_document
from steward.jobs.runner import JobContext, JobFailed
from steward.llm.cache import SEARCH_CACHE_DAYS, SEARCH_CACHE_DAYS_FRESH, ResultCache
from steward.llm.client import create_llm
from steward.llm.routing import routing_client
from steward.news.normalize import host_of
from steward.research.agents import (
    AgentRun,
    LLMWorker,
    Worker,
    WorkerRecord,
    findings_text,
    run_agents,
)
from steward.research.gather import Gathered, Valuation, gather
from steward.research.layout import SectionSpec, investigation_profile, resolve_layout
from steward.research.ledger import ClaimLedger, SectionInput, build_ledger
from steward.research.model_plan import SYSTEM as PLANNER_SYSTEM
from steward.research.model_plan import (
    LLMResearchPlanner,
    PlannerResult,
    ResearchPlanner,
    get_draft,
    make_plan,
    same_layout,
)
from steward.research.page_evidence import FetchPage, attach_page_text
from steward.research.plan import ResearchPlan, build_plan, effective_searches, focus_for
from steward.research.read_tools import (
    ReadToolRun,
    ToolCallRecord,
    comparison,
    evidence_for,
    full_data_calls,
    run_read_tools,
)
from steward.research.rules import load_compliance
from steward.research.scouts import SYSTEM as SCOUT_SYSTEM
from steward.research.scouts import (
    LLMScout,
    Scout,
    ScoutRequest,
    ScoutResult,
    question_for,
    snippet_of,
)
from steward.research.snapshot import RunSnapshot, take_snapshot
from steward.research.source_policy import (
    PRIORITY_TEXT,
    SourcePolicy,
    normalise_host,
    publisher_of,
)
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
from steward.research.writer import (
    SECTION_TASKS,
    LLMResearchWriter,
    ResearchWriter,
    SectionRequest,
)
from steward.research.writer import SYSTEM as WRITER_SYSTEM
from steward.settings import get_settings
from steward.thesis.proposal import (
    ThesisProposal,
    ThesisReview,
    build_proposal,
    review_thesis,
)
from steward.verify.claims import RESEARCH_SYSTEM as VERIFIER_SYSTEM
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
    # The engine that ran and, for the agentic one, what each worker did (metadata only).
    engine: str = "fixed"
    agents: list[WorkerRecord] = Field(default_factory=list)
    # Per-call trace of the run (model calls, searches, retrieval, output validation): metadata
    # only, removed by the purge and the retention job. Older reports have none.
    trace: ResearchTrace | None = None


def agent_model_trace(record: WorkerRecord) -> ModelCallTrace:
    outcome = {"ok": "ok", "no_findings": "ok", "refused": "refused", "not_run": "skipped"}
    return ModelCallTrace(
        step=f"worker:{record.lens}",
        model=record.model,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cache_read_tokens=record.cache_read_tokens,
        searches=record.searches,
        cost_usd=record.cost_usd,
        ms=record.ms,
        outcome=outcome.get(record.outcome, "failed"),
        note=clip(record.note or "", 160) or None,
    )


@dataclass
class ResearchDeps:
    writer: ResearchWriter | None
    verifier: ClaimVerifier | None
    now: dt.datetime
    app_url: str | None = None
    prompts: dict[str, str] = field(default_factory=dict)  # the system texts, for the snapshot
    planner: ResearchPlanner | None = None  # the model planning step; None = code plan
    worker: Worker | None = None  # the agentic engine's lens worker; None = fixed pipeline
    scout: Scout | None = None  # search_mode "scouts": the cheap model that runs the searches
    # The text of a cited web page (None when it cannot be read), so the claim checker has
    # the page's words and not only its title. None = titles only.
    fetch_page: FetchPage | None = None


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
            # Code sentences can embed a stored news headline: it is data, like a source title.
            lines.append(f"- {neutralise(sentence.text)} [{', '.join(sentence.source_ids)}]")
    return "\n".join(lines)


PLANNER_DIGEST_CHARS = 9000
PLANNER_HEADLINES = 25


def planner_digest(g: Gathered, reg: Registry) -> str:
    """What the planner reads before it writes the questions: the holding, thesis and strategy
    context, what the code already wrote from stored records, and the newest source titles. A
    planner that sees what is on file can aim the run at what is not."""
    lines = [neutralise(g.context), "", "Already on file:"]
    for section in g.sections.values():
        lines += [f"- {neutralise(s.text)}" for s in section.sentences]
    dated = sorted((s for s in reg.sources if s.as_of), key=lambda s: s.as_of or "", reverse=True)
    if dated:
        lines += ["", "Newest stored sources:"]
        lines += [f"- {s.as_of} {neutralise(s.title)}" for s in dated[:PLANNER_HEADLINES]]
    return "\n".join(lines)[:PLANNER_DIGEST_CHARS]


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
    # `decide` strips `www.` from each page host, so the issuer's own domains must be bare too.
    ir = [normalise_host(d) for d in profile.allowed_domains()] if profile else []
    domains = [
        *(ir if 1 in tiers else []),
        *(d.domain for d in registry.domain_tiers if d.tier in tiers),
    ]

    def tier_of(host: str | None) -> int | None:
        if host and any(host == d or host.endswith(f".{d}") for d in ir):
            return 1
        return registry.domain_tier(host)

    return tuple(dict.fromkeys(domains)), tier_of


# D4: written after the evidence sections when `write_order` is "summary_last".
LATE_SECTIONS = ("executive_summary", "thesis_kpis", "open_questions")


def write_order(specs: list[SectionSpec], order: str) -> list[SectionSpec]:
    """The order model sections are written in. Display order is the layout's and never
    changes; only the late sections move to the end, keeping their relative order."""
    if order != "summary_last":
        return list(specs)
    return [s for s in specs if s.id not in LATE_SECTIONS] + [
        s for s in specs if s.id in LATE_SECTIONS
    ]


def late_material(g: Gathered, reg: Registry, written: dict[str, dict[str, Any]]) -> str:
    """The material for a late section: the company context, the sentences already written
    (from stored records and by the evidence sections) and only the sources they cite. Empty
    when no evidence section wrote anything, so the section falls back to the whole material."""
    model_lines = [
        (s["text"], list(s.get("source_ids", [])))
        for out in written.values()
        for s in out.get("sentences", [])
    ]
    if not model_lines:
        return ""
    code_lines = [
        (sentence.text, list(sentence.source_ids))
        for section in g.sections.values()
        for sentence in section.sentences
    ]
    cited = {i for _, ids in (*code_lines, *model_lines) for i in ids}
    lines = [f"Company context: {g.context}", "", "Sources cited by the sentences below:"]
    lines += [_source_line(s) for s in reg.sources if s.id in cited]
    lines += ["", "Already written from stored records (cite the same ids if you use them):"]
    lines += [f"- {neutralise(t)} [{', '.join(ids)}]" for t, ids in code_lines]
    lines += ["", "Already written by the other sections (cite the same ids if you use them):"]
    lines += [f"- {neutralise(t)} [{', '.join(ids)}]" for t, ids in model_lines]
    return "\n".join(lines)


def merge_attempts(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Two writes of one section (cheap, then the profile's model): the second one's text,
    with both attempts' cost, sources and trace kept."""
    out = dict(second)
    for key in ("steps", "searches", "dropped", "links_removed"):
        out[key] = int(first.get(key, 0)) + int(second.get(key, 0))
    out["new_sources"] = [*first.get("new_sources", []), *second.get("new_sources", [])]
    out["discovered"] = [*first.get("discovered", []), *second.get("discovered", [])]
    out["search_errors"] = [*first.get("search_errors", []), *second.get("search_errors", [])]
    out["scout_urls"] = [*first.get("scout_urls", []), *second.get("scout_urls", [])]
    out["extra_traces"] = [
        *first.get("extra_traces", []),
        *([first["trace"]] if first.get("trace") else []),
        *second.get("extra_traces", []),
    ]
    return out


class _FailedCall(Exception):
    """A model call that wrote nothing: its result must not become a checkpoint."""


NO_KEY_NOTE = "not written: no AI provider key is set"
TRANSIENT_NOTES = ("HTTP 5", "HTTP 429", "HTTP 408", "Timeout", "no answer within")
TRANSIENT_WAITS = (15.0, 45.0)  # seconds before the 2nd and 3rd try of an overloaded provider


def transient_failure(output: dict[str, Any]) -> bool:
    """A section the provider failed to answer (overloaded, timed out), not one the model
    declined or the budget stopped: asking again later can succeed."""
    note = str(output.get("note") or "")
    return (
        not output.get("sentences")
        and "the call failed" in note
        and any(marker in note for marker in TRANSIENT_NOTES)
    )


async def retry_transient(
    attempt: Callable[[], Awaitable[dict[str, Any]]],
    waits: tuple[float, ...] = TRANSIENT_WAITS,
) -> dict[str, Any]:
    """`attempt()`, repeated after a pause while the provider's failure looks temporary. A failed
    call produced no sentences, so a retry cannot duplicate any; the steps of the failed tries
    stay counted."""
    output = await attempt()
    for wait in waits:
        if not transient_failure(output):
            break
        await asyncio.sleep(wait)
        output = merge_attempts(output, await attempt())
    return output


FULL_DATA_MAX_CALLS = 48
PAGE_MAX_BYTES = 12_000_000  # a cited filing page; larger documents are not read for evidence
NO_BUDGET_NOTE = "not written: the profile's step budget is used up"
# Notes of a section no follow-up could fill; any other "not written" is a failed call.
PERMANENT_NOTES = (NO_KEY_NOTE, NO_BUDGET_NOTE)


def section_cap(spec: SectionSpec, depth: str) -> int:
    """Most sentences a model section may hold, follow-ups included."""
    return spec.max_sentences or SENTENCES_BY_DEPTH[depth]


def thin_sections(
    specs: list[SectionSpec],
    written: dict[str, dict[str, Any]],
    minimum: int,
    depth: str = "deep",
) -> dict[str, str]:
    """Model sections with fewer cited sentences than the profile needs, and why. A section
    the run could not write at all (no provider key, budget used up) is not a gap a follow-up
    could fill, so it is left out; one whose call failed is, and is tried again. The need never
    exceeds what the section may hold."""
    out: dict[str, str] = {}
    for spec in specs:
        got = written.get(spec.id)
        if got is None or str(got.get("note", "")).startswith(PERMANENT_NOTES):
            continue
        n = len(got.get("sentences", []))
        need = min(minimum, section_cap(spec, depth))
        if n < need:
            out[spec.id] = f"only {n} cited sentence(s); the profile needs {need}"
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
            publishers = {publisher_of(r.url) or r.id for r in found}
            if len(publishers) == 1 and all(
                r.kind in ("search", "news_item") and r.tier != 1 and r.role != "primary"
                for r in found
            ):
                out.setdefault(spec.id, []).append(s["text"])
    return {k: v[:MAX_CORROBORATE] for k, v in out.items()}


# Positions in the version list whose whole-config id changes with edits this run does not read
# (another holding's thesis or profile, another research profile): a resume compares the
# fingerprint of the part it reads instead, at this position of `run_inputs`.
_FINGERPRINT_OF = {1: 0, 3: 1, 0: 2}


def _fingerprint(data: Any) -> str | None:
    if data is None:
        return None
    text = (
        data.model_dump_json()
        if hasattr(data, "model_dump_json")
        else json.dumps(data, sort_keys=True, default=str)
    )
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def run_inputs(
    ticker: str,
    theses_v: Any,
    profiles_v: Any,
    research_v: Any = None,
    profile_id: str | None = None,
) -> list[str | None]:
    """A fingerprint of this holding's own thesis and profile entry, and of the research profile
    and template the run uses (None: there is none)."""
    thesis = (
        next((t for t in theses_v.body.theses if t.ticker == ticker), None) if theses_v else None
    )
    profile = profiles_v.body.for_ticker(ticker) if profiles_v else None
    used = None
    chosen = research_v.body.get(profile_id) if research_v else None
    if chosen is not None:
        template = research_v.body.template_for(chosen)
        used = {
            "profile": chosen.model_dump(mode="json"),
            "template": template.model_dump(mode="json") if template else None,
        }
    return [_fingerprint(thesis), _fingerprint(profile), _fingerprint(used)]


def same_run_inputs(first: dict[str, Any], versions: list[Any], own: list[str | None]) -> bool:
    """Whether a resume runs under what the first attempt used. Another holding's thesis being
    saved (a batch drafts many at once), or another research profile being edited, is not a
    change; this holding's own entry and the profile the run uses are."""
    then = first.get("ids") or []
    if len(then) != len(versions):
        return False
    before = first.get("own")
    # A checkpoint from before a fingerprint existed is judged on that config's id instead;
    # one from before any fingerprint has no own entries to compare at all.
    known = len(before) if before is not None else 2
    # A research fingerprint of None means no profile was resolved, not "none in use": then
    # the research id is compared as before.
    unresolved = known > 2 and (before is None or before[2] is None or own[2] is None)
    by_id = [
        i
        for i in range(len(versions))
        if i not in _FINGERPRINT_OF or _FINGERPRINT_OF[i] >= known or (i == 0 and unresolved)
    ]
    if any(then[i] != versions[i] for i in by_id):
        return False
    return before is None or before == own[:known]


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
        versions = [v.id if v else None for v in (
            research_v, theses_v, strategy_v, profiles_v, sources_v, verification_v
        )]  # fmt: skip
        own = run_inputs(ticker, theses_v, profiles_v, research_v, ctx.params.get("profile"))

        async def first_versions() -> dict[str, Any]:
            return {"ids": versions, "own": own}

        # The cached steps below were made under the versions of the first attempt. A resume
        # under newer ones would mix two layouts, so it stops and the owner starts a new run.
        if not same_run_inputs(
            await ctx.checkpoint("config_versions", first_versions), versions, own
        ):
            raise JobFailed("the configuration changed since this run started; start a new run")
        for version_id in versions:
            await ctx.record_config_version(version_id)
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
                    context=planner_digest(gathered, reg),
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
                        outcome=outcome,
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
            if ctx.params.get("full_data"):
                # A thesis the owner asked for: every stored record about the company and the
                # owner's own position, notes, history and risk, not only the plan's tools.
                tools = ChatTools(pool, reg, None, None, owner_id=await owner_of(pool))
                outcome = await run_read_tools(
                    tools.call,
                    reg,
                    plan,
                    ticker,
                    owner=True,
                    extra=full_data_calls(ticker, gathered.name),
                    max_calls=FULL_DATA_MAX_CALLS,
                )
            else:
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
        prompts={
            **deps.prompts,
            # The other texts a report rests on, so the snapshot is the whole prompt set.
            "research_planner": PLANNER_SYSTEM,
            "section_tasks": json.dumps(SECTION_TASKS, sort_keys=True, ensure_ascii=False),
            "source_priority": PRIORITY_TEXT,
            **({"research_scout": SCOUT_SYSTEM} if profile.search_mode == "scouts" else {}),
        },
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

    agent_run: AgentRun | None = None
    agent_notes: list[str] = []
    if profile.engine == "agentic" and model_specs and deps.worker is not None:
        # Workers spend from the same profile budget: leave one step per section for the
        # writer, divide the rest between the lenses, and cap their searches by the first-pass
        # share. The writer then searches nothing in the first pass: the workers did.
        step_room = first_steps - len(model_specs)
        worker_steps = min(profile.worker_max_steps, step_room // len(profile.lenses))

        async def do_agents() -> dict[str, Any]:
            async with ctx.step("agents") as step:
                run_out = await run_agents(
                    deps.worker,  # type: ignore[arg-type]
                    lenses=list(profile.lenses),
                    name=gathered.name,
                    ticker=ticker,
                    today=deps.now.date().isoformat(),
                    model=profile.worker_model,
                    effort=profile.worker_effort,
                    max_steps=profile.worker_max_steps,
                    total_steps=step_room,
                    worker_searches=profile.worker_max_searches,
                    total_searches=first_searches,
                    parallel=profile.parallel_workers,
                    domains=domains,
                    caller=ChatTools(pool, reg, None, None, owner_id=await owner_of(pool)).call,
                    registry=reg,
                    tier_of=tier_of,
                    cost_of=lambda u: cost_of(u),
                    questions=plan.questions,
                )
                total = Usage(model=profile.worker_model)
                for rec in run_out.records:
                    total.input_tokens += rec.input_tokens
                    total.output_tokens += rec.output_tokens
                    total.cache_read_tokens += rec.cache_read_tokens
                    total.searches += rec.searches
                step.usage = total.step_usage()
                step.detail.update(
                    workers=len(run_out.records),
                    findings=len(run_out.findings),
                    sources_added=run_out.sources_added,
                    searches=run_out.searches_used,
                    by_outcome=dict(Counter(r.outcome for r in run_out.records)),
                )
                return {
                    "run": run_out.model_dump(mode="json"),
                    "sources": [s.model_dump(mode="json") for s in reg.sources[known_sources:]],
                }

        if worker_steps >= 2:
            known_sources = len(reg.sources)
            stored = await ctx.checkpoint("agents", do_agents)
            agent_run = AgentRun.model_validate(stored["run"])
            for src in (Source.model_validate(s) for s in stored["sources"]):
                # Resumed: the workers did not run again, so put their sources back, in order.
                reg.add(
                    src.kind,
                    src.ref,
                    src.title,
                    **src.model_dump(exclude={"id", "kind", "ref", "title"}),
                )
            flag_sources(reg.sources[known_sources:])
            budget["steps"] += sum(r.steps for r in agent_run.records)
            budget["searches"] += agent_run.searches_used
            trace_parts.extend(
                {"model": agent_model_trace(r).model_dump()} for r in agent_run.records
            )
        else:
            agent_notes.append(
                "Agentic engine skipped: the profile's step budget leaves too little for workers."
            )
    if profile.engine == "agentic" and model_specs and deps.worker is None:
        agent_notes.append(
            "Agentic engine not used: no AI provider key is set, so the fixed pipeline ran."
        )
    if agent_run is not None:
        per_section_searches = 0
    # The gathered material is identical for every section (the writer caches it); sources
    # added by search go in a separate, per-section part.
    material = material_text(gathered, reg)
    if agent_run is not None and (block := findings_text(agent_run)):
        material += f"\n\n{block}"
    gathered_count = len(reg.sources)

    async def run_scout(
        spec: SectionSpec,
        *,
        round_no: int,
        searches: int,
        focus: str,
        step_name: str,
        out: dict[str, Any],
    ) -> ScoutResult | None:
        """D2: a scout runs the section's searches; its extracts join the registry as ordinary
        search sources, tier-checked like any result. None when the scout failed, so the
        writer searches itself (a failed scout never costs the section its web evidence)."""
        assert deps.scout is not None
        async with ctx.step(f"scout:{step_name}") as step:
            started = time.monotonic()
            result = await deps.scout.scout(
                ScoutRequest(
                    name=gathered.name,
                    ticker=ticker,
                    question=question_for(
                        spec.title,
                        spec.task or SECTION_TASKS.get(spec.id, ""),
                        focus,
                        counter=spec.id == "counter_case",
                    ),
                    domains=domains,
                    max_searches=searches,
                )
            )
            step.usage = result.usage.step_usage()
            out["steps"] += 1 + result.usage.searches
            out["searches"] += result.usage.searches
            failed = result.note is not None and not result.extracts
            out.setdefault("extra_traces", []).append(
                {
                    "model": ModelCallTrace(
                        step=f"scout:{step_name}",
                        section=spec.id,
                        round=round_no,
                        model=result.usage.model,
                        input_tokens=result.usage.input_tokens,
                        output_tokens=result.usage.output_tokens,
                        cache_read_tokens=result.usage.cache_read_tokens,
                        searches=result.usage.searches,
                        cost_usd=cost_of(result.usage),
                        ms=round((time.monotonic() - started) * 1000),
                        outcome="ok" if not failed else "failed",
                        note=clip(result.note or "", 160) or None,
                    ).model_dump()
                }
            )
            if failed:
                step.detail.update(extracts=0, note=result.note)
                return None
            kept = 0
            for extract in result.extracts:
                title = result.titles.get(extract.url)
                snippet = snippet_of(extract)
                if title is None or not snippet:
                    continue  # not a page the search returned, or nothing to read
                decision = policy.decide(host_of(extract.url))
                if not decision.allowed:
                    continue
                before = len(reg.sources)
                reg.add(
                    "search",
                    extract.url,
                    title,
                    url=extract.url,
                    tier=decision.tier,
                    snippet=snippet,
                    role=decision.role,
                    caveat=decision.reason,
                    retrieved_at=deps.now.isoformat(timespec="seconds"),
                )
                kept += 1
                out.setdefault("scout_urls", []).append(extract.url)
                if len(reg.sources) > before:
                    flag_sources(reg.sources[before:])
                    out["new_sources"].append(reg.sources[-1].model_dump())
            step.detail.update(extracts=kept, returned=len(result.extracts), round=round_no)
        return result

    async def write_section(
        spec: SectionSpec,
        *,
        round_no: int,
        searches: int,
        max_sentences: int,
        focus: str,
        existing: str,
        step_name: str,
        model: str | None = None,
        effort: str | None = None,
        material_override: str | None = None,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {"sentences": [], "new_sources": [], "steps": 0, "searches": 0}
        if deps.writer is None:
            out["note"] = NO_KEY_NOTE
            return out
        scouted: ScoutResult | None = None
        writer_searches = searches
        if profile.search_mode == "scouts" and searches > 0 and deps.scout is not None:
            scouted = await run_scout(
                spec,
                round_no=round_no,
                searches=searches,
                focus=focus,
                step_name=step_name,
                out=out,
            )
            if scouted is not None:
                writer_searches = 0
        scout_urls = set(out.get("scout_urls", []))
        async with ctx.step(step_name) as step:
            offered = len(reg.sources)
            started = time.monotonic()
            result = await deps.writer.write(
                SectionRequest(
                    section=spec.id,
                    name=gathered.name,
                    ticker=ticker,
                    material=material_override or material,
                    added_material=added_material_text(reg, gathered_count),
                    language=profile.language,
                    max_sentences=max_sentences,
                    domains=domains,
                    max_searches=writer_searches,
                    peers=gathered.peers,
                    today=deps.now.date(),
                    title=spec.title,
                    task=spec.task,
                    focus=focus,
                    policy=PRIORITY_TEXT if writer_searches else "",
                    existing=existing,
                    horizon_days=profile.date_horizon_days,
                    tool_evidence=evidence_for(
                        tool_run, spec.id, first=spec.id == model_specs[0].id
                    ),
                    model=model,
                    effort=effort,
                )
            )
            ms = round((time.monotonic() - started) * 1000)
            step.usage = result.usage.step_usage()
            out["steps"] += 1 + result.usage.searches
            out["searches"] += result.usage.searches
            hits = result.hits or [SearchHit(u, t) for u, t in result.titles.items()]
            if scouted is not None:
                hits = [*scouted.hits, *hits]
                result.queries = [*scouted.queries, *result.queries]
                result.search_errors = [*scouted.search_errors, *result.search_errors]
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
                    outcome=call_outcome,
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
                if scout_urls:
                    # Cited by registry id: the page a scout found and the section used.
                    cited_urls |= {
                        r.url
                        for i in ids
                        if (r := reg.get(i)) is not None and r.url and r.url in scout_urls
                    }
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
        trace_parts.extend(output.get("extra_traces", []))
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

    async def checkpoint_written(
        key: str, compute: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        """`ctx.checkpoint`, except a failed model call is not stored: a resume or a follow-up
        round tries it again instead of replaying the failure."""
        failed: dict[str, Any] = {}

        async def run() -> dict[str, Any]:
            out = await compute()
            note = str(out.get("note", ""))
            if note.startswith("not written") and not note.startswith(PERMANENT_NOTES):
                failed["out"] = out
                raise _FailedCall
            return out

        try:
            return dict(await ctx.checkpoint(key, run))
        except _FailedCall:
            return dict(failed["out"])

    ordered_specs = write_order(model_specs, profile.write_order)

    async def write_with_fallback(
        spec: SectionSpec,
        *,
        cheap: bool,
        enough: Callable[[dict[str, Any]], bool],
        steps_room: int,
        **kw: Any,
    ) -> dict[str, Any]:
        """D3/D6: the cheap model writes first; a draft that is not `enough` (too few cited
        sentences, or a failed call) is written again by the profile's model, inside the same
        step budget. The final answer is always the stronger model's when the cheap one fell
        short, so a weak draft never reaches the report."""
        if not cheap:
            return await write_section(spec, **kw)
        first = await write_section(
            spec, model=profile.cheap_model, effort=profile.cheap_effort, **kw
        )
        if enough(first) or str(first.get("note", "")).startswith(PERMANENT_NOTES):
            return first
        room = steps_room - int(first.get("steps", 0))
        if room < 1:
            return first
        again = {
            **kw,
            "step_name": f"{kw['step_name']}:escalated",
            "searches": max(0, min(kw["searches"] - int(first.get("searches", 0)), room - 1)),
        }
        return merge_attempts(first, await write_section(spec, **again))

    for index, spec in enumerate(ordered_specs):

        async def do_section(spec: SectionSpec = spec, index: int = index) -> dict[str, Any]:
            left = first_steps - budget["steps"]
            if deps.writer is not None and left < 1:
                return {
                    "sentences": [],
                    "new_sources": [],
                    "steps": 0,
                    "searches": 0,
                    "note": NO_BUDGET_NOTE,
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
            if spec.id == model_specs[0].id and plan.questions and plan.questions[0].id == "mode":
                focus = f"- {plan.questions[0].question}\n{focus}".strip()
            cap = spec.max_sentences or SENTENCES_BY_DEPTH[profile.depth]
            need = min(profile.min_sentences, cap)
            # D4: a late section reads what the evidence sections wrote, not the whole pile.
            compact = (
                late_material(gathered, reg, written)
                if profile.write_order == "summary_last" and spec.id in LATE_SECTIONS
                else ""
            )

            def enough(o: dict[str, Any], need: int = need) -> bool:
                return len(o["sentences"]) >= need

            async def attempt() -> dict[str, Any]:
                return await write_with_fallback(
                    spec,
                    cheap=spec.id in profile.cheap_sections,
                    enough=enough,
                    steps_room=left,
                    round_no=0,
                    searches=searches,
                    max_sentences=cap,
                    focus=focus,
                    existing="",
                    step_name=f"section:{spec.id}",
                    material_override=compact or None,
                )

            return await retry_transient(attempt)

        output = await checkpoint_written(f"section:{spec.id}", do_section)
        absorb(output)
        written[spec.id] = output

    # Follow-up rounds: only for profiles that iterate.
    rounds: list[RoundRecord] = []
    stopped: str | None = None
    first_pass_sources = len(reg.sources)
    if profile.iterate and model_specs and deps.writer is None:
        stopped = "no follow-up rounds: no AI provider key is set"
    elif profile.iterate and model_specs:
        stopped = "round limit reached"
        # A claim already sent for corroboration is not sent again, and a section targeted
        # less often goes first, so later sections are not starved by the first three.
        corroborated: set[str] = set()
        attempts: dict[str, int] = {}
        for round_no in range(1, profile.max_rounds + 1):
            thin = thin_sections(model_specs, written, profile.min_sentences, profile.depth)
            single = {
                section_id: fresh
                for section_id, texts in single_source_claims(model_specs, written, reg).items()
                if (fresh := [t for t in texts if t not in corroborated])
            }
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
            for spec in sorted(model_specs, key=lambda s: attempts.get(s.id, 0)):
                if spec.id not in targets or len(record.sections) >= MAX_TARGETS_PER_ROUND:
                    continue
                # A section holds at most its cap, follow-ups included.
                room = section_cap(spec, profile.depth) - len(written[spec.id]["sentences"])
                if room < 1:
                    continue
                steps_left = profile.max_steps - budget["steps"]
                if steps_left < 1:
                    break
                attempts[spec.id] = attempts.get(spec.id, 0) + 1
                corroborated.update(single.get(spec.id, []))
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
                    room: int = room,
                    steps_left: int = steps_left,
                ) -> dict[str, Any]:
                    return await write_with_fallback(
                        spec,
                        cheap=profile.follow_up_cheap,
                        enough=lambda o: bool(o["sentences"]),
                        steps_room=steps_left,
                        round_no=round_no,
                        searches=searches,
                        max_sentences=min(FOLLOW_UP_SENTENCES, room),
                        focus="\n".join(x for x in lines if x),
                        existing=existing,
                        step_name=f"follow_up:{round_no}:{spec.id}",
                    )

                got = await checkpoint_written(f"follow_up:{round_no}:{spec.id}", do_follow_up)
                absorb(got)
                record.sections.append(spec.id)
                record.reasons[spec.id] = targets[spec.id]
                record.sentences_added += len(got["sentences"])
                record.steps += int(got.get("steps", 0))
                base = written[spec.id]
                base["sentences"] = [*base["sentences"], *got["sentences"]][
                    : section_cap(spec, profile.depth)
                ]
                if base["sentences"] and str(base.get("note", "")).startswith("not written"):
                    del base["note"]  # a failed first call that the follow-up has now filled
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
                stopped = (
                    "the step budget is used up"
                    if profile.max_steps - budget["steps"] < 1
                    else "no section with a gap has room for more sentences"
                )
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
            if deps.fetch_page is not None:
                # A web citation is a URL and a title; the checker needs the page's own words.
                cited_by: dict[str, list[str]] = {}
                for _, _, s in model_sentences:
                    for sid in s.source_ids:
                        cited_by.setdefault(sid, []).append(s.text)
                step.detail["pages_read"] = await attach_page_text(reg, cited_by, deps.fetch_page)
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
        notes.extend(agent_notes)
        if agent_run is not None:
            notes.extend(agent_run.notices)
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
                (k.kpi_id, f"{k.metric} {k.rule}", k.status, k.rationale)
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
            engine=profile.engine,
            agents=agent_run.records if agent_run else [],
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


def _md_line(text: str) -> str:
    """Text safe to put on one Markdown line: a title or sentence from the web or a model may
    contain newlines and a leading `#`, `>` or `-`, which would start a heading, quote or list."""
    flat = " ".join(text.split())
    return f"\\{flat}" if flat[:1] in ("#", ">", "-", "+", "|") else flat


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
            lines.append(f"{_md_line(s.text)} [{', '.join(s.source_ids)}]{tag}{mark}")
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
            lines += [
                f"  - {_md_line(line.text)} [{', '.join(line.evidence_ids)}]"
                for line in change.after
            ]
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
        lines.append(f"- [{src.id}] {_md_line(src.title)} ({label}): {_md_line(where)}")
    return "\n".join(lines)


async def research_handler(ctx: JobContext) -> None:
    await run_research_job(ctx)


async def run_research_job(ctx: JobContext) -> int:
    """The research job; returns the stored report's id (the thesis job reads it next)."""
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
    worker: Worker | None = None
    scout: Scout | None = None
    try:
        llm = create_llm(settings)
        # Cost lever C8: a section whose material, prompt, model and effort are unchanged is
        # not written (or searched) again, so a re-run after a small data change pays only for
        # the sections that changed. The prompt carries today's date, so a hit is a same-day
        # re-run. `fresh` in the job params bypasses it (benchmarks, a deliberate second take).
        reuse = ResultCache(ctx.pool, "research_section", search_ttl_days=SEARCH_CACHE_DAYS_FRESH)
        client = routing_client(llm, None if ctx.params.get("fresh") else reuse)
    except AINotConfigured:
        writer = verifier = None
    else:
        if profile and profile.engine == "agentic":
            worker = LLMWorker(llm)
        cheap = (verification.body if verification else VerificationConfig()).claim_verifier.model
        if profile and profile.planning == "model":
            planner = LLMResearchPlanner(
                client, profile.planner_model or cheap, effort=profile.planner_effort
            )
        # D5: a run nobody waits on may take the discounted tier (flex), and its independent
        # claim checks the batch API.
        tier = "flex" if profile and profile.priority in ("flex", "batch") else "standard"
        writer = (
            LLMResearchWriter(
                client,
                profile.model,
                profile.effort,
                writer_system,
                cache_key=f"research:{ctx.job_id}",
                priority=tier,
            )
            if profile
            else None
        )
        if profile and profile.search_mode == "scouts":
            # Scout extracts are kept for the 3-day search window (D7): the same question
            # about the same company is not searched again. The prompt carries no date.
            scout_client = routing_client(
                llm,
                None
                if ctx.params.get("fresh")
                else ResultCache(ctx.pool, "research_scout", search_ttl_days=SEARCH_CACHE_DAYS),
            )
            scout = LLMScout(
                scout_client,
                profile.scout_model,
                profile.scout_effort,
                cache_key=f"research-scout:{ctx.job_id}",
                priority=tier,
            )
        claim_settings = (
            verification.body if verification else VerificationConfig()
        ).claim_verifier
        verifier = LLMClaimVerifier(
            client,
            claim_settings.model,
            verifier_system,
            effort=claim_settings.effort,
            cache=ResultCache(ctx.pool, "claim_verifier"),
            use_batch=bool(profile and profile.priority == "batch"),
        )
    async with httpx.AsyncClient(timeout=30.0) as http:

        async def fetch_page(url: str) -> str | None:
            headers = default_headers(settings.sec_user_agent if "sec.gov" in url else None)
            try:
                document = await fetch_document(
                    http, url, max_bytes=PAGE_MAX_BYTES, headers=headers
                )
                return await document.text_async()
            except FetchError:
                return None

        return await run_research(
            ctx,
            ResearchDeps(
                writer=writer,
                verifier=verifier,
                now=dt.datetime.now(dt.UTC),
                app_url=settings.app_url,
                prompts={"research_writer": writer_system, "claim_verifier": verifier_system},
                planner=planner,
                worker=worker,
                scout=scout,
                fetch_page=fetch_page,
            ),
        )
