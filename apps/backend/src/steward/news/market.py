"""N6.7: what the market did after a story, and what stories like it did before.

Retrospective labelling only, computed in code from stored prices:
- the holding's move over 1, 3 and 5 trading days after the story's first report, against the
  last close before it, and the same move of the market benchmark, so the abnormal move is the
  difference;
- whether the holding's next report date falls within a week of the story (earnings in the window
  make the move unattributable);
- an event study over the owner's own history: per event type, region and best outlet tier, the
  median abnormal move, its quartiles and the number of stories behind it. Always shown with `n`
  and the spread. It describes what happened, it is not a prediction.
"""

import datetime as dt
import json
import statistics
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from steward.news.analysis import StoryMaterial

WINDOWS = (1, 3, 5)
LOOKBACK_DAYS = 10
LOOKAHEAD_DAYS = 14
EARNINGS_NEAR_DAYS = 7
MIN_STUDY_N = 5

Closes = Sequence[tuple[dt.date, Decimal]]

COUNTRY_BENCHMARK = {"US": "^spx", "DE": "^dax", "GB": "^ukx"}


class WindowMove(BaseModel):
    days: int
    stock_pct: float | None
    benchmark_pct: float | None
    abnormal_pct: float | None


class MarketContext(BaseModel):
    symbol: str | None
    benchmark: str | None
    base_date: dt.date | None
    windows: list[WindowMove]
    earnings_near: bool
    complete: bool
    notes: list[str] = Field(default_factory=list)


def window_moves(
    stock: Closes, bench: Closes, event_day: dt.date, windows: Sequence[int] = WINDOWS
) -> tuple[dt.date | None, list[WindowMove]]:
    """Moves over `windows` trading days after `event_day`, from the last close before it."""
    before = [(d, c) for d, c in stock if d < event_day]
    if not before:
        return None, [
            WindowMove(days=w, stock_pct=None, benchmark_pct=None, abnormal_pct=None)
            for w in windows
        ]
    base_date, base = before[-1]
    after = [(d, c) for d, c in stock if d >= event_day]
    bench_before = [(d, c) for d, c in bench if d <= base_date]
    bench_after = [(d, c) for d, c in bench if d >= event_day]
    out: list[WindowMove] = []
    for w in windows:
        stock_pct = bench_pct = abnormal = None
        if len(after) >= w and base > 0:
            stock_pct = float((after[w - 1][1] / base - 1) * 100)
            if bench_before and len(bench_after) >= w and bench_before[-1][1] > 0:
                bench_pct = float((bench_after[w - 1][1] / bench_before[-1][1] - 1) * 100)
                abnormal = stock_pct - bench_pct
        out.append(
            WindowMove(
                days=w,
                stock_pct=_round(stock_pct),
                benchmark_pct=_round(bench_pct),
                abnormal_pct=_round(abnormal),
            )
        )
    return base_date, out


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 2)


def pick_benchmark(country: str | None, configured: Sequence[str]) -> str | None:
    """The benchmark of the holding's country when configured, else the first configured one."""
    wanted = COUNTRY_BENCHMARK.get(country or "")
    if wanted and wanted in configured:
        return wanted
    return configured[0] if configured else None


async def load_closes(
    pool: asyncpg.Pool, symbol: str, since: dt.date, until: dt.date
) -> list[tuple[dt.date, Decimal]]:
    from steward.fundresearch.performance import load_prices

    return list(await load_prices(pool, symbol, since, until))


async def market_context(
    pool: asyncpg.Pool, story: StoryMaterial, configured_benchmarks: Sequence[str]
) -> MarketContext | None:
    """The story's market context, or None for a story with no holding or no price symbol."""
    if story.ticker is None:
        return None
    row = await pool.fetchrow(
        "select price_symbol, home_country, next_report_date from public.instrument"
        " where t212_ticker = $1",
        story.ticker,
    )
    if row is None or not row["price_symbol"]:
        return None
    event_day = (
        story.event_date
        if story.event_date and story.event_date <= story.first_seen.date()
        else story.first_seen.date()
    )
    since = event_day - dt.timedelta(days=LOOKBACK_DAYS)
    until = event_day + dt.timedelta(days=LOOKAHEAD_DAYS)
    stock = await load_closes(pool, row["price_symbol"], since, until)
    benchmark = pick_benchmark(row["home_country"], configured_benchmarks)
    bench = await load_closes(pool, benchmark, since, until) if benchmark else []
    base_date, moves = window_moves(stock, bench, event_day)
    notes: list[str] = []
    if base_date is None:
        notes.append("no close before the story in the stored prices")
    if benchmark is None or not bench:
        notes.append("no benchmark prices, so no abnormal move")
    nxt = row["next_report_date"]
    near = nxt is not None and abs((nxt - event_day).days) <= EARNINGS_NEAR_DAYS
    if near:
        notes.append("the next report date is within a week, so the move may be about earnings")
    return MarketContext(
        symbol=row["price_symbol"],
        benchmark=benchmark,
        base_date=base_date,
        windows=moves,
        earnings_near=near,
        complete=all(m.stock_pct is not None for m in moves),
        notes=notes,
    )


class StudyRow(BaseModel):
    event_type: str
    region: str
    tier: int
    n: int
    median_pct: float
    p25_pct: float
    p75_pct: float


def quartiles(values: Sequence[float]) -> tuple[float, float, float]:
    ordered = sorted(values)
    median = statistics.median(ordered)
    if len(ordered) < 4:
        return ordered[0], median, ordered[-1]
    q = statistics.quantiles(ordered, n=4, method="inclusive")
    return q[0], median, q[2]


def region_of(ticker: str | None) -> str:
    parts = (ticker or "").split("_")
    return parts[1] if len(parts) > 1 and len(parts[1]) == 2 else "??"


def study_rows(
    samples: Sequence[tuple[str, str, int, float]], min_n: int = MIN_STUDY_N
) -> list[StudyRow]:
    """Group (event_type, region, tier, 3-day abnormal move) samples; groups under `min_n` are
    left out because their median would say nothing."""
    groups: dict[tuple[str, str, int], list[float]] = {}
    for event_type, region, tier, move in samples:
        groups.setdefault((event_type, region, tier), []).append(move)
    rows: list[StudyRow] = []
    for (event_type, region, tier), values in sorted(groups.items()):
        if len(values) < min_n:
            continue
        p25, median, p75 = quartiles(values)
        rows.append(
            StudyRow(
                event_type=event_type,
                region=region,
                tier=tier,
                n=len(values),
                median_pct=round(median, 2),
                p25_pct=round(p25, 2),
                p75_pct=round(p75, 2),
            )
        )
    return rows


async def event_study(pool: asyncpg.Pool) -> list[StudyRow]:
    rows = await pool.fetch(
        "select distinct on (a.cluster_id) a.payload, c.event_type, c.ticker, c.best_tier"
        " from public.story_analysis a join public.story_cluster c on c.id = a.cluster_id"
        " where a.kind = 'market' and c.event_type is not null"
        " order by a.cluster_id, a.created_at desc, a.id desc"
    )
    samples: list[tuple[str, str, int, float]] = []
    for r in rows:
        payload: Any = r["payload"]
        doc = json.loads(payload) if isinstance(payload, str) else payload
        if doc.get("earnings_near"):
            continue  # a move that may be about earnings says nothing about the story type
        three = next((w for w in doc.get("windows", []) if w["days"] == 3), None)
        if three and three.get("abnormal_pct") is not None:
            samples.append(
                (r["event_type"], region_of(r["ticker"]), r["best_tier"], three["abnormal_pct"])
            )
    return study_rows(samples)
