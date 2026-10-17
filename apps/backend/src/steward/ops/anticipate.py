"""Anticipation (autonomous engine plan, L4): tell the owner before a held company reports.

A holding whose report date falls in the next few days becomes one line in "Needs you" saying
when it reports, how much of the portfolio it is, and when its research was last refreshed, so
the owner can read it ahead of the numbers. Nothing is queued and nothing costs money; the
notice resolves itself once the date has passed.
"""

import datetime as dt
from dataclasses import dataclass

import asyncpg

from steward.autopilot import ledger
from steward.engine import triggers
from steward.ops import tasks

KIND = "pre_earnings"
HORIZON_DAYS = 3
STALE_DAYS = 45  # research older than this is called out


@dataclass(frozen=True)
class Upcoming:
    ticker: str
    name: str
    report_date: dt.date
    weight: float
    last_research: dt.date | None


def describe(item: Upcoming, today: dt.date) -> tuple[str, str]:
    """(title, detail) of the notice for one upcoming report."""
    days = (item.report_date - today).days
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
    title = f"{item.name} reports {when}"
    if item.last_research is None:
        research = "It has no research report yet."
    else:
        age = (today - item.last_research).days
        ago = "today" if age == 0 else "yesterday" if age == 1 else f"{age} days ago"
        research = f"Its research was last refreshed {ago}" + (
            " - worth refreshing first." if age > STALE_DAYS else "."
        )
    return (
        title,
        f"{item.weight:.1%} of the portfolio, reporting {item.report_date:%d %b}. {research}",
    )


async def upcoming(pool: asyncpg.Pool, today: dt.date) -> list[Upcoming]:
    weights = await ledger.holding_weights(pool)
    if not weights:
        return []
    rows = await pool.fetch(
        "select i.t212_ticker as ticker, coalesce(i.name, i.t212_ticker) as name,"
        " i.next_report_date as report_date,"
        " (select max(created_at)::date from public.report r where r.type = 'research'"
        "  and r.ticker = i.t212_ticker) as last_research"
        " from public.instrument i where i.t212_ticker = any($1::text[])"
        " and i.next_report_date between $2 and $3 order by i.next_report_date",
        list(weights),
        today,
        today + dt.timedelta(days=HORIZON_DAYS),
    )
    return [
        Upcoming(r["ticker"], r["name"], r["report_date"], weights[r["ticker"]], r["last_research"])
        for r in rows
    ]


async def announce(pool: asyncpg.Pool, today: dt.date) -> dict[str, int]:
    items = await upcoming(pool, today)
    if items and await triggers.suppressed(pool, "anticipation"):
        # You dismissed the last few of these without acting: stop for now, say so once.
        await tasks.see(
            pool,
            triggers.STOPPED_NOTICE,
            "anticipation",
            title="Heads-ups before a report are paused",
            detail=f"You dismissed the last {triggers.SUPPRESS_AFTER} without acting on any, so "
            f"they stop for {triggers.SUPPRESS_DAYS} days.",
            href="/engine-ledger",
            weight=0.1,
            status="needs_owner",
        )
        return {"announced": 0, "resolved": 0}
    for item in items:
        title, detail = describe(item, today)
        await triggers.log(
            pool,
            "anticipation",
            f"{item.ticker}:{item.report_date.isoformat()}",
            f"{item.name} reports on {item.report_date:%d %b}, and it is "
            f"{item.weight:.1%} of the portfolio.",
            "tell the owner before the report",
            cooldown_days=30,
        )
        await tasks.see(
            pool,
            KIND,
            item.ticker,
            title=title,
            detail=detail,
            href=f"/companies/{item.ticker}",
            weight=item.weight,
            status="needs_owner",
        )
    resolved = await tasks.settle(pool, KIND, {i.ticker for i in items})
    return {"announced": len(items), "resolved": resolved}
