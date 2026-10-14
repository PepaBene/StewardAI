"""Things worth telling the owner before they ask (autonomous engine plan, L4): a position that
grew past the cap, insiders buying together, the monthly review coming up.

They cost nothing (no model, no job); each one is logged through `triggers.log` with the reason
and opens one notice. A notice kind the owner keeps dismissing pauses itself, like the other
proactive notices, and is told about once. Large price moves and filings are not here: the news
escalation already handles them.
"""

import calendar
import datetime as dt

import asyncpg

from steward.autopilot import ledger
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig
from steward.engine import triggers
from steward.ops import tasks
from steward.portfolio.concentration import concentration_report

OVER_CAP = "watch_over_cap"
INSIDERS = "watch_insider"
PRE_REVIEW = "pre_review"
INSIDER_DAYS = 14
INSIDER_MIN_PEOPLE = 3
REVIEW_LEAD_DAYS = 3
COOLDOWN_DAYS = 14


def month_end(today: dt.date) -> dt.date:
    return today.replace(day=calendar.monthrange(today.year, today.month)[1])


def review_is_near(today: dt.date) -> bool:
    return 0 <= (month_end(today) - today).days < REVIEW_LEAD_DAYS


async def _notice(
    pool: asyncpg.Pool,
    kind: str,
    subject: str,
    reason: str,
    action: str,
    title: str,
    detail: str,
    href: str,
    now: dt.datetime,
    cooldown_days: int = COOLDOWN_DAYS,
) -> bool:
    if not await triggers.log(
        pool, "event", f"{kind}:{subject}", reason, action, now=now, cooldown_days=cooldown_days
    ):
        return False
    await tasks.see(
        pool, kind, subject, title=title, detail=detail, href=href, weight=0.3, status="needs_owner"
    )
    return True


async def over_cap(pool: asyncpg.Pool, now: dt.datetime) -> int:
    report = await concentration_report(pool, None)
    if report.reason:
        return 0
    told = 0
    present: set[str] = set()
    for p in report.positions:
        present.add(p.ticker)
        reason = f"{p.ticker} is {p.weight:.1%} of the portfolio, above the {p.cap:.0%} cap"
        told += await _notice(
            pool,
            OVER_CAP,
            p.ticker,
            reason,
            "tell the owner it is above the cap",
            f"{p.name or p.ticker} is {p.weight:.1%} of the portfolio",
            f"The cap is {p.cap:.0%}. "
            + (
                f"Deposits alone would take about {p.months_to_cap} months to dilute it."
                if p.months_to_cap
                else "Deposits alone would not bring it under in three years."
            )
            + " Whether to trim is your decision.",
            f"/holdings/{p.ticker}",
            now,
        )
    await tasks.settle(pool, OVER_CAP, present)
    return told


async def insider_clusters(pool: asyncpg.Pool, now: dt.datetime) -> int:
    held = list(await ledger.holding_weights(pool))
    if not held:
        return 0
    rows = await pool.fetch(
        "select f.ticker, count(distinct x.reporting_owner_cik) as people"
        " from public.insider_filing f join public.insider_transaction x"
        "  on x.accession = f.accession where f.ticker = any($1::text[])"
        " and x.transaction_code = 'P' and not x.derivative and x.transaction_date >= $2"
        " group by f.ticker having count(distinct x.reporting_owner_cik) >= $3",
        held,
        now.date() - dt.timedelta(days=INSIDER_DAYS),
        INSIDER_MIN_PEOPLE,
    )
    told = 0
    for r in rows:
        told += await _notice(
            pool,
            INSIDERS,
            r["ticker"],
            f"{r['people']} insiders of {r['ticker']} bought on the open market in "
            f"{INSIDER_DAYS} days",
            "tell the owner about the cluster of insider buys",
            f"{r['people']} insiders bought {r['ticker']} in the last {INSIDER_DAYS} days",
            "Open-market purchases by several different insiders. Read the filings before "
            "drawing a conclusion.",
            f"/companies/{r['ticker']}",
            now,
        )
    await tasks.settle(pool, INSIDERS, {r["ticker"] for r in rows})
    return told


async def pre_review(pool: asyncpg.Pool, now: dt.datetime) -> int:
    today = now.date()
    if not review_is_near(today):
        await tasks.settle(pool, PRE_REVIEW, set())
        return 0
    month = today.strftime("%Y-%m")
    already = await pool.fetchval(
        "select exists (select 1 from public.report where type = 'review' and period = 'monthly'"
        " and created_at >= date_trunc('month', $1::date))",
        today,
    )
    if already:
        return 0
    stored = await load_active(pool, "theses", ThesesConfig)
    due = (
        sum(1 for t in stored.body.theses if t.review_date and t.review_date <= month_end(today))
        if stored
        else 0
    )
    capped = len((await concentration_report(pool, None)).positions)
    return int(
        await _notice(
            pool,
            PRE_REVIEW,
            month,
            f"The monthly review is due by {month_end(today):%d %b}",
            "tell the owner the monthly review is coming",
            f"Your monthly review is due by {month_end(today):%d %b}",
            f"{due} thesis reviews fall due this month and {capped} positions are above the cap.",
            "/reports",
            now,
            cooldown_days=31,
        )
    )


async def run(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, int]:
    """Every watcher, unless the owner has been dismissing these notices without acting."""
    if await triggers.suppressed(pool, "event", now):
        await tasks.see(
            pool,
            triggers.STOPPED_NOTICE,
            "event",
            title="Heads-ups about positions and insiders are paused",
            detail=f"You dismissed the last {triggers.SUPPRESS_AFTER} without acting on any, so "
            f"they stop for {triggers.SUPPRESS_DAYS} days.",
            href="/engine-ledger",
            weight=0.1,
            status="needs_owner",
        )
        return {"paused": 1}
    return {
        "over_cap": await over_cap(pool, now),
        "insider_clusters": await insider_clusters(pool, now),
        "pre_review": await pre_review(pool, now),
    }
