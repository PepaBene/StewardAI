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
import hashlib
import html
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.company.peer_coverage import peer_key, peer_symbol, uncovered_peers
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
from steward.fundamentals.statements import (
    FilingMeta,
    apply_reconciliation,
    companyfacts_checks,
    companyfacts_reference,
    filing_evidence,
    has_full_statements,
    internal_checks,
    resolve_issuer_id,
    sec_known_at,
    sec_statement_facts,
    store_statement_facts,
)
from steward.jobs.runner import JobContext, JobFailed
from steward.reference.identity import holding_tickers
from steward.settings import get_settings
from steward.sources.edgar import (
    COMPANY_FACTS_URL,
    EdgarClient,
    EdgarError,
    Filing,
    amended_filing,
    amendments_to_check,
    is_amendment,
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
    instance_sha256: str = ""


async def fetch_filing(edgar: EdgarClient, filing: Filing, max_bytes: int) -> ParsedFiling:
    files = await edgar.filing_files(filing)
    instance_name, label_name = xbrl_documents(filing, files)
    if instance_name is None:
        raise XbrlError(f"{filing.form} {filing.accession} has no XBRL instance")
    instance_url = filing.url(instance_name)
    raw = await edgar.document(instance_url, max_bytes)
    instance = parse_instance(raw, text_blocks=True)
    label_url = filing.url(label_name) if label_name else None
    labels = parse_labels(await edgar.document(label_url, max_bytes)) if label_url else Labels()
    return ParsedFiling(
        filing, instance, labels, instance_url, label_url, hashlib.sha256(raw).hexdigest()
    )


async def store_segments(
    pool: asyncpg.Pool,
    ticker: str,
    parsed: ParsedFiling,
    job_id: int | None,
    *,
    company_facts: dict[str, Any] | None = None,
    amends_accession: str | None = None,
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
        # Keep a periodized, as-filed fact history beside the purpose-built revenue table:
        # the statement set, cover-page facts and cited note text blocks, reconciled to the
        # filing's own identities and to SEC companyfacts before anything is published.
        cik10 = pad_cik(f.cik)
        known_at = sec_known_at(f.accepted_at, f.filing_date)
        evidence_id = await filing_evidence(
            conn,
            regulator="SEC",
            external_id=f.accession,
            stable_url=f.index_url,
            document_type=f.form,
            content_hash=parsed.instance_sha256 or hashlib.sha256(f.accession.encode()).hexdigest(),
            filed_at=f.filing_date,
            accepted_at=f.accepted_at,
            known_at=known_at,
            quote=(
                f"{f.form} {f.accession} filed {f.filing_date}: "
                f"XBRL financial statements ({ticker})"
            ),
            amends_external_id=amends_accession,
        )
        meta = FilingMeta(
            ticker=ticker,
            accession=f.accession,
            form=f.form,
            filing_date=f.filing_date,
            filing_url=f.url(f.primary_document),
            source_url=f.url(f.primary_document),
            accepted_at=f.accepted_at,
            cik=cik10,
            issuer_id=await resolve_issuer_id(conn, ticker, cik10),
            evidence_id=evidence_id,
            amends_accession=amends_accession,
        )
        statement_facts = sec_statement_facts(parsed.instance, meta)
        checks = internal_checks(statement_facts)
        if company_facts:
            checks += companyfacts_checks(
                statement_facts, companyfacts_reference(company_facts, f.accession)
            )
        statement_facts = apply_reconciliation(statement_facts, checks)
        stored = await store_statement_facts(conn, ticker, "sec_xbrl", f.accession, statement_facts)
    kinds: dict[str, int] = {}
    for r in result.rows:
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    return {
        "accession": f.accession,
        "form": f.form,
        "rows": kinds,
        "facts_seen": result.facts_seen,
        "skipped_cross_tabs": result.skipped_multi,
        "statement_facts": stored["rows"],
        "linked_to_original": stored["linked_to_original"],
        "reconciled": sum(1 for x in statement_facts if x.published),
        "failed_checks": sum(1 for c in checks if not c.passed),
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
    *,
    peer: bool = False,
) -> None:
    """One company's latest filings. A `peer` (confirmed, not held) is read only when it files
    a 10-K: a 20-F's statements are in the home currency against a US dollar price."""
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
    max_bytes = settings.max_document_mb << 20
    company_facts = await edgar.company_facts(cik)
    parsed_now: dict[str, ParsedFiling] = {}
    parsed_detail: list[dict[str, Any]] = []
    force = bool(ctx.params.get("force"))

    async def parse_and_store(filing: Filing) -> ParsedFiling:
        original = amended_filing(filings, filing) if is_amendment(filing.form) else None
        if (
            original is not None
            and original.accession not in parsed_now
            and await parser_version(original) != PARSER_VERSION
        ):
            # The amendment's rows link to the original's: store the original first.
            try:
                await parse_and_store(original)
            except (EdgarError, XbrlError):
                original = None  # no instance to link to; the amendment stands alone
        parsed = await fetch_filing(edgar, filing, max_bytes)
        parsed_now[filing.accession] = parsed
        parsed_detail.append(
            await store_segments(
                pool,
                ticker,
                parsed,
                ctx.job_id,
                company_facts=company_facts,
                amends_accession=original.accession if original else None,
            )
        )
        return parsed

    async def parser_version(filing: Filing) -> int | None:
        value = await pool.fetchval(
            "select parser_version from public.xbrl_filing where ticker = $1 and accession = $2",
            ticker,
            filing.accession,
        )
        return int(value) if value is not None else None

    # A 10-K/A is usually Part III only; it replaces the annual report only when its own
    # instance carries the financial statements (a restatement).
    full_amendments: set[str] = set()
    for amendment in amendments_to_check(filings):
        if await parser_version(amendment) == PARSER_VERSION and not force:
            has_statements = await pool.fetchval(
                "select exists(select 1 from public.financial_statement_fact where ticker = $1"
                " and accession = $2 and line_item in ('revenue', 'net_income', 'total_assets')"
                " and statement <> 'segment')",
                ticker,
                amendment.accession,
            )
        else:
            try:
                parsed_amendment = await parse_and_store(amendment)
            except (EdgarError, XbrlError):
                continue  # an amendment without an instance is not a restatement
            has_statements = has_full_statements(parsed_amendment.instance)
        if has_statements:
            full_amendments.add(amendment.accession)
    annual, quarter = latest_periodic(filings, full_amendments)
    if peer and (annual is None or not annual.form.startswith("10-K")):
        detail["skipped"] = "a peer without a 10-K: not covered"
        return
    targets = [f for f in (annual, quarter if settings.quarterly else None) if f is not None]
    if annual is not None and is_amendment(annual.form):
        original = amended_filing(filings, annual)
        if original is not None:
            targets.append(original)  # keeps the lineage a restatement points at
    detail["filings"] = [f"{f.form} {f.accession} ({f.filing_date})" for f in targets]
    if not targets:
        detail["skipped"] = "no 10-K, 10-Q or 20-F on EDGAR"
    for filing in targets:
        if filing.accession in parsed_now:
            continue
        if await parser_version(filing) == PARSER_VERSION and not force:
            continue
        await parse_and_store(filing)
    detail["parsed"] = parsed_detail
    detail["unchanged"] = [f.accession for f in targets if f.accession not in parsed_now]
    if full_amendments:
        detail["full_amendments"] = sorted(full_amendments)

    # Shares outstanding.
    latest = max(targets, key=lambda f: (f.filing_date, f.accession)) if targets else None
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
        # Confirmed peers not held: their statements make the peer comparison (see
        # steward.company.peer_coverage).
        peers = [] if only else [peer_key(s) for s in await uncovered_peers(pool)]
        step.detail["tickers"] = tickers
        step.detail["peers"] = peers
        if not settings.enabled:
            step.detail["skipped"] = "disabled in the Jobs config (xbrl.enabled)"
            return
    if edgar is None:
        raise JobFailed("SEC_USER_AGENT is not set: the SEC requires a contact User-Agent")
    failed: list[str] = []
    held = set(tickers)
    for ticker in [*tickers, *peers]:
        async with ctx.step(f"xbrl:{ticker}") as step:
            profile = config.for_ticker(ticker) or HoldingProfile(ticker=ticker)
            try:
                await collect_ticker(
                    ctx,
                    edgar,
                    ticker,
                    profile,
                    settings,
                    step.detail,
                    peer=ticker not in held and peer_symbol(ticker) is not None,
                )
            except (
                EdgarError,
                XbrlError,
                httpx.HTTPError,
                ValueError,
                asyncpg.PostgresError,
            ) as exc:
                # Keep what is stored; the next run tries again.
                step.detail["error"] = f"{type(exc).__name__}: {exc}"[:500]
                failed.append(ticker)
    async with ctx.step("summary") as step:
        step.detail.update(
            holdings=len(tickers),
            peers=len(peers),
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
