# ruff: noqa: E501
"""Headline risk score, method `risk_score` v1 (insight I12.1), and the verdict phrases (I12.2).

Pure: no I/O, and nothing is recomputed here that the risk report (`decision/risk_report.py`)
already computes; the inputs are its figures plus the issuer concentration from the X-Ray.

Each input is a "size of risk" figure, larger meaning riskier:

| input | figure | market reference | very high |
| --- | --- | --- | --- |
| volatility | annualised stdev of daily returns | the benchmark's, same window | 40 % |
| max_drawdown | worst peak-to-trough fall (as a positive size) | the benchmark's, same window | 50 % |
| expected_shortfall | 95 % one-day historical ES | the benchmark's, same window | 6 % |
| concentration | three largest issuers' share of the portfolio | 12 % (a broad world index) | 60 % |
| beta | against the benchmark | 1.0 (the benchmark itself) | 2.0 |

A sub-score is the figure's position on a two-piece line: 0 at no risk, 50 at the market
reference, 100 at the very-high level, clamped to 0..100. So a portfolio exactly as risky as its
benchmark on every input scores 50, and the benchmark's own marker sits at 50 by construction
(its concentration reference is the fixed index figure).

score = sum(weight x sub-score) with weights volatility 0.30, max drawdown 0.25, expected
shortfall 0.20, concentration 0.15, beta 0.10. When the benchmark has no figure for an input, the
fallback reference in `FALLBACK` is used and said. The score is `unknown`, never partial, when any
input is missing (the risk report withholds a figure below `THRESHOLDS.min_obs_*`): a score
without one of its inputs would be a different method.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from steward.analytics.versions import version

Tone = Literal["good", "caution", "bad", "neutral", "unknown"]

WEIGHTS: dict[str, Decimal] = {
    "volatility": Decimal("0.30"),
    "max_drawdown": Decimal("0.25"),
    "expected_shortfall": Decimal("0.20"),
    "concentration": Decimal("0.15"),
    "beta": Decimal("0.10"),
}
LABELS = {
    "volatility": "Volatility",
    "max_drawdown": "Maximum drawdown",
    "expected_shortfall": "Expected shortfall (95 %, one day)",
    "concentration": "Three largest issuers",
    "beta": "Beta",
}
VERY_HIGH: dict[str, Decimal] = {
    "volatility": Decimal("0.40"),
    "max_drawdown": Decimal("0.50"),
    "expected_shortfall": Decimal("0.06"),
    "concentration": Decimal("0.60"),
    "beta": Decimal("2.0"),
}
# Used when the benchmark has no figure for the input (and always for concentration and beta).
FALLBACK: dict[str, Decimal] = {
    "volatility": Decimal("0.16"),
    "max_drawdown": Decimal("0.20"),
    "expected_shortfall": Decimal("0.025"),
    "concentration": Decimal("0.12"),
    "beta": Decimal("1.0"),
}
SCORE_BANDS: tuple[tuple[Decimal, str, Tone], ...] = (
    (Decimal(35), "Lower risk than the market", "good"),
    (Decimal(55), "About as risky as the market", "neutral"),
    (Decimal(75), "Riskier than the market", "caution"),
    (Decimal(101), "Much riskier than the market", "bad"),
)
HUNDRED = Decimal(100)
FIFTY = Decimal(50)


class SubScore(BaseModel):
    input: str
    label: str
    value: float | None = Field(description="The figure as a positive size of risk.")
    reference: float = Field(description="Market reference: sub-score 50.")
    reference_basis: Literal["benchmark", "fixed"]
    very_high: float = Field(description="Sub-score 100.")
    score: float | None
    weight: float
    reason: str | None = None


class RiskScore(BaseModel):
    status: Literal["complete", "unknown"]
    score: float | None
    benchmark_score: float | None = Field(
        description="The benchmark scored on the same references (50 unless a reference is fixed)."
    )
    band: str | None
    tone: Tone
    sub_scores: list[SubScore]
    reason: str | None
    method_version: str
    formula: str


def sub_score(value: Decimal, reference: Decimal, very_high: Decimal) -> Decimal:
    """0 at no risk, 50 at the reference, 100 at the very-high level; clamped, non-decreasing."""
    v = max(value, Decimal(0))
    if reference <= 0 or very_high <= reference:
        raise ValueError("references must satisfy 0 < reference < very high")
    if v <= reference:
        s = v / reference * FIFTY
    else:
        s = FIFTY + (v - reference) / (very_high - reference) * FIFTY
    return min(max(s, Decimal(0)), HUNDRED)


@dataclass(frozen=True)
class Inputs:
    """Positive sizes of risk (None when the risk report withheld the figure, with `reasons`)."""

    values: dict[str, Decimal | None]
    benchmark: dict[str, Decimal | None]
    reasons: dict[str, str]


FORMULA = (
    "Each input is placed on a line from 0 (no risk) through 50 (the market reference, the "
    "benchmark's own figure over the same window) to 100 (a very high level), clamped. Score = "
    "0.30 volatility + 0.25 maximum drawdown + 0.20 expected shortfall + 0.15 concentration "
    "(three largest issuers) + 0.10 beta. Unknown when any input is missing."
)


def score(inputs: Inputs) -> RiskScore:
    subs: list[SubScore] = []
    missing: list[str] = []
    total = Decimal(0)
    bench_total = Decimal(0)
    for key, weight in WEIGHTS.items():
        bench = inputs.benchmark.get(key)
        fixed = key in ("concentration", "beta") or bench is None or bench <= 0
        ref = FALLBACK[key] if fixed else bench
        assert ref is not None
        hi = max(VERY_HIGH[key], ref * Decimal("1.5"))  # a very risky benchmark still has headroom
        value = inputs.values.get(key)
        s = None if value is None else sub_score(value, ref, hi)
        reason = inputs.reasons.get(key)
        if fixed and key not in ("concentration", "beta"):
            reason = "; ".join(
                x
                for x in (
                    reason,
                    "the benchmark has no figure, so a fixed market reference is used",
                )
                if x
            )
        if s is None:
            missing.append(LABELS[key])
        else:
            total += weight * s
        bench_val = FALLBACK[key] if key in ("concentration", "beta") else bench
        bench_total += weight * (
            sub_score(bench_val, ref, hi) if bench_val is not None and bench_val > 0 else FIFTY
        )
        subs.append(
            SubScore(input=key, label=LABELS[key], value=None if value is None else float(value),
                     reference=float(ref), reference_basis="fixed" if fixed else "benchmark",
                     very_high=float(hi), score=None if s is None else float(round(s, 1)),
                     weight=float(weight), reason=reason)
        )  # fmt: skip
    if missing:
        return RiskScore(
            status="unknown", score=None, benchmark_score=float(round(bench_total, 1)), band=None,
            tone="unknown", sub_scores=subs,
            reason=f"Not scored: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not available yet.",
            method_version=version("risk_score"), formula=FORMULA,
        )  # fmt: skip
    band, tone = next((label, t) for limit, label, t in SCORE_BANDS if total < limit)
    return RiskScore(
        status="complete", score=float(round(total, 1)), benchmark_score=float(round(bench_total, 1)),
        band=band, tone=tone, sub_scores=subs, reason=None,
        method_version=version("risk_score"), formula=FORMULA,
    )  # fmt: skip


# --- verdict phrases (I12.2) --------------------------------------------------------------------

# metric -> ordered (upper bound exclusive, band id, tone, phrase). The figure's unit is the one the
# risk report uses (fractions; ratios as is). `{bench}` is filled with the benchmark comparison
# when one exists. Versioned with `verdict_phrases`; edit the table, bump the version.
PHRASES: dict[str, tuple[tuple[Decimal, str, Tone, str], ...]] = {
    "sharpe": (
        (Decimal(0), "negative", "bad", "Returns did not beat cash for the risk taken."),
        (Decimal("0.5"), "weak", "caution", "Little extra return for the risk taken."),
        (Decimal(1), "fair", "neutral", "A fair return for the risk taken."),
        (Decimal(2), "good", "good", "A good return for the risk taken."),
        (
            Decimal(999),
            "exceptional",
            "good",
            "An unusually high return for the risk; such runs rarely last.",
        ),
    ),
    "sortino": (
        (Decimal(0), "negative", "bad", "Losing days outweighed the return over cash."),
        (Decimal(1), "fair", "neutral", "Some return for each unit of downside."),
        (Decimal(2), "good", "good", "A good return for the downside taken."),
        (Decimal(999), "exceptional", "good", "Very little downside for the return earned."),
    ),
    "beta": (
        (
            Decimal("0.8"),
            "defensive",
            "good",
            "Moves less than the market: a 10 % market fall has meant less here.",
        ),
        (Decimal("1.2"), "market", "neutral", "Moves about in line with the market."),
        (Decimal(999), "amplified", "caution", "Moves more than the market, both ways."),
    ),
    "volatility": (
        (Decimal("0.10"), "low", "good", "Calm: day-to-day swings are small."),
        (Decimal("0.20"), "moderate", "neutral", "Swings about as much as a broad stock index."),
        (Decimal("0.30"), "high", "caution", "Swings more than a broad stock index."),
        (Decimal(999), "very_high", "bad", "Large swings; a bad year could cost a third or more."),
    ),
    "max_drawdown": (
        (Decimal("0.10"), "shallow", "good", "The worst fall in the window was shallow."),
        (
            Decimal("0.25"),
            "moderate",
            "neutral",
            "The worst fall was in line with a normal market correction.",
        ),
        (Decimal("0.40"), "deep", "caution", "A deep fall from the peak in this window."),
        (
            Decimal(999),
            "severe",
            "bad",
            "A severe fall from the peak; recoveries from such depths take years.",
        ),
    ),
    "es_95": (
        (Decimal("0.015"), "low", "good", "On the worst 5 % of days the average loss was small."),
        (
            Decimal("0.03"),
            "moderate",
            "neutral",
            "On the worst 5 % of days the loss was typical for stocks.",
        ),
        (Decimal(999), "high", "caution", "On the worst 5 % of days the losses were large."),
    ),
    "tracking_error": (
        (Decimal("0.03"), "close", "neutral", "Tracks the benchmark closely."),
        (Decimal("0.08"), "active", "neutral", "Differs from the benchmark by a moderate amount."),
        (Decimal(999), "distinct", "caution", "Behaves quite differently from the benchmark."),
    ),
    "correlation_benchmark": (
        (Decimal("0.5"), "independent", "good", "Moves largely independently of the benchmark."),
        (Decimal("0.85"), "related", "neutral", "Moves partly with the benchmark."),
        (Decimal("1.01"), "tied", "neutral", "Moves almost exactly with the benchmark."),
    ),
    "concentration": (
        (Decimal("0.25"), "spread", "good", "No small group of names dominates."),
        (Decimal("0.45"), "focused", "caution", "A few names carry a large share of the outcome."),
        (Decimal("1.01"), "concentrated", "bad", "Three names decide much of the outcome."),
    ),
    "diversification": (
        (
            Decimal("0.10"),
            "little",
            "caution",
            "The holdings mostly move together, so mixing them removes little risk.",
        ),
        (
            Decimal("0.30"),
            "some",
            "neutral",
            "Mixing the holdings removes a fair part of their separate risk.",
        ),
        (Decimal("1.01"), "strong", "good", "The holdings offset each other well."),
    ),
}


class Verdict(BaseModel):
    metric: str
    band: str
    tone: Tone
    phrase: str
    method_version: str


def phrase(metric: str, value: Decimal | float | None) -> Verdict | None:
    """The verdict for a figure, or None for a metric without phrases or an unknown figure."""
    table = PHRASES.get(metric)
    if table is None or value is None:
        return None
    v = abs(Decimal(str(value))) if metric == "max_drawdown" else Decimal(str(value))
    for limit, band, tone, text in table:
        if v < limit:
            return Verdict(
                metric=metric,
                band=band,
                tone=tone,
                phrase=text,
                method_version=version("verdict_phrases"),
            )
    _limit, band, tone, text = table[-1]
    return Verdict(
        metric=metric, band=band, tone=tone, phrase=text, method_version=version("verdict_phrases")
    )
