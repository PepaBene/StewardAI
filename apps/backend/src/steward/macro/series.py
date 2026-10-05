"""Macro series history for small-multiple charts (first recorded value against the latest).

Every row of `macro_observation` is a published vintage of one period. For each period this
returns the first value the app recorded and the latest value known by the cutoff, how many
vintages exist, and when each was known. A period with a single vintage shows no revision.

`first_basis` says what "first" means, so a chart never calls it the original release when it is
not: `provider_vintage` for ALFRED (the provider published its own real-time vintages), otherwise
`first_recorded` (the first value this app fetched, which can be later than the original release
for any source that does not expose past vintages, e.g. Eurostat before the archive started).
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel

SeriesKey = tuple[str, str, str]  # (source, series_id, geography)


class SeriesPoint(BaseModel):
    period: dt.date
    first_value: Decimal | None
    first_known_at: dt.datetime
    latest_value: Decimal | None
    latest_known_at: dt.datetime
    release_at: dt.datetime | None
    vintages: int
    revised: bool


class MacroSeriesHistory(BaseModel):
    source: str
    series_id: str
    geography: str
    label: str | None
    unit: str
    frequency: str | None
    seasonal_adjustment: str | None
    first_basis: Literal["provider_vintage", "first_recorded"]
    source_url: str | None
    points: list[SeriesPoint]
    revised_periods: int


def assemble_series(
    rows: Sequence[Mapping[str, Any]],
    *,
    periods: int,
    meta: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[MacroSeriesHistory]:
    """Group per-period rows (first and latest vintage joined) into series, newest `periods` kept.

    `rows` carry: source, series_id, geography, period, unit, frequency, seasonal_adjustment,
    source_url, release_at, first_value, first_known_at, latest_value, latest_known_at, vintages."""
    grouped: dict[SeriesKey, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["source"], row["series_id"], row["geography"]), []).append(row)
    out: list[MacroSeriesHistory] = []
    for (source, series_id, geography), items in grouped.items():
        items = sorted(items, key=lambda r: r["period"])[-periods:]
        points = [
            SeriesPoint(
                period=r["period"],
                first_value=r["first_value"],
                first_known_at=r["first_known_at"],
                latest_value=r["latest_value"],
                latest_known_at=r["latest_known_at"],
                release_at=r["release_at"],
                vintages=r["vintages"],
                # A different first and latest value is a revision; equal values across vintages
                # (a re-fetch that changed nothing) is not.
                revised=r["first_value"] is not None
                and r["latest_value"] is not None
                and r["first_value"] != r["latest_value"],
            )
            for r in items
        ]
        last = items[-1]
        info = meta.get((source, series_id), {})
        out.append(
            MacroSeriesHistory(
                source=source,
                series_id=series_id,
                geography=geography,
                label=info.get("label"),
                unit=last["unit"],
                frequency=last.get("frequency") or info.get("frequency"),
                seasonal_adjustment=last.get("seasonal_adjustment")
                or info.get("seasonal_adjustment"),
                first_basis="provider_vintage" if source == "alfred" else "first_recorded",
                source_url=last.get("source_url"),
                points=points,
                revised_periods=sum(1 for p in points if p.revised),
            )
        )
    # Series with the most recent data first, then by name, so the top of a list is current.
    out.sort(key=lambda s: (-(s.points[-1].period.toordinal()), s.source, s.series_id, s.geography))
    return out


async def macro_series_history(
    pool: asyncpg.Pool,
    *,
    geography: str | None,
    source: str | None,
    series_id: str | None,
    periods: int,
    limit_series: int,
    as_of: dt.datetime | None,
) -> list[MacroSeriesHistory]:
    """Per-period first and latest vintage known by `as_of` for up to `limit_series` series."""
    rows = await pool.fetch(
        """
        with scoped as (
          select * from public.macro_observation
          where value is not null
            and ($1::text is null or geography=$1)
            and ($2::text is null or source=$2)
            and ($3::text is null or series_id=$3)
            and ($4::timestamptz is null or known_at<=$4)
        ), keys as (
          select source,series_id,geography from scoped
          group by source,series_id,geography
          order by max(period) desc,source,series_id,geography
          limit $5
        ), first_v as (
          select distinct on (s.source,s.series_id,s.geography,s.period)
                 s.source,s.series_id,s.geography,s.period,
                 s.value first_value,s.known_at first_known_at
          from scoped s join keys k using(source,series_id,geography)
          order by s.source,s.series_id,s.geography,s.period,s.known_at,s.revision
        ), latest_v as (
          select distinct on (s.source,s.series_id,s.geography,s.period)
                 s.source,s.series_id,s.geography,s.period,s.value latest_value,
                 s.known_at latest_known_at,s.release_at,s.unit,s.frequency,
                 s.seasonal_adjustment,s.source_url
          from scoped s join keys k using(source,series_id,geography)
          order by s.source,s.series_id,s.geography,s.period,s.known_at desc,s.revision desc
        ), counts as (
          select s.source,s.series_id,s.geography,s.period,count(*)::int vintages
          from scoped s join keys k using(source,series_id,geography)
          group by s.source,s.series_id,s.geography,s.period
        )
        select l.source,l.series_id,l.geography,l.period,l.unit,l.frequency,l.seasonal_adjustment,
               l.source_url,l.release_at,f.first_value,f.first_known_at,l.latest_value,
               l.latest_known_at,c.vintages
        from latest_v l
        join first_v f using(source,series_id,geography,period)
        join counts c using(source,series_id,geography,period)
        order by l.source,l.series_id,l.geography,l.period
        """,
        geography,
        source,
        series_id,
        as_of,
        limit_series,
    )
    metadata = await pool.fetch(
        "select source,series_id,label,frequency,seasonal_adjustment "
        "from public.macro_series_metadata"
    )
    meta = {(m["source"], m["series_id"]): dict(m) for m in metadata}
    return assemble_series([dict(r) for r in rows], periods=periods, meta=meta)
