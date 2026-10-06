"""The price and exchange-rate series that belong to a holding's currency and input exposures.

A `commodity_input` exposure links to the commodity series the macro collector stores
(`commodity_series.py`); a `revenue_currency` exposure links to the stored daily fixings
(`fx_rate`). An exposure with no series is listed as unlinked, never given a stand-in.
"""

from typing import Any

import asyncpg

from steward.config.holding_profiles import Exposure
from steward.macro.commodity_series import COMMODITY_FRED, series_keys_for

YEARS = 3
MAX_POINTS = 48
LABELS = {key: (label, unit) for key, label, _id, unit, _words in COMMODITY_FRED}
FX_SOURCES = ("ecb", "cnb")  # ECB first; ČNB is the only source of EUR itself


def thinned(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """At most MAX_POINTS evenly spread points, always keeping the newest, so a daily and a
    monthly series cover the same window."""
    if len(points) <= MAX_POINTS:
        return points
    step = -(-len(points) // MAX_POINTS)
    return points[::-1][::step][::-1]


async def commodity_points(pool: asyncpg.Pool, key: str) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """select * from (
             select distinct on (period) period, value, source_url
             from public.macro_observation
             where source = 'fred' and series_id = $1 and value is not null
               and period >= current_date - make_interval(years => $2::int)
             order by period desc, known_at desc, revision desc
           ) t order by period""",
        key,
        YEARS,
    )
    return thinned(
        [
            {"date": r["period"], "value": float(r["value"]), "source_url": r["source_url"]}
            for r in rows
        ]
    )


async def currency_points(
    pool: asyncpg.Pool, code: str
) -> tuple[str, str, list[dict[str, Any]]] | None:
    """(source, home currency, points) of the stored fixings of one currency, from the first
    source in FX_SOURCES that has any, so the series never flips between sources."""
    for source in FX_SOURCES:
        rows = await pool.fetch(
            """select home_currency, rate_date, rate from public.fx_rate
               where currency = $1 and source = $2
                 and rate_date >= current_date - make_interval(years => $3::int)
               order by rate_date""",
            code,
            source,
            YEARS,
        )
        if rows:
            points = [
                {"date": r["rate_date"], "value": float(r["rate"]), "source_url": None}
                for r in rows
            ]
            return source, str(rows[0]["home_currency"]), thinned(points)
    return None


async def linked_series(
    pool: asyncpg.Pool, exposures: list[Exposure]
) -> tuple[list[dict[str, Any]], list[str]]:
    """(series with the exposure that asked for them, names of exposures with no series)."""
    out: list[dict[str, Any]] = []
    unlinked: list[str] = []
    for e in exposures:
        found = 0
        if e.kind == "commodity_input":
            for key in series_keys_for(e.name):
                points = await commodity_points(pool, key)
                if points:
                    label, unit = LABELS[key]
                    out.append(
                        {
                            "exposure_kind": e.kind,
                            "exposure_name": e.name,
                            "series": f"fred:{key}",
                            "label": label,
                            "unit": unit,
                            "currency": "USD",
                            "source_url": points[-1]["source_url"],
                            "points": points,
                        }
                    )
                    found += 1
        elif e.kind == "revenue_currency" and len(e.name) == 3:
            fx = await currency_points(pool, e.name.upper())
            if fx:
                source, home, points = fx
                out.append(
                    {
                        "exposure_kind": e.kind,
                        "exposure_name": e.name,
                        "series": f"fx:{source}:{e.name.upper()}",
                        "label": f"{e.name.upper()} / {home} ({source.upper()} fixing)",
                        "unit": home,
                        "currency": home,
                        "source_url": None,
                        "points": points,
                    }
                )
                found += 1
        if not found and e.kind in ("commodity_input", "revenue_currency"):
            unlinked.append(f"{e.kind}:{e.name}")
    return out, unlinked
