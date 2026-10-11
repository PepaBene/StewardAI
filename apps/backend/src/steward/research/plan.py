"""The research plan: what a run intends to find out, written down before it searches.

The plan is built in code from the profile's mode and the report layout, so it is the same
for the same inputs and costs nothing. A model can later refine the questions into this same
schema; the run only reads the schema. It records the questions, the stored-data tools that
answer them, the web search intents, the source tiers wanted, the search for the strongest
opposing case, when to stop, and the planned sections. The run keeps it with the report, so a
reader can see what was asked and compare it with what was found.
"""

import datetime as dt
import math
from typing import Literal

from pydantic import BaseModel, Field

from steward.config.research import ResearchProfile
from steward.research.layout import SectionSpec

PLAN_VERSION = 1

# section -> (question, stored-data tools that answer it, what to look for on the web)
SECTION_PLANS: dict[str, tuple[str, list[str], str | None]] = {
    "executive_summary": (
        "What do the stored records and the new evidence say about the case for owning "
        "{name}, and how strong is it?",
        ["holding", "thesis_and_reports"],
        None,
    ),
    "business": (
        "What does {name} do, how does it earn, and in which segments and markets?",
        ["fundamentals", "filing_evidence", "company_context"],
        "{name} business model, segments and markets, from the company's own reports",
    ),
    "financial_trend": (
        "How have revenue, margins, cash flow and leverage developed over the filed periods?",
        ["fundamentals", "price_history"],
        None,
    ),
    "moat_risks": (
        "What protects {name}'s position, what threatens it, and how do peers compare?",
        ["company_context", "filing_evidence"],
        "{name} competitive position, market share and main risks against peers",
    ),
    "valuation": (
        "What do the code-computed multiples and scenarios say, and on what assumptions?",
        ["valuation_scenarios", "price_history"],
        None,
    ),
    "scenarios": (
        "What would have to happen for the bear, base and bull case of {name}?",
        ["valuation_scenarios", "thesis_and_reports"],
        "{name} outlook, guidance and analyst scenarios",
    ),
    "catalysts": (
        "Which events could move the case, and when are they due?",
        ["filing_evidence", "company_context", "search_news"],
        "{name} upcoming catalysts, results date, regulatory or contract milestones",
    ),
    "counter_case": (
        "What is the strongest evidence against owning {name}?",
        ["search_news", "filing_evidence", "event_paths"],
        "Evidence against owning {name}: criticism, downgrades, short-seller reports, "
        "litigation, accounting questions, lost customers or contracts",
    ),
    "strategy_fit": (
        "How does {name} stand against each rule and note of the owner's strategy?",
        ["strategy_rules", "holding", "over_cap_positions", "thesis_and_reports"],
        None,
    ),
    "thesis_kpis": (
        "Which checkable conditions prove or end the case, and where do they stand now?",
        ["thesis_and_reports", "fundamentals", "filing_evidence"],
        "{name} guidance and reported figures for the thesis KPIs",
    ),
    "ownership": (
        "Who owns {name}, and are insiders and large holders buying or selling?",
        ["filing_evidence"],
        None,
    ),
    "relationships": (
        "Who are {name}'s peers, suppliers, customers and group companies?",
        ["filing_evidence", "company_context"],
        None,
    ),
    "risk": (
        "How volatile is {name} and how does it add to the portfolio's risk?",
        ["position_risk", "factor_tilts", "risk_forecast"],
        None,
    ),
    "liquidity": (
        "How easily can the position be traded?",
        ["holding", "price_history"],
        None,
    ),
    "open_questions": (
        "What do the sources leave open that matters for the case?",
        ["data_freshness"],
        None,
    ),
}

MODE_QUESTIONS: dict[str, tuple[str, list[str], str | None]] = {
    "earnings_update": (
        "What changed since the last report: results, guidance, and which thesis KPIs moved?",
        ["thesis_and_reports", "fundamentals", "filing_evidence", "search_news"],
        "{name} latest results, guidance change and management comments",
    ),
    "portfolio_macro": (
        "How do macro conditions and the portfolio's exposure to {name}'s theme and country "
        "bear on it?",
        ["macro_series", "country_context", "theme_exposure", "portfolio_overview"],
        "{name} sector and country macro conditions",
    ),
}

COUNTER_INTENT = SECTION_PLANS["counter_case"][2] or ""


class PlanQuestion(BaseModel):
    id: str
    section: str | None = Field(
        description="The report section it feeds; None for a mode-wide one."
    )
    question: str
    internal_tools: list[str] = Field(description="Chat tool names that read the stored data.")
    web_intent: str | None = Field(default=None, description="What a web search should look for.")


class StopConditions(BaseModel):
    max_steps: int
    max_searches: int
    max_rounds: int
    min_sentences: int = Field(description="Cited sentences a section needs to count as answered.")
    rule: str


class ResearchPlan(BaseModel):
    schema_version: int = PLAN_VERSION
    mode: str
    ticker: str
    question: str
    horizon_days: int | None
    web_emphasis: str
    questions: list[PlanQuestion]
    internal_tools: list[str]
    web_intents: list[str]
    preferred_tiers: list[int]
    primary_required_for: Literal["facts"] | None = Field(
        description="'facts' when tier 1 is allowed: figures and dates should come from it."
    )
    counter_evidence_intent: str | None
    stop: StopConditions
    planned_sections: list[str]
    iterate: bool


def effective_searches(profile: ResearchProfile) -> int:
    """The search budget after the profile's web emphasis."""
    if profile.web_emphasis == "internal":
        return math.ceil(profile.max_searches / 4)
    return profile.max_searches


def build_plan(
    profile: ResearchProfile,
    layout: list[SectionSpec],
    *,
    ticker: str,
    name: str,
    today: dt.date,
) -> ResearchPlan:
    questions: list[PlanQuestion] = []
    if profile.mode in MODE_QUESTIONS:
        q, tools, web = MODE_QUESTIONS[profile.mode]
        questions.append(
            PlanQuestion(
                id="mode",
                section=None,
                question=q.format(name=name),
                internal_tools=tools,
                web_intent=web.format(name=name) if web else None,
            )
        )
    for spec in layout:
        if spec.custom:
            questions.append(
                PlanQuestion(
                    id=spec.id,
                    section=spec.id,
                    question=(spec.task or spec.title)[:400],
                    internal_tools=["data_freshness"],
                    web_intent=f"{name} {spec.title}"[:200],
                )
            )
            continue
        q, tools, web = SECTION_PLANS[spec.id]
        questions.append(
            PlanQuestion(
                id=spec.id,
                section=spec.id,
                question=q.format(name=name),
                internal_tools=tools,
                web_intent=web.format(name=name) if web else None,
            )
        )
    searches = effective_searches(profile)
    if searches == 0:
        for item in questions:
            item.web_intent = None
    tools_union = list(dict.fromkeys(t for item in questions for t in item.internal_tools))
    intents = [item.web_intent for item in questions if item.web_intent]
    has_counter = any(s.id == "counter_case" for s in layout)
    counter = COUNTER_INTENT.format(name=name) if has_counter and searches else None
    rounds = profile.max_rounds if profile.iterate else 0
    rule = (
        "Stop when every planned model section has at least "
        f"{profile.min_sentences} cited sentences, or the step or search budget is used"
        + (f", or {rounds} follow-up round(s) have run." if rounds else ".")
    )
    return ResearchPlan(
        mode=profile.mode,
        ticker=ticker,
        question=f"Is the case for owning {name} sound, and where could it fail?",
        horizon_days=profile.date_horizon_days,
        web_emphasis=profile.web_emphasis,
        questions=questions,
        internal_tools=tools_union,
        web_intents=intents,
        preferred_tiers=sorted(profile.source_tiers),
        primary_required_for="facts" if 1 in profile.source_tiers else None,
        counter_evidence_intent=counter,
        stop=StopConditions(
            max_steps=profile.max_steps,
            max_searches=searches,
            max_rounds=rounds,
            min_sentences=profile.min_sentences,
            rule=rule,
        ),
        planned_sections=[s.id for s in layout],
        iterate=profile.iterate,
    )


def focus_for(plan: ResearchPlan, section: str) -> str:
    """The plan's questions for one section, as the lines the writer sees."""
    lines = [f"- {q.question}" for q in plan.questions if q.section == section]
    if section == "counter_case" and plan.counter_evidence_intent:
        lines.append(f"- Search for: {plan.counter_evidence_intent}")
    else:
        for q in plan.questions:
            if q.section == section and q.web_intent:
                lines.append(f"- Search for: {q.web_intent}")
    return "\n".join(lines)
