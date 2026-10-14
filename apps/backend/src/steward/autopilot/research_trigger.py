"""Research on its own schedule: a held thesis whose review date has passed gets a research run
without the owner asking for one.

The run is the ordinary `research` job with the default profile and the profile's own plan, queued
as an event run with no requester, so the budget guard and the per-run caps apply as they do to
any run. A holding is skipped while a run for it is queued or running and for
`research_min_days` after its last report, and one run of the job queues at most
`research_max_per_run`. Nothing here edits a thesis: the report's thesis proposal still waits for
the owner, because its fields (why owned, exit conditions, pre-mortem) are the owner's stance.
"""

import datetime as dt
import logging
from typing import Any

import asyncpg

from steward.config.research import ResearchProfilesConfig
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig, ThesisConfig
from steward.engine import triggers
from steward.jobs import queue
from steward.jobs.runner import create_job

logger = logging.getLogger("steward.autopilot")


def due_tickers(
    theses: list[ThesisConfig],
    today: dt.date,
    recently_researched: set[str],
    in_flight: set[str],
    limit: int,
) -> list[str]:
    """Tickers whose thesis review date is today or earlier, most overdue first, skipping those
    researched recently or already queued."""
    overdue = sorted(
        (t for t in theses if t.review_date is not None and t.review_date <= today),
        key=lambda t: (t.review_date or today, t.ticker),
    )
    out = [
        t.ticker
        for t in overdue
        if t.ticker not in recently_researched and t.ticker not in in_flight
    ]
    return out[:limit]


async def queue_stale_research(
    pool: asyncpg.Pool, *, min_days: int, max_per_run: int, now: dt.datetime | None = None
) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.UTC)
    stored = await load_active(pool, "theses", ThesesConfig)
    if stored is None:
        return {"queued": [], "skipped": "no theses saved"}
    profiles_stored = await load_active(pool, "research_profiles", ResearchProfilesConfig)
    profile = (
        profiles_stored.body if profiles_stored else ResearchProfilesConfig()
    ).default_profile
    recent = {
        r["ticker"]
        for r in await pool.fetch(
            "select distinct ticker from public.report where type = 'research'"
            " and ticker is not null and created_at >= $1",
            now - dt.timedelta(days=min_days),
        )
    }
    active = {
        r["ticker"]
        for r in await pool.fetch(
            "select distinct params->>'ticker' as ticker from public.job"
            " where job_type = 'research' and status in ('queued', 'waiting', 'running')"
            " and params->>'ticker' is not null"
        )
    }
    chosen = due_tickers(stored.body.theses, now.date(), recent, active, max_per_run)
    review_dates = {t.ticker: t.review_date for t in stored.body.theses}
    queued: list[dict[str, Any]] = []
    held: dict[str, str] = {}
    for ticker in chosen:

        async def start(ticker: str = ticker) -> int | None:
            async with pool.acquire() as conn, conn.transaction():
                job_id = await create_job(
                    conn, "research", "event", params={"ticker": ticker, "profile": profile}
                )
                await queue.enqueue(conn, job_id, "research")
            return job_id

        outcome = await triggers.fire(
            pool,
            "staleness",
            ticker,
            f"The thesis review date {review_dates[ticker]} has passed and there has been no "
            f"research report in {min_days} days.",
            "start a research run",
            start,
            now=now,
        )
        if outcome.fired:
            queued.append({"ticker": ticker, "job_id": outcome.job_id})
        elif outcome.skipped != "cooldown":
            held[ticker] = outcome.skipped or ""
    return {"queued": queued, "profile": profile, **({"held_back": held} if held else {})}
