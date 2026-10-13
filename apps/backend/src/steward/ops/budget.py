"""The budget forecast (D5): will this month's AI spend pass the monthly cap?

The budget guard already degrades or pauses non-critical jobs at the cap. This warns days
earlier, once a month, with the jobs that spend the most, so the owner can raise the cap or
lower a job's share before work stops. It only looks; it changes no config.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import asyncpg

from steward.budget_guard import BUDGET_TZ, month_bounds, month_of

MIN_DAYS = 3  # a forecast from fewer days of spend is noise


@dataclass(frozen=True)
class Forecast:
    month: str
    spent: float
    projected: float
    cap: float
    days_elapsed: float
    days_in_month: int

    @property
    def over(self) -> bool:
        return self.projected > self.cap


def forecast(month: str, spent: Decimal | float, cap: float, now: dt.datetime) -> Forecast | None:
    """Spend so far extended in a straight line to the end of the month; None in the first
    days of the month, when a straight line says nothing."""
    start, end = month_bounds(month)
    days_in_month = (end - start).days
    elapsed = (now.astimezone(BUDGET_TZ) - start).total_seconds() / 86400
    if elapsed < MIN_DAYS:
        return None
    spent_f = float(spent)
    return Forecast(
        month=month,
        spent=spent_f,
        projected=spent_f / elapsed * days_in_month,
        cap=cap,
        days_elapsed=elapsed,
        days_in_month=days_in_month,
    )


async def top_spenders(pool: asyncpg.Pool, month: str, limit: int = 5) -> list[tuple[str, float]]:
    start, end = month_bounds(month)
    rows = await pool.fetch(
        "select j.job_type, sum(s.cost_usd) as cost from public.job_step s"
        " join public.job j on j.id = s.job_id where s.cost_usd is not null"
        " and s.started_at >= $1 and s.started_at < $2 group by j.job_type"
        " order by cost desc limit $3",
        start,
        end,
        limit,
    )
    return [(r["job_type"], float(r["cost"])) for r in rows]


def describe(f: Forecast, spenders: list[tuple[str, float]]) -> str:
    top = ", ".join(f"{name} ${cost:.2f}" for name, cost in spenders) or "no single job stands out"
    return (
        f"AI spend is ${f.spent:.2f} after {f.days_elapsed:.0f} of {f.days_in_month} days, on "
        f"course for ${f.projected:.2f} against the ${f.cap:.2f} cap. Biggest spenders: {top}. "
        "At the cap, non-critical jobs are paused or downgraded."
    )


__all__ = ["Forecast", "describe", "forecast", "month_of", "top_spenders"]
