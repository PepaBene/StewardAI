"""The research benchmark: labelled cases, the checks that keep them honest, and the part of
scoring that code can do.

`evals/research_cases.json` labels, per question, which tools a good run must reach, the
datasets and strategy rules involved, the figures code must own, what the answer must admit
and the claims it must not make. Code scores what a run's trace shows: whether the expected
tool groups were reached and whether any call failed. Fact accuracy, citation support and
balance stay human-reviewed against the same labels. A failure is filed under the stage it
broke at, so a regression points at retrieval, analysis, citation, strategy or rendering
instead of "the answer got worse".
"""

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from steward.chat import catalog

CASES_PATH = Path(__file__).resolve().parents[3] / "evals" / "research_cases.json"
CATEGORIES = (
    "quick_fact",
    "holding_thesis",
    "new_ticker",
    "earnings_review",
    "disputed_news",
    "portfolio_exposure",
    "macro",
    "counter_case",
    "missing_data",
    "general_research",
)
FailureStage = Literal["retrieval", "analysis", "citation", "strategy", "rendering"]
# Tools that start something rather than read; a case that does not expect one must not see it.
ACTION_TOOLS = frozenset({"propose_job"})


class Case(BaseModel):
    id: str
    category: str
    question: str = Field(min_length=10)
    tool_groups: list[list[str]] = Field(min_length=1)
    datasets: list[str] = Field(min_length=1)
    strategy_rules: list[str] = Field(default_factory=list)
    code_owned: list[str] = Field(default_factory=list)
    must_admit: list[str] = Field(default_factory=list)
    unsupported_traps: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _known(self) -> "Case":
        if self.category not in CATEGORIES:
            raise ValueError(f"{self.id}: unknown category {self.category}")
        if any(not group for group in self.tool_groups):
            raise ValueError(f"{self.id}: an empty tool group can never be satisfied")
        return self


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Case.model_validate(c) for c in raw["cases"]]


def problems(cases: Sequence[Case], tool_names: set[str]) -> list[str]:
    """Everything wrong with the case set itself: duplicate ids, tools or datasets that do not
    exist, a category with fewer than three cases, a tool or dataset no case exercises."""
    out: list[str] = []
    ids = Counter(c.id for c in cases)
    out += [f"duplicate case id {i}" for i, n in ids.items() if n > 1]
    datasets = {d.id for d in catalog.DATASETS}
    reached: set[str] = set()
    covered_datasets: set[str] = set()
    for c in cases:
        for group in c.tool_groups:
            for tool in group:
                if tool not in tool_names:
                    out.append(f"{c.id}: unknown tool {tool}")
                reached.add(tool)
        for dataset in c.datasets:
            if dataset not in datasets:
                out.append(f"{c.id}: unknown dataset {dataset}")
            covered_datasets.add(dataset)
    per_category = Counter(c.category for c in cases)
    out += [
        f"category {cat} has {per_category.get(cat, 0)} cases, needs at least 3"
        for cat in CATEGORIES
        if per_category.get(cat, 0) < 3
    ]
    out += [f"no case reaches tool {t}" for t in sorted(tool_names - reached)]
    out += [
        f"no case covers dataset {d.id}"
        for d in catalog.DATASETS
        if d.tools and d.id not in covered_datasets
    ]
    return out


class Score(BaseModel):
    case: str
    tool_recall: float = Field(description="Share of expected tool groups the run reached.")
    missed_groups: list[list[str]]
    failed_calls: list[str]
    unexpected_actions: list[str]
    failure: FailureStage | None = Field(
        description="The stage this run broke at, from the trace alone; null when the trace "
        "shows no break. Analysis, citation, strategy and rendering failures are assigned in "
        "review."
    )


def score_trace(case: Case, trace: Sequence[dict[str, Any]]) -> Score:
    """Scores one run's tool trace (`ChatTools.trace`) against its case."""
    called = {str(t.get("tool")) for t in trace}
    missed = [g for g in case.tool_groups if not called.intersection(g)]
    expected = {tool for g in case.tool_groups for tool in g}
    failed = [str(t["tool"]) for t in trace if not t.get("ok", True)]
    actions = sorted((called & ACTION_TOOLS) - expected)
    recall = (len(case.tool_groups) - len(missed)) / len(case.tool_groups)
    return Score(
        case=case.id,
        tool_recall=round(recall, 3),
        missed_groups=missed,
        failed_calls=failed,
        unexpected_actions=actions,
        failure="retrieval" if missed or failed else None,
    )


def summarize(scores: Sequence[Score], cases: Sequence[Case]) -> dict[str, Any]:
    """Mean tool recall overall and per category, and the failures by stage."""
    by_id = {c.id: c for c in cases}
    per_category: dict[str, list[float]] = {}
    for s in scores:
        per_category.setdefault(by_id[s.case].category, []).append(s.tool_recall)
    return {
        "cases": len(scores),
        "tool_recall": round(sum(s.tool_recall for s in scores) / len(scores), 3)
        if scores
        else None,
        "by_category": {k: round(sum(v) / len(v), 3) for k, v in sorted(per_category.items())},
        "failures": dict(Counter(s.failure for s in scores if s.failure)),
        "unexpected_actions": sum(len(s.unexpected_actions) for s in scores),
    }
