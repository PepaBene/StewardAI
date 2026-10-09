# ruff: noqa: E501 - rule text and messages stay on one line each
"""Quality-value-momentum composite, red flags and the idea-pipeline screen (plan M1.8).

Published, versioned rules (method `composite_qvm`):

- each component is the mean *percentile rank* of its metrics, within the issuer's sector when
  that sector has at least `min_universe_for_percentile` issuers with the metric, otherwise
  within the whole chosen universe (the basis is reported per component);
- percentile rank = (below + 0.5 x equal) / n, inverted for a metric where lower is better;
- a component abstains (no number) when fewer than half of its metrics have a value or no
  ranking group is large enough; the composite abstains when any component abstains, and says
  which;
- default weights quality 40 %, value 40 %, momentum 20 %.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from steward.analytics.thresholds import THRESHOLDS
from steward.analytics.versions import version
from steward.discovery.engine import Record

ZERO = Decimal(0)
ONE = Decimal(1)

WEIGHTS: dict[str, Decimal] = {
    "quality": Decimal("0.4"),
    "value": Decimal("0.4"),
    "momentum": Decimal("0.2"),
}
COMPONENTS: dict[str, tuple[str, ...]] = {
    "quality": ("gross_profit_to_assets", "roic", "operating_margin"),
    "value": ("fcf_yield", "earnings_yield", "ev_ebit"),
    "momentum": ("momentum_12_1",),
}
LOWER_IS_BETTER = {"ev_ebit"}

# Red-flag thresholds (versioned with the method; the owner changes them by version).
RED_FLAG_RULES = {
    "dilution": (
        "share_change_1y",
        Decimal("0.10"),
        "gt",
        "share count up more than 10 % in a year",
    ),
    "asset_growth": (
        "asset_growth",
        Decimal("0.30"),
        "gt",
        "total assets up more than 30 % in a year",
    ),
    "accruals": ("accruals_ratio", Decimal("0.10"), "gt", "accruals above 10 % of average assets"),
    "young_listing": (
        "years_since_listing",
        Decimal(2),
        "lt",
        "first traded less than 2 years ago",
    ),
    "short_runway": (
        "cash_runway_years",
        Decimal(1),
        "lt",
        "cash covers less than a year of cash burn",
    ),
}
SPECULATIVE_MIN_RUNWAY_YEARS = Decimal(2)
SPECULATIVE_MAX_DILUTION = Decimal("0.10")


def _num(rec: Record, metric: str) -> Decimal | None:
    v = rec.values.get(metric)
    return v.value if v is not None and isinstance(v.value, Decimal) else None


def _text(rec: Record, metric: str) -> str | None:
    v = rec.values.get(metric)
    if v is not None and isinstance(v.value, str):
        return v.value
    return rec.identity.get(metric)


def percentile(value: Decimal, group: Sequence[Decimal]) -> Decimal:
    below = sum(1 for g in group if g < value)
    equal = sum(1 for g in group if g == value)
    return (Decimal(below) + Decimal("0.5") * Decimal(equal)) / Decimal(len(group))


@dataclass
class QvmScore:
    score: Decimal | None  # 0-100
    components: dict[str, Decimal | None]
    bases: dict[str, str] = field(default_factory=dict)
    abstained: list[str] = field(default_factory=list)
    method_version: str = ""


def compute_qvm(
    records: Sequence[Record], weights: Mapping[str, Decimal] = WEIGHTS
) -> dict[str, QvmScore]:
    """One score per record key. `weights` must sum to 1."""
    if sum(weights.values(), ZERO) != ONE:
        raise ValueError("composite weights must sum to 1")
    method = version("composite_qvm")
    sector_of = {r.key: _text(r, "sector") for r in records}
    # metric -> sector -> values, and metric -> all values
    by_sector: dict[str, dict[str | None, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    everyone: dict[str, list[Decimal]] = defaultdict(list)
    for r in records:
        for metrics in COMPONENTS.values():
            for m in metrics:
                v = _num(r, m)
                if v is not None:
                    by_sector[m][sector_of[r.key]].append(v)
                    everyone[m].append(v)
    out: dict[str, QvmScore] = {}
    for r in records:
        comps: dict[str, Decimal | None] = {}
        bases: dict[str, str] = {}
        abstained: list[str] = []
        for name, metrics in COMPONENTS.items():
            ranks: list[Decimal] = []
            used_bases: set[str] = set()
            for m in metrics:
                v = _num(r, m)
                if v is None:
                    continue
                sec = sector_of[r.key]
                sector_group = by_sector[m][sec] if sec else []
                if len(sector_group) >= THRESHOLDS.min_universe_for_percentile:
                    group, basis = sector_group, "sector"
                elif len(everyone[m]) >= THRESHOLDS.min_universe_for_percentile:
                    group, basis = everyone[m], "universe"
                else:
                    continue
                p = percentile(v, group)
                ranks.append(ONE - p if m in LOWER_IS_BETTER else p)
                used_bases.add(basis)
            needed = Decimal(len(metrics)) * THRESHOLDS.min_metric_coverage_for_rank
            if ranks and Decimal(len(ranks)) >= needed:
                comps[name] = sum(ranks, ZERO) / len(ranks)
                bases[name] = "+".join(sorted(used_bases))
            else:
                comps[name] = None
                abstained.append(name)
        if abstained:
            out[r.key] = QvmScore(None, comps, bases, abstained, method)
        else:
            total = sum((weights[n] * (comps[n] or ZERO) for n in COMPONENTS), ZERO)
            out[r.key] = QvmScore(total * 100, comps, bases, [], method)
    return out


@dataclass(frozen=True)
class Flag:
    id: str
    label: str
    state: str  # flag, clear, unchecked
    detail: str


def red_flags(rec: Record) -> list[Flag]:
    flags: list[Flag] = []
    for fid, (metric, limit, op, label) in RED_FLAG_RULES.items():
        v = _num(rec, metric)
        if v is None:
            # A self-funding company has no runway: that is clear, not unchecked.
            reason = rec.values.get(metric).reason if rec.values.get(metric) else None  # type: ignore[union-attr]
            if metric == "cash_runway_years" and reason and reason.startswith("not burning"):
                flags.append(Flag(fid, label, "clear", reason))
            else:
                flags.append(Flag(fid, label, "unchecked", reason or "no value"))
            continue
        hit = v > limit if op == "gt" else v < limit
        flags.append(Flag(fid, label, "flag" if hit else "clear", f"{v:.4f} vs limit {limit}"))
    buyers = _num(rec, "insider_buyers_90d")
    if buyers is not None and buyers >= 2:
        flags.append(
            Flag(
                "insider_cluster",
                "two or more insiders bought in 90 days",
                "flag",
                f"{buyers:.0f} distinct insiders (a positive signal, listed with the flags)",
            )
        )
    return flags


@dataclass(frozen=True)
class IdeaVerdict:
    key: str
    passes: bool
    reasons: list[str]
    speculative: bool


def idea_pipeline(
    records: Sequence[Record], scores: Mapping[str, QvmScore]
) -> dict[str, IdeaVerdict]:
    """Top third on the composite, no red flag, and the speculative-tier requirements (two-year
    cash runway, dilution at most 10 % a year) for a cash-burning company."""
    scored = sorted((s.score for s in scores.values() if s.score is not None), reverse=True)
    cutoff = scored[max(0, -(-len(scored) // 3) - 1)] if scored else None
    out: dict[str, IdeaVerdict] = {}
    for r in records:
        s = scores.get(r.key)
        reasons: list[str] = []
        if s is None or s.score is None:
            reasons.append(
                "composite abstains: "
                + (", ".join(s.abstained) if s else "no score")
                + " lacks coverage"
            )
        elif cutoff is not None and s.score < cutoff:
            reasons.append(f"composite {s.score:.1f} is below the top-third cut {cutoff:.1f}")
        flags = red_flags(r)
        for f in flags:
            if f.state == "flag" and f.id != "insider_cluster":
                reasons.append(f"red flag: {f.label}")
        if any(f.state == "unchecked" for f in flags):
            reasons.append("red flags not checked")
        runway = _num(r, "cash_runway_years")
        speculative = runway is not None  # a runway exists only for a company burning cash
        if speculative:
            assert runway is not None
            if runway < SPECULATIVE_MIN_RUNWAY_YEARS:
                reasons.append(
                    f"speculative tier needs a {SPECULATIVE_MIN_RUNWAY_YEARS}-year runway; has {runway:.1f}"
                )
            dilution = _num(r, "share_change_1y")
            if dilution is None:
                reasons.append(
                    "speculative tier needs dilution known; share count change is missing"
                )
            elif dilution > SPECULATIVE_MAX_DILUTION:
                reasons.append(f"speculative tier allows 10 % dilution a year; has {dilution:.1%}")
        out[r.key] = IdeaVerdict(r.key, not reasons, reasons, speculative)
    return out
