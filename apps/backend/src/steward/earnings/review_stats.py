"""Counts for the review page: what waits for the owner, what was decided, and how accurate
the extraction was when last measured. Every number is a count of stored rows or a figure
read from the committed eval baseline; nothing is estimated.

- pending: facts held by the validator or quarantined by reconciliation, now;
- blocking_verdict: those of them that a configured thesis KPI of the same holding reads
  (the fact's metric is the KPI's metric or one it is derived from, e.g. revenue for a
  gross-margin KPI, with the KPI's basis): a verdict waits on them;
- reviewed_count / reviewed_per_day: owner decisions (`fact_review`) in the window;
- marked_wrong: distinct facts the owner marked wrong (feedback) in the window;
- golden_accuracy: the earnings extraction component's score in evals/baseline.json (the
  live golden-set run the evals gate recorded), with the gate's target; null when no run is
  recorded.
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from steward.config.store import load_active
from steward.config.thesis import ThesesConfig
from steward.daily import DayCount, per_day, window_bounds, window_days
from steward.earnings.metrics import derivation
from steward.verify.evals_gate import BASELINE, COMPONENTS


class GoldenAccuracy(BaseModel):
    value: float = Field(description="Matched labelled figures / all labelled figures (0..1).")
    target: float = Field(description="The evals gate's target for the component (0..1).")
    evaluated_at: dt.datetime | None = Field(
        description="When the baseline file was last written by a passing live run."
    )
    model: str | None = Field(description="The extraction model the run measured.")


class ReviewStats(BaseModel):
    days: int
    pending: int = Field(description="Facts held or quarantined now, waiting for the owner.")
    blocking_verdict: int = Field(
        description="Pending facts whose metric feeds a configured thesis KPI of the same "
        "holding (the KPI's metric or a component it is derived from, same basis)."
    )
    reviewed_count: int = Field(description="Owner decisions (accept, correct, reject) in window.")
    reviewed_per_day: list[DayCount] = Field(description="Every UTC day, oldest first.")
    marked_wrong: int = Field(description="Distinct facts the owner marked wrong in the window.")
    golden_accuracy: GoldenAccuracy | None = Field(
        description="The last recorded live golden-set run of the earnings extraction; null "
        "when none is recorded."
    )


def golden_accuracy(path: Path = BASELINE) -> GoldenAccuracy | None:
    """The earnings component's recorded live score, or None when not recorded."""
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = (data.get("components") or {}).get("earnings") or {}
    if entry.get("score") is None:
        return None
    recorded = data.get("recorded_at")
    target = next(c.target for c in COMPONENTS if c.name == "earnings")
    return GoldenAccuracy(
        value=float(entry["score"]),
        target=target,
        evaluated_at=dt.datetime.fromisoformat(recorded) if recorded else None,
        model=entry.get("model"),
    )


async def review_stats(
    pool: asyncpg.Pool, days: int, now: dt.datetime, baseline: Path = BASELINE
) -> ReviewStats:
    window = window_days(now, days)
    start, end = window_bounds(window)
    pending = await pool.fetch(
        "select ticker, metric, basis from public.fact"
        " where status in ('unverified', 'quarantined')"
    )
    theses = await load_active(pool, "theses", ThesesConfig)
    feeds: set[tuple[str, str, str]] = set()
    for thesis in theses.body.theses if theses else []:
        for kpi in thesis.kpis:
            for metric in {kpi.metric, *derivation(kpi.metric).components}:
                feeds.add((thesis.ticker, metric, kpi.basis))
    reviewed = {
        r["day"]: r["n"]
        for r in await pool.fetch(
            "select (created_at at time zone 'UTC')::date as day, count(*) as n"
            " from public.fact_review where created_at >= $1 and created_at < $2 group by 1",
            start,
            end,
        )
    }
    wrong = await pool.fetchval(
        "select count(distinct target_id) from public.feedback where target_kind = 'fact'"
        " and rating = 'wrong' and created_at >= $1 and created_at < $2",
        start,
        end,
    )
    return ReviewStats(
        days=days,
        pending=len(pending),
        blocking_verdict=sum((r["ticker"], r["metric"], r["basis"]) in feeds for r in pending),
        reviewed_count=sum(reviewed.values()),
        reviewed_per_day=per_day(window, reviewed),
        marked_wrong=wrong or 0,
        golden_accuracy=golden_accuracy(baseline),
    )
