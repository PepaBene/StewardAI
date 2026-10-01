"""`sec_xbrl` (weekly by default): shares outstanding and revenue by region / segment /
product from each holding's latest SEC XBRL filings.

Per holding with an SEC CIK: the submissions list gives the latest annual report (10-K,
20-F) and, for US issuers, a newer 10-Q. A filing not parsed yet (by this parser version)
has its full XBRL instance and label linkbase downloaded from the SEC archive and its
revenue breakdown stored; a filing already parsed is not downloaded again, so a run
without new filings costs one submissions request and one companyfacts request per holding.
Shares outstanding follows the chain in steward.fundamentals.shares.

A holding whose SEC requests fail keeps its stored data; the failure is recorded on its step
and the run goes on with the next holding. Holdings outside EDGAR (Prague listings, UCITS
ETFs) get only what their holding profile provides (manual shares outstanding / segments).
"""

import datetime as dt
import html
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import JobsConfig, XbrlSettings
from steward.config.store import load_active
from steward.earnings.check import resolve_cik
from steward.fundamentals.shares import (
    SharesFigure,
    basis_for,
    manual_figure,
    shares_chain,
)
from steward.jobs.runner import JobContext, JobFailed
from steward.reference.identity import holding_tickers
from steward.settings import get_settings
from steward.sources.edgar import (
    COMPANY_FACTS_URL,
    EdgarClient,
    EdgarError,
    Filing,
    latest_periodic,
    pad_cik,
    xbrl_documents,
)
from steward.sources.xbrl import (
    PARSER_VERSION,
    Instance,
    Labels,
    XbrlError,
    ads_ratio,
    instance_shares,
    parse_instance,
    parse_labels,
    segment_revenue,
)

ETF_TYPES = {"ETP", "ETF", "FUND"}


@dataclass
class ParsedFiling:
    filing: Filing
    instance: Instance
    labels: Labels
    instance_url: str
    label_url: str | None


async def fetch_filing(edgar: EdgarClient, filing: Filing, max_bytes: int) -> ParsedFiling:
    files = await edgar.filing_files(filing)
    instance_name, label_name = xbrl_documents(filing, files)
    if instance_name is None:
        raise XbrlError(f"{filing.form} {filing.accession} has no XBRL instance")
    instance_url = filing.url(instance_name)
    instance = parse_instance(await edgar.document(instance_url, max_bytes))
    label_url = filing.url(label_name) if label_name else None
    labels = parse_labels(await edgar.document(label_url, max_bytes)) if label_url else Labels()
    return ParsedFiling(filing, instance, labels, instance_url, label_url)


async def store_segments(
    pool: asyncpg.Pool, ticker: str, parsed: ParsedFiling, job_id: int | None
) -> dict[str, Any]:
    result = segment_revenue(parsed.instance, parsed.labels)
    f = parsed.filing
    async with pool.acquire() as conn, conn.transaction():
        filing_id = await conn.fetchval(
            "insert into public.xbrl_filing (ticker, cik, accession, form, filed, report_date,"
            " instance_url, label_url, facts_seen, rows_kept, skipped_multi, parser_version,"
            " job_id) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)"
            " on conflict (ticker, accession) do update set form = excluded.form,"
            " filed = excluded.filed, report_date = excluded.report_date,"
            " instance_url = excluded.instance_url, label_url = excluded.label_url,"
            " facts_seen = excluded.facts_seen, rows_kept = excluded.rows_kept,"
            " skipped_multi = excluded.skipped_multi, parser_version = excluded.parser_version,"
            " job_id = excluded.job_id, parsed_at = now() returning id",
            ticker,
            pad_cik(f.cik),
            f.accession,
            f.form,
            f.filing_date,
            f.report_date,
            parsed.instance_url,
            parsed.label_url,
            result.facts_seen,
            len(result.rows),
            result.skipped_multi,
            PARSER_VERSION,
            job_id,
        )
        await conn.execute("delete from public.segment_revenue where filing_id = $1", filing_id)
        await conn.executemany(
            "insert into public.segment_revenue (filing_id, kind, axis, member, label,"
            " label_source, concept, period_start, period_end, value, unit, decimals, subtotal,"
            " context_id, fact_id) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,"
            " $13, $14, $15)",
            [
                (
                    filing_id,
                    r.kind,
                    r.axis,
                    r.member,
                    r.label,
                    r.label_source,
                    r.concept,
                    r.start,
                    r.end,
                    r.value,
                    r.unit,
                    r.decimals,
                    r.subtotal,
                    r.context_id,
                    r.fact_id,
                )
                for r in result.rows
            ],
        )
        # Keep a periodized, as-filed fact history beside the purpose-built revenue table.
        # Unreviewed filing facts stay unpublished; only named concepts can later be promoted
        # into comparable ratios after source/unit reconciliation.
        await conn.execute(
            "delete from public.financial_statement_fact where ticker=$1 and accession=$2",
            ticker,
            f.accession,
        )
        fact_rows = []
        for fact in parsed.instance.facts:
            number = fact.number
            text_value = fact.text.strip()
            if number is None and "TextBlock" not in fact.concept:
                continue
            if fact.context.typed:
                dimensions = {"typed": True, "members": list(fact.context.dims)}
            else:
                dimensions = {"members": list(fact.context.dims)}
            name = fact.concept.split(":", 1)[-1].lower()
            statement = (
                "segment"
                if fact.context.dims
                else "income"
                if any(
                    s in name
                    for s in ("revenue", "income", "expense", "profit", "loss", "eps", "earnings")
                )
                else "cash_flow"
                if any(
                    s in name
                    for s in ("cashflow", "cashflows", "payments", "proceeds", "repayments")
                )
                else "shares"
                if "sharesoutstanding" in name
                else "balance_sheet"
                if any(
                    s in name for s in ("assets", "liabilities", "equity", "debt", "cashandcash")
                )
                else "note"
            )
            # Retain cited note blocks as text, not as fabricated numerical facts.
            block = text_value[:20000] if "TextBlock" in fact.concept else None
            fact_rows.append(
                (
                    ticker,
                    f.accession,
                    f.form,
                    f.url(f.primary_document),
                    fact.concept,
                    statement,
                    fact.context.start,
                    fact.context.end or fact.context.instant,
                    "IFRS"
                    if fact.concept.startswith("ifrs-full:")
                    else "US_GAAP"
                    if fact.concept.startswith("us-gaap:")
                    else "issuer_extension",
                    fact.unit or "pure",
                    number,
                    json.dumps(dimensions),
                    block,
                    f.filing_date,
                    f.filing_date,
                    f.url(f.primary_document),
                    fact.id,
                )
            )
        await conn.executemany(
            """insert into public.financial_statement_fact
               (ticker,accession,form,filing_url,concept,statement,period_start,period_end,basis,
                unit,value,dimensions,text_block,published_at,known_at,source,source_url,validation)
               values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13,$14::timestamptz,
                      $15::timestamptz,'sec_xbrl',$16,
                      jsonb_build_object('source_fact_id',$17::text,'reviewed',false))""",
            fact_rows,
        )
    kinds: dict[str, int] = {}
    for r in result.rows:
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    return {
        "accession": f.accession,
        "form": f.form,
        "rows": kinds,
        "facts_seen": result.facts_seen,
        "skipped_cross_tabs": result.skipped_multi,
    }


async def store_shares(
    pool: asyncpg.Pool,
    ticker: str,
    cik: str | None,
    figure: SharesFigure,
    ratio: tuple[Decimal | None, str] | None,
    job_id: int | None,
) -> None:
    raw = dict(figure.raw)
    if figure.notes:
        raw["notes"] = figure.notes
    await pool.execute(
        "insert into public.shares_outstanding (ticker, cik, shares, as_of, method, classes,"
        " accession, form, filed, source_url, ads_ratio_reported, ads_ratio_text, raw, job_id)"
        " values ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12, $13::jsonb, $14)"
        " on conflict (ticker, method, as_of, coalesce(accession, '')) do update set"
        " shares = excluded.shares, cik = excluded.cik, classes = excluded.classes,"
        " form = excluded.form, filed = excluded.filed, source_url = excluded.source_url,"
        " ads_ratio_reported = coalesce(excluded.ads_ratio_reported,"
        " shares_outstanding.ads_ratio_reported),"
        " ads_ratio_text = coalesce(excluded.ads_ratio_text, shares_outstanding.ads_ratio_text),"
        " raw = excluded.raw, job_id = excluded.job_id, fetched_at = now()",
        ticker,
        cik,
        figure.shares,
        figure.as_of,
        figure.method,
        json.dumps(figure.classes),
        figure.accession,
        figure.form,
        figure.filed,
        figure.source_url,
        ratio[0] if ratio else None,
        ratio[1] if ratio else None,
        json.dumps(raw, default=str),
        job_id,
    )


def _document_text(data: bytes) -> str:
    text = re.sub(rb"<[^>]+>", b" ", data).decode("utf-8", errors="replace")
    return html.unescape(text)


async def reported_ads_ratio(
    pool: asyncpg.Pool,
    edgar: EdgarClient,
    ticker: str,
    annual: Filing | None,
    parsed: ParsedFiling | None,
    settings: XbrlSettings,
) -> tuple[Decimal | None, str] | None:
    """The ADS ratio a 20-F states and where: from dei:Security12bTitle ("American
    Depositary Receipts, each representing one B Share"), else from the annual report's
    text ("each ADS represents five (5) common shares"). Read once per 20-F: the text is
    tagged with the filing, and a later run reuses it (also when nothing was found)."""
    if annual is None or not annual.form.startswith(("20-F", "40-F")):
        return None
    tag = f"[{annual.form} {annual.accession}]"
    stored = await pool.fetchrow(
        "select ads_ratio_reported, ads_ratio_text from public.shares_outstanding"
        " where ticker = $1 and strpos(ads_ratio_text, $2) > 0"
        " order by fetched_at desc limit 1",
        ticker,
        tag,
    )
    if stored is not None:
        return stored["ads_ratio_reported"], stored["ads_ratio_text"]
    if parsed is not None and parsed.filing.accession == annual.accession:
        for title in parsed.instance.texts("dei:Security12bTitle"):
            found = ads_ratio(title)
            if found:
                return found[0], f"{found[1]} (cover page) {tag}"
    if not settings.read_ads_ratio:
        return None
    body = await edgar.document(annual.url(annual.primary_document), settings.max_document_mb << 20)
    found = ads_ratio(_document_text(body))
    if found is None:
        return None, f"No ADS ratio stated in the report text {tag}"
    return found[0], f"{found[1]} {tag}"


async def collect_ticker(
    ctx: JobContext,
    edgar: EdgarClient,
    ticker: str,
    profile: HoldingProfile,
    settings: XbrlSettings,
    detail: dict[str, Any],
) -> None:
    pool = ctx.pool
    instrument = await pool.fetchrow(
        "select t212_ticker, cik, security_type from public.instrument where t212_ticker = $1",
        ticker,
    )
    manual = manual_figure(profile)
    if instrument is not None and (instrument["security_type"] or "").upper() in ETF_TYPES:
        detail["skipped"] = "an ETF: no company filings"
        return
    cik = await resolve_cik(pool, edgar, profile, instrument)
    if cik is None:
        detail["skipped"] = "no SEC CIK (not an EDGAR filer); set sec_cik or sec_ticker"
        if manual is not None:
            await store_shares(pool, ticker, None, manual, None, ctx.job_id)
            detail["shares"] = {"method": "manual", "shares": str(manual.shares)}
        return
    cik = pad_cik(cik)
    filings = await edgar.filings(cik)
    annual, quarter = latest_periodic(filings)
    targets = [f for f in (annual, quarter if settings.quarterly else None) if f is not None]
    detail["filings"] = [f"{f.form} {f.accession} ({f.filing_date})" for f in targets]
    if not targets:
        detail["skipped"] = "no 10-K, 10-Q or 20-F on EDGAR"
    parsed_now: dict[str, ParsedFiling] = {}
    parsed_detail: list[dict[str, Any]] = []
    for filing in targets:
        done = await pool.fetchval(
            "select parser_version from public.xbrl_filing where ticker = $1 and accession = $2",
            ticker,
            filing.accession,
        )
        if done == PARSER_VERSION and not ctx.params.get("force"):
            continue
        parsed = await fetch_filing(edgar, filing, settings.max_document_mb << 20)
        parsed_now[filing.accession] = parsed
        parsed_detail.append(await store_segments(pool, ticker, parsed, ctx.job_id))
    detail["parsed"] = parsed_detail
    detail["unchanged"] = [f.accession for f in targets if f.accession not in parsed_now]

    # Shares outstanding.
    latest = max(targets, key=lambda f: (f.filing_date, f.accession)) if targets else None
    company_facts = await edgar.company_facts(cik)
    basis = basis_for(profile, cik)
    parsed_latest = parsed_now.get(latest.accession) if latest else None
    figure = shares_chain(
        cik=cik,
        company_facts=company_facts,
        latest=latest,
        instance=parsed_latest.instance if parsed_latest else None,
        instance_shares=(
            instance_shares(parsed_latest.instance, parsed_latest.labels) if parsed_latest else None
        ),
        instance_url=parsed_latest.instance_url if parsed_latest else None,
        basis=basis,
        companyfacts_url=COMPANY_FACTS_URL.format(cik=cik),
    )
    needs_instance = figure is None or (
        figure.method not in ("dei_companyfacts",) and parsed_latest is None
    )
    if needs_instance and latest is not None and parsed_latest is None:
        # companyfacts alone is not enough and the latest filing was parsed on an earlier
        # run: read its instance again for the cover-page class facts.
        parsed_latest = await fetch_filing(edgar, latest, settings.max_document_mb << 20)
        parsed_now[latest.accession] = parsed_latest
        figure = shares_chain(
            cik=cik,
            company_facts=company_facts,
            latest=latest,
            instance=parsed_latest.instance,
            instance_shares=instance_shares(parsed_latest.instance, parsed_latest.labels),
            instance_url=parsed_latest.instance_url,
            basis=basis,
            companyfacts_url=COMPANY_FACTS_URL.format(cik=cik),
        )
    figure = figure or manual
    if figure is None:
        detail["shares"] = "no shares-outstanding figure found"
        return
    annual_parsed = parsed_now.get(annual.accession) if annual else None
    ratio = await reported_ads_ratio(pool, edgar, ticker, annual, annual_parsed, settings)
    await store_shares(pool, ticker, cik, figure, ratio, ctx.job_id)
    detail["shares"] = {
        "method": figure.method,
        "shares": str(figure.shares),
        "as_of": figure.as_of.isoformat(),
        "accession": figure.accession,
        "ads_ratio_reported": str(ratio[0]) if ratio and ratio[0] is not None else None,
        "notes": figure.notes,
    }


async def run_sec_xbrl(ctx: JobContext, edgar: EdgarClient | None) -> None:
    """The job body; `edgar` is None when SEC_USER_AGENT is not set (the run fails then)."""
    pool = ctx.pool
    async with ctx.step("load_config") as step:
        jobs = await load_active(pool, "jobs", JobsConfig)
        profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
        await ctx.record_config_version(jobs.id if jobs else None)
        await ctx.record_config_version(profiles.id if profiles else None)
        settings = (jobs.body if jobs else JobsConfig()).xbrl
        config = profiles.body if profiles else HoldingProfilesConfig()
        only = ctx.params.get("ticker")
        tickers = [only] if isinstance(only, str) and only else await holding_tickers(pool)
        step.detail["tickers"] = tickers
        if not settings.enabled:
            step.detail["skipped"] = "disabled in the Jobs config (xbrl.enabled)"
            return
    if edgar is None:
        raise JobFailed("SEC_USER_AGENT is not set: the SEC requires a contact User-Agent")
    failed: list[str] = []
    for ticker in tickers:
        async with ctx.step(f"xbrl:{ticker}") as step:
            profile = config.for_ticker(ticker) or HoldingProfile(ticker=ticker)
            try:
                await collect_ticker(ctx, edgar, ticker, profile, settings, step.detail)
            except (EdgarError, XbrlError, httpx.HTTPError, ValueError) as exc:
                # Keep what is stored; the next run tries again.
                step.detail["error"] = f"{type(exc).__name__}: {exc}"[:500]
                failed.append(ticker)
    async with ctx.step("summary") as step:
        step.detail.update(
            holdings=len(tickers),
            failed=failed,
            checked_at=dt.datetime.now(dt.UTC).isoformat(),
        )


async def sec_xbrl_handler(ctx: JobContext) -> None:
    agent = get_settings().sec_user_agent
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, read=180.0),
        headers={"User-Agent": f"StewardAI/{__version__}"},
        follow_redirects=True,
    ) as http:
        await run_sec_xbrl(ctx, EdgarClient(http, agent) if agent else None)
