"""Reliability of stated confidence (autonomous engine plan, 3.4): when an autopilot kind says
it is 90% sure, is it right about 90% of the time?

Reviewed decisions of each kind are grouped by the confidence they were made with. Each group
shows the average confidence against the share the owner or the audit judged right. The expected
calibration error (ECE) is the average gap, weighted by group size; a kind that is sure and wrong
has a high one. Only decisions with a confidence and a review count (a rule has no confidence).
"""

import datetime as dt
from dataclasses import dataclass
from itertools import pairwise

import asyncpg
from pydantic import BaseModel

EDGES = (0.0, 0.5, 0.7, 0.8, 0.9, 0.95, 1.0001)
MIN_REVIEWED = 20  # fewer reviewed decisions than this and the kind is shown without a verdict
WINDOW_DAYS = 180
OVERCONFIDENT = 0.1  # average confidence above the share right by this much


class Bin(BaseModel):
    low: float
    high: float
    n: int
    mean_confidence: float
    share_right: float


class KindCalibration(BaseModel):
    kind: str
    reviewed: int
    ece: float | None
    verdict: str
    bins: list[Bin]


@dataclass(frozen=True)
class Point:
    confidence: float
    right: bool


def bins_of(points: list[Point]) -> list[Bin]:
    out: list[Bin] = []
    for low, high in pairwise(EDGES):
        inside = [p for p in points if low <= p.confidence < high]
        if not inside:
            continue
        out.append(
            Bin(
                low=low,
                high=min(high, 1.0),
                n=len(inside),
                mean_confidence=round(sum(p.confidence for p in inside) / len(inside), 4),
                share_right=round(sum(p.right for p in inside) / len(inside), 4),
            )
        )
    return out


def ece(bins: list[Bin]) -> float | None:
    total = sum(b.n for b in bins)
    if not total:
        return None
    return round(sum(b.n * abs(b.mean_confidence - b.share_right) for b in bins) / total, 4)


def verdict(bins: list[Bin], reviewed: int) -> str:
    if reviewed < MIN_REVIEWED:
        return f"Too few reviewed decisions yet ({reviewed} of {MIN_REVIEWED})."
    total = sum(b.n for b in bins)
    mean_conf = sum(b.n * b.mean_confidence for b in bins) / total
    right = sum(b.n * b.share_right for b in bins) / total
    if mean_conf - right >= OVERCONFIDENT:
        return f"Overconfident: {mean_conf:.0%} sure on average, {right:.0%} right."
    if right - mean_conf >= OVERCONFIDENT:
        return f"Underconfident: {mean_conf:.0%} sure on average, {right:.0%} right."
    return f"Well calibrated: {mean_conf:.0%} sure on average, {right:.0%} right."


async def report(pool: asyncpg.Pool, now: dt.datetime) -> list[KindCalibration]:
    rows = await pool.fetch(
        "select kind, confidence, review from public.auto_decision where confidence is not null"
        " and review is not null and status in ('applied', 'shadow', 'reverted')"
        " and decided_at >= $1 order by kind",
        now - dt.timedelta(days=WINDOW_DAYS),
    )
    by_kind: dict[str, list[Point]] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(Point(float(r["confidence"]), r["review"] == "ok"))
    out = []
    for kind, points in sorted(by_kind.items()):
        bins = bins_of(points)
        out.append(
            KindCalibration(
                kind=kind,
                reviewed=len(points),
                ece=ece(bins),
                verdict=verdict(bins, len(points)),
                bins=bins,
            )
        )
    return out
