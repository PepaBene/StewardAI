"""Daily ingestion of structured SEC insider ownership Forms 3, 4 and 5."""

import datetime as dt
from typing import cast

import asyncpg
import httpx

from steward import __version__
from steward.jobs.runner import JobContext
from steward.ownership.insider import InsiderFiling, parse_ownership_xml
from steward.settings import get_settings
from steward.sources.edgar import EdgarClient, EdgarError, Filing

INSIDER_FORMS = {"3", "3/A", "4", "4/A", "5", "5/A"}


async def _issuers(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return cast(
        list[asyncpg.Record],
        await pool.fetch(
            """select distinct i.t212_ticker as ticker, i.cik
           from public.position_snapshot p join public.instrument i on i.t212_ticker = p.ticker
           where p.snapshot_date = (select max(snapshot_date) from public.account_snapshot)
             and i.cik is not null order by i.t212_ticker"""
        ),
    )


async def _upsert(
    pool: asyncpg.Pool, ticker: str, filing: Filing, source_url: str, parsed: InsiderFiling
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """insert into public.insider_filing
               (accession, ticker, issuer_cik, form, filed, period_of_report,
                issuer_name, source_url)
               values ($1,$2,$3,$4,$5,$6,$7,$8) on conflict (accession) do update set
                 ticker=excluded.ticker, form=excluded.form, filed=excluded.filed,
                 period_of_report=excluded.period_of_report, issuer_name=excluded.issuer_name,
                 source_url=excluded.source_url, loaded_at=now()""",
            filing.accession,
            ticker,
            parsed.issuer_cik,
            filing.form,
            filing.filing_date,
            parsed.reporting_period,
            parsed.issuer_name,
            source_url,
        )
        await conn.execute(
            "delete from public.insider_transaction where accession = $1", filing.accession
        )
        await conn.executemany(
            """insert into public.insider_transaction
               (accession,row_index,reporting_owner_cik,owner_name,officer_title,is_director,
                is_officer,is_ten_percent_owner,security_title,transaction_date,transaction_code,
                shares,price_per_share,acquired_disposed,shares_owned_after,direct_indirect,derivative)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)""",
            [
                (
                    filing.accession,
                    i,
                    row.reporting_owner_cik,
                    row.owner_name,
                    row.officer_title,
                    row.is_director,
                    row.is_officer,
                    row.is_ten_percent_owner,
                    row.security_title,
                    row.transaction_date,
                    row.transaction_code,
                    row.shares,
                    row.price_per_share,
                    row.acquired_disposed,
                    row.shares_owned_after,
                    row.direct_indirect,
                    row.derivative,
                )
                for i, row in enumerate(parsed.rows)
            ],
        )


async def _load_filing(edgar: EdgarClient, filing: Filing) -> tuple[bytes, str]:
    names = await edgar.filing_files(filing)
    candidates = [
        filing.primary_document,
        *[name for name in names if name.lower().endswith(".xml")],
    ]
    seen: set[str] = set()
    for name in candidates:
        if name in seen or not name.lower().endswith(".xml"):
            continue
        seen.add(name)
        url = filing.url(name)
        try:
            body = await edgar.document(url, max_bytes=8 * 1024 * 1024)
            if b"ownershipDocument" in body or b"ownershipdocument" in body.lower():
                return body, url
        except (EdgarError, httpx.HTTPError):
            continue
    raise EdgarError(f"No structured ownership XML found for {filing.accession}")


async def collect_insiders(ctx: JobContext) -> None:
    settings = get_settings()
    if not settings.sec_user_agent:
        raise RuntimeError("SEC_USER_AGENT must include a contact email address")
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, read=40.0)) as http:
        edgar = EdgarClient(http, f"{settings.sec_user_agent} StewardAI/{__version__}")
        issuers = await _issuers(ctx.pool)
        async with ctx.step("issuer-filings") as step:
            step.detail["issuers"] = len(issuers)
            imported = 0
            for issuer in issuers:
                ticker, cik = issuer["ticker"], issuer["cik"]
                latest = await ctx.pool.fetchval(
                    "select max(filed) from public.insider_filing where ticker = $1", ticker
                )
                since = (
                    (latest - dt.timedelta(days=30))
                    if latest
                    else dt.date.today() - dt.timedelta(days=365 * 5)
                )
                filings = [
                    f for f in await edgar.filings(cik, since=since) if f.form in INSIDER_FORMS
                ]
                for filing in filings:
                    exists = await ctx.pool.fetchval(
                        "select exists(select 1 from public.insider_filing where accession = $1)",
                        filing.accession,
                    )
                    if exists:
                        continue
                    body, url = await _load_filing(edgar, filing)
                    parsed = parse_ownership_xml(body, filing)
                    await _upsert(ctx.pool, ticker, filing, url, parsed)
                    imported += 1
            step.detail["filings_imported"] = imported


async def insider_trades_handler(ctx: JobContext) -> None:
    await collect_insiders(ctx)
