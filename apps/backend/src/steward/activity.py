"""The Activity page's coverage view: per holding, when each collector last covered it.

A holding shows a gap when no successful news run has covered it for the monitoring
window (the same rule the `monitor` job alerts on), or when its latest daily price bar is
older than the price gap allowance. Holdings held for less than the window are not gaps.
"""

import datetime as dt
import json

import asyncpg
from pydantic import BaseModel, Field

from steward.config.jobs import JobsConfig

PRICE_GAP_DAYS = 4  # a long weekend plus one missed run


async def news_last_covered(pool: asyncpg.Pool, since: dt.datetime) -> dict[str, dt.datetime]:
    """The latest successful news run that covered each holding, looking back to `since`."""
    runs = await pool.fetch(
        "select j.finished_at, s.detail from public.job j join public.job_step s"
        " on s.job_id = j.id and s.name = 'load_config' where j.job_type = 'news_ingest'"
        " and j.status = 'succeeded' and j.finished_at >= $1 order by j.finished_at desc",
        since,
    )
    covered: dict[str, dt.datetime] = {}
    for run in runs:
        detail = json.loads(run["detail"]) if run["detail"] else {}
        for ticker in detail.get("holdings", []):
            covered.setdefault(ticker, run["finished_at"])
    return covered


async def held_since(pool: asyncpg.Pool) -> dict[str, dt.datetime]:
    """Open positions (latest snapshot) and when each was first seen."""
    rows = await pool.fetch(
        "select p.ticker, min(p.captured_at) as since from public.position_snapshot p"
        " where p.ticker in (select ticker from public.position_snapshot"
        " where snapshot_date = coalesce((select max(snapshot_date) from public.account_snapshot),"
        " (select max(snapshot_date) from public.position_snapshot)))"
        " group by p.ticker"
    )
    return {r["ticker"]: r["since"] for r in rows}


class HoldingCoverage(BaseModel):
    ticker: str
    name: str | None
    held_since: dt.datetime
    news_last_covered: dt.datetime | None
    news_gap: bool = Field(description="No successful news run covered it within the window.")
    earnings_last_check: dt.datetime | None
    earnings_last_status: str | None
    price_symbol: str | None
    prices_last_date: dt.date | None
    prices_gap: bool = Field(
        description=f"A price symbol whose latest bar is more than {PRICE_GAP_DAYS} days old."
    )


class ActivityCoverage(BaseModel):
    checked_at: dt.datetime
    news_window_hours: int
    holdings: list[HoldingCoverage]
    gaps: int = Field(description="Holdings with at least one gap.")


async def coverage(pool: asyncpg.Pool, jobs: JobsConfig, now: dt.datetime) -> ActivityCoverage:
    window = dt.timedelta(hours=jobs.monitoring.news_coverage_hours)
    held = await held_since(pool)
    news = await news_last_covered(pool, now - window - dt.timedelta(days=30))
    rows = await pool.fetch(
        "select i.t212_ticker as ticker, i.name, i.price_symbol,"
        " (select max(date) from public.price_bar b where b.symbol = i.price_symbol) as last_bar,"
        " e.finished_at as earnings_at, e.status as earnings_status"
        " from public.instrument i left join lateral (select finished_at, status"
        " from public.job where job_type = 'earnings_check' and params ->> 'ticker' ="
        " i.t212_ticker and status in ('succeeded', 'failed') order by id desc limit 1) e on true"
        " where i.t212_ticker = any($1::text[]) order by i.t212_ticker",
        list(held),
    )
    by_ticker = {r["ticker"]: r for r in rows}
    holdings = []
    for ticker in sorted(held):
        since = held[ticker]
        row = by_ticker.get(ticker)
        last = news.get(ticker)
        news_gap = (
            jobs.news_ingest.enabled
            and since <= now - window
            and (last is None or last < now - window)
        )
        last_bar = row["last_bar"] if row else None
        symbol = row["price_symbol"] if row else None
        prices_gap = symbol is not None and (
            last_bar is None or (now.date() - last_bar).days > PRICE_GAP_DAYS
        )
        holdings.append(
            HoldingCoverage(
                ticker=ticker,
                name=row["name"] if row else None,
                held_since=since,
                news_last_covered=last,
                news_gap=news_gap,
                earnings_last_check=row["earnings_at"] if row else None,
                earnings_last_status=row["earnings_status"] if row else None,
                price_symbol=symbol,
                prices_last_date=last_bar,
                prices_gap=prices_gap,
            )
        )
    return ActivityCoverage(
        checked_at=now,
        news_window_hours=jobs.monitoring.news_coverage_hours,
        holdings=holdings,
        gaps=sum(1 for h in holdings if h.news_gap or h.prices_gap),
    )
