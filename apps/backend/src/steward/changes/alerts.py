"""Alerts for deal and patent changes at companies you hold (plan M6.6).

Three rules, each raised once:

- An event path: a confirmed story whose supply-chain path reaches a holding at high severity
  (the event feed's `would_alert`), severity 3. Needs OWNER_USER_ID, as the graph is the owner's.

- A deal: a news story classified as mergers and acquisitions about a holding, from a tier 1
  source (the company or a regulator filing) and not a rumour, in the last few days.
- A patent: a new patent family first stored in the last week and owned by a holding.

Severity is fixed (a deal 2, a patent 1) and routing, quiet hours and the daily cap are the
notification config's, as for every other alert. Nothing here judges whether the change is good
or bad for the position.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import asyncpg

from steward.config.notifications import NotificationsConfig
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig
from steward.events import feed as events_feed
from steward.jobs.runner import JobContext
from steward.notify.alerts import create_alerts
from steward.notify.route import AlertRequest
from steward.settings import get_settings
from steward.xray.supply_chain import symbol_of

DEAL_DAYS = 3
PATENT_DAYS = 7
EVENT_DAYS = 3
DEAL_SEVERITY = 2
PATENT_SEVERITY = 1
EVENT_SEVERITY = 3
REVIEW_SEVERITY = 1
REVIEW_GRACE_DAYS = 30


@dataclass(frozen=True)
class Change:
    key: str  # dedupe key: the same change is never alerted twice
    ticker: str
    kind: str  # "deal" or "patent"
    severity: int
    message: str


async def held_symbols(pool: asyncpg.Pool) -> dict[str, str]:
    """Bare symbol -> broker ticker of what the latest snapshot holds."""
    rows = await pool.fetch(
        "select ticker from public.position_snapshot where snapshot_date ="
        " (select max(snapshot_date) from public.position_snapshot) and quantity > 0"
    )
    return {symbol_of(r["ticker"]): r["ticker"] for r in rows}


async def find_changes(pool: asyncpg.Pool, now: dt.datetime) -> list[Change]:
    held = await held_symbols(pool)
    out: list[Change] = []
    deals = await pool.fetch(
        "select id, headline, url, affected_ticker, published_at from public.news_item"
        " where relevant and event_type = 'm_and_a' and tier = 1 and not rumour"
        " and affected_ticker is not null and published_at >= $1",
        now - dt.timedelta(days=DEAL_DAYS),
    )
    for r in deals:
        if symbol_of(r["affected_ticker"]) in held:
            out.append(
                Change(
                    key=f"change:deal:{r['id']}",
                    ticker=r["affected_ticker"],
                    kind="deal",
                    severity=DEAL_SEVERITY,
                    message=f"Deal news at {r['affected_ticker']}: {r['headline']} ({r['url']})",
                )
            )
    patents = await pool.fetch(
        "select f.id, f.family_title, f.source_url, o.ticker from public.patent_family f"
        " join public.patent_family_owner o on o.family_id = f.id"
        " where f.known_at >= $1 and o.valid_to is null",
        now - dt.timedelta(days=PATENT_DAYS),
    )
    for r in patents:
        if r["ticker"] and symbol_of(r["ticker"]) in held:
            out.append(
                Change(
                    key=f"change:patent:{r['id']}:{r['ticker']}",
                    ticker=held[symbol_of(r["ticker"])],
                    kind="patent",
                    severity=PATENT_SEVERITY,
                    message=f"New patent family at {r['ticker']}: {r['family_title'] or 'untitled'}"
                    + (f" ({r['source_url']})" if r["source_url"] else ""),
                )
            )
    return out


async def find_review_changes(pool: asyncpg.Pool, today: dt.date) -> list[Change]:
    """A thesis or journal entry whose review date has come (and passed by under a month) with
    no review recorded since. One alert per item and date."""
    out: list[Change] = []
    since = today - dt.timedelta(days=REVIEW_GRACE_DAYS)
    stored = await load_active(pool, "theses", ThesesConfig)
    for t in stored.body.theses if stored else []:
        if t.review_date is not None and since <= t.review_date <= today:
            out.append(
                Change(
                    key=f"change:thesis_review:{t.ticker}:{t.review_date}",
                    ticker=t.ticker,
                    kind="review",
                    severity=REVIEW_SEVERITY,
                    message=f"The thesis for {t.ticker} was due for review on {t.review_date}.",
                )
            )
    # Review entries (parent_id set) can carry a review date too. Each entry belongs to the chain
    # of its root; only the newest entry of a chain alerts, so a review that has been followed
    # by a later review of the same chain does not alert again.
    rows = await pool.fetch(
        "with recursive chain as ("
        "  select id, id as root from public.decision_journal where parent_id is null"
        "  union all"
        "  select j.id, c.root from public.decision_journal j join chain c on j.parent_id = c.id)"
        " select j.id, j.ticker, j.review_date from public.decision_journal j"
        " join chain c on c.id = j.id"
        " where j.review_date between $1 and $2"
        " and not exists (select 1 from chain c2 where c2.root = c.root and c2.id > j.id)",
        since,
        today,
    )
    for r in rows:
        out.append(
            Change(
                key=f"change:journal_review:{r['id']}:{r['review_date']}",
                ticker=r["ticker"] or "portfolio",
                kind="review",
                severity=REVIEW_SEVERITY,
                message=f"Journal entry {r['id']} ({r['ticker'] or 'portfolio'}) was due for "
                f"review on {r['review_date']}; no review has been recorded.",
            )
        )
    return out


async def find_event_changes(pool: asyncpg.Pool, owner_id: str, now: dt.datetime) -> list[Change]:
    """A confirmed story whose supply-chain path reaches a holding with a high-severity rating
    (a confirmed event on high-confidence links reaching 2% of the portfolio)."""
    chain, indicators = await events_feed.chain_and_indicators(pool, owner_id)
    today = now.date()
    since = today - dt.timedelta(days=EVENT_DAYS)
    stories = await events_feed.load_stories(pool, since)
    feed = events_feed.build(stories, chain, indicators, today, since)
    out: list[Change] = []
    for item in feed.items:
        if not item.would_alert:
            continue
        top = item.top
        out.append(
            Change(
                key=f"change:event:{item.story.id}:{item.direction}",
                ticker=top.end,
                kind="event",
                severity=EVENT_SEVERITY,
                message=(
                    f"{item.story.headline} ({item.story.url}) at {item.story.ticker} reaches "
                    f"{top.end} ({top.weight:.1%} of the portfolio) {item.direction}, "
                    f"weakest link {top.confidence}."
                ),
            )
        )
    return out


async def raise_alerts(
    pool: asyncpg.Pool, changes: list[Change], *, now: dt.datetime, job_id: int
) -> int:
    stored = await load_active(pool, "notifications", NotificationsConfig)
    notify = stored.body if stored else NotificationsConfig()
    created = 0
    for c in changes:
        result = await create_alerts(
            pool,
            AlertRequest("portfolio", c.severity, c.ticker, f"{c.kind}_change"),
            f"[Severity {c.severity}] {c.message}",
            notify,
            now=now,
            config_version_id=stored.id if stored else None,
            job_id=job_id,
            dedupe_key=c.key,
        )
        created += bool(result.ids)
    return created


async def change_alerts_handler(ctx: JobContext) -> None:
    now = dt.datetime.now(dt.UTC)
    async with ctx.step("change_alerts") as step:
        changes = await find_changes(ctx.pool, now)
        changes += await find_review_changes(ctx.pool, now.date())
        owner = get_settings().owner_user_id
        if owner:
            try:
                changes += await find_event_changes(ctx.pool, owner, now)
            except Exception as exc:  # the other rules still alert
                step.detail["event_paths"] = f"failed: {exc}"[:200]
        else:
            step.detail["event_paths"] = "OWNER_USER_ID is not set: event paths were not read"
        step.detail["found"] = len(changes)
        step.detail["alerts"] = await raise_alerts(ctx.pool, changes, now=now, job_id=ctx.job_id)
