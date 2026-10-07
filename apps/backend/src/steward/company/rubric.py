"""The StewardAI assessment rubric (plan M2.13): a compact, decomposable score of a company's
financial durability, capital allocation, valuation assumptions and evidence quality.

Rules that make it a transparent aid rather than a verdict:

* Every indicator publishes its source, direction, the two thresholds that bound its bands and
  its weight; a band is worth 100 (good), 60 (acceptable) or 20 (weak) points, so a score can be
  reproduced by hand from the table.
* A missing indicator is left out of its component and lowers the component's *coverage*. A
  component below `MIN_COMPONENT_COVERAGE` abstains (no number), and the rest is scaled by a
  published missing-data penalty: `PENALTY_POINTS x (1 - coverage)` points are deducted.
* The overall figure abstains unless components worth `MIN_OVERALL_WEIGHT` of the weight scored and
  financial durability is one of them. There is no star, medal or moat label.
* The rubric has not been tested against outcomes. `calibration` says so; it is not a forecast.
"""

# ruff: noqa: E501
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from steward.analytics.contract import input_hash, rounded
from steward.company.ratios import RatiosView
from steward.company.statements import StatementsView

RUBRIC_VERSION = "1.0.0"
D = Decimal
GOOD, OK, WEAK = D(100), D(60), D(20)
PENALTY_POINTS = D(20)
MIN_COMPONENT_COVERAGE = D("0.6")
MIN_OVERALL_WEIGHT = D("0.7")

Direction = Literal["higher", "lower", "abs_lower"]


@dataclass(frozen=True)
class Spec:
    id: str
    label: str
    component: str
    weight: Decimal
    direction: Direction
    good: Decimal
    ok: Decimal
    unit: str  # "fraction" | "multiple" | "count" | "days" | "years" | "share"
    source: str
    use: Literal["median", "latest"] = "median"


COMPONENTS: dict[str, tuple[str, Decimal, str]] = {
    "durability": (
        "Financial durability",
        D("0.35"),
        "Whether the business earns well above its cost of capital and could absorb a bad year.",
    ),
    "allocation": (
        "Capital allocation",
        D("0.25"),
        "Whether profits turn into cash and are kept for owners rather than diluted away.",
    ),
    "valuation": (
        "Valuation assumptions",
        D("0.20"),
        "How demanding the price is against what the business earns and has delivered.",
    ),
    "evidence": (
        "Evidence quality",
        D("0.20"),
        "How much of the picture rests on filed, reconciled, recent and cited evidence.",
    ),
}

SPECS: tuple[Spec, ...] = (
    Spec(
        "roic",
        "Return on invested capital",
        "durability",
        D("0.30"),
        "higher",
        D("0.12"),
        D("0.06"),
        "fraction",
        "ratio roic, 5-year own median",
    ),
    Spec(
        "interest_coverage",
        "Interest coverage",
        "durability",
        D("0.20"),
        "higher",
        D(8),
        D(3),
        "multiple",
        "ratio interest_coverage, 5-year own median",
    ),
    Spec(
        "net_debt_to_ebitda",
        "Net debt to EBITDA",
        "durability",
        D("0.20"),
        "lower",
        D("1.5"),
        D("3.5"),
        "multiple",
        "ratio net_debt_to_ebitda, 5-year own median",
    ),
    Spec(
        "fcf_margin",
        "Free-cash-flow margin",
        "durability",
        D("0.15"),
        "higher",
        D("0.10"),
        D("0.03"),
        "fraction",
        "ratio fcf_margin, 5-year own median",
    ),
    Spec(
        "current_ratio",
        "Current ratio",
        "durability",
        D("0.15"),
        "higher",
        D("1.5"),
        D("1.0"),
        "multiple",
        "ratio current_ratio, latest year",
        "latest",
    ),
    Spec(
        "dilution_3y",
        "Share-count change per year (3 years)",
        "allocation",
        D("0.40"),
        "lower",
        D("0.0"),
        D("0.03"),
        "fraction",
        "ratio dilution_3y, latest",
        "latest",
    ),
    Spec(
        "fcf_conversion",
        "Free cash flow to net income",
        "allocation",
        D("0.35"),
        "higher",
        D("0.8"),
        D("0.5"),
        "fraction",
        "ratio fcf_conversion, 5-year own median",
    ),
    Spec(
        "accruals_ratio",
        "Accruals ratio (absolute)",
        "allocation",
        D("0.25"),
        "abs_lower",
        D("0.05"),
        D("0.10"),
        "fraction",
        "ratio accruals_ratio, 5-year own median",
    ),
    Spec(
        "earnings_yield",
        "Earnings yield",
        "valuation",
        D("0.40"),
        "higher",
        D("0.06"),
        D("0.035"),
        "fraction",
        "ratio earnings_yield, latest",
        "latest",
    ),
    Spec(
        "fcf_yield",
        "Free-cash-flow yield",
        "valuation",
        D("0.30"),
        "higher",
        D("0.05"),
        D("0.03"),
        "fraction",
        "ratio fcf_yield, latest",
        "latest",
    ),
    Spec(
        "implied_growth",
        "Growth the price implies (reverse DCF)",
        "valuation",
        D("0.30"),
        "lower",
        D("0.04"),
        D("0.08"),
        "fraction",
        "reverse DCF on the starting scenario",
        "latest",
    ),
    Spec(
        "history_years",
        "Years of filed annual history",
        "evidence",
        D("0.25"),
        "higher",
        D(8),
        D(4),
        "count",
        "standardized statements",
    ),
    Spec(
        "checks_passed",
        "Statement checks that passed",
        "evidence",
        D("0.30"),
        "higher",
        D("0.95"),
        D("0.80"),
        "share",
        "statement tie-out checks",
    ),
    Spec(
        "period_age_days",
        "Age of the latest annual period",
        "evidence",
        D("0.20"),
        "lower",
        D(450),
        D(640),
        "days",
        "latest annual period end",
    ),
    Spec(
        "citations",
        "Case items with a citation",
        "evidence",
        D("0.25"),
        "higher",
        D("0.8"),
        D("0.5"),
        "share",
        "your cited case items",
    ),
)


class Indicator(BaseModel):
    id: str
    label: str
    component: str
    weight: Decimal
    value: Decimal | None
    unit: str
    direction: str
    good: Decimal
    ok: Decimal
    band: Literal["good", "ok", "weak", "missing"]
    points: Decimal | None
    source: str
    reason: str | None = None


class Component(BaseModel):
    id: str
    label: str
    weight: Decimal
    description: str
    score: Decimal | None
    raw_score: Decimal | None
    coverage: Decimal
    abstained: bool
    reason: str | None = None
    indicators: list[Indicator]


class Calibration(BaseModel):
    status: Literal["uncalibrated"] = "uncalibrated"
    note: str


class Rubric(BaseModel):
    ticker: str
    as_of: dt.date
    version: str
    overall: Decimal | None
    overall_coverage: Decimal
    abstained: bool
    reason: str | None
    components: list[Component]
    penalty_rule: str
    calibration: Calibration
    caveats: list[str]
    input_hash: str


def _band(spec: Spec, value: Decimal) -> tuple[Literal["good", "ok", "weak"], Decimal]:
    v = abs(value) if spec.direction == "abs_lower" else value
    if spec.direction == "higher":
        passed_good, passed_ok = v >= spec.good, v >= spec.ok
    else:
        passed_good, passed_ok = v <= spec.good, v <= spec.ok
    if passed_good:
        return "good", GOOD
    if passed_ok:
        return "ok", OK
    return "weak", WEAK


def _ratio_value(view: RatiosView, rid: str, use: str) -> tuple[Decimal | None, str | None]:
    for g in view.groups:
        for r in g.ratios:
            if r.id != rid:
                continue
            if use == "median" and r.own_median_5y is not None:
                return r.own_median_5y, None
            if r.latest is not None and r.latest.value is not None:
                note = "latest year (no 5-year median yet)" if use == "median" else None
                return r.latest.value, note
            return None, (r.latest.reason if r.latest and r.latest.reason else "no value")
    return None, "ratio not in the library"


def build_rubric(
    ticker: str,
    ratios: RatiosView,
    annual: StatementsView,
    case_cited: tuple[int, int] | None,
    implied_growth: Decimal | None,
    today: dt.date,
) -> Rubric:
    """`case_cited` is (items with a citation, items), or None when there are no case items."""
    annual_columns = [c for c in annual.columns if c.kind == "annual"]
    decided = [c for c in annual.checks if c.passed is not None]
    derived: dict[str, tuple[Decimal | None, str | None]] = {
        "history_years": (D(len(annual_columns)), None)
        if annual_columns
        else (None, "no annual periods"),
        "checks_passed": (
            (D(sum(1 for c in decided if c.passed)) / D(len(decided)), None)
            if decided
            else (None, "no statement check could be evaluated")
        ),
        "period_age_days": (
            (D((today - annual_columns[-1].end).days), None)
            if annual_columns
            else (None, "no annual period")
        ),
        "citations": (
            (D(case_cited[0]) / D(case_cited[1]), None)
            if case_cited and case_cited[1] > 0
            else (None, "no case items recorded")
        ),
        "implied_growth": (
            (implied_growth, None)
            if implied_growth is not None
            else (None, "reverse DCF unavailable (no price or scenario)")
        ),
    }
    by_component: dict[str, list[Indicator]] = {cid: [] for cid in COMPONENTS}
    for spec in SPECS:
        if spec.id in derived:
            value, note = derived[spec.id]
        else:
            value, note = _ratio_value(ratios, spec.id, spec.use)
        if value is None:
            band: Literal["good", "ok", "weak", "missing"] = "missing"
            points = None
        else:
            band, points = _band(spec, value)
        by_component[spec.component].append(
            Indicator(
                id=spec.id,
                label=spec.label,
                component=spec.component,
                weight=spec.weight,
                value=value,
                unit=spec.unit,
                direction=spec.direction,
                good=spec.good,
                ok=spec.ok,
                band=band,
                points=points,
                source=spec.source,
                reason=note if value is None or note else None,
            )
        )
    components: list[Component] = []
    for cid, (label, weight, description) in COMPONENTS.items():
        inds = by_component[cid]
        known = [i for i in inds if i.points is not None]
        total_w = sum((i.weight for i in inds), D(0))
        known_w = sum((i.weight for i in known), D(0))
        coverage = known_w / total_w if total_w else D(0)
        raw = score = None
        abstained = coverage < MIN_COMPONENT_COVERAGE
        reason = None
        if abstained:
            missing = ", ".join(i.label for i in inds if i.points is None)
            reason = f"Only {rounded(coverage * 100, 'money')}% of the indicators have a value; missing: {missing}."
        else:
            raw = sum((i.weight * (i.points or D(0)) for i in known), D(0)) / known_w
            score = raw - PENALTY_POINTS * (D(1) - coverage)
        components.append(
            Component(
                id=cid,
                label=label,
                weight=weight,
                description=description,
                score=rounded(score, "ratio") if score is not None else None,
                raw_score=rounded(raw, "ratio") if raw is not None else None,
                coverage=rounded(coverage, "ratio") or D(0),
                abstained=abstained,
                reason=reason,
                indicators=inds,
            )
        )
    scored = [c for c in components if c.score is not None]
    scored_w = sum((c.weight for c in scored), D(0))
    overall = None
    abstained = False
    why = None
    durability_scored = any(c.id == "durability" for c in scored)
    if scored_w < MIN_OVERALL_WEIGHT or not durability_scored:
        abstained = True
        names = ", ".join(c.label for c in components if c.score is None)
        why = f"Not enough evidence for a combined figure: no score for {names}."
        if not names:
            why = "Not enough evidence for a combined figure."
    else:
        overall = sum((c.weight * (c.score or D(0)) for c in scored), D(0)) / scored_w
    return Rubric(
        ticker=ticker,
        as_of=today,
        version=RUBRIC_VERSION,
        overall=rounded(overall, "ratio") if overall is not None else None,
        overall_coverage=rounded(scored_w, "ratio") or D(0),
        abstained=abstained,
        reason=why,
        components=components,
        penalty_rule=(
            f"Each component below {MIN_COMPONENT_COVERAGE * 100:.0f}% indicator coverage abstains; "
            f"otherwise {PENALTY_POINTS} x (1 - coverage) points are deducted. The combined figure "
            f"needs components worth at least {MIN_OVERALL_WEIGHT * 100:.0f}% of the weight, "
            "including financial durability."
        ),
        calibration=Calibration(
            note=(
                "Not tested against later outcomes, so the thresholds are StewardAI's judgement and "
                "the figure is not a forecast or a rating. It is reproducible from the published "
                "table; golden cases pin the arithmetic only."
            )
        ),
        caveats=[
            "Banks and insurers lack most durability ratios and normally abstain.",
            "Uses the company's own 5-year medians where available; one weak year does not decide a band.",
            "A good score does not mean the price is attractive; read it with the valuation component and your own thesis.",
        ],
        input_hash=input_hash(
            {
                "ratios": ratios.input_hash,
                "statements": annual.input_hash,
                "cited": case_cited,
                "implied": implied_growth,
                "version": RUBRIC_VERSION,
                "day": today,
            }
        ),
    )


def sequence_len(items: Sequence[object]) -> int:
    return len(items)
