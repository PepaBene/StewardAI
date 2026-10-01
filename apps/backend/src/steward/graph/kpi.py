"""Link company events to the owner's thesis KPIs, verdicts and news clusters (D5.6).

`company_event.thesis_kpi` used to be free text. It must now name a KPI of the ticker's active
thesis, and the read below joins each linked event to the KPI definition, the latest verdict
for it and the story clusters about the same KPI around the event. A suggested or disputed
event is shown as a review prompt: it never counts towards a breach.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import asyncpg

from steward.config import ThesesConfig
from steward.config.store import load_active

CLUSTER_WINDOW = dt.timedelta(days=30)


async def thesis_kpis(
    pool: asyncpg.Pool | asyncpg.Connection, ticker: str
) -> dict[str, dict[str, Any]]:
    stored = await load_active(pool, "theses", ThesesConfig)
    if stored is None:
        return {}
    for thesis in stored.body.theses:
        if thesis.ticker == ticker:
            return {k.id: k.model_dump(mode="json") for k in thesis.kpis}
    return {}


async def validate_thesis_kpi(
    pool: asyncpg.Pool | asyncpg.Connection, ticker: str, kpi_id: str | None
) -> None:
    """Raise ValueError unless `kpi_id` is a KPI of the ticker's active thesis."""
    if kpi_id is None:
        return
    known = await thesis_kpis(pool, ticker)
    if kpi_id not in known:
        options = ", ".join(sorted(known)) or "none defined"
        raise ValueError(f"thesis KPI {kpi_id!r} is not in {ticker}'s active thesis ({options})")


async def kpi_event_links(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None
) -> list[dict[str, Any]]:
    known = await thesis_kpis(pool, ticker)
    events = await pool.fetch(
        """select id,event_type,event_state,status,announced_at,effective_at,known_at,thesis_kpi,
                  source_url,evidence_id,counterparty_key
           from public.company_event
           where ticker=$1 and thesis_kpi is not null and ($2::timestamptz is null or known_at<=$2)
           order by coalesce(effective_at,announced_at,known_at::date) desc,id desc""",
        ticker,
        as_of,
    )
    out: list[dict[str, Any]] = []
    for ev in events:
        when = ev["effective_at"] or ev["announced_at"] or ev["known_at"].date()
        verdict = await pool.fetchrow(
            """select status,observed_value,threshold,operator,fiscal_year,fiscal_period,created_at
               from public.verdict where ticker=$1 and kpi_id=$2
                 and ($3::timestamptz is null or created_at<=$3)
               order by id desc limit 1""",
            ticker,
            ev["thesis_kpi"],
            as_of,
        )
        clusters = await pool.fetch(
            """select id,headline,status,confidence,severity,event_date,first_seen
               from public.story_cluster
               where ticker=$1 and kpi_id=$2 and first_seen between $3 and $4
                 and ($5::timestamptz is null or first_seen<=$5)
               order by first_seen desc limit 10""",
            ticker,
            ev["thesis_kpi"],
            dt.datetime.combine(when, dt.time.min, tzinfo=dt.UTC) - CLUSTER_WINDOW,
            dt.datetime.combine(when, dt.time.min, tzinfo=dt.UTC) + CLUSTER_WINDOW,
            as_of,
        )
        out.append(
            {
                "event_id": ev["id"],
                "event_type": ev["event_type"],
                "event_state": ev["event_state"],
                "status": ev["status"],
                "date": when,
                "kpi": known.get(ev["thesis_kpi"]),
                "kpi_id": ev["thesis_kpi"],
                "kpi_defined": ev["thesis_kpi"] in known,
                "latest_verdict": dict(verdict) if verdict else None,
                "news_clusters": [dict(c) for c in clusters],
                "can_support_breach": ev["status"] == "confirmed" and ev["evidence_id"] is not None,
                "source_url": ev["source_url"],
            }
        )
    return out
