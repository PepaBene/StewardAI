"""The research run's per-call trace (Phase 0 gap 1): what each model call, search and
validation did, in the same spirit as the chat tool trace.

Only metadata is kept: step names, models, token counts, cost, time, clipped query text, the
host and title of URLs, counts and clipped reasons. Never a prompt, a model answer, a page or a
source snippet. The trace lives in `ResearchBody.trace`; the owner's purge and the retention
job remove it (`retention.scrub_body`). Reports stored before it existed have none.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from steward.research.web import DiscoveredUrl

CLIP = 200
MAX_URLS_PER_STEP = 12
MAX_QUERIES_PER_STEP = 8
MAX_REASONS = 6

Outcome = Literal["ok", "failed", "refused", "schema_failure", "skipped"]


def clip(text: str, limit: int = CLIP) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ModelCallTrace(BaseModel):
    step: str
    section: str | None = None
    round: int = 0
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    searches: int = 0
    cost_usd: float | None = Field(default=None, description="None when the model has no price.")
    ms: int = 0
    retries: int = Field(
        default=0,
        description="Re-asks the run made. The HTTP client's own retries are not visible here.",
    )
    outcome: Outcome = "ok"
    note: str | None = None


class WebTrace(BaseModel):
    step: str
    section: str | None = None
    round: int = 0
    queries: list[str] = Field(default_factory=list, description="As the provider reports them.")
    urls_found: int = 0
    urls_cited: int = 0
    urls_excluded: int = 0
    found: list[str] = Field(
        default_factory=list, description="host + title of the first URLs found (no query string)."
    )
    opened: list[str] = Field(
        default_factory=list,
        description="URLs a sentence cites. The provider does not say which pages the model "
        "read in full, so a cited page is the nearest evidence of one that was opened.",
    )
    errors: list[str] = Field(default_factory=list)


class RetrievalTrace(BaseModel):
    section: str
    sources_offered: int = Field(description="Sources in the registry when the section was asked.")
    sources_cited: int = Field(description="Distinct sources the kept sentences cite.")
    sources_uncited: int = 0
    tools_planned: list[str] = Field(default_factory=list)


class ValidationTrace(BaseModel):
    step: str
    section: str | None = None
    schema_failure: bool = False
    retries: int = 0
    sentences_returned: int = 0
    dropped_uncited: int = Field(default=0, description="No checkable source id or allowed URL.")
    links_removed: int = 0
    dropped_by_check: int = Field(
        default=0, description="Removed after the claim check found them unsupported."
    )
    check_reasons: list[str] = Field(default_factory=list)


class ResearchTrace(BaseModel):
    model_calls: list[ModelCallTrace] = Field(default_factory=list)
    web: list[WebTrace] = Field(default_factory=list)
    retrieval: list[RetrievalTrace] = Field(default_factory=list)
    validation: list[ValidationTrace] = Field(default_factory=list)
    total_cost_usd: float | None = None
    total_ms: int = 0
    plan_source: str | None = Field(
        default=None, description="code, model or model fallback (with the reason)."
    )


def web_trace(
    step: str,
    section: str,
    round_no: int,
    queries: list[str],
    found: list[DiscoveredUrl],
    errors: list[str],
) -> WebTrace:
    return WebTrace(
        step=step,
        section=section,
        round=round_no,
        queries=[clip(q) for q in queries[:MAX_QUERIES_PER_STEP]],
        urls_found=len(found),
        urls_cited=sum(1 for d in found if d.status == "cited"),
        urls_excluded=sum(1 for d in found if d.status == "excluded"),
        found=[clip(f"{d.host} {d.title}") for d in found[:MAX_URLS_PER_STEP]],
        opened=[clip(d.url.split("?")[0]) for d in found if d.status == "cited"][
            :MAX_URLS_PER_STEP
        ],
        errors=[clip(e, 80) for e in errors[:MAX_REASONS]],
    )


def finish(parts: list[dict[str, Any]], check_drops: dict[str, list[str]]) -> ResearchTrace:
    """The stored trace from the per-step parts the run collected (each step's checkpoint
    holds its own part, so a resumed run keeps the trace of steps it did not repeat)."""
    trace = ResearchTrace()
    for part in parts:
        if part.get("model"):
            trace.model_calls.append(ModelCallTrace.model_validate(part["model"]))
        if part.get("web"):
            trace.web.append(WebTrace.model_validate(part["web"]))
        if part.get("validation"):
            v = ValidationTrace.model_validate(part["validation"])
            reasons = check_drops.get(v.section or "", [])
            v.dropped_by_check = len(reasons)
            v.check_reasons = [clip(r, 120) for r in dict.fromkeys(reasons)][:MAX_REASONS]
            trace.validation.append(v)
    costs = [c.cost_usd for c in trace.model_calls if c.cost_usd is not None]
    trace.total_cost_usd = round(sum(costs), 6) if costs else None
    trace.total_ms = sum(c.ms for c in trace.model_calls)
    return trace
