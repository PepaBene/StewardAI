"""Thesis scores: one transparent number per holding, computed from stored verdicts only.

The score is arithmetic on the KPI verdicts the board already shows; no model, no weights
the owner cannot see, nothing estimated:

    score = round_half_up(100 x mean over the holding's judged KPIs of s_k)

    s_k = 1.0  the KPI's current verdict is pass, judged under the theses config that was
               active at that time;
          0.5  it is unknown (no verified figure), or it is stale (judged under an older
               theses config than the one active at that time), whatever its status;
          0.0  it breaches, judged under the config active at that time.

A KPI with no verdict at all (never judged) is left out of the mean; a holding whose KPIs
are all left out has no score (null), never a made-up one. A KPI's current verdict is the
one the board shows: the latest verdict of its latest judged fiscal period.

State: `unjudged` without a score; otherwise by how many KPIs' current verdict is a breach
(the board's breach count, stale or not): `intact` none, `watch` one, `at_risk` two or more.

`score_history` recomputes the score as of the end of each UTC day on which a verdict for
one of the holding's KPIs was recorded, from the verdicts recorded up to then (the latest
`HISTORY_POINTS` such days, oldest first). The portfolio score is the mean of the held
holdings' scores weighted by their current position weight, over holdings that have a score
and a weight; its history recomputes each held holding's score as of each day any of them
had a verdict recorded (the latest `HISTORY_POINTS` such days) and applies today's weights,
so it shows how the theses moved, not how the positions did.
"""

import datetime as dt
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from steward.earnings.periods import sort_key

HISTORY_POINTS = 12

THESIS_SCORE_METHOD = (
    "score = round_half_up(100 x mean of s_k over the holding's KPIs that have a verdict); "
    "s_k = 1.0 pass, 0.5 unknown or stale (judged under an older theses config), "
    "0.0 breach; KPIs never judged are left out, and a holding with none judged has no "
    "score. History: the score as of the end of each UTC day a verdict was recorded. "
    "Portfolio score: mean of held holdings' scores weighted by current position weight, "
    "rounded half up. State: unjudged (no score), intact (no KPI breaching), watch (one), "
    "at_risk (two or more)."
)

VerdictStatus = Literal["pass", "breach", "unknown"]
ThesisState = Literal["intact", "watch", "at_risk", "unjudged"]

POINTS: dict[str, float] = {"pass": 1.0, "unknown": 0.5, "breach": 0.0}
STALE_POINTS = 0.5


@dataclass(frozen=True)
class Judgement:
    """A KPI's current verdict, reduced to what the score reads."""

    status: VerdictStatus
    stale: bool


def kpi_points(judgement: Judgement) -> float:
    """s_k for one judged KPI."""
    return STALE_POINTS if judgement.stale else POINTS[judgement.status]


def round_half_up(value: float) -> int:
    """0.5 rounds up (as JavaScript's Math.round does for these non-negative scores)."""
    return math.floor(value + 0.5)


def thesis_score(judgements: Iterable[Judgement | None]) -> int | None:
    """round_half_up(100 x mean s_k) over judged KPIs; None when no KPI has been judged."""
    points = [kpi_points(j) for j in judgements if j is not None]
    if not points:
        return None
    return round_half_up(100 * sum(points) / len(points))


def thesis_state(score: int | None, breaches: int) -> ThesisState:
    """unjudged without a score; else intact (no breach), watch (one), at_risk (two+)."""
    if score is None:
        return "unjudged"
    if breaches == 0:
        return "intact"
    return "watch" if breaches == 1 else "at_risk"


def weighted_score(scores: Iterable[tuple[int | None, float | None]]) -> int | None:
    """Weight-weighted mean of (score, weight) pairs that have both and weight > 0."""
    pairs = [(s, w) for s, w in scores if s is not None and w is not None and w > 0]
    total = sum(w for _, w in pairs)
    if not pairs or total <= 0:
        return None
    return round_half_up(sum(s * w for s, w in pairs) / total)


@dataclass(frozen=True)
class VerdictPoint:
    """One stored verdict row, as the history reads it."""

    id: int
    kpi_id: str
    fiscal_year: int | None
    fiscal_period: str | None
    status: VerdictStatus
    config_version_id: int
    created_at: dt.datetime


def active_version_at(
    versions: Sequence[tuple[int, dt.datetime]], cutoff: dt.datetime
) -> int | None:
    """The theses config active just before `cutoff`: the newest version saved before it (a
    save or a rollback always inserts a new active version, so this is exact)."""
    best: tuple[dt.datetime, int] | None = None
    for version_id, created_at in versions:
        if created_at < cutoff and (best is None or (created_at, version_id) > best):
            best = (created_at, version_id)
    return best[1] if best else None


def current_as_of(rows: Iterable[VerdictPoint], cutoff: dt.datetime) -> dict[str, VerdictPoint]:
    """Per KPI, the verdict the board would have shown just before `cutoff`: the latest row
    per fiscal period, then the latest judged period (an undated "no data" verdict only
    when no period has been judged), as `read.kpi_status` picks it."""
    latest: dict[tuple[str, int | None, str | None], VerdictPoint] = {}
    for row in rows:
        if row.created_at >= cutoff:
            continue
        key = (row.kpi_id, row.fiscal_year, row.fiscal_period)
        if key not in latest or row.id > latest[key].id:
            latest[key] = row
    out: dict[str, VerdictPoint] = {}
    for row in latest.values():
        chosen = out.get(row.kpi_id)
        if chosen is None or _rank(row) > _rank(chosen):
            out[row.kpi_id] = row
    return out


def _rank(row: VerdictPoint) -> tuple[int, tuple[int, int, int], int]:
    if row.fiscal_year is None or row.fiscal_period is None:
        return (0, (0, 0, 0), row.id)
    return (1, sort_key(row.fiscal_year, row.fiscal_period), row.id)


def day_end(day: dt.date) -> dt.datetime:
    """The start of the next UTC day: verdicts before it count for `day`."""
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tzinfo=dt.UTC)


def score_as_of(
    rows: Sequence[VerdictPoint],
    kpi_ids: Sequence[str],
    versions: Sequence[tuple[int, dt.datetime]],
    cutoff: dt.datetime,
) -> int | None:
    """The thesis score from the verdicts recorded before `cutoff`, with staleness judged
    against the theses config active then."""
    current = current_as_of((r for r in rows if r.kpi_id in kpi_ids), cutoff)
    active = active_version_at(versions, cutoff)
    return thesis_score(
        Judgement(v.status, v.config_version_id != active) if (v := current.get(k)) else None
        for k in kpi_ids
    )


def verdict_days(rows: Iterable[VerdictPoint], kpi_ids: Sequence[str]) -> list[dt.date]:
    """UTC days on which a verdict for one of these KPIs was recorded, oldest first."""
    return sorted({r.created_at.astimezone(dt.UTC).date() for r in rows if r.kpi_id in kpi_ids})


def score_history(
    rows: Sequence[VerdictPoint],
    kpi_ids: Sequence[str],
    versions: Sequence[tuple[int, dt.datetime]],
    days: Sequence[dt.date],
) -> list[tuple[dt.date, int]]:
    """(day, score) at the end of each of `days`, skipping days with no score."""
    out = []
    for day in days:
        score = score_as_of(rows, kpi_ids, versions, day_end(day))
        if score is not None:
            out.append((day, score))
    return out


def portfolio_history(
    scores_at: Mapping[str, Mapping[dt.date, int | None]],
    weights: Mapping[str, float | None],
    days: Sequence[dt.date],
) -> list[tuple[dt.date, int]]:
    """The weighted portfolio score at each day, from each holding's score as of that day
    and today's weights; days with no score are skipped."""
    out = []
    for day in days:
        score = weighted_score((s.get(day), weights.get(t)) for t, s in scores_at.items())
        if score is not None:
            out.append((day, score))
    return out
