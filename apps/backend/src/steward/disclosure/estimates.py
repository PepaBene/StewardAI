"""P7: published consensus, guidance, named estimates, revisions and surprises, as pure functions.

The rules this module enforces:
  * five series are different things and never share a lane or an average: company guidance (a
    management target, a bracket), published analyst consensus (a file an issuer or a vendor
    published), named individual estimates, owner scenarios and event-calendar estimates (one
    vendor number for one event, not a panel);
  * a management target is never an analyst estimate: guidance carries low/high only;
  * two snapshots are compared only when they share issuer, series, metric, scope or segment,
    fiscal period, unit, currency, basis AND panel methodology; otherwise the result is a typed
    break reason (`basis_changed`, `currency_changed`, `panel_method_changed`, `period_redefined`,
    `unit_changed`, `restated`) and never a number;
  * a revision is only between consecutive compatible snapshots: absolute and percent change,
    elapsed days, analyst-count change when BOTH counts are stated (else null with a reason),
    and a direction. Nothing is interpolated to daily data;
  * a surprise is the reported actual against the latest snapshot known STRICTLY BEFORE the
    release; a file archived or corrected later never scores an earlier surprise. The formula is
    versioned and stated in the output; the percentage exists only when the estimate is not zero;
  * a segment comparison maps each estimate to a reported segment of the same fiscal period;
    recasts and elimination or non-additive lines are kept and flagged; a missing segment estimate
    stays missing and a taxonomy break is never scored;
  * a named broker estimate needs analyst, broker, metric, period and publication time. When no
    source returns them the answer is a coverage gap, and analyst-level values are NEVER derived
    from a consensus mean or range;
  * revision alerts and factor-like screens stay off until 20 reviewed snapshot pairs pass a
    stated bar.
Null means "not stated". It is never zero.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise
from typing import Any, Literal

ZERO = Decimal(0)
HUNDRED = Decimal(100)

Series = Literal[
    "company_guidance",
    "published_analyst_consensus",
    "named_individual_estimate",
    "owner_scenario",
    "event_calendar_estimate",
]
SERIES: tuple[Series, ...] = (
    "published_analyst_consensus",
    "named_individual_estimate",
    "company_guidance",
    "event_calendar_estimate",
    "owner_scenario",
)
BreakReason = Literal[
    "basis_changed",
    "currency_changed",
    "panel_method_changed",
    "period_redefined",
    "unit_changed",
    "restated",
]
Statistic = Literal["mean", "median", "midpoint"]

SURPRISE_FORMULA_VERSION = "surprise.v1"
SURPRISE_FORMULA = (
    "surprise = reported actual - estimate; percent = surprise / |estimate|, defined only when "
    "the estimate is not zero. The estimate is the mean (else the median, else the midpoint of "
    "low and high) of the latest snapshot known strictly before the release."
)
REVISION_FORMULA_VERSION = "revision.v1"
REVISION_GATE_PAIRS = 20
REVISION_GATE_BAR = Decimal("0.90")


@dataclass(frozen=True)
class Value:
    """One estimate line of one snapshot, with its snapshot's provenance."""

    id: int
    snapshot_id: int
    series: Series
    published_at: dt.datetime
    known_at: dt.datetime
    metric: str
    fiscal_period: str
    period_end: dt.date | None
    unit: str
    basis: str
    scale: int = 0
    segment: str | None = None
    segment_taxonomy: str | None = None
    non_additive: bool = False
    restated_prior: bool = False
    fiscal_calendar: str | None = None
    period_start: dt.date | None = None
    currency: str | None = None
    basis_definition: str | None = None
    mean: Decimal | None = None
    median: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    analyst_count: int | None = None
    panel_method: str | None = None
    panel_description: str | None = None
    broker: str | None = None
    analyst: str | None = None
    supersedes_snapshot_id: int | None = None
    source: str | None = None
    document_url: str | None = None
    evidence_id: int | None = None
    original_string: str | None = None


# ------------------------------------------------------------------------------- validity
def problems(v: Value) -> list[str]:
    """Why a line is malformed. A line with problems is shown but never compared or scored."""
    out: list[str] = []
    if v.series == "company_guidance" and (
        v.mean is not None or v.median is not None or v.analyst_count is not None
    ):
        out.append("company guidance is a management target: low/high only, never analyst data")
    if v.series == "named_individual_estimate" and not (v.broker and v.analyst):
        out.append("a named estimate needs its analyst and broker")
    if v.series != "named_individual_estimate" and (v.broker or v.analyst):
        out.append("only a named estimate carries a broker or analyst")
    if v.low is not None and v.high is not None and v.low > v.high:
        out.append("low is above high")
    for name, x in (("mean", v.mean), ("median", v.median)):
        if x is None:
            continue
        if (v.low is not None and x < v.low) or (v.high is not None and x > v.high):
            out.append(f"{name} lies outside the stated range")
    if v.mean is None and v.median is None and v.low is None and v.high is None:
        out.append("no value is stated")
    if v.period_end is None:
        out.append("the fiscal period has no end date")
    if v.series == "named_individual_estimate" and not v.published_at:
        out.append("a named estimate needs a publication time")
    return out


def visible(values: Iterable[Value], as_of: dt.datetime, *, strict: bool = False) -> list[Value]:
    """Lines known by `as_of` (strictly before it when `strict`). A snapshot that a corrected file
    replaced is dropped only if the correction is itself known by then, so history never changes."""
    known = [v for v in values if (v.known_at < as_of if strict else v.known_at <= as_of)]
    replaced = {v.supersedes_snapshot_id for v in known if v.supersedes_snapshot_id is not None}
    return [v for v in known if v.snapshot_id not in replaced]


def lanes(values: Iterable[Value]) -> dict[Series, list[Value]]:
    """One list per series, in the fixed lane order, newest snapshot first. Nothing is merged."""
    out: dict[Series, list[Value]] = {s: [] for s in SERIES}
    for v in values:
        out[v.series].append(v)
    for rows in out.values():
        rows.sort(key=lambda v: (v.known_at, v.id), reverse=True)
    return out


def scaled(x: Decimal | None, scale: int) -> Decimal | None:
    """`x` in base units (a value reported in millions times 10**6). Percent and per-share lines
    keep a scale of 0."""
    return None if x is None else x * (Decimal(10) ** scale)


def base(v: Value, x: Decimal | None) -> Decimal | None:
    return scaled(x, v.scale)


def statistic_of(v: Value) -> tuple[Statistic, Decimal] | None:
    """The one number a comparison uses: mean, else median, else the midpoint of low and high."""
    if v.mean is not None:
        return "mean", v.mean
    if v.median is not None:
        return "median", v.median
    if v.low is not None and v.high is not None:
        return "midpoint", (v.low + v.high) / 2
    return None


# ----------------------------------------------------------------------------- comparability
def group_key(v: Value) -> tuple[str, str, str, str, str]:
    """What must be identical for two lines to be the same estimate series over time. A named
    estimate is also per broker and analyst."""
    who = f"{v.broker}|{v.analyst}" if v.series == "named_individual_estimate" else ""
    return (v.series, who, v.metric.lower(), (v.segment or "").lower(), v.fiscal_period)


def break_reasons(a: Value, b: Value) -> tuple[BreakReason, ...]:
    """Why `a` (earlier) and `b` (later) are not comparable. Empty means they are."""
    out: list[BreakReason] = []
    if (a.period_start, a.period_end, a.fiscal_calendar) != (
        b.period_start,
        b.period_end,
        b.fiscal_calendar,
    ):
        out.append("period_redefined")
    if b.restated_prior or (
        a.segment_taxonomy is not None
        and b.segment_taxonomy is not None
        and a.segment_taxonomy != b.segment_taxonomy
    ):
        out.append("restated")
    if (a.unit, a.scale) != (b.unit, b.scale):
        out.append("unit_changed")
    if a.currency != b.currency:
        out.append("currency_changed")
    if (a.basis, (a.basis_definition or "").strip().lower()) != (
        b.basis,
        (b.basis_definition or "").strip().lower(),
    ):
        out.append("basis_changed")
    if a.panel_method != b.panel_method:
        out.append("panel_method_changed")
    return tuple(out)


BREAK_TEXT: Mapping[str, str] = {
    "basis_changed": "the accounting basis or the adjusted definition changed",
    "currency_changed": "the currency changed",
    "panel_method_changed": "the panel or its methodology changed",
    "period_redefined": "the fiscal period was redefined",
    "unit_changed": "the unit or scale changed",
    "restated": "the company restated or recast the prior figures",
}


@dataclass(frozen=True)
class Pair:
    earlier_id: int
    later_id: int
    earlier_snapshot_id: int
    later_snapshot_id: int
    series: str
    metric: str
    segment: str | None
    fiscal_period: str
    earlier_known_at: dt.datetime
    later_known_at: dt.datetime
    comparable: bool
    reasons: tuple[BreakReason, ...]
    statistic: Statistic | None
    earlier_value: Decimal | None
    later_value: Decimal | None
    change: Decimal | None
    change_pct: Decimal | None
    pct_note: str | None
    elapsed_days: int
    analyst_count_change: int | None
    analyst_count_note: str | None
    direction: Literal["up", "down", "flat"] | None
    note: str | None
    formula_version: str = REVISION_FORMULA_VERSION


def revision(a: Value, b: Value) -> Pair:
    """The revision from `a` to `b`, or a break. A break carries no change, no percent, no
    direction: the point is that no number is offered."""
    reasons = break_reasons(a, b)
    days = max(0, (b.known_at - a.known_at).days)
    common = dict(
        earlier_id=a.id,
        later_id=b.id,
        earlier_snapshot_id=a.snapshot_id,
        later_snapshot_id=b.snapshot_id,
        series=b.series,
        metric=b.metric,
        segment=b.segment,
        fiscal_period=b.fiscal_period,
        earlier_known_at=a.known_at,
        later_known_at=b.known_at,
        elapsed_days=days,
    )
    if reasons:
        text = "; ".join(BREAK_TEXT[r] for r in reasons)
        return Pair(
            **common,  # type: ignore[arg-type]
            comparable=False,
            reasons=reasons,
            statistic=None,
            earlier_value=None,
            later_value=None,
            change=None,
            change_pct=None,
            pct_note=None,
            analyst_count_change=None,
            analyst_count_note=None,
            direction=None,
            note=f"Not comparable: {text}.",
        )
    sa, sb = statistic_of(a), statistic_of(b)
    if sa is None or sb is None or sa[0] != sb[0]:
        return Pair(
            **common,  # type: ignore[arg-type]
            comparable=True,
            reasons=(),
            statistic=None,
            earlier_value=None,
            later_value=None,
            change=None,
            change_pct=None,
            pct_note=None,
            analyst_count_change=None,
            analyst_count_note=None,
            direction=None,
            note="The two snapshots do not state the same statistic, so no revision is computed.",
        )
    before, after = sa[1], sb[1]
    change = after - before
    if before == 0:
        pct, pct_note = None, "percent is undefined because the earlier estimate is zero"
    else:
        pct, pct_note = change / abs(before) * HUNDRED, None
    if a.analyst_count is None or b.analyst_count is None:
        n_change: int | None = None
        n_note: str | None = "analyst count not stated in one or both snapshots"
    else:
        n_change, n_note = b.analyst_count - a.analyst_count, None
    return Pair(
        **common,  # type: ignore[arg-type]
        comparable=True,
        reasons=(),
        statistic=sa[0],
        earlier_value=before,
        later_value=after,
        change=change,
        change_pct=pct,
        pct_note=pct_note,
        analyst_count_change=n_change,
        analyst_count_note=n_note,
        direction="up" if change > 0 else "down" if change < 0 else "flat",
        note=None,
    )


def revision_pairs(values: Iterable[Value]) -> list[Pair]:
    """Consecutive snapshots of each series, oldest to newest. A later snapshot is always compared
    with its immediate predecessor only, so a break is visible where it happened. Lines with
    problems, and snapshots published at the same instant, are skipped."""
    groups: dict[tuple[str, str, str, str, str], list[Value]] = defaultdict(list)
    for v in values:
        if not problems(v):
            groups[group_key(v)].append(v)
    out: list[Pair] = []
    for rows in groups.values():
        rows.sort(key=lambda v: (v.known_at, v.id))
        for a, b in pairwise(rows):
            if a.snapshot_id == b.snapshot_id or a.known_at == b.known_at:
                continue
            out.append(revision(a, b))
    out.sort(key=lambda p: (p.later_known_at, p.later_id), reverse=True)
    return out


# ------------------------------------------------------------------------------- surprise
@dataclass(frozen=True)
class Actual:
    """A reported figure, normally from `disclosure_observation`."""

    observation_id: int | None
    metric: str
    period_end: dt.date
    value: Decimal
    unit: str
    scale: int = 0
    currency: str | None = None
    basis: str | None = None
    segment: str | None = None
    segment_taxonomy: str | None = None
    non_additive: bool = False
    released_at: dt.datetime | None = None
    evidence_id: int | None = None
    document_url: str | None = None


@dataclass(frozen=True)
class EventComparison:
    status: Literal["scored", "no_prior_estimate", "actual_not_loaded", "not_comparable"]
    metric: str
    segment: str | None
    fiscal_period: str | None
    period_end: dt.date | None
    release_at: dt.datetime | None
    estimate_value_id: int | None
    estimate_snapshot_known_at: dt.datetime | None
    statistic: Statistic | None
    estimate: Decimal | None
    actual: Decimal | None
    surprise: Decimal | None
    surprise_pct: Decimal | None
    pct_note: str | None
    analyst_count: int | None
    basis: str | None
    currency: str | None
    unit: str | None
    later_files_only: bool
    warnings: tuple[str, ...]
    actual_observation_id: int | None
    formula_version: str = SURPRISE_FORMULA_VERSION
    formula: str = SURPRISE_FORMULA


def _empty_event(
    status: str,
    metric: str,
    segment: str | None,
    *,
    period: str | None = None,
    period_end: dt.date | None = None,
    release_at: dt.datetime | None = None,
    later_only: bool = False,
    warnings: Sequence[str] = (),
    actual: Actual | None = None,
) -> EventComparison:
    return EventComparison(
        status=status,  # type: ignore[arg-type]
        metric=metric,
        segment=segment,
        fiscal_period=period,
        period_end=period_end,
        release_at=release_at,
        estimate_value_id=None,
        estimate_snapshot_known_at=None,
        statistic=None,
        estimate=None,
        actual=None if actual is None else scaled(actual.value, actual.scale),
        surprise=None,
        surprise_pct=None,
        pct_note=None,
        analyst_count=None,
        basis=actual.basis if actual else None,
        currency=actual.currency if actual else None,
        unit=actual.unit if actual else None,
        later_files_only=later_only,
        warnings=tuple(warnings),
        actual_observation_id=None if actual is None else actual.observation_id,
    )


def surprise(
    actual: Actual | None,
    candidates: Iterable[Value],
    *,
    metric: str,
    segment: str | None,
    release_at: dt.datetime | None,
    series: Series = "published_analyst_consensus",
) -> EventComparison:
    """Score `actual` against the latest `series` snapshot known strictly before `release_at`.

    `candidates` may contain snapshots from anywhere in time: the cut is made here, so a later
    archived or corrected file can never be used. Supersession is judged at the release instant."""
    if actual is None:
        return _empty_event(
            "actual_not_loaded",
            metric,
            segment,
            release_at=release_at,
            warnings=("The reported actual is not loaded, so no surprise is computed.",),
        )
    seg = (segment or "").lower()
    pool = [
        v
        for v in candidates
        if v.series == series
        and v.metric.lower() == metric.lower()
        and (v.segment or "").lower() == seg
        and v.period_end == actual.period_end
        and not problems(v)
    ]
    release = release_at or actual.released_at
    if release is None:
        return _empty_event(
            "not_comparable",
            metric,
            segment,
            period_end=actual.period_end,
            warnings=("The release time is unknown, so no estimate can be shown as pre-release.",),
            actual=actual,
        )
    before = visible(pool, release, strict=True)
    if not before:
        later = any(v.known_at >= release for v in pool)
        return _empty_event(
            "no_prior_estimate",
            metric,
            segment,
            period=pool[0].fiscal_period if pool else None,
            period_end=actual.period_end,
            release_at=release,
            later_only=later,
            warnings=(
                "Only a file published after the release exists; it cannot score this surprise."
                if later
                else "No snapshot known before the release exists for this metric and period.",
            ),
            actual=actual,
        )
    est = max(before, key=lambda v: (v.known_at, v.id))
    stat = statistic_of(est)
    warnings: list[str] = []
    if stat is None:
        return _empty_event(
            "not_comparable",
            metric,
            segment,
            period=est.fiscal_period,
            period_end=actual.period_end,
            release_at=release,
            warnings=("The snapshot states no usable value.",),
            actual=actual,
        )
    if est.currency != actual.currency:
        warnings.append("currency differs between estimate and actual")
    if est.unit != actual.unit:
        warnings.append("unit differs between estimate and actual")
    if actual.basis is not None and est.basis != actual.basis:
        warnings.append(f"basis differs: estimate is {est.basis}, actual is {actual.basis}")
    if actual.basis is None:
        warnings.append("the actual's basis is not stated, so basis equality is unverified")
    if est.analyst_count is None:
        warnings.append("analyst count not stated")
    if est.panel_method is None:
        warnings.append("panel methodology not stated")
    blocking = [
        w for w in warnings if w.split(" ")[0] in ("currency", "unit", "basis") and "differs" in w
    ]
    a_base = scaled(actual.value, actual.scale)
    e_base = base(est, stat[1])
    if blocking or a_base is None or e_base is None:
        out = _empty_event(
            "not_comparable",
            metric,
            segment,
            period=est.fiscal_period,
            period_end=actual.period_end,
            release_at=release,
            warnings=warnings,
            actual=actual,
        )
        return out
    diff = a_base - e_base
    if e_base == 0:
        pct, pct_note = None, "percent is undefined because the estimate is zero"
    else:
        pct, pct_note = diff / abs(e_base) * HUNDRED, None
    return EventComparison(
        status="scored",
        metric=metric,
        segment=segment,
        fiscal_period=est.fiscal_period,
        period_end=actual.period_end,
        release_at=release,
        estimate_value_id=est.id,
        estimate_snapshot_known_at=est.known_at,
        statistic=stat[0],
        estimate=e_base,
        actual=a_base,
        surprise=diff,
        surprise_pct=pct,
        pct_note=pct_note,
        analyst_count=est.analyst_count,
        basis=est.basis,
        currency=est.currency,
        unit=est.unit,
        later_files_only=False,
        warnings=tuple(warnings),
        actual_observation_id=actual.observation_id,
    )


# -------------------------------------------------------------------------------- segments
@dataclass(frozen=True)
class SegmentLine:
    segment: str
    reported_segment: str | None
    estimate_value_id: int | None
    actual_observation_id: int | None
    estimate: Decimal | None
    actual: Decimal | None
    delta: Decimal | None
    delta_pct: Decimal | None
    status: Literal[
        "scored", "estimate_missing", "actual_missing", "taxonomy_break", "not_comparable"
    ]
    non_additive: bool
    flags: tuple[str, ...]
    estimate_evidence_id: int | None
    actual_evidence_id: int | None


@dataclass(frozen=True)
class SegmentBridge:
    metric: str
    fiscal_period: str | None
    period_end: dt.date
    lines: tuple[SegmentLine, ...]
    additive_estimate_total: Decimal | None
    additive_actual_total: Decimal | None
    unmapped_estimates: tuple[str, ...]
    notes: tuple[str, ...]
    inputs: tuple[str, ...]


def segment_bridge(
    estimates: Iterable[Value],
    actuals: Iterable[Actual],
    *,
    metric: str,
    period_end: dt.date,
    segment_map: Mapping[str, str] | None = None,
) -> SegmentBridge:
    """Segment actual vs consensus for one fiscal period. `estimates` should already be the
    pre-release set. The additive totals count only additive segments that BOTH have an estimate
    and an actual, so a missing estimate never makes a total look like a miss."""
    mapping = {k.lower(): v.lower() for k, v in (segment_map or {}).items()}
    est = {
        (v.segment or "").lower(): v
        for v in sorted(estimates, key=lambda v: (v.known_at, v.id))
        if v.metric.lower() == metric.lower()
        and v.segment
        and v.period_end == period_end
        and not problems(v)
    }
    act = {
        a.segment.lower(): a
        for a in actuals
        if a.segment and a.metric.lower() == metric.lower() and a.period_end == period_end
    }
    lines: list[SegmentLine] = []
    used: set[str] = set()
    inputs: list[str] = []
    for key, a in sorted(act.items()):
        e = next((v for k, v in est.items() if mapping.get(k, k) == key), None)
        flags: list[str] = []
        if a.non_additive:
            flags.append("elimination or non-additive line")
        if e is None:
            lines.append(
                SegmentLine(
                    a.segment or key,
                    a.segment,
                    None,
                    a.observation_id,
                    None,
                    scaled(a.value, a.scale),
                    None,
                    None,
                    "estimate_missing",
                    a.non_additive,
                    tuple(flags),
                    None,
                    a.evidence_id,
                )
            )
            continue
        used.add((e.segment or "").lower())
        stat = statistic_of(e)
        if e.non_additive:
            flags.append("elimination or non-additive line")
        a_base = scaled(a.value, a.scale)
        e_base = base(e, stat[1]) if stat else None
        status: str = "scored"
        if (
            e.segment_taxonomy is not None
            and a.segment_taxonomy is not None
            and e.segment_taxonomy != a.segment_taxonomy
        ):
            status = "taxonomy_break"
            flags.append(
                f"segment definitions differ ({e.segment_taxonomy} vs {a.segment_taxonomy}): recast"
            )
        elif e.restated_prior:
            flags.append("the publisher says the prior figures were recast")
        if status == "scored" and (e.currency != a.currency or e.unit != a.unit):
            status = "not_comparable"
            flags.append("currency or unit differs")
        if status == "scored" and e_base is None:
            status = "not_comparable"
        delta = pct = None
        if status == "scored" and e_base is not None and a_base is not None:
            delta = a_base - e_base
            pct = None if e_base == 0 else delta / abs(e_base) * HUNDRED
        inputs.append(f"estimate:{e.id}")
        if a.observation_id is not None:
            inputs.append(f"actual:{a.observation_id}")
        lines.append(
            SegmentLine(
                a.segment or key,
                a.segment,
                e.id,
                a.observation_id,
                e_base,
                a_base,
                delta,
                pct,
                status,  # type: ignore[arg-type]
                a.non_additive or e.non_additive,
                tuple(flags),
                e.evidence_id,
                a.evidence_id,
            )
        )
    unmapped = tuple(sorted(v.segment or "" for k, v in est.items() if k not in used))
    scored = [ln for ln in lines if ln.status == "scored" and not ln.non_additive]
    est_total = (
        sum((ln.estimate for ln in scored if ln.estimate is not None), ZERO) if scored else None
    )
    act_total = sum((ln.actual for ln in scored if ln.actual is not None), ZERO) if scored else None
    notes: list[str] = []
    missing = [ln.segment for ln in lines if ln.status == "estimate_missing"]
    if missing:
        notes.append("No segment estimate was published for: " + ", ".join(missing) + ".")
    if unmapped:
        notes.append("Estimates with no reported segment this period: " + ", ".join(unmapped) + ".")
    if any(ln.non_additive for ln in lines):
        notes.append("Elimination and non-additive lines are shown but excluded from the totals.")
    if scored:
        notes.append(f"The totals add only the {len(scored)} additive segment(s) scored above.")
    first = next(iter(est.values()), None)
    return SegmentBridge(
        metric=metric,
        fiscal_period=first.fiscal_period if first else None,
        period_end=period_end,
        lines=tuple(lines),
        additive_estimate_total=est_total,
        additive_actual_total=act_total,
        unmapped_estimates=unmapped,
        notes=tuple(notes),
        inputs=tuple(inputs),
    )


# ------------------------------------------------------------------------------ named / gaps
@dataclass(frozen=True)
class CoverageGap:
    kind: str
    state: str
    detail: str


def named_estimates(
    values: Iterable[Value], *, source_gaps: Sequence[str] = ()
) -> tuple[list[Value], CoverageGap | None]:
    """Verified named observations, or an explicit gap. Nothing is derived from a consensus."""
    named = [v for v in values if v.series == "named_individual_estimate" and not problems(v)]
    if named:
        return sorted(named, key=lambda v: (v.known_at, v.id), reverse=True), None
    why = (
        " Registry: " + "; ".join(source_gaps) + "."
        if source_gaps
        else " No accessible source has been registered for named broker estimates."
    )
    return [], CoverageGap(
        "named_estimates",
        "not_covered",
        "No accessible source returned a named analyst and broker with metric, period and "
        "publication time." + why + " Broker-level values are never derived from a consensus "
        "mean or range.",
    )


def lane_gaps(
    lane: Mapping[Series, Sequence[Value]], *, source_gaps: Sequence[str] = ()
) -> list[CoverageGap]:
    """One explicit statement for every lane that holds nothing at the cutoff."""
    out: list[CoverageGap] = []
    why = {
        "published_analyst_consensus": "No published consensus file was known at this date.",
        "company_guidance": "No company guidance snapshot was known at this date.",
        "event_calendar_estimate": "No event-calendar estimate was known at this date.",
        "owner_scenario": "No owner scenario has been recorded.",
    }
    for s, text in why.items():
        if not lane.get(s):  # type: ignore[call-overload]
            extra = (
                f" {' '.join(source_gaps)}"
                if s == "published_analyst_consensus" and source_gaps
                else ""
            )
            out.append(CoverageGap(s, "not_covered", text + extra))
    _, gap = named_estimates(lane.get("named_individual_estimate", ()), source_gaps=source_gaps)
    if gap:
        out.append(gap)
    return out


# ------------------------------------------------------------------------------------ gate
@dataclass(frozen=True)
class RevisionGate:
    reviewed: int
    needed: int
    correct: int
    rate: Decimal | None
    bar: Decimal
    met: bool
    reason: str


def revision_gate(
    reviews: Sequence[Mapping[str, Any]],
    *,
    needed: int = REVISION_GATE_PAIRS,
    bar: Decimal = REVISION_GATE_BAR,
) -> RevisionGate:
    """Revision alerts and factor-like screens stay off until `needed` DISTINCT pairs are reviewed
    and the share labelled correct reaches `bar`. Rows are in decision order; the latest label for a
    pair wins, as with the other gates."""
    latest: dict[tuple[int, int], bool] = {}
    for r in reviews:
        latest[(int(r["earlier_value_id"]), int(r["later_value_id"]))] = bool(r["correct"])
    reviewed = len(latest)
    correct = sum(1 for ok in latest.values() if ok)
    rate = Decimal(correct) / reviewed if reviewed else None
    if reviewed < needed:
        reason = f"{reviewed} of {needed} reviewed pairs"
        met = False
    elif rate is not None and rate < bar:
        reason = f"{reviewed} pairs reviewed but only {rate:.0%} correct (bar {bar:.0%})"
        met = False
    else:
        reason = f"{reviewed} pairs reviewed, {correct} correct"
        met = True
    return RevisionGate(reviewed, needed, correct, rate, bar, met, reason)


@dataclass(frozen=True)
class ConsensusSourceRow:
    source_key: str
    publisher: str
    origin: str
    format: str
    access_state: str
    gap_reason: str | None
    url: str
    cadence: str | None = None
    tested_on: dt.date | None = None
    selected_metrics_only: bool = False
    selected_brokers_only: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


def source_gap_texts(sources: Iterable[ConsensusSourceRow]) -> list[str]:
    """A missing file or an incompatible definition is a coverage gap, never a licensing block."""
    return [
        f"{s.publisher} ({s.source_key}): {s.access_state.replace('_', ' ')}"
        + (f" - {s.gap_reason}" if s.gap_reason else "")
        for s in sources
        if s.access_state != "available"
    ]
