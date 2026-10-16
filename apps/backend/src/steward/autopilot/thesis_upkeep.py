"""Thesis upkeep on the app's own initiative (autonomy plan C5).

Three jobs, none of which edits your stance:

- **Draft the missing theses.** A held, non-core holding with no thesis gets a deep-thesis
  research run (at most `draft_max_per_run` per autopilot run, heaviest holding first, and not
  again for `draft_min_days` after its last report). The report carries a thesis proposal.
- **Tell you once** that a draft is ready: one "Needs you" notice per holding, linking to the
  report, which clears when the thesis exists. Accepting a new thesis stays with you: it is the
  one place the autopilot never writes `why_owned`.
- **Move the review date** of a thesis the evidence just confirmed: every KPI's latest verdict
  passes and a research report written since the date proposes no change. That date is
  bookkeeping, and the change is a normal `theses` config version you can roll back.
"""

import datetime as dt
import json
import logging
from typing import Any

import asyncpg

from steward.autopilot import ledger
from steward.config.research import ResearchProfilesConfig
from steward.config.store import ConfigConflictError, load_active, save_config
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig, ThesisConfig
from steward.earnings.verdicts import NEWEST_PERIOD_FIRST
from steward.engine import triggers
from steward.jobs import queue
from steward.jobs.runner import create_job
from steward.ops import tasks

logger = logging.getLogger("steward.autopilot")

DRAFT_PROFILE = "deep-thesis"
NOTE = (
    "Autopilot moved the review date: every KPI passes and the latest research proposes no change"
)


def drafts_due(
    weights: dict[str, float],
    thesis_tickers: set[str],
    core: set[str],
    recently_researched: set[str],
    in_flight: set[str],
    limit: int,
) -> list[str]:
    """Held, non-core tickers with no thesis, heaviest first, skipping those researched
    recently or already queued."""
    wanted = [
        t
        for t in sorted(weights, key=lambda t: (-weights[t], t))
        if t not in thesis_tickers
        and t not in core
        and t not in recently_researched
        and t not in in_flight
    ]
    return wanted[:limit]


def review_extension(
    thesis: ThesisConfig,
    verdicts: dict[str, str],
    researched_since_review: bool,
    proposes_change: bool,
    today: dt.date,
    days: int,
) -> dt.date | None:
    """The new review date, or None when the evidence does not support moving it."""
    if thesis.review_date is None or thesis.review_date > today or not thesis.kpis:
        return None
    if not researched_since_review or proposes_change:
        return None
    if any(verdicts.get(k.id) != "pass" for k in thesis.kpis):
        return None
    return today + dt.timedelta(days=days)


async def _core_tickers(pool: asyncpg.Pool) -> set[str]:
    strategy = await load_active(pool, "strategy", StrategyConfig)
    classes = strategy.body.classifications if strategy else {}
    return {t for t, c in classes.items() if c.get("risk") == "core"}


async def queue_thesis_drafts(
    pool: asyncpg.Pool, *, max_per_run: int, min_days: int, now: dt.datetime | None = None
) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.UTC)
    profiles = await load_active(pool, "research_profiles", ResearchProfilesConfig)
    known = {p.id for p in (profiles.body if profiles else ResearchProfilesConfig()).profiles}
    if DRAFT_PROFILE not in known:
        return {"queued": [], "skipped": f"no {DRAFT_PROFILE} research profile"}
    stored = await load_active(pool, "theses", ThesesConfig)
    have = {t.ticker for t in (stored.body.theses if stored else [])}
    weights = await ledger.holding_weights(pool)
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
    chosen = drafts_due(weights, have, await _core_tickers(pool), recent, active, max_per_run)
    queued: list[dict[str, Any]] = []
    for ticker in chosen:

        async def start(ticker: str = ticker) -> int | None:
            async with pool.acquire() as conn, conn.transaction():
                job_id = await create_job(
                    conn, "research", "event", params={"ticker": ticker, "profile": DRAFT_PROFILE}
                )
                await queue.enqueue(conn, job_id, "research")
            return job_id

        outcome = await triggers.fire(
            pool,
            "coverage",
            ticker,
            f"{ticker} is held ({weights.get(ticker, 0):.1%} of the portfolio) and has no thesis.",
            "start a research run that drafts a thesis",
            start,
            now=now,
        )
        if outcome.fired:
            queued.append({"ticker": ticker, "job_id": outcome.job_id})
    return {"queued": queued}


async def announce_drafts(pool: asyncpg.Pool) -> dict[str, Any]:
    """One notice per holding whose newest research report drafts a thesis that does not exist
    yet; resolved when the thesis exists."""
    stored = await load_active(pool, "theses", ThesesConfig)
    have = {t.ticker for t in (stored.body.theses if stored else [])}
    weights = await ledger.holding_weights(pool)
    rows = await pool.fetch(
        "select distinct on (ticker) id, ticker, body from public.report"
        " where type = 'research' and ticker is not null and created_at >= now() - interval"
        " '90 days' order by ticker, id desc"
    )
    ready: set[str] = set()
    for row in rows:
        ticker = row["ticker"]
        if ticker in have or ticker not in weights:
            continue
        body = json.loads(row["body"]) if isinstance(row["body"], str) else row["body"]
        proposal = body.get("thesis_proposal") or {}
        if not any(c.get("field") == "why_owned" for c in proposal.get("changes", [])):
            continue
        ready.add(ticker)
        await tasks.see(
            pool,
            "thesis_draft",
            ticker,
            title=f"Review the drafted thesis for {ticker}",
            detail=f"{ticker} is held but has no thesis. The research report drafted one from "
            "cited sources. Read it and accept the lines you agree with; nothing is saved "
            "until you do.",
            href=f"/research/{row['id']}",
            weight=weights[ticker],
            status="needs_owner",
        )
    return {"ready": sorted(ready), "resolved": await tasks.settle(pool, "thesis_draft", ready)}


async def refresh_review_dates(
    pool: asyncpg.Pool, *, days: int, today: dt.date | None = None
) -> dict[str, Any]:
    today = today or dt.datetime.now(dt.UTC).date()
    stored = await load_active(pool, "theses", ThesesConfig)
    if stored is None:
        return {"moved": []}
    moved: dict[str, dt.date] = {}
    updated: list[ThesisConfig] = []
    for thesis in stored.body.theses:
        if thesis.review_date is None or thesis.review_date > today or not thesis.kpis:
            updated.append(thesis)
            continue
        verdicts = {
            r["kpi_id"]: r["status"]
            for r in await pool.fetch(
                "select distinct on (kpi_id) kpi_id, status from public.verdict"  # noqa: S608
                f" where ticker = $1 order by kpi_id, {NEWEST_PERIOD_FIRST}",
                thesis.ticker,
            )
        }
        report = await pool.fetchrow(
            "select body from public.report where type = 'research' and ticker = $1"
            " and created_at >= $2 order by id desc limit 1",
            thesis.ticker,
            dt.datetime.combine(thesis.review_date, dt.time.min, tzinfo=dt.UTC),
        )
        proposes = False
        if report is not None:
            body = json.loads(report["body"]) if isinstance(report["body"], str) else report["body"]
            proposes = bool((body.get("thesis_proposal") or {}).get("changes"))
        new = review_extension(thesis, verdicts, report is not None, proposes, today, days)
        if new is None:
            updated.append(thesis)
            continue
        moved[thesis.ticker] = new
        updated.append(thesis.model_copy(update={"review_date": new}))
    if not moved:
        return {"moved": []}
    try:
        async with pool.acquire() as conn:
            await save_config(
                conn,
                "theses",
                ThesesConfig(theses=updated),
                created_by=None,
                note=f"{NOTE}: {', '.join(sorted(moved))}",
                expected_active_id=stored.id,
            )
    except ConfigConflictError:
        return {"moved": [], "skipped": "the theses changed while this ran"}
    return {"moved": {t: d.isoformat() for t, d in moved.items()}}
