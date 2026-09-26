"""`ownership_13f` (monthly by default): load the SEC Form 13F data sets not yet loaded.

For each new data set (newest first, `ownership.datasets_per_run` per run), the zip is
downloaded to a temporary file and streamed: every holdings filing is stored (a few
thousand small rows) but positions only for the portfolio's CUSIPs. A holding's CUSIP comes
from its ISIN (US and Canadian ISINs embed it) or from OpenFIGI. Loading is idempotent:
filings and positions are keyed by accession number and information-table row.
"""

import asyncio
import tempfile
from pathlib import Path

import asyncpg
import httpx

from steward import __version__
from steward.config.jobs import JobsConfig, OwnershipSettings
from steward.config.store import load_active
from steward.jobs.runner import JobContext, JobFailed
from steward.settings import get_settings
from steward.sources.form13f import Dataset, cusip_of_isin, dataset_links, parse_dataset


async def portfolio_cusips(pool: asyncpg.Pool) -> dict[str, str]:
    """CUSIP -> holding ticker, for every instrument with a US/Canadian ISIN or an
    OpenFIGI CUSIP."""
    rows = await pool.fetch(
        "select i.t212_ticker, i.isin, (select max(cusip) from public.instrument_identifier d"
        " where d.ticker = i.t212_ticker and d.cusip is not null) as figi_cusip"
        " from public.instrument i"
    )
    out: dict[str, str] = {}
    for r in rows:
        for cusip in (cusip_of_isin(r["isin"]), r["figi_cusip"]):
            if cusip:
                out.setdefault(cusip.upper(), r["t212_ticker"])
    return out


async def store_dataset(
    pool: asyncpg.Pool, url: str, data: Dataset, tickers: dict[str, str], job_id: int | None
) -> tuple[int, int]:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "insert into public.form13f_dataset (url, filings, rows_kept, job_id)"
            " values ($1, $2, $3, $4) on conflict (url) do update set loaded_at = now(),"
            " filings = excluded.filings, rows_kept = excluded.rows_kept, job_id = excluded.job_id",
            url,
            len(data.filings),
            len(data.positions),
            job_id,
        )
        await conn.executemany(
            "insert into public.form13f_filing (accession, cik, manager_name, period, filed,"
            " submission_type, amendment_type, dataset_url) values ($1, $2, $3, $4, $5, $6,"
            " $7, $8) on conflict (accession) do nothing",
            [
                (
                    f.accession,
                    f.cik,
                    f.manager_name,
                    f.period,
                    f.filed,
                    f.submission_type,
                    f.amendment_type,
                    url,
                )
                for f in data.filings
            ],
        )
        await conn.executemany(
            "insert into public.institutional_holding (accession, infotable_sk, cusip, ticker,"
            " issuer_name, title_of_class, value_usd, shares, shares_type, put_call, discretion,"
            " voting_sole, voting_shared, voting_none) values ($1, $2, $3, $4, $5, $6, $7, $8,"
            " $9, $10, $11, $12, $13, $14) on conflict do nothing",
            [
                (
                    p.accession,
                    p.infotable_sk,
                    p.cusip,
                    tickers.get(p.cusip),
                    p.issuer_name,
                    p.title_of_class,
                    p.value_usd,
                    p.shares,
                    p.shares_type,
                    p.put_call,
                    p.discretion,
                    p.voting_sole,
                    p.voting_shared,
                    p.voting_none,
                )
                for p in data.positions
            ],
        )
    return len(data.filings), len(data.positions)


async def _download(http: httpx.AsyncClient, url: str, target: Path) -> None:
    async with http.stream("GET", url) as response:
        response.raise_for_status()
        with target.open("wb") as out:
            async for chunk in response.aiter_bytes(1 << 20):
                out.write(chunk)


async def collect(ctx: JobContext, http: httpx.AsyncClient, settings: OwnershipSettings) -> None:
    pool = ctx.pool
    async with ctx.step("datasets") as step:
        response = await http.get(settings.index_url)
        response.raise_for_status()
        links = dataset_links(response.text, settings.index_url)
        loaded = {r["url"] for r in await pool.fetch("select url from public.form13f_dataset")}
        todo = [u for u in links if u not in loaded][: settings.datasets_per_run]
        step.detail.update(listed=len(links), to_load=todo)
    tickers = await portfolio_cusips(pool)
    for url in todo:
        async with ctx.step(f"load:{url.rsplit('/', 1)[-1]}") as step:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "form13f.zip"
                await _download(http, url, path)
                data = await asyncio.to_thread(parse_dataset, path, set(tickers))
            filings, rows = await store_dataset(pool, url, data, tickers, ctx.job_id)
            step.detail.update(filings=filings, positions=rows, cusips=sorted(tickers))


async def ownership_13f_handler(ctx: JobContext) -> None:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    settings = (stored.body if stored else JobsConfig()).ownership
    if not settings.enabled:
        return
    agent = get_settings().sec_user_agent
    if not agent:
        raise JobFailed("SEC_USER_AGENT is not set: the SEC requires a contact User-Agent")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, read=300.0),
        headers={"User-Agent": f"{agent} StewardAI/{__version__}"},
        follow_redirects=True,
    ) as http:
        await collect(ctx, http, settings)
