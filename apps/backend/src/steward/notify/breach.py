"""Alerts for thesis KPI breaches found by an earnings check (the spec's "breach -> alert").

Only verdicts the earnings check itself wrote are alerted, once per verdict and channel; a
threshold edit re-judged by `thesis_verdicts` shows up in the digest instead.
"""

import datetime as dt

import asyncpg

from steward.config.event_rules import EventRulesConfig
from steward.config.notifications import NotificationsConfig
from steward.config.store import load_active
from steward.earnings.verdicts import is_current
from steward.notify.alerts import create_alerts
from steward.notify.route import AlertRequest


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


async def alert_breaches(
    pool: asyncpg.Pool, job_id: int, now: dt.datetime, app_url: str | None = None
) -> list[str]:
    rules_stored = await load_active(pool, "event_rules", EventRulesConfig)
    notify_stored = await load_active(pool, "notifications", NotificationsConfig)
    rules = rules_stored.body if rules_stored else EventRulesConfig()
    notify = notify_stored.body if notify_stored else NotificationsConfig()
    if rules.thesis_breach_severity == 0:
        return []
    rows = await pool.fetch(
        "select v.id, v.ticker, v.kpi_id, v.period, v.observed_value, v.operator, v.threshold,"  # noqa: S608
        " v.threshold_unit, v.rationale, i.name from public.verdict v"
        " left join public.instrument i on i.t212_ticker = v.ticker"
        f" where v.job_id = $1 and v.status = 'breach' and {is_current('v')} order by v.id",
        job_id,
    )
    outcomes = []
    for row in rows:
        lines = [
            f"[Severity {rules.thesis_breach_severity}] {row['name'] or row['ticker']}: "
            f"thesis KPI breach",
            f"KPI {row['kpi_id']} ({row['period'] or 'latest'}): observed "
            f"{_fmt(row['observed_value'])}, threshold {row['operator']} {_fmt(row['threshold'])} "
            f"{row['threshold_unit']}.",
            row["rationale"][:500],
        ]
        if app_url:
            lines.append(f"{app_url.rstrip('/')}/holdings/{row['ticker']}")
        created = await create_alerts(
            pool,
            AlertRequest(
                "thesis_breach", rules.thesis_breach_severity, row["ticker"], "thesis_breach"
            ),
            "\n".join(lines),
            notify,
            now=now,
            config_version_id=notify_stored.id if notify_stored else None,
            verdict_id=row["id"],
            job_id=job_id,
        )
        if created.ids:
            outcomes.append(f"verdict {row['id']}: {created.explanation}")
    return outcomes
