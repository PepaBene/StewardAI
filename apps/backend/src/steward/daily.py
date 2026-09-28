"""UTC day windows for the activity counts the dashboards chart."""

import datetime as dt

from pydantic import BaseModel


class DayCount(BaseModel):
    date: dt.date
    count: int


def window_days(now: dt.datetime, days: int) -> list[dt.date]:
    """The last `days` UTC days up to and including `now`'s, oldest first."""
    today = now.astimezone(dt.UTC).date()
    return [today - dt.timedelta(days=n) for n in range(days - 1, -1, -1)]


def window_bounds(window: list[dt.date]) -> tuple[dt.datetime, dt.datetime]:
    """[start of the first day, start of the day after the last), in UTC."""
    start = dt.datetime.combine(window[0], dt.time(0), tzinfo=dt.UTC)
    end = dt.datetime.combine(window[-1] + dt.timedelta(days=1), dt.time(0), tzinfo=dt.UTC)
    return start, end


def per_day(window: list[dt.date], counts: dict[dt.date, int]) -> list[DayCount]:
    """Every day of the window with its count (0 when absent)."""
    return [DayCount(date=d, count=counts.get(d, 0)) for d in window]
