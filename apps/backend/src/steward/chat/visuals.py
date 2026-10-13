"""Visual answers (autonomous engine plan, L7).

The model never draws and never supplies a number. It names a visual (`kind`); this module
fetches the data from the same stored records the screens use and returns a block the web app
renders with its own chart components. Every block carries its source, an as-of date, a text
summary and a table, so it works without the chart and every plotted value can be traced.

`check_block` is the guard: a block with a non-finite value, mismatched series, or no data is
replaced by an explicit "no data" block instead of a chart that looks complete and is not.
"""

import datetime as dt
import math
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel

from steward.autopilot import ledger

Kind = Literal["allocation", "engine_week"]
KINDS: tuple[str, ...] = ("allocation", "engine_week")
ChartType = Literal["bar", "donut", "none"]
MAX_POINTS = 30


class Block(BaseModel):
    kind: str
    chart: ChartType
    title: str
    unit: str
    labels: list[str]
    values: list[float]
    source: str
    as_of: str | None
    summary: str
    caption: str | None = None
    table: list[dict[str, Any]]


def empty(kind: str, title: str, why: str) -> Block:
    return Block(
        kind=kind,
        chart="none",
        title=title,
        unit="",
        labels=[],
        values=[],
        source="",
        as_of=None,
        summary=why,
        caption=why,
        table=[],
    )


def check_block(block: Block) -> Block:
    """The block itself, or a "no data" block when it could mislead."""
    if block.chart == "none":
        return block
    if not block.labels or len(block.labels) != len(block.values):
        return empty(block.kind, block.title, "The data for this chart was incomplete.")
    if any(not math.isfinite(v) for v in block.values):
        return empty(block.kind, block.title, "The data for this chart had invalid values.")
    if not block.as_of or not block.source:
        return empty(block.kind, block.title, "The data for this chart has no source or date.")
    if len(block.table) != len(block.labels):
        return empty(block.kind, block.title, "The chart and its table did not match.")
    return block


async def allocation(pool: asyncpg.Pool) -> Block:
    """Holding weights from the latest snapshot, largest first."""
    rows = await pool.fetch(
        "select ticker, weight, snapshot_date from public.position_snapshot"
        " where weight is not null and snapshot_date ="
        " (select max(snapshot_date) from public.position_snapshot) order by weight desc"
    )
    if not rows:
        return empty("allocation", "Allocation", "There is no portfolio snapshot yet.")
    top = rows[:MAX_POINTS]
    rest = sum(float(r["weight"]) for r in rows[MAX_POINTS:])
    labels = [r["ticker"] for r in top]
    values = [round(float(r["weight"]) * 100, 2) for r in top]
    if rest > 0:
        labels.append("Other")
        values.append(round(rest * 100, 2))
    day: dt.date = rows[0]["snapshot_date"]
    return check_block(
        Block(
            kind="allocation",
            chart="donut",
            title="Allocation by holding",
            unit="% of portfolio",
            labels=labels,
            values=values,
            source="position_snapshot",
            as_of=day.isoformat(),
            summary=f"{len(rows)} holdings; the largest is {labels[0]} at {values[0]}%.",
            table=[{"holding": k, "weight_pct": v} for k, v in zip(labels, values, strict=True)],
        )
    )


async def engine_week(pool: asyncpg.Pool, now: dt.datetime) -> Block:
    """What the engine decided in the last 7 days, and how much of it the owner overruled."""
    stats = await ledger.stats(pool, now - dt.timedelta(days=7))
    stats = [s for s in stats if s.decided or s.escalated]
    if not stats:
        return empty("engine_week", "The engine this week", "The engine decided nothing this week.")
    labels = [s.kind for s in stats]
    values = [float(s.decided) for s in stats]
    return check_block(
        Block(
            kind="engine_week",
            chart="bar",
            title="Automatic decisions in the last 7 days",
            unit="decisions",
            labels=labels,
            values=values,
            source="auto_decision",
            as_of=now.date().isoformat(),
            summary=(
                f"{int(sum(values))} decisions; {sum(s.reverted for s in stats)} undone by you, "
                f"{sum(s.escalated for s in stats)} waiting for you."
            ),
            table=[
                {
                    "kind": s.kind,
                    "decided": s.decided,
                    "applied": s.applied,
                    "shadow": s.shadow,
                    "undone": s.reverted,
                    "waiting": s.escalated,
                }
                for s in stats
            ],
        )
    )


async def build(pool: asyncpg.Pool, kind: str, now: dt.datetime) -> Block:
    if kind == "allocation":
        return await allocation(pool)
    if kind == "engine_week":
        return await engine_week(pool, now)
    raise ValueError(f"Unknown visual {kind!r}; choose one of {', '.join(KINDS)}")
