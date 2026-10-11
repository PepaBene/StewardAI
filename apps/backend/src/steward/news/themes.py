"""N6.8: portfolio themes and regime flags.

The themes are the portfolio watch topics of the news sources config (`portfolio_topics`). Wire
items are tagged with them at ingest (`news_article.topics`); here the tags become daily counts
of wire items and of holding-linked stories per theme and region, and a day is flagged when the
theme's volume or reach changed regime against the previous two weeks:

- `spike`: at least `MIN_SPIKE` items and more than the baseline mean plus three standard
  deviations (a baseline of fewer than five days is not trusted);
- `spread`: two or more regions have at least two items on the day where none did on any day of
  the baseline.

GDELT tone and attention are not used: the GDELT feed is not wired into the theme counts yet.
"""

import datetime as dt
import statistics
from collections.abc import Mapping, Sequence

import asyncpg

BASELINE_DAYS = 14
MIN_SPIKE = 5
MIN_BASELINE_DAYS = 5
SPREAD_MIN_ITEMS = 2


def flag_for(
    today_by_region: Mapping[str, int],
    baseline_totals: Sequence[int],
    baseline_region_peaks: Sequence[int],
) -> str | None:
    """`spike`, `spread` or None for one theme-day."""
    total = sum(today_by_region.values())
    if len(baseline_totals) >= MIN_BASELINE_DAYS and total >= MIN_SPIKE:
        mean = statistics.fmean(baseline_totals)
        sd = statistics.pstdev(baseline_totals)
        if total > mean + 3 * max(sd, 1.0):
            return "spike"
    busy = [r for r, n in today_by_region.items() if r and n >= SPREAD_MIN_ITEMS]
    if (
        len(busy) >= 2
        and len(baseline_totals) >= MIN_BASELINE_DAYS
        and not any(p >= SPREAD_MIN_ITEMS for p in baseline_region_peaks)
    ):
        return "spread"
    return None


async def update_themes(pool: asyncpg.Pool, today: dt.date, days: int = 3) -> int:
    """Recompute the last `days` days of every theme's counts and flags; the rows written."""
    since = today - dt.timedelta(days=days + BASELINE_DAYS)
    wire = await pool.fetch(
        "select t as theme, (a.published_at at time zone 'UTC')::date as day,"
        " coalesce(a.region, '') as region, count(*) as n"
        " from public.news_article a, unnest(a.topics) t"
        " where a.published_at >= $1 and cardinality(a.topics) > 0 group by 1, 2, 3",
        dt.datetime.combine(since, dt.time.min, dt.UTC),
    )
    stories = await pool.fetch(
        "select t as theme, (c.first_seen at time zone 'UTC')::date as day,"
        " count(distinct c.id) as n"
        " from public.story_cluster c join public.news_item i on i.cluster_id = c.id"
        " join public.news_article a on a.news_item_id = i.id, unnest(a.topics) t"
        " where c.first_seen >= $1 and cardinality(a.topics) > 0 group by 1, 2",
        dt.datetime.combine(since, dt.time.min, dt.UTC),
    )
    cube: dict[str, dict[dt.date, dict[str, int]]] = {}
    for r in wire:
        cube.setdefault(r["theme"], {}).setdefault(r["day"], {})[r["region"]] = r["n"]
    story_counts = {(r["theme"], r["day"]): r["n"] for r in stories}
    written = 0
    for theme, by_day in sorted(cube.items()):
        for offset in range(days):
            day = today - dt.timedelta(days=offset)
            baseline_days = [day - dt.timedelta(days=k) for k in range(1, BASELINE_DAYS + 1)]
            # Days the theme had no item count as zero once the baseline has started.
            first = min(by_day)
            counted = [d for d in baseline_days if d >= first]
            totals = [sum(by_day.get(d, {}).values()) for d in counted]
            peaks = [max(by_day.get(d, {"": 0}).values(), default=0) for d in counted]
            today_regions = by_day.get(day, {})
            flag = flag_for(today_regions, totals, peaks) if today_regions else None
            regions = today_regions or {"": 0}
            for region, n in sorted(regions.items()):
                await pool.execute(
                    "insert into public.news_theme_day (theme, day, region, wire_items, stories,"
                    " flag) values ($1, $2, $3, $4, $5, $6)"
                    " on conflict (theme, day, region) do update set"
                    " wire_items = excluded.wire_items, stories = excluded.stories,"
                    " flag = excluded.flag",
                    theme,
                    day,
                    region,
                    n,
                    story_counts.get((theme, day), 0) if region == sorted(regions)[0] else 0,
                    flag,
                )
                written += 1
    return written
