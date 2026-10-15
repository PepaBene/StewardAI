"""`ownership_13f` (monthly by default): load the SEC Form 13F data sets not yet loaded.

For each new data set among the newest `ownership.history_datasets` (newest first,
`ownership.datasets_per_run` per run; the rest follow on later runs), the zip is
downloaded to a temporary file and streamed: every holdings filing is stored (a few
thousand small rows) but positions only for the portfolio's CUSIPs. A holding's CUSIP comes
from its ISIN (US and Canadian ISINs embed it) or from OpenFIGI. Loading is idempotent:
filings and positions are keyed by accession number and information-table row.
"""

import asyncio
import datetime as dt
import hashlib
import json
import tempfile
from pathlib import Path

import asyncpg
import httpx

from steward import __version__
from steward.config.jobs import JobsConfig, OwnershipSettings
from steward.config.store import load_active
from steward.jobs.runner import JobContext, JobFailed
from steward.settings import get_settings
from steward.sources.documents import link_public_document
from steward.sources.form13f import (
    Dataset,
    cusip_of_isin,
    dataset_links,
    issuer_key,
    parse_dataset,
)


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


async def depositary_names(pool: asyncpg.Pool, cusips: dict[str, str]) -> dict[str, str]:
    """`issuer_key` name -> ticker for US-listed holdings with no known CUSIP (ADRs of
    foreign issuers): their 13F rows are found by issuer name. A holding listed outside the
    US is left out (holders of its ADR do not hold its local shares)."""
    known = set(cusips.values())
    rows = await pool.fetch(
        "select t212_ticker, name from public.instrument where t212_ticker like '%\\_US\\_EQ'"
    )
    out: dict[str, str] = {}
    for r in rows:
        key = issuer_key(r["name"])
        if r["t212_ticker"] not in known and key:
            out.setdefault(key, r["t212_ticker"])
    return out


async def store_dataset(
    pool: asyncpg.Pool,
    url: str,
    data: Dataset,
    tickers: dict[str, str],
    job_id: int | None,
    tracked_ciks: set[str] | None = None,
    source_hash: str | None = None,
    searched: list[str] | None = None,
) -> tuple[int, int]:
    """`searched`: the tickers this load looked for (a later holding re-reads the set)."""
    tickers = {**tickers, **data.name_matched}
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "insert into public.form13f_dataset (url, filings, rows_kept, job_id, tickers)"
            " values ($1, $2, $3, $4, $5) on conflict (url) do update set loaded_at = now(),"
            " filings = excluded.filings, rows_kept = excluded.rows_kept,"
            " job_id = excluded.job_id, tickers = excluded.tickers",
            url,
            len(data.filings),
            len(data.positions),
            job_id,
            sorted(set(searched if searched is not None else tickers.values())),
        )
        # An already stored filing keeps its identity; a re-read only fills what was unknown (the
        # summary-page totals and the confidential-treatment flag), never overwriting a value.
        await conn.executemany(
            "insert into public.form13f_filing (accession, cik, manager_name, period, filed,"
            " submission_type, amendment_type, dataset_url, reported_line_count,"
            " reported_total_value_usd, omitted_holdings_notice, confidential_treatment)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)"
            " on conflict (accession) do update set"
            " reported_line_count = coalesce(public.form13f_filing.reported_line_count,"
            "   excluded.reported_line_count),"
            " reported_total_value_usd = coalesce(public.form13f_filing.reported_total_value_usd,"
            "   excluded.reported_total_value_usd),"
            " omitted_holdings_notice = coalesce(public.form13f_filing.omitted_holdings_notice,"
            "   excluded.omitted_holdings_notice),"
            " confidential_treatment = case when public.form13f_filing.confidential_treatment"
            "   = 'unknown' then excluded.confidential_treatment"
            "   else public.form13f_filing.confidential_treatment end",
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
                    f.reported_line_count,
                    f.reported_total_value_usd,
                    f.omitted_holdings_notice,
                    "unknown"
                    if f.omitted_holdings_notice is None
                    else "requested"
                    if f.omitted_holdings_notice
                    else "none_indicated",
                )
                for f in data.filings
            ],
        )
        known_at = dt.datetime.now(dt.UTC)
        version_by_accession: dict[str, int] = {}
        for filing in data.filings:
            archive_url = (
                "https://www.sec.gov/Archives/edgar/data/"
                f"{int(filing.cik)}/{filing.accession.replace('-', '')}/"
            )
            amends_version_id = None
            if filing.submission_type.endswith("/A"):
                prior_accession = await conn.fetchval(
                    """select accession from public.form13f_filing
                       where cik=$1 and period=$2 and submission_type='13F-HR'
                         and filed <= $3 order by filed desc limit 1""",
                    filing.cik,
                    filing.period,
                    filing.filed,
                )
                if prior_accession:
                    amends_version_id = await conn.fetchval(
                        """select v.id from public.source_document d
                           join public.source_document_version v on v.document_id=d.id
                           where d.regulator='sec' and d.external_id=$1
                           order by v.known_at desc limit 1""",
                        prior_accession,
                    )
            version_by_accession[filing.accession] = await link_public_document(
                conn,
                regulator="sec",
                external_id=filing.accession,
                stable_url=archive_url,
                document_type=filing.submission_type,
                content=json.dumps(
                    {
                        "accession": filing.accession,
                        "cik": filing.cik,
                        "period": filing.period.isoformat(),
                        "filed": filing.filed.isoformat(),
                        "submission_type": filing.submission_type,
                        "amendment_type": filing.amendment_type,
                        "manager_name": filing.manager_name,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8"),
                content_hash=source_hash,
                raw_location=url,
                amends_version_id=amends_version_id,
                known_at=known_at,
                filed_at=filing.filed,
                published_at=filing.filed,
                domain_table="form13f_filing",
                record_key=filing.accession,
                quote=filing.manager_name,
            )
        seen_ciks = {filing.cik for filing in data.filings} & {
            cik.zfill(10) for cik in (tracked_ciks or set())
        }
        if seen_ciks:
            await conn.executemany(
                "insert into public.form13f_manager_dataset (cik, dataset_url) values ($1, $2)"
                " on conflict do nothing",
                [(cik, url) for cik in seen_ciks],
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
    # No link row per position (443K rows, a third of the database): positions inherit the
    # filing's link, which `record_evidence_as_of` resolves from the accession.
    return len(data.filings), len(data.positions)


async def _download(http: httpx.AsyncClient, url: str, target: Path) -> None:
    async with http.stream("GET", url) as response:
        response.raise_for_status()
        with target.open("wb") as out:
            async for chunk in response.aiter_bytes(1 << 20):
                out.write(chunk)


def _sha256_file(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


async def collect(ctx: JobContext, http: httpx.AsyncClient, settings: OwnershipSettings) -> None:
    pool = ctx.pool
    async with ctx.step("datasets") as step:
        response = await http.get(settings.index_url)
        response.raise_for_status()
        links = dataset_links(response.text, settings.index_url)
        # A dataset that kept no positions was stored before the portfolio's CUSIPs were
        # known (identity lookups had failed), so it is loaded again once there are some; one
        # not yet searched for a holding (bought, or its CUSIP found, since) is loaded again too.
        tickers = await portfolio_cusips(pool)
        names = await depositary_names(pool, tickers)
        searching = set(tickers.values()) | set(names.values())
        loaded = {
            r["url"]
            for r in await pool.fetch("select url, rows_kept, tickers from public.form13f_dataset")
            if (r["rows_kept"] > 0 or not tickers)
            and (r.get("tickers") is None or searching <= set(r["tickers"]))
        }
        # The newest `history_datasets` are wanted; older ones are deliberately not loaded.
        wanted = links[: settings.history_datasets]
        missing = [u for u in wanted if u not in loaded]
        todo = missing[: settings.datasets_per_run]
        step.detail.update(
            listed=len(links),
            wanted=len(wanted),
            to_load=todo,
            # Still missing after this run (loaded on the next runs, newest first).
            remaining=len(missing) - len(todo),
            older_not_loaded=len(links) - len(wanted),
        )
    tracked_ciks = {manager.cik.zfill(10) for manager in settings.tracked_managers}
    unprocessed = (
        await pool.fetch(
            "select distinct f.dataset_url as url from public.form13f_filing f"
            " join public.form13f_dataset d on d.url=f.dataset_url"
            " left join public.form13f_manager_dataset m"
            " on m.cik=f.cik and m.dataset_url=f.dataset_url"
            " where f.cik=any($1::text[]) and m.cik is null order by f.dataset_url",
            list(tracked_ciks),
        )
        if tracked_ciks
        else []
    )
    backfill = [row["url"] for row in unprocessed if row["url"] not in todo]
    work = list(dict.fromkeys([*todo, *backfill[: max(0, settings.datasets_per_run - len(todo))]]))
    for url in work:
        async with ctx.step(f"load:{url.rsplit('/', 1)[-1]}") as step:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "form13f.zip"
                await _download(http, url, path)
                data = await asyncio.to_thread(
                    parse_dataset,
                    path,
                    set(tickers),
                    tracked_ciks,
                    names,
                )
                source_hash = await asyncio.to_thread(_sha256_file, path)
            filings, rows = await store_dataset(
                pool,
                url,
                data,
                tickers,
                ctx.job_id,
                tracked_ciks,
                source_hash,
                sorted(searching),
            )
            step.detail.update(
                filings=filings,
                positions=rows,
                cusips=sorted(tickers),
                name_matched=data.name_matched,
            )


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
