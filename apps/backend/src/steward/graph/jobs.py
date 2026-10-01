"""Job `graph_review`: passage review of queued filing hits, 8-K / 6-K deal extraction and TED
award notices for the portfolio's holdings. Register it as `graph_review` (see the report)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.graph.deal_extract import DEAL_FORMS, scan_filing
from steward.graph.passages import review_pending_hits
from steward.graph.ted import collect_ted_awards
from steward.jobs.runner import JobContext
from steward.settings import get_settings
from steward.sources.edgar import EdgarClient, EdgarError
from steward.sources.free_market import FreeSourceError

DEAL_LOOKBACK = dt.timedelta(days=30)
MAX_FILINGS_PER_HOLDING = 8
MAX_TED_HOLDINGS = 10


async def load_graph_holdings(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Open positions with their names (instrument name and resolved legal / other names and
    aliases; subsidiaries are not holding names) and CIK."""
    rows = await pool.fetch(
        """select i.t212_ticker ticker, i.name, i.cik,
                  coalesce(array_agg(distinct a.alias) filter (
                    where a.kind in ('legal_name','other_name','alias')), '{}') aliases
           from public.instrument i
           left join public.entity_alias a on a.ticker=i.t212_ticker
           where i.t212_ticker in (
             select ticker from public.position_snapshot where snapshot_date = coalesce(
               (select max(snapshot_date) from public.account_snapshot),
               (select max(snapshot_date) from public.position_snapshot)))
           group by i.t212_ticker, i.name, i.cik order by i.t212_ticker"""
    )
    return [
        {
            "ticker": r["ticker"],
            "cik": r["cik"],
            "names": [n for n in dict.fromkeys([r["name"], *r["aliases"]]) if n and len(n) >= 3],
        }
        for r in rows
    ]


async def _already_scanned(pool: asyncpg.Pool, accession: str) -> bool:
    return bool(
        await pool.fetchval(
            "select exists(select 1 from public.source_document where regulator='sec'"
            " and external_id like $1)",
            f"{accession}:%",
        )
    )


async def graph_review_handler(ctx: JobContext) -> None:
    settings = get_settings()
    holdings = await load_graph_holdings(ctx.pool)
    names = {h["ticker"]: h["names"] for h in holdings if h["names"]}
    today = dt.datetime.now(dt.UTC).date()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, read=120.0),
        headers={"User-Agent": f"StewardAI/{__version__}"},
        follow_redirects=True,
    ) as http:
        edgar = EdgarClient(http, settings.sec_user_agent) if settings.sec_user_agent else None
        async with ctx.step("passage_review") as step:
            if edgar is None:
                step.detail["skipped"] = "SEC_USER_AGENT is not set"
            else:
                results = await review_pending_hits(ctx.pool, edgar, names, limit=20)
                step.detail["reviewed"] = len(results)
                step.detail["states"] = [str(r.get("state")) for r in results]
        async with ctx.step("deal_scan") as step:
            if edgar is None:
                step.detail["skipped"] = "SEC_USER_AGENT is not set"
            else:
                stored = 0
                errors: list[str] = []
                for holding in holdings:
                    if not holding["cik"]:
                        continue
                    try:
                        filings = await edgar.filings(holding["cik"], since=today - DEAL_LOOKBACK)
                        recent = [
                            f
                            for f in filings
                            if f.form in DEAL_FORMS and f.filing_date >= today - DEAL_LOOKBACK
                        ][:MAX_FILINGS_PER_HOLDING]
                        for filing in recent:
                            if await _already_scanned(ctx.pool, filing.accession):
                                continue
                            stored += len(
                                await scan_filing(
                                    ctx.pool, edgar, ticker=holding["ticker"], filing=filing
                                )
                            )
                    except (EdgarError, httpx.HTTPError, ValueError) as exc:
                        errors.append(f"{holding['ticker']}: {type(exc).__name__}")
                step.detail["events_stored"] = stored
                step.detail["errors"] = errors
        async with ctx.step("ted_awards") as step:
            awards = 0
            errors = []
            for holding in holdings[:MAX_TED_HOLDINGS]:
                if not holding["names"]:
                    continue
                try:
                    awards += await collect_ted_awards(
                        ctx.pool,
                        http,
                        ticker=holding["ticker"],
                        names=holding["names"][:5],
                        since=today - dt.timedelta(days=365),
                    )
                except (FreeSourceError, httpx.HTTPError) as exc:
                    errors.append(f"{holding['ticker']}: {type(exc).__name__}")
            step.detail["awards_stored"] = awards
            step.detail["errors"] = errors
