"""The expected-gap calendar: days a source has nothing to publish and a market is closed.

- ČNB fixes rates on Czech business days: no fixing on weekends and Czech public holidays.
- The ECB publishes reference rates on TARGET business days: no rates on weekends, New Year,
  Good Friday, Easter Monday, 1 May and 25-26 December (a published rule set, so it is code).
- An exchange is closed on weekends and on the closures in the Jobs config's calendar
  (public holidays are not exchange calendars; Nager.Date only seeds suggestions).

So an FX rate missing on a holiday is not stale, a sync with nothing to collect on an
exchange holiday is not a missed run, and the digest can mark market holidays.
"""

import datetime as dt
from collections.abc import Iterable

import asyncpg

from steward.config.jobs import CalendarSettings


def easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def target_holidays(year: int) -> set[dt.date]:
    sunday = easter(year)
    return {
        dt.date(year, 1, 1),
        sunday - dt.timedelta(days=2),
        sunday + dt.timedelta(days=1),
        dt.date(year, 5, 1),
        dt.date(year, 12, 25),
        dt.date(year, 12, 26),
    }


async def public_holidays(
    pool: asyncpg.Pool | asyncpg.Connection, country: str, start: dt.date, end: dt.date
) -> set[dt.date]:
    """National public holidays of `country` stored from Nager.Date."""
    rows = await pool.fetch(
        "select date from public.holiday where calendar = $1 and scope = 'national'"
        " and date between $2 and $3",
        country,
        start,
        end,
    )
    return {r["date"] for r in rows}


def _days(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    day = start
    while day <= end:
        yield day
        day += dt.timedelta(days=1)


async def fx_publication_days(
    pool: asyncpg.Pool | asyncpg.Connection, source: str, start: dt.date, end: dt.date
) -> list[dt.date]:
    """Days in [start, end] on which `source` ('cnb' or 'ecb') publishes rates."""
    if source == "cnb":
        closed = await public_holidays(pool, "CZ", start, end)
    else:
        closed = set().union(*(target_holidays(y) for y in range(start.year, end.year + 1)))
    return [d for d in _days(start, end) if d.weekday() < 5 and d not in closed]


async def fx_stale(
    pool: asyncpg.Pool | asyncpg.Connection,
    source: str,
    latest_rate_date: dt.date | None,
    as_of: dt.date,
) -> list[dt.date]:
    """Publication days after the newest stored rate, up to and including `as_of`: rates
    that should exist and don't. Empty means not stale (a holiday is never a gap)."""
    if latest_rate_date is None:
        return [as_of]
    if latest_rate_date >= as_of:
        return []
    return await fx_publication_days(pool, source, latest_rate_date + dt.timedelta(days=1), as_of)


def exchange_open(settings: CalendarSettings, mic: str | None, day: dt.date) -> bool:
    """False on weekends and on the configured closures of the exchange."""
    if day.weekday() >= 5:
        return False
    return mic is None or day not in settings.closures(mic)


async def closure_suggestions(
    pool: asyncpg.Pool | asyncpg.Connection,
    settings: CalendarSettings,
    mics: dict[str, str],
    start: dt.date,
    end: dt.date,
) -> list[tuple[str, dt.date, str]]:
    """Public holidays in each exchange's country (mics: MIC -> country) that are not yet
    confirmed closures: (mic, date, name) for the owner to confirm into the config."""
    suggestions = []
    for mic, country in sorted(mics.items()):
        confirmed = settings.closures(mic)
        rows = await pool.fetch(
            "select date, name from public.holiday where calendar = $1 and scope = 'national'"
            " and date between $2 and $3 order by date",
            country,
            start,
            end,
        )
        suggestions += [
            (mic, r["date"], r["name"])
            for r in rows
            if r["date"] not in confirmed and r["date"].weekday() < 5
        ]
    return suggestions
