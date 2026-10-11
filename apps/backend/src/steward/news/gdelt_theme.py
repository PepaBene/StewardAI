"""GDELT tone and attention for the portfolio watch themes (N6.8).

GDELT's DOC 2.0 API (free, no key) returns, for a keyword query, a timeline of the average tone
of matching coverage worldwide (`timelinetone`: negative below zero) and of its share of all
monitored coverage (`timelinevol`, "volume intensity"). Both are averaged per UTC day and stored
on the theme's rows in `news_theme_day` as context next to the counts from the wire; neither is
used for ranking or alerts.

The API asks for one request every five seconds, so the job spaces its calls, and fetches a
theme at most once a day.
"""

import asyncio
import datetime as dt
import statistics
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import quote

import asyncpg

API = "https://api.gdeltproject.org/api/v2/doc/doc"
SPACING_SECONDS = 5.5
TIMESPAN = "7d"

GetJson = Callable[[str], Awaitable[dict[str, Any] | None]]


def query_for(keywords: Sequence[str]) -> str:
    """`("a b" OR c)`: multi-word keywords quoted, at most eight of them."""
    terms = [f'"{k}"' if " " in k else k for k in keywords[:8] if k.strip()]
    return terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"


def url_for(keywords: Sequence[str], mode: str) -> str:
    return f"{API}?query={quote(query_for(keywords))}&mode={mode}&format=json&timespan={TIMESPAN}"


def parse_daily(doc: dict[str, Any] | None) -> dict[dt.date, float]:
    """The daily mean of a GDELT timeline (hourly or coarser points); empty when malformed."""
    by_day: dict[dt.date, list[float]] = defaultdict(list)
    try:
        for series in doc["timeline"] if doc else []:
            for point in series["data"]:
                day = dt.datetime.strptime(point["date"], "%Y%m%dT%H%M%SZ").date()
                by_day[day].append(float(point["value"]))
    except (KeyError, TypeError, ValueError):
        return {}
    return {d: round(statistics.fmean(v), 4) for d, v in by_day.items()}


async def theme_series(
    get: GetJson,
    keywords: Sequence[str],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[dict[dt.date, float], dict[dt.date, float]]:
    """(tone by day, attention by day) of one theme, with the API's request spacing."""
    tone = parse_daily(await get(url_for(keywords, "timelinetone")))
    await sleep(SPACING_SECONDS)
    attention = parse_daily(await get(url_for(keywords, "timelinevol")))
    await sleep(SPACING_SECONDS)
    return tone, attention


async def stale_themes(pool: asyncpg.Pool, ids: Sequence[str], today: dt.date) -> list[str]:
    """Themes whose tone was not stored for today or yesterday, oldest data first."""
    rows = await pool.fetch(
        "select theme, max(day) filter (where tone is not null) as last"
        " from public.news_theme_day where theme = any($1::text[]) group by theme",
        list(ids),
    )
    last = {r["theme"]: r["last"] for r in rows}
    fresh = today - dt.timedelta(days=1)
    return sorted(
        (i for i in ids if last.get(i) is None or last[i] < fresh),
        key=lambda i: (last.get(i) is not None, last.get(i) or dt.date.min),
    )


async def store_series(
    pool: asyncpg.Pool,
    theme: str,
    tone: dict[dt.date, float],
    attention: dict[dt.date, float],
) -> int:
    """Write the series onto the theme's rows (region ''), one row per day; rows written."""
    written = 0
    for day in sorted(set(tone) | set(attention)):
        await pool.execute(
            "insert into public.news_theme_day (theme, day, region, tone, attention)"
            " values ($1, $2, '', $3, $4) on conflict (theme, day, region) do update set"
            " tone = excluded.tone, attention = excluded.attention",
            theme,
            day,
            tone.get(day),
            attention.get(day),
        )
        written += 1
    return written
