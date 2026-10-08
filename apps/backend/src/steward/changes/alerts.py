"""Alerts for deal and patent changes at companies you hold (plan M6.6).

Two rules, both facts about a holding that was not known before, each raised once:

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
from steward.jobs.runner import JobContext
from steward.notify.alerts import create_alerts
from steward.notify.route import AlertRequest
from steward.xray.supply_chain import symbol_of

DEAL_DAYS = 3
PATENT_DAYS = 7
DEAL_SEVERITY = 2
PATENT_SEVERITY = 1


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
        step.detail["found"] = len(changes)
        step.detail["alerts"] = await raise_alerts(ctx.pool, changes, now=now, job_id=ctx.job_id)
