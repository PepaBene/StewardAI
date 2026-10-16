"""The macro dashboard: one card per stored series, read from `macro_series` (the current value
of every period; revisions are the history view's concern, from `macro_observation`).

Each card carries what a reader scans for: the latest value and when it is for, the change on
the previous reading and on a year earlier, where the latest value sits in its one-, five- and
ten-year range, and a five-year line thinned to one point per month. Every number comes from the
stored rows; a window with too little history to say anything is left out rather than guessed.
"""

import datetime as dt
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

import asyncpg
from pydantic import BaseModel

HISTORY_YEARS = 10
SPARK_YEARS = 5
SPARK_MAX_POINTS = 72
# A window counts only with this share of its span covered, so two points from last month are
# not called a ten-year range.
RANGE_MIN_COVERAGE = 0.6


class RangeStat(BaseModel):
    years: int
    low: Decimal
    high: Decimal
    # Share of the window's readings at or below the latest one (0: the lowest, 1: the highest).
    percentile: float
    readings: int


class SparkPoint(BaseModel):
    period: dt.date
    value: Decimal


class MacroCard(BaseModel):
    series_id: str
    source: str
    label: str
    unit: str
    geography: str
    latest_period: dt.date
    latest_value: Decimal
    previous_period: dt.date | None
    previous_value: Decimal | None
    year_ago_period: dt.date | None
    year_ago_value: Decimal | None
    ranges: list[RangeStat]
    spark: list[SparkPoint]
    source_url: str | None
    fetched_at: dt.datetime | None


def geography_of(series_id: str) -> str:
    """Where a series is about, from its key: `gdp:DE` -> DE, `us_10y` -> US, a commodity -> WLD."""
    if ":" in series_id:
        return series_id.split(":", 1)[1]
    head = series_id.split("_", 1)[0]
    if head == "commodity":
        return "WLD"
    if series_id in ("eur_usd", "usd_jpy", "usd_broad_index"):
        return "FX"
    return {
        "us": "US",
        "fed": "US",
        "jp": "JP",
        "euro": "EA",
        "ea": "EA",
        "ecb": "EA",
        "cnb": "CZ",
        "czeonia": "CZ",
    }.get(head, "OTHER")


def _year_before(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def _range(points: list[tuple[dt.date, Decimal]], years: int) -> RangeStat | None:
    latest_day, latest = points[-1]
    start = _year_before(latest_day, years)
    window = [v for d, v in points if d > start]
    if len(window) < 3 or (latest_day - points[0][0]).days < RANGE_MIN_COVERAGE * 365 * years:
        return None
    at_or_below = sum(1 for v in window if v <= latest)
    return RangeStat(
        years=years,
        low=min(window),
        high=max(window),
        percentile=round(at_or_below / len(window), 3),
        readings=len(window),
    )


def _spark(points: list[tuple[dt.date, Decimal]]) -> list[SparkPoint]:
    """The last `SPARK_YEARS`, one point per month (the month's last reading) when denser."""
    start = _year_before(points[-1][0], SPARK_YEARS)
    window = [(d, v) for d, v in points if d > start]
    if len(window) > SPARK_MAX_POINTS:
        monthly: dict[tuple[int, int], tuple[dt.date, Decimal]] = {}
        for d, v in window:
            monthly[(d.year, d.month)] = (d, v)
        window = list(monthly.values())
        if window[-1][0] != points[-1][0]:
            window.append(points[-1])
    return [SparkPoint(period=d, value=v) for d, v in window]


def build_cards(rows: Iterable[Mapping[str, Any]]) -> list[MacroCard]:
    """Rows of (series_id, source, period, value, label, unit, url, fetched_at), any order."""
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        if row["value"] is not None:
            grouped.setdefault((row["series_id"], row["source"]), []).append(row)
    cards: list[MacroCard] = []
    for (series_id, source), items in grouped.items():
        items.sort(key=lambda r: r["period"])
        points = [(r["period"], Decimal(r["value"])) for r in items]
        last = items[-1]
        latest_day, latest = points[-1]
        previous = points[-2] if len(points) > 1 else None
        # The reading closest to a year before the latest, within six weeks of it.
        target = _year_before(latest_day, 1)
        near = min(points[:-1], key=lambda p: abs((p[0] - target).days), default=None)
        year_ago = near if near is not None and abs((near[0] - target).days) <= 45 else None
        cards.append(
            MacroCard(
                series_id=series_id,
                source=source,
                label=last["label"] or series_id,
                unit=last["unit"] or "",
                geography=geography_of(series_id),
                latest_period=latest_day,
                latest_value=latest,
                previous_period=previous[0] if previous else None,
                previous_value=previous[1] if previous else None,
                year_ago_period=year_ago[0] if year_ago else None,
                year_ago_value=year_ago[1] if year_ago else None,
                ranges=[r for y in (1, 5, 10) if (r := _range(points, y)) is not None],
                spark=_spark(points),
                source_url=last["url"],
                fetched_at=last["fetched_at"],
            )
        )
    return sorted(cards, key=lambda c: c.series_id)


async def macro_dashboard(pool: asyncpg.Pool, today: dt.date) -> list[MacroCard]:
    rows = await pool.fetch(
        "select series_id, source, period, value, label, unit, url, fetched_at"
        " from public.macro_series where period > $1 and value is not null",
        _year_before(today, HISTORY_YEARS) - dt.timedelta(days=40),
    )
    return build_cards(rows)
