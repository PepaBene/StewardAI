"""Unit tests (fakes, no database) for the research trace, the model-written plan and the
formal evaluation of strategy rules in the claim ledger. Fake sentences carry no digits (the
code-side claim check)."""

import datetime as dt
from decimal import Decimal

import pytest

from steward.ai import Usage
from steward.chat import catalog
from steward.config.research import ResearchProfile, ResearchProfilesConfig
from steward.config.strategy import (
    CashWeightRule,
    PositionCountRule,
    PositionWeightRule,
    StrategyConfig,
)
from steward.llm.types import LLMError
from steward.portfolio.drift import HoldingValue, compute_drift
from steward.research.layout import resolve_layout
from steward.research.model_plan import (
    ModelPlanOut,
    ModelQuestion,
    PlannerRequest,
    PlannerResult,
    PlanProblem,
    QuestionEdit,
    apply_edits,
    foreign_hosts,
    make_plan,
    validate_model_plan,
)
from steward.research.plan import SOURCE_GROUPS, ResearchPlan, build_plan
from steward.research.retention import scrub_body
from steward.research.rules import rule_compliance
from steward.research.run import ResearchBody
from steward.research.trace import clip, finish, web_trace
from steward.research.web import DiscoveredUrl

NOW = dt.datetime(2026, 10, 11, tzinfo=dt.UTC)


def profile(**over: object) -> ResearchProfile:
    base = ResearchProfilesConfig().get("standard")
    assert base is not None
    return base.model_copy(update=over)


def code_plan(p: ResearchProfile | None = None) -> ResearchPlan:
    p = p or profile()
    cfg = ResearchProfilesConfig()
    return build_plan(
        p, resolve_layout(p, cfg.template_for(p)), ticker="ACME", name="Acme", today=NOW.date()
    )


# ------------------------------------------------------------------------------------ trace


def test_trace_parts_become_a_bounded_trace_without_bodies() -> None:
    model = {
        "step": "section:business",
        "section": "business",
        "model": "fake",
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": 0.5,
        "ms": 40,
    }
    urls = [
        DiscoveredUrl(
            url=f"https://news.example/a{n}?token=secret",
            title="T" * 400,
            host="news.example",
            status="cited" if n == 0 else "found_not_cited",
            section="business",
            retrieved_at="2026-10-11",
        )
        for n in range(30)
    ]
    web = web_trace("section:business", "business", 0, ["q" * 500] * 20, urls, ["err"])
    parts = [
        {
            "model": model,
            "web": web.model_dump(),
            "validation": {"step": "section:business", "section": "business", "dropped_uncited": 1},
        }
    ]
    trace = finish(parts, {"business": ["not in sources"] * 10})
    assert trace.total_cost_usd == 0.5 and trace.total_ms == 40
    assert len(trace.web[0].queries) <= 8 and all(len(q) <= 200 for q in trace.web[0].queries)
    assert len(trace.web[0].found) <= 12 and all(len(f) <= 200 for f in trace.web[0].found)
    assert "token=secret" not in " ".join(trace.web[0].opened)  # the query string is dropped
    assert trace.web[0].urls_found == 30 and trace.web[0].urls_cited == 1
    assert trace.validation[0].dropped_by_check == 10
    assert trace.validation[0].check_reasons == ["not in sources"]
    assert clip("x" * 300).endswith("…")


def test_old_reports_load_and_the_purge_removes_the_trace() -> None:
    assert "trace" in ResearchBody.model_fields
    body = {"ticker": "A", "trace": {"model_calls": []}, "sources": [], "discovered_urls": []}
    scrubbed = scrub_body(body, NOW)
    assert "trace" not in scrubbed and scrubbed["trace_purged_at"]


# ------------------------------------------------------------------------------- model plan


def test_every_source_group_tool_is_in_the_catalog() -> None:
    for group, tools in SOURCE_GROUPS.items():
        assert all(t in catalog.TOOLS for t in tools), group


def q(section: str, groups: list[str], text: str = "What do the filings say here?", web=None):  # type: ignore[no-untyped-def]
    return ModelQuestion(section=section, question=text, source_groups=groups, web_query=web)  # type: ignore[arg-type]


def test_a_model_plan_is_trimmed_to_the_sections_groups_and_domain_policy() -> None:
    base = code_plan()
    out = ModelPlanOut(
        questions=[
            q("business", ["filings", "web"], web="Acme segments"),
            q("business", ["financials"], "How did revenue develop over the years?"),
            q("business", ["news"], "A third question for one section is too many"),
            q("nonsense", ["filings"]),
            q("valuation", ["financials"], "Compare with prices on evil.example/page please"),
            q("risk", ["portfolio"], "How does it add to portfolio risk overall?"),
        ]
    )
    plan, notes = validate_model_plan(out, base, profile())
    assert plan is not None and plan.authored_by == "model"
    sections = [x.section for x in plan.questions]
    assert sections.count("business") == 2
    assert "nonsense" not in sections
    # the question that named a foreign site was dropped; the code question stays for it
    val = next(x for x in plan.questions if x.section == "valuation")
    assert "evil.example" not in val.question
    assert any("evil.example" in n for n in notes)
    # tools come from groups, all in the catalog; the model never set a tool
    assert all(t in catalog.TOOLS for x in plan.questions for t in x.internal_tools)
    biz = next(x for x in plan.questions if x.id == "business")
    assert biz.web_intent == "Acme segments" and "web" in biz.source_groups
    assert plan.planned_sections == base.planned_sections


def test_no_web_group_survives_a_profile_that_does_not_search() -> None:
    p = profile(max_searches=0)
    base = code_plan(p)
    out = ModelPlanOut(questions=[q("business", ["filings", "web"], web="x")])
    plan, notes = validate_model_plan(out, base, p)
    assert plan is not None
    assert all(x.web_intent is None for x in plan.questions)
    assert any("does not search" in n for n in notes)


def test_a_site_the_profile_allows_may_be_named() -> None:
    assert foreign_hosts("see ir.acme.com today", ["acme.com"]) == []
    assert foreign_hosts("see evil.example", ["acme.com"]) == ["evil.example"]
    assert foreign_hosts("e.g. the U.S. market", []) == []


class FakePlanner:
    def __init__(self, result: PlannerResult | Exception) -> None:
        self.result = result
        self.calls: list[PlannerRequest] = []

    async def plan(self, request: PlannerRequest) -> PlannerResult:
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_planning_falls_back_to_the_code_plan_and_says_why() -> None:
    base = code_plan()
    p = profile()
    got, _ = await make_plan(base, p, None, name="Acme", use_model=True)
    assert got.authored_by == "code" and "no AI provider" in (got.fallback_reason or "")
    got, _ = await make_plan(base, p, FakePlanner(LLMError("down")), name="Acme", use_model=True)
    assert got.fallback_reason and "failed" in got.fallback_reason
    bad = PlannerResult(None, Usage(model="fake"), "no parseable plan", 5, True)
    got, res = await make_plan(base, p, FakePlanner(bad), name="Acme", use_model=True)
    assert "schema" in (got.fallback_reason or "") and res is bad
    empty = PlannerResult(ModelPlanOut(questions=[q("zzz", ["news"])]), Usage(model="fake"))
    got, _ = await make_plan(base, p, FakePlanner(empty), name="Acme", use_model=True)
    assert got.authored_by == "code" and "no valid question" in (got.fallback_reason or "")
    got, res = await make_plan(base, p, None, name="Acme", use_model=False)
    assert got is base and res is None


async def test_a_valid_model_plan_is_used_and_marked_as_model_written() -> None:
    out = ModelPlanOut(questions=[q("business", ["filings"])])
    planner = FakePlanner(PlannerResult(out, Usage(model="fake"), None, 7))
    got, res = await make_plan(code_plan(), profile(), planner, name="Acme", use_model=True)
    assert got.authored_by == "model" and got.fallback_reason is None and res is not None
    assert planner.calls[0].sections and planner.calls[0].web_allowed


# --------------------------------------------------------------------------------- editing


def edit_of(plan: ResearchPlan, **over: object) -> list[QuestionEdit]:
    return [
        QuestionEdit(
            id=x.id,
            section=x.section,
            question=x.question,
            source_groups=x.source_groups,
            web_intent=x.web_intent,
        ).model_copy(update=over.get(x.id, {}))  # type: ignore[arg-type]
        for x in plan.questions
    ]


def test_the_owner_can_reorder_remove_reword_and_toggle_groups() -> None:
    plan = code_plan()
    edits = edit_of(plan)
    edits = [e for e in edits if e.id != "liquidity"]
    edits.reverse()
    biz = next(e for e in edits if e.id == "business")
    biz.question = "What are the three biggest segments by revenue?"
    biz.source_groups = ["filings"]  # web off
    biz.web_intent = None
    got = apply_edits(plan, edits, profile())
    assert got.edited_by_owner
    assert [x.id for x in got.questions] == [e.id for e in edits]
    changed = next(x for x in got.questions if x.id == "business")
    assert changed.web_intent is None and changed.internal_tools == [
        "filing_evidence",
        "company_context",
    ]
    # an untouched question keeps its original tools
    risk = next(x for x in got.questions if x.id == "risk")
    old = next(x for x in plan.questions if x.id == "risk")
    assert risk.internal_tools == old.internal_tools
    assert "liquidity" not in [x.id for x in got.questions]
    assert "web_intents" and len(got.web_intents) == sum(1 for x in got.questions if x.web_intent)


def test_edits_with_a_foreign_site_unknown_group_or_unknown_id_are_refused() -> None:
    plan = code_plan()
    edits = edit_of(plan)
    edits[0].question = "Check what evil.example says about this company"
    edits[1].source_groups = ["telepathy"]
    edits.append(QuestionEdit(id="made-up", section="risk", question="A new question here ok"))
    with pytest.raises(PlanProblem) as caught:
        apply_edits(plan, edits, profile())
    text = " ".join(caught.value.problems)
    assert "evil.example" in text and "telepathy" in text and "made-up" in text
    with pytest.raises(PlanProblem):
        apply_edits(plan, [], profile())


def test_a_section_outside_the_plan_is_refused() -> None:
    plan = code_plan()
    edits = edit_of(plan)
    edits[1].section = "not_a_section"
    with pytest.raises(PlanProblem):
        apply_edits(plan, edits, profile())


def test_old_plans_load_without_the_new_fields() -> None:
    data = code_plan().model_dump()
    for key in ("authored_by", "fallback_reason", "edited_by_owner", "draft_id", "notes"):
        data.pop(key)
    for item in data["questions"]:
        item.pop("source_groups")
        item.pop("label")
    old = ResearchPlan.model_validate(data)
    assert old.authored_by == "code" and not old.edited_by_owner


# ------------------------------------------------------------------------------------ rules


def strategy() -> StrategyConfig:
    return StrategyConfig(
        base_currency="CZK",
        rules=[
            PositionWeightRule(id="cap", type="position_weight", operator="<=", value=0.20),
            PositionWeightRule(
                id="near", type="position_weight", operator="<=", value=0.09, ticker="ACME"
            ),
            PositionWeightRule(
                id="other", type="position_weight", operator="<=", value=0.05, ticker="ZZZ"
            ),
            CashWeightRule(id="cash", type="cash_weight", operator=">=", value=0.02),
            PositionCountRule(id="count", type="position_count", operator="<=", value=20),
        ],
    )


def drift(weight_acme: str):  # type: ignore[no-untyped-def]
    holdings = [
        HoldingValue("ACME", "CZK", Decimal(weight_acme)),
        HoldingValue("REST", "CZK", Decimal(1000) - Decimal(weight_acme)),
    ]
    return compute_drift(holdings, Decimal(50), strategy())


def test_rules_get_a_status_value_limit_and_date_from_the_engine() -> None:
    got = rule_compliance(strategy(), drift("88"), "ACME", "2026-10-10")
    assert got["cap"].status == "pass" and got["cap"].measured is not None
    assert got["cap"].limit == 0.20 and got["cap"].as_of == "2026-10-10"
    assert got["near"].status == "warn"  # holds at 8 %, within 10 % of 9 %
    assert got["other"].status == "unknown" and "does not apply" in got["other"].reason
    assert got["cash"].scope == "portfolio" and got["cash"].status == "pass"
    assert got["count"].unit == "count" and got["count"].measured == 2.0


def test_a_breach_reports_the_measured_weight() -> None:
    got = rule_compliance(strategy(), drift("300"), "ACME", "2026-10-10")
    assert got["cap"].status == "breach" and got["cap"].measured is not None
    assert got["cap"].measured > 0.20 and "ACME" in got["cap"].reason


def test_no_drift_report_or_a_ticker_not_held_leaves_rules_unknown_with_a_reason() -> None:
    none = rule_compliance(strategy(), None, "ACME", None)
    assert {r.status for r in none.values()} == {"unknown"}
    assert all("no drift report" in r.reason or "does not apply" in r.reason for r in none.values())
    not_held = rule_compliance(strategy(), drift("100"), "NOPE", "2026-10-10")
    assert not_held["cap"].status == "unknown" and not_held["cap"].reason == "not held"
    assert not_held["cash"].status in ("pass", "warn", "breach")  # portfolio-level still known
