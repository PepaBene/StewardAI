"""The expected-gap calendar: days a source has nothing to publish and a market is closed.

- ČNB fixes rates on Czech business days: no fixing on weekends and Czech public holidays.
- The ECB publishes reference rates on TARGET business days: no rates on weekends, New Year,
  Good Friday, Easter Monday, 1 May and 25-26 December (a published rule set, so it is code).
- An exchange session is, in order of authority: a confirmed `exchange_session` row (open,
  half-day with its own open/close, or closed: an ad hoc closure needs no public holiday and a
  national holiday on which the exchange trades needs no closure), else a closure in the Jobs
  config's calendar, else any weekday. Public holidays are not exchange calendars: Nager.Date
  only seeds suggestions.

So an FX rate missing on a holiday is not stale, a sync with nothing to collect on an
exchange holiday is not a missed run, a return window is judged against the sessions that
existed, and the digest can mark market holidays.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

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


@dataclass(frozen=True)
class SessionRow:
    """A confirmed `exchange_session` row."""

    mic: str
    session_date: dt.date
    state: str  # open | half_day | closed
    open_at: dt.datetime | None = None
    close_at: dt.datetime | None = None
    source: str = ""
    reason: str | None = None

    @property
    def trades(self) -> bool:
        return self.state in ("open", "half_day")


SessionIndex = dict[tuple[str, dt.date], SessionRow]


async def load_sessions(
    pool: asyncpg.Pool | asyncpg.Connection,
    mics: Iterable[str | None],
    start: dt.date,
    end: dt.date,
) -> SessionIndex:
    """Confirmed exchange sessions of `mics` in [start, end]. When several sources confirm the
    same day, the most recently known row wins (a later closure overrides an earlier open)."""
    wanted = sorted({m for m in mics if m})
    if not wanted:
        return {}
    rows = await pool.fetch(
        "select mic, session_date, state, open_at, close_at, source, reason"
        " from public.exchange_session where confirmed and mic = any($1::text[])"
        " and session_date between $2 and $3 order by known_at",
        wanted,
        start,
        end,
    )
    return {
        (r["mic"], r["session_date"]): SessionRow(
            r["mic"],
            r["session_date"],
            r["state"],
            r["open_at"],
            r["close_at"],
            r["source"],
            r["reason"],
        )
        for r in rows
    }


def exchange_open(
    settings: CalendarSettings,
    mic: str | None,
    day: dt.date,
    sessions: SessionIndex | None = None,
) -> bool:
    """Whether the exchange holds a session on `day`.

    A confirmed `exchange_session` row (in `sessions`) decides; without one, False on
    weekends and on the closures confirmed in the Jobs config. Callers that can reach the
    database pass `await load_sessions(...)`; the argument is optional so config-only callers
    keep working."""
    if mic is not None and sessions is not None:
        row = sessions.get((mic, day))
        if row is not None:
            return row.trades
    if day.weekday() >= 5:
        return False
    return mic is None or day not in settings.closures(mic)


async def exchange_open_on(
    pool: asyncpg.Pool | asyncpg.Connection,
    settings: CalendarSettings,
    mic: str | None,
    day: dt.date,
) -> bool:
    """`exchange_open` with the stored sessions of that day."""
    return exchange_open(settings, mic, day, await load_sessions(pool, [mic], day, day))


@dataclass(frozen=True)
class ExpectedSessions:
    days: list[dt.date]
    # confirmed_calendar: every weekday of the window has a confirmed row (weekend rows
    # do not count towards that);
    # partial_confirmed_calendar: some do; weekdays_minus_configured_closures: none does.
    basis: str
    confirmed_days: int

    @property
    def count(self) -> int:
        return len(self.days)


async def expected_sessions(
    pool: asyncpg.Pool | asyncpg.Connection,
    settings: CalendarSettings,
    mic: str | None,
    start: dt.date,
    end: dt.date,
) -> ExpectedSessions:
    """The sessions an exchange held in [start, end] (the denominator of return-window
    coverage). Never derived from the bars that exist: a window with 10 bars and 43 expected
    sessions has coverage 10/43."""
    if end < start:
        return ExpectedSessions([], "weekdays_minus_configured_closures", 0)
    index = await load_sessions(pool, [mic], start, end)
    days = [d for d in _days(start, end) if exchange_open(settings, mic, d, index)]
    rows = [d for (m, d) in index if m == mic and start <= d <= end]
    confirmed = len(rows)
    # Weekend rows (a Saturday session, a confirmed weekend closure) do not make a weekday
    # confirmed: the label compares the weekdays that have a row with the weekdays there are.
    confirmed_weekdays = sum(1 for d in rows if d.weekday() < 5)
    weekdays = sum(1 for d in _days(start, end) if d.weekday() < 5)
    if confirmed == 0:
        basis = "weekdays_minus_configured_closures"
    elif confirmed_weekdays >= weekdays:
        basis = "confirmed_calendar"
    else:
        basis = "partial_confirmed_calendar"
    return ExpectedSessions(days, basis, confirmed)


async def last_expected_session(
    pool: asyncpg.Pool | asyncpg.Connection,
    settings: CalendarSettings,
    mic: str | None,
    on_or_before: dt.date,
    *,
    lookback_days: int = 14,
) -> dt.date | None:
    """The latest session of the exchange on or before a day (price freshness is judged
    against it: a bar is stale when it is older than this)."""
    start = on_or_before - dt.timedelta(days=lookback_days)
    expected = await expected_sessions(pool, settings, mic, start, on_or_before)
    return expected.days[-1] if expected.days else None


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
