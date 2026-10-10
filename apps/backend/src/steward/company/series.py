"""Period series from stored statement facts.

One key (a standardized line item, or one concept as filed) becomes annual values, quarterly
values (filed three-month figures, else the difference of year-to-date figures) and instants.
A value derived from other filed values says so and how; a restated figure keeps the original.
Nothing here guesses: a period that cannot be built from filed facts is simply absent.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import datetime as dt
import itertools
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

ANNUAL_DAYS = (330, 380)
QUARTER_DAYS = (80, 100)
MATCH_DAYS = 7  # an instant belongs to a duration column ending within this many days

View = Literal["restated", "original"]


@dataclass(frozen=True)
class Obs:
    """One stored fact, reduced to what the period builder reads."""

    key: str
    concept: str
    label: str | None
    statement: str
    start: dt.date | None
    end: dt.date
    value: Decimal
    unit: str
    currency: str | None
    known_at: dt.datetime
    accession: str | None
    form: str | None
    filing_url: str | None
    source_url: str | None
    reconciled: bool | None
    fact_id: int
    fiscal_period: str | None = None


@dataclass(frozen=True)
class Val:
    value: Decimal
    obs: Obs  # the filed fact it was read or derived from (the later one, for a difference)
    derived: str | None = None
    restated: bool = False
    original: Decimal | None = None


@dataclass
class Series:
    key: str
    annual: dict[dt.date, Val]
    quarter: dict[dt.date, Val]
    instant: dict[dt.date, Val]
    ttm: Val | None = None
    ttm_end: dt.date | None = None


def _within(days: int, bounds: tuple[int, int]) -> bool:
    return bounds[0] <= days <= bounds[1]


def _choose(obs: Sequence[Obs], view: View) -> dict[tuple[dt.date | None, dt.date], Val]:
    """One value per (start, end): the latest filing's, or the first one's in the original view.
    `restated` is set when the filings disagreed; `original` is then the first value."""
    grouped: dict[tuple[dt.date | None, dt.date], list[Obs]] = defaultdict(list)
    for o in obs:
        grouped[(o.start, o.end)].append(o)
    out: dict[tuple[dt.date | None, dt.date], Val] = {}
    for span, versions in grouped.items():
        versions.sort(key=lambda o: (o.known_at, o.fact_id))
        first, last = versions[0], versions[-1]
        picked = last if view == "restated" else first
        changed = any(v.value != first.value for v in versions)
        out[span] = Val(
            picked.value,
            picked,
            restated=changed,
            original=first.value if changed else None,
        )
    return out


def _months(days: int) -> int:
    return round(days / 30.44)


def build_series(key: str, obs: Sequence[Obs], view: View = "restated") -> Series:
    chosen = _choose(obs, view)
    series = Series(key=key, annual={}, quarter={}, instant={})
    cumulative: dict[dt.date, dict[dt.date, Val]] = defaultdict(dict)  # start -> end -> value
    for (start, end), val in chosen.items():
        if start is None:
            series.instant[end] = val
            continue
        days = (end - start).days
        if _within(days, ANNUAL_DAYS):
            series.annual[end] = val
            cumulative[start][end] = val
        elif _within(days, QUARTER_DAYS):
            series.quarter[end] = val
            cumulative[start][end] = val
        elif days > QUARTER_DAYS[1]:
            cumulative[start][end] = val
    # Quarters that were filed only as year-to-date figures: this period's cumulative minus the
    # previous one's, when the two end one quarter apart. A share count is a period average, not
    # a flow: two year-to-date averages do not subtract to a quarter's average, so none is built.
    counts = any(o.unit.lower() == "shares" for o in obs)
    for start, by_end in () if counts else cumulative.items():
        ends = sorted(by_end)
        for prev, cur in itertools.pairwise(ends):
            if cur in series.quarter or not _within((cur - prev).days, QUARTER_DAYS):
                continue
            a, b = by_end[prev], by_end[cur]
            label = (
                f"{_months((cur - start).days)}M YTD − {_months((prev - start).days)}M YTD"
                if not _within((cur - start).days, ANNUAL_DAYS)
                else f"FY − {_months((prev - start).days)}M YTD"
            )
            series.quarter[cur] = Val(
                b.value - a.value,
                b.obs,
                derived=label,
                restated=a.restated or b.restated,
                original=None,
            )
    series.ttm, series.ttm_end = _ttm(series, counts=counts)
    return series


def _ttm(series: Series, *, counts: bool = False) -> tuple[Val | None, dt.date | None]:
    last_q = max(series.quarter, default=None)
    last_a = max(series.annual, default=None)
    if last_a is not None and (last_q is None or last_a >= last_q - dt.timedelta(days=MATCH_DAYS)):
        return series.annual[last_a], last_a
    if last_q is None:
        return None, None
    if counts:
        # Four quarterly share counts are not added up: the latest quarter's is the TTM figure.
        newest = series.quarter[last_q]
        latest = Val(
            newest.value, newest.obs, derived="latest quarter, share counts are not summed"
        )
        return latest, last_q
    ends = [last_q]
    for _ in range(3):
        before = [e for e in series.quarter if _within((ends[-1] - e).days, QUARTER_DAYS)]
        if not before:
            return None, None
        ends.append(max(before))
    total = sum((series.quarter[e].value for e in ends), Decimal(0))
    newest = series.quarter[last_q]
    return (
        Val(total, newest.obs, derived="sum of the last four quarters"),
        last_q,
    )


def cluster_ends(ends: Sequence[dt.date]) -> list[dt.date]:
    """Period ends within MATCH_DAYS of each other are one column; the earliest names it."""
    out: list[dt.date] = []
    for end in sorted(set(ends)):
        if out and (end - out[-1]).days <= MATCH_DAYS:
            continue
        out.append(end)
    return out


def nearest_end(columns: Sequence[dt.date], day: dt.date) -> dt.date | None:
    best = min(columns, key=lambda c: abs((c - day).days), default=None)
    return best if best is not None and abs((best - day).days) <= MATCH_DAYS else None
