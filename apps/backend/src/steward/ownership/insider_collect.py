"""Daily ingestion of structured SEC insider ownership Forms 3, 4 and 5."""

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any, cast

import asyncpg
import httpx

from steward import __version__
from steward.jobs.runner import JobContext
from steward.ownership.insider import (
    InsiderFiling,
    OriginalCandidate,
    RowKey,
    match_original,
    parse_ownership_xml,
)
from steward.settings import get_settings
from steward.sources.documents import link_public_document
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


def tickers_by_cik(issuers: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Held tickers per issuer CIK. Dual-class listings share one CIK and one set of filings:
    each filing is stored once and linked to every ticker (`insider_filing_ticker`)."""
    out: dict[str, list[str]] = {}
    for issuer in issuers:
        cik = str(issuer["cik"]).strip()
        if not cik:
            continue
        tickers = out.setdefault(cik, [])
        if issuer["ticker"] not in tickers:
            tickers.append(issuer["ticker"])
    return out


async def link_tickers(
    conn: asyncpg.Pool | asyncpg.Connection, accession: str, tickers: Sequence[str]
) -> None:
    await conn.executemany(
        "insert into public.insider_filing_ticker (accession, ticker) values ($1, $2)"
        " on conflict do nothing",
        [(accession, t) for t in tickers],
    )


async def _upsert(
    pool: asyncpg.Pool,
    tickers: Sequence[str],
    filing: Filing,
    source_url: str,
    content: bytes,
    parsed: InsiderFiling,
) -> None:
    ticker = tickers[0]
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """insert into public.insider_filing
               (accession, ticker, issuer_cik, form, filed, period_of_report,
                issuer_name, source_url, reporting_owner_cik, joint_owner_count, is_amendment,
                original_submission_date, aff10b5_one, footnotes, remarks, enriched_at)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15,now())
               on conflict (accession) do update set
                 ticker=excluded.ticker, form=excluded.form, filed=excluded.filed,
                 period_of_report=excluded.period_of_report, issuer_name=excluded.issuer_name,
                 source_url=excluded.source_url, reporting_owner_cik=excluded.reporting_owner_cik,
                 joint_owner_count=excluded.joint_owner_count,
                 is_amendment=excluded.is_amendment,
                 original_submission_date=excluded.original_submission_date,
                 aff10b5_one=excluded.aff10b5_one, footnotes=excluded.footnotes,
                 remarks=excluded.remarks, enriched_at=now(), loaded_at=now()""",
            filing.accession,
            ticker,
            parsed.issuer_cik,
            filing.form,
            filing.filing_date,
            parsed.reporting_period,
            parsed.issuer_name,
            source_url,
            parsed.reporting_owner_cik,
            parsed.joint_owner_count,
            parsed.is_amendment,
            parsed.original_submission_date,
            parsed.aff10b5_one,
            json.dumps(parsed.footnotes or {}),
            parsed.remarks,
        )
        await link_tickers(conn, filing.accession, tickers)
        document_version_id = await link_public_document(
            conn,
            regulator="sec",
            external_id=filing.accession,
            stable_url=source_url,
            document_type=filing.form,
            content=content,
            known_at=dt.datetime.now(dt.UTC),
            filed_at=filing.filing_date,
            published_at=filing.filing_date,
            domain_table="insider_filing",
            record_key=filing.accession,
            quote=parsed.issuer_name,
        )
        await conn.execute(
            "delete from public.insider_transaction where accession = $1", filing.accession
        )
        await conn.executemany(
            """insert into public.insider_transaction
               (accession,row_index,reporting_owner_cik,owner_name,officer_title,is_director,
                is_officer,is_ten_percent_owner,security_title,transaction_date,transaction_code,
                shares,price_per_share,acquired_disposed,shares_owned_after,direct_indirect,
                derivative,footnote_ids,footnote_text,exercise_price,exercisable_date,
                expiration_date,underlying_security_title,underlying_shares,
                deemed_execution_date,equity_swap_involved,economic_class,
                classification_basis,rule_10b5_1_plan,rule_10b5_1_basis,rule_10b5_1_adopted)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
                       $21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31)""",
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
                    list(row.footnote_ids),
                    row.footnote_text,
                    row.exercise_price,
                    row.exercisable_date,
                    row.expiration_date,
                    row.underlying_security_title,
                    row.underlying_shares,
                    row.deemed_execution_date,
                    row.equity_swap_involved,
                    row.economic_class,
                    row.classification_basis,
                    row.rule_10b5_1_plan,
                    row.rule_10b5_1_basis,
                    row.rule_10b5_1_adopted,
                )
                for i, row in enumerate(parsed.rows)
            ],
        )
        await conn.executemany(
            """insert into public.source_record_evidence
                 (domain_table,record_key,document_version_id,known_at)
               values('insider_transaction',$1,$2,$3) on conflict do nothing""",
            [
                (f"{filing.accession}:{i}", document_version_id, dt.datetime.now(dt.UTC))
                for i in range(len(parsed.rows))
            ],
        )

        await relink_amendments(conn, parsed.issuer_cik, parsed.reporting_owner_cik)


def _base_form(form: str) -> str:
    return form.removesuffix("/A")


async def relink_amendments(
    conn: asyncpg.Connection, issuer_cik: str, owner_cik: str | None
) -> None:
    """Recompute amendment supersession for one issuer and reporting owner.

    Each /A is matched to the original it restates (see `match_original`); every filing in
    a restated group except the latest is `superseded_by` the next. Rewritten whole, so
    filings that arrive out of order still link correctly."""
    if owner_cik is None:
        return
    filings = await conn.fetch(
        """select accession, form, filed, is_amendment, original_submission_date
           from public.insider_filing where issuer_cik = $1 and reporting_owner_cik = $2
           order by filed, accession""",
        issuer_cik,
        owner_cik,
    )
    accessions = [f["accession"] for f in filings]
    keys: dict[str, set[RowKey]] = {a: set() for a in accessions}
    for r in await conn.fetch(
        """select accession, transaction_date, transaction_code, security_title
           from public.insider_transaction where accession = any($1::text[])""",
        accessions,
    ):
        keys[r["accession"]].add(
            (r["transaction_date"], r["transaction_code"], r["security_title"])
        )
    originals = [f for f in filings if not f["is_amendment"]]
    groups: dict[str, list[str]] = {}
    status: dict[str, tuple[str | None, str]] = {}
    for f in filings:
        if not f["is_amendment"]:
            status[f["accession"]] = (None, "not_amendment")
            continue
        candidates = [
            OriginalCandidate(o["accession"], o["filed"], frozenset(keys[o["accession"]]))
            for o in originals
            if _base_form(o["form"]) == _base_form(f["form"])
            and (o["filed"], o["accession"]) <= (f["filed"], f["accession"])
        ]
        original, link = match_original(
            candidates,
            frozenset(keys[f["accession"]]),
            f["original_submission_date"],
        )
        status[f["accession"]] = (original, link)
        if original is not None:
            groups.setdefault(original, []).append(f["accession"])
    superseded: dict[str, str | None] = {a: None for a in accessions}
    for original, amendments in groups.items():
        chain = [original, *sorted(amendments)]
        by_filed = {f["accession"]: (f["filed"], f["accession"]) for f in filings}
        chain.sort(key=lambda a: by_filed[a])
        for earlier, later in pairwise(chain):
            superseded[earlier] = later
    for accession in accessions:
        original, link = status[accession]
        await conn.execute(
            """update public.insider_filing set amends_accession = $2,
                 amendment_link_status = $3, superseded_by_accession = $4
               where accession = $1""",
            accession,
            original,
            link,
            superseded[accession],
        )


async def _load_filing(edgar: EdgarClient, filing: Filing) -> tuple[bytes, str]:
    names = await edgar.filing_files(filing)
    # EDGAR lists a Form 3/4/5's primary document as its rendered view, "xslF345X06/doc4.xml"
    # (an HTML transform, checked live 2026-10-03); the structured XML is the same file name at
    # the folder root.
    primary = filing.primary_document.rsplit("/", 1)[-1]
    candidates = [
        primary,
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
        await run_insider_filings(
            ctx, EdgarClient(http, f"{settings.sec_user_agent} StewardAI/{__version__}")
        )


async def run_insider_filings(ctx: JobContext, edgar: EdgarClient) -> None:
    """Fetch, parse and store the Forms 3/4/5 of every held issuer (testable with a fake
    EDGAR client)."""
    issuers = tickers_by_cik(await _issuers(ctx.pool))
    async with ctx.step("issuer-filings") as step:
        step.detail["issuers"] = len(issuers)
        imported = 0
        failed: list[dict[str, str]] = []
        for cik, tickers in issuers.items():
            latest = await ctx.pool.fetchval(
                "select max(f.filed) from public.insider_filing f"
                " join public.insider_filing_ticker l on l.accession = f.accession"
                " where l.ticker = any($1::text[])",
                tickers,
            )
            # Filings stored before the enrichment have no footnotes or classification:
            # reach back to the oldest of them so they are re-read once.
            unenriched = await ctx.pool.fetchval(
                "select min(f.filed) from public.insider_filing f"
                " join public.insider_filing_ticker l on l.accession = f.accession"
                " where l.ticker = any($1::text[]) and f.enriched_at is null",
                tickers,
            )
            since = (
                (latest - dt.timedelta(days=30))
                if latest
                else dt.date.today() - dt.timedelta(days=365 * 5)
            )
            if unenriched is not None:
                since = min(since, unenriched)
            try:
                filings = [
                    f for f in await edgar.filings(cik, since=since) if f.form in INSIDER_FORMS
                ]
            except (EdgarError, httpx.HTTPError, ValueError) as exc:
                failed.append({"cik": cik, "error": f"{type(exc).__name__}: {exc}"[:300]})
                continue
            for filing in filings:
                # One bad filing is recorded and skipped; it never aborts the run or
                # blocks the filings behind it (B35).
                try:
                    exists = await ctx.pool.fetchval(
                        "select exists(select 1 from public.insider_filing"
                        " where accession = $1 and enriched_at is not null)",
                        filing.accession,
                    )
                    if exists:
                        await link_tickers(ctx.pool, filing.accession, tickers)
                        continue
                    body, url = await _load_filing(edgar, filing)
                    parsed = parse_ownership_xml(body, filing)
                    await _upsert(ctx.pool, tickers, filing, url, body, parsed)
                    imported += 1
                except (EdgarError, httpx.HTTPError, ValueError, asyncpg.PostgresError) as exc:
                    failed.append(
                        {
                            "accession": filing.accession,
                            "cik": cik,
                            "error": f"{type(exc).__name__}: {exc}"[:300],
                        }
                    )
        step.detail["filings_imported"] = imported
        step.detail["filings_failed"] = len(failed)
        if failed:
            step.detail["failures"] = failed[:50]


async def insider_trades_handler(ctx: JobContext) -> None:
    await collect_insiders(ctx)
