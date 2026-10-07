"""Alerts for the two Phase 4 review items: stocks trailing the core ETFs (M4.9) and a lot whose
sell target is reached shortly before it passes the holding-period test (M4.8).

Both are facts against the owner's own thresholds, raised once: the sleeves gap once a month, a
lot once for good. Severity comes from the owner's performance settings (0 turns them off), and
routing, quiet hours and the daily cap are the notification config's, as for every other alert.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import asyncpg

from steward.config.notifications import NotificationsConfig
from steward.config.store import load_active
from steward.notify.alerts import create_alerts
from steward.notify.route import AlertRequest


@dataclass(frozen=True)
class PerformanceBreach:
    id: str  # "sleeves:gap" or "time_test:<ticker>:<lot id>"
    description: str
    detail: str


def dedupe_key(breach: PerformanceBreach, month: str) -> str:
    """A sleeves gap repeats monthly while it lasts; a lot alerts only once."""
    if breach.id.startswith("sleeves:"):
        return f"performance:{breach.id}:{month}"
    return f"performance:{breach.id}"


def ticker_of(breach: PerformanceBreach) -> str | None:
    parts = breach.id.split(":")
    return parts[1] if parts[0] == "time_test" and len(parts) > 2 else None


async def alert_performance(
    pool: asyncpg.Pool,
    breaches: list[PerformanceBreach],
    *,
    severity: int,
    now: dt.datetime,
    job_id: int,
    app_url: str | None = None,
) -> list[str]:
    """Create the alerts; returns one explanation per alert created."""
    if severity <= 0 or not breaches:
        return []
    stored = await load_active(pool, "notifications", NotificationsConfig)
    notify = stored.body if stored else NotificationsConfig()
    month = now.strftime("%Y-%m")
    outcomes: list[str] = []
    for breach in breaches:
        lines = [f"[Severity {severity}] Portfolio: {breach.description}", breach.detail]
        if app_url:
            lines.append(f"{app_url.rstrip('/')}/performance")
        created = await create_alerts(
            pool,
            AlertRequest("portfolio", severity, ticker_of(breach), "performance"),
            "\n".join(lines),
            notify,
            now=now,
            config_version_id=stored.id if stored else None,
            job_id=job_id,
            dedupe_key=dedupe_key(breach, month),
        )
        if created.ids:
            outcomes.append(f"{breach.id}: {created.explanation}")
    return outcomes
