"""Authenticated API routes for Phase 5: deep research, the monthly review, report search,
research profiles and the 13F collector."""

import datetime as dt
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import IdPath, Pool
from api.routes import BaseVersionParam, NoteParam, _as_dict, _enqueue, _not_found, save_versioned
from steward.ai import AINotConfigured
from steward.config import ResearchProfilesConfig, ThesesConfig
from steward.config.research import ResearchProfile
from steward.config.store import load_active
from steward.config.verification import VerificationConfig
from steward.llm.client import create_llm
from steward.llm.routing import routing_client
from steward.portfolio.read import JobSummary
from steward.research.layout import resolve_layout
from steward.research.model_plan import (
    Draft,
    LLMResearchPlanner,
    PlanProblem,
    QuestionEdit,
    ResearchPlanner,
    apply_edits,
    create_draft,
    get_draft,
    make_plan,
    save_draft,
)
from steward.research.plan import ResearchPlan, build_plan
from steward.research.retention import purge_old_traces, purge_report_trace, set_claim_review
from steward.research.run import ResearchBody, render_md
from steward.research.views import apply_view
from steward.review.monthly import ReviewBody
from steward.settings import get_settings
from steward.thesis.proposal import ProposalError, ProposalField, apply_changes, replace_thesis

router = APIRouter()

TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


class ResearchRequest(BaseModel):
    ticker: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = Field(
        description="Any ticker, owned or not (a Trading212 ticker gives the richest data)."
    )
    profile: str | None = Field(
        default=None, description="Research profile id; default if omitted."
    )
    name: str | None = Field(default=None, max_length=120, description="Company name hint.")


@router.post("/jobs/research", tags=["research"], status_code=status.HTTP_202_ACCEPTED)
async def run_research_now(body: ResearchRequest, pool: Pool, owner: Owner) -> JobSummary:
    """Queue a deep research run for a ticker under a research profile."""
    stored = await load_active(pool, "research_profiles", ResearchProfilesConfig)
    profiles = stored.body if stored else ResearchProfilesConfig()
    profile = profiles.get(body.profile)
    if profile is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"No profile {body.profile!r}")
    params: dict[str, Any] = {"ticker": body.ticker, "profile": profile.id}
    if body.name:
        params["name"] = body.name
    return await _enqueue(pool, "research", owner.user_id, params)


class PlanDraftRequest(BaseModel):
    ticker: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,32}$")]
    profile: str | None = Field(
        default=None, description="Research profile id; default if omitted."
    )
    name: str | None = Field(default=None, max_length=120, description="Company name hint.")
    planner: Literal["profile", "model", "code"] = Field(
        default="profile",
        description="profile: the profile's `planning` setting; model: ask a model (falls back "
        "to the code plan with the reason); code: the code-built plan.",
    )


class PlanDraftEdit(BaseModel):
    questions: list[QuestionEdit] | None = Field(
        default=None,
        description="The whole question list in the order wanted; one left out is removed.",
    )
    approve: bool = Field(default=False, description="Approve the plan as it now stands.")


class PlanDraftOut(BaseModel):
    id: int
    ticker: str
    profile: str
    name: str | None
    status: Literal["draft", "approved", "started", "discarded"]
    edited: bool
    planner_note: str | None
    plan: ResearchPlan
    job_id: int | None


def _draft_out(d: Draft) -> PlanDraftOut:
    return PlanDraftOut(
        id=d.id,
        ticker=d.ticker,
        profile=d.profile,
        name=d.name,
        status=d.status,  # type: ignore[arg-type]
        edited=d.edited,
        planner_note=d.planner_note,
        plan=d.plan,
        job_id=d.job_id,
    )


async def make_planner(pool: Pool, profile: ResearchProfile) -> ResearchPlanner | None:
    """The model planner for a draft, or None when no provider key is set."""
    try:
        client = routing_client(create_llm(get_settings()))
    except AINotConfigured:
        return None
    verification = await load_active(pool, "verification", VerificationConfig)
    cheap = (verification.body if verification else VerificationConfig()).claim_verifier.model
    return LLMResearchPlanner(client, profile.planner_model or cheap)


async def _draft_or_404(pool: Pool, draft_id: int) -> Draft:
    draft = await get_draft(pool, draft_id)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No plan draft {draft_id}")
    return draft


@router.post("/research/plans", tags=["research"], status_code=status.HTTP_201_CREATED)
async def create_plan_draft(body: PlanDraftRequest, pool: Pool, _owner: Owner) -> PlanDraftOut:
    """Write a research plan for a ticker and profile without running it: by a model when the
    profile (or `planner`) asks, else by code. The owner can edit and approve it, then start
    the run from it."""
    stored = await load_active(pool, "research_profiles", ResearchProfilesConfig)
    profiles = stored.body if stored else ResearchProfilesConfig()
    profile = profiles.get(body.profile)
    if profile is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"No profile {body.profile!r}")
    layout = resolve_layout(profile, profiles.template_for(profile))
    name = body.name or body.ticker
    code_plan = build_plan(
        profile, layout, ticker=body.ticker, name=name, today=dt.datetime.now(dt.UTC).date()
    )
    use_model = body.planner == "model" or (
        body.planner == "profile" and profile.planning == "model"
    )
    planner = await make_planner(pool, profile) if use_model else None
    plan, result = await make_plan(code_plan, profile, planner, name=name, use_model=use_model)
    usage: dict[str, Any] = (
        {
            "model": result.usage.model,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "ms": result.ms,
        }
        if result
        else {}
    )
    draft = await create_draft(
        pool, body.ticker, profile.id, body.name, plan, plan.fallback_reason, usage
    )
    return _draft_out(draft)


@router.get("/research/plans/{draft_id}", tags=["research"])
async def get_plan_draft(draft_id: IdPath, pool: Pool, _owner: Owner) -> PlanDraftOut:
    return _draft_out(await _draft_or_404(pool, draft_id))


@router.patch("/research/plans/{draft_id}", tags=["research"])
async def edit_plan_draft(
    draft_id: IdPath, body: PlanDraftEdit, pool: Pool, _owner: Owner
) -> PlanDraftOut:
    """Edit a draft (reorder, remove, relabel, reword, toggle source groups) under the same
    checks as a model plan, and/or approve it. Any edit withdraws an earlier approval unless
    `approve` is sent with it. A started draft is final."""
    draft = await _draft_or_404(pool, draft_id)
    if draft.status in ("started", "discarded"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"The draft is {draft.status}")
    stored = await load_active(pool, "research_profiles", ResearchProfilesConfig)
    profile = (stored.body if stored else ResearchProfilesConfig()).get(draft.profile)
    if profile is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Profile {draft.profile!r} no longer exists")
    plan = draft.plan
    edited = draft.edited
    if body.questions is not None:
        try:
            plan = apply_edits(plan, body.questions, profile)
        except PlanProblem as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, {"problems": exc.problems}
            ) from exc
        edited = True
    saved = await save_draft(pool, draft_id, plan, "approved" if body.approve else "draft", edited)
    return _draft_out(saved)


@router.post(
    "/research/plans/{draft_id}/start", tags=["research"], status_code=status.HTTP_202_ACCEPTED
)
async def start_from_plan(draft_id: IdPath, pool: Pool, owner: Owner) -> JobSummary:
    """Queue the research run for an approved draft. The run uses exactly this plan and stores
    it with the report, with whether the owner edited it."""
    draft = await _draft_or_404(pool, draft_id)
    if draft.status != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, "Approve the plan before starting the run")
    params: dict[str, Any] = {
        "ticker": draft.ticker,
        "profile": draft.profile,
        "plan_draft_id": draft.id,
    }
    if draft.name:
        params["name"] = draft.name
    job = await _enqueue(pool, "research", owner.user_id, params)
    await pool.execute(
        "update public.research_plan_draft set status = 'started', job_id = $2, updated_at = now()"
        " where id = $1",
        draft.id,
        job.id,
    )
    return job


class ResearchSummary(BaseModel):
    id: int
    ticker: str | None
    title: str
    profile: str | None
    headline: str | None
    created_at: dt.datetime
    job_id: int | None


@router.get("/research", tags=["research"])
async def list_research(
    pool: Pool,
    _owner: Owner,
    ticker: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ResearchSummary]:
    rows = await pool.fetch(
        "select id, ticker, title, profile, body ->> 'headline' as headline, created_at, job_id"
        " from public.report where type = 'research' and ($1::text is null or ticker = $1)"
        " order by id desc limit $2",
        ticker,
        limit,
    )
    return [ResearchSummary(**dict(r)) for r in rows]


class ResearchReportOut(BaseModel):
    id: int
    ticker: str | None
    title: str
    created_at: dt.datetime
    job_id: int | None
    body: ResearchBody
    body_md: str


ViewParam = Annotated[
    Literal["concise", "detailed"],
    Query(description="concise: key sentences only; detailed: the whole stored report."),
]


async def _research_row(pool: Pool, report_id: int) -> Any:
    row = await pool.fetchrow(
        "select id, ticker, title, created_at, job_id, body, body_md from public.report"
        " where id = $1 and type = 'research'",
        report_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No research report {report_id}")
    return row


@router.get("/research/{report_id}", tags=["research"])
async def get_research(
    report_id: IdPath, pool: Pool, _owner: Owner, view: ViewParam = "detailed"
) -> ResearchReportOut:
    """A research report: sections of cited sentences, computed valuation figures and the
    source list every citation points into. `view=concise` shows the same report with fewer
    sentences and without the sections a template marks detail-only."""
    row = await _research_row(pool, report_id)
    stored = ResearchBody.model_validate(json.loads(row["body"]))
    body_md = row["body_md"] if view == "detailed" else render_md(stored, view)
    return ResearchReportOut(**{**dict(row), "body": apply_view(stored, view), "body_md": body_md})


@router.get("/research/{report_id}/export", tags=["research"])
async def export_research(
    report_id: IdPath, pool: Pool, _owner: Owner, view: ViewParam = "detailed"
) -> PlainTextResponse:
    """The report as Markdown, a rendering of the stored typed report in the chosen view."""
    row = await _research_row(pool, report_id)
    body = ResearchBody.model_validate(json.loads(row["body"]))
    return PlainTextResponse(
        render_md(body, view),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="research-{report_id}-{view}.md"'},
    )


class ThesisProposalAccept(BaseModel):
    fields: list[ProposalField] = Field(min_length=1, description="The fields to apply.")


class ThesisProposalApplied(BaseModel):
    config_version_id: int
    applied: list[ProposalField]


@router.post("/research/{report_id}/thesis-proposal/accept", tags=["research"])
async def accept_thesis_proposal(
    report_id: IdPath, body: ThesisProposalAccept, pool: Pool, owner: Owner
) -> ThesisProposalApplied:
    """Apply chosen fields of a report's thesis proposal. This saves a new `theses` config
    version (the earlier one stays in the history). It is refused when the theses changed
    after the report was made, so a stale proposal cannot overwrite newer edits."""
    row = await _research_row(pool, report_id)
    report = ResearchBody.model_validate(json.loads(row["body"]))
    proposal = report.thesis_proposal
    if proposal is None or not proposal.changes:
        raise HTTPException(status.HTTP_409_CONFLICT, "This report proposes no thesis changes")
    stored = await load_active(pool, "theses", ThesesConfig)
    if (stored.id if stored else None) != proposal.base_version_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The theses changed after this report was made; run the research again",
        )
    config = stored.body if stored else ThesesConfig()
    current = next((t for t in config.theses if t.ticker == proposal.ticker), None)
    try:
        updated = apply_changes(current, proposal, list(body.fields))
        new_config = replace_thesis(config, updated)
    except (ProposalError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    saved = await save_versioned(
        pool,
        "theses",
        new_config,
        created_by=owner.user_id,
        note=f"Accepted {', '.join(body.fields)} from research report #{report_id}",
        base_version_id=proposal.base_version_id,
    )
    return ThesisProposalApplied(config_version_id=saved.id, applied=list(body.fields))


# ---------------------------------------------------------------------------
# Monthly review
# ---------------------------------------------------------------------------


class ReviewReportOut(BaseModel):
    id: int
    title: str
    created_at: dt.datetime
    job_id: int | None
    body: ReviewBody
    body_md: str


class TracePurged(BaseModel):
    urls: int = Field(description="Discovered-URL records removed from the report.")
    checkpoints: int = Field(description="Step checkpoints deleted (the job cannot resume).")
    traces: int = Field(description="Tool traces removed from step details.")
    report_traces: int = Field(
        default=0, description="Per-call run traces removed from stored report bodies."
    )


class ClaimReview(BaseModel):
    state: Literal["owner_confirmed", "owner_rejected", "unreviewed"]


class ClaimReviewed(BaseModel):
    claim_id: str
    state: str


@router.post("/research/{report_id}/trace/purge", tags=["research"])
async def purge_research_trace(report_id: IdPath, pool: Pool, _owner: Owner) -> TracePurged:
    """Delete the working records of a research run: the URL list, web snippets, the resume
    checkpoints and the tool trace. The report's sentences, sources and claim ledger stay."""
    try:
        counts = await purge_report_trace(pool, report_id, dt.datetime.now(dt.UTC))
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No research report {report_id}") from None
    return TracePurged(**counts)


@router.post("/research/{report_id}/claims/{claim_id}/review", tags=["research"])
async def review_claim(
    report_id: IdPath,
    claim_id: Annotated[str, Path(pattern=r"^c[0-9]{1,6}$")],
    body: ClaimReview,
    pool: Pool,
    _owner: Owner,
) -> ClaimReviewed:
    """The owner confirms or rejects one claim of the report's ledger. The sentence is not
    changed; the review state is kept beside it."""
    row = await _research_row(pool, report_id)
    try:
        updated = set_claim_review(json.loads(row["body"]), claim_id, body.state)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    await pool.execute(
        "update public.report set body = $2::jsonb where id = $1", report_id, json.dumps(updated)
    )
    return ClaimReviewed(claim_id=claim_id, state=body.state)


@router.post("/maintenance/purge-traces", tags=["research"])
async def purge_traces(
    pool: Pool,
    _owner: Owner,
    older_than_days: Annotated[int, Query(ge=1, le=3650)] = 30,
) -> TracePurged:
    """Delete the checkpoints and tool traces of succeeded jobs finished more than N days ago."""
    counts = await purge_old_traces(pool, older_than_days, dt.datetime.now(dt.UTC))
    return TracePurged(urls=0, **counts)


@router.get("/reviews", tags=["research"])
async def list_reviews(
    pool: Pool, _owner: Owner, limit: Annotated[int, Query(ge=1, le=100)] = 24
) -> list[ResearchSummary]:
    rows = await pool.fetch(
        "select id, ticker, title, profile, body ->> 'headline' as headline, created_at, job_id"
        " from public.report where type = 'review' order by id desc limit $1",
        limit,
    )
    return [ResearchSummary(**dict(r)) for r in rows]


@router.get("/reviews/{report_id}", tags=["research"])
async def get_review(report_id: IdPath, pool: Pool, _owner: Owner) -> ReviewReportOut:
    """A monthly review: every strategy rule, hard limit and band with pass / breach /
    unknown, thesis KPI statuses, flags, and proposed actions each citing a rule or KPI."""
    row = await pool.fetchrow(
        "select id, title, created_at, job_id, body, body_md from public.report"
        " where id = $1 and type = 'review'",
        report_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No review {report_id}")
    return ReviewReportOut(
        **{**dict(row), "body": ReviewBody.model_validate(json.loads(row["body"]))}
    )


@router.post("/jobs/monthly-review", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_monthly_review(pool: Pool, owner: Owner) -> JobSummary:
    """Write a monthly review now (a manual run is not limited to the first weekday)."""
    return await _enqueue(pool, "monthly_review", owner.user_id)


# ---------------------------------------------------------------------------
# Report search
# ---------------------------------------------------------------------------


class ReportHit(BaseModel):
    id: int
    type: Literal["digest", "research", "review", "escalation"]
    period: str | None
    ticker: str | None
    profile: str | None
    title: str
    created_at: dt.datetime
    excerpt: str = Field(description="The matching passage, with matches in <b></b>.")


@router.get("/reports/search", tags=["reports"])
async def search_reports(
    pool: Pool,
    _owner: Owner,
    q: Annotated[
        str,
        Query(
            min_length=1, max_length=200, description='Words, a ticker, "a phrase", or -excluded.'
        ),
    ],
    type: Annotated[
        list[Literal["digest", "research", "review", "escalation"]] | None, Query()
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ReportHit]:
    """Full-text search over every report (title, ticker, profile and text), newest first."""
    rows = await pool.fetch(
        "select id, type, period, ticker, profile, title, created_at,"
        " ts_headline('simple', body_md, websearch_to_tsquery('simple', $1),"
        " 'MaxFragments=2, MaxWords=30, MinWords=10') as excerpt"
        " from public.report where search @@ websearch_to_tsquery('simple', $1)"
        " and ($2::text[] is null or type = any($2)) order by created_at desc, id desc limit $3",
        q,
        list(type) if type else None,
        limit,
    )
    return [ReportHit(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Research profiles config, 13F collector
# ---------------------------------------------------------------------------


class ResearchProfilesConfigVersion(BaseModel):
    id: int
    created_at: dt.datetime
    note: str | None
    body: ResearchProfilesConfig


@router.get("/config/research-profiles", tags=["config"])
async def get_research_profiles_config(pool: Pool, _owner: Owner) -> ResearchProfilesConfigVersion:
    """The saved research profiles. 404 means the defaults (standard, quick) apply."""
    stored = await load_active(pool, "research_profiles", ResearchProfilesConfig)
    if stored is None:
        raise _not_found("research profiles")
    return ResearchProfilesConfigVersion(**_as_dict(stored))


@router.put("/config/research-profiles", tags=["config"])
async def save_research_profiles_config(
    body: ResearchProfilesConfig,
    pool: Pool,
    owner: Owner,
    note: NoteParam = None,
    base_version_id: BaseVersionParam = None,
) -> ResearchProfilesConfigVersion:
    stored = await save_versioned(
        pool,
        "research_profiles",
        body,
        created_by=owner.user_id,
        note=note,
        base_version_id=base_version_id,
    )
    return ResearchProfilesConfigVersion(**_as_dict(stored))


@router.post("/config/research-profiles/validate", tags=["config"])
async def validate_research_profiles(
    body: ResearchProfilesConfig, _owner: Owner
) -> ResearchProfilesConfig:
    return body


@router.post("/jobs/ownership-13f", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_ownership_13f(pool: Pool, owner: Owner) -> JobSummary:
    """Load new SEC Form 13F data sets now (about 100 MB each)."""
    return await _enqueue(pool, "ownership_13f", owner.user_id)
