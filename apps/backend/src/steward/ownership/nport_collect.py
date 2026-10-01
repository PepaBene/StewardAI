"""`nport_holdings`: SEC N-PORT fund holdings for US-registered funds the portfolio holds.

Scope (deliberately narrow):

- Gap funds only: portfolio instruments typed ETF/ETP/FUND with a US ISIN and no issuer-published
  look-through snapshot (`etf_snapshot`). UCITS funds (IE/LU ISINs) do not file N-PORT.
- Identity first: a held fund is tied to an N-PORT series through SEC's own ticker->series/class
  file (`company_tickers_mf.json`), kept in `nport_fund_map`. A filing is stored as readable
  (`reconcile_status = confirmed`) only when its series id equals the mapped series, the mapped
  class id appears in the filing's class list, and the report date on the filing agrees with
  EDGAR's own report date and precedes the filing date.
- Fund positions are not beneficial ownership and carry a ~60 day public lag; they are never
  added to 13F, 13D/G or issuer-published fund holdings.
- Issuer mapping uses identifiers only (ISIN, LEI, OpenFIGI/ISIN-derived CUSIP). A name never
  creates a mapping; conflicting identifiers leave the issuer NULL.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.jobs.runner import JobContext, JobFailed
from steward.settings import get_settings
from steward.sources.documents import link_public_document
from steward.sources.edgar import EdgarClient, EdgarError, Filing
from steward.sources.form13f import cusip_of_isin
from steward.sources.nport import NportError, NportHolding, NportReport, parse_nport

MF_TICKERS_URL = "https://www.sec.gov/files/company_tickers_mf.json"
FUND_TYPES = ("ETP", "ETF", "FUND")
NPORT_FORMS = ("NPORT-P", "NPORT-P/A")
# A stored report this recent needs no new scan (monthly/quarterly cadence plus the 60 day lag).
FRESH_DAYS = 100
LOOKBACK_DAYS = 200
MAX_SCAN_PER_FUND = 60


@dataclass(frozen=True, slots=True)
class FundMap:
    ticker: str
    registrant_cik: str
    series_id: str
    class_id: str | None


async def gap_funds(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Held US-ISIN funds without an issuer-published holdings snapshot."""
    return list(
        await pool.fetch(
            """select i.t212_ticker, i.short_name, i.isin, i.name
               from public.position_snapshot p
               join public.instrument i on i.t212_ticker = p.ticker
               where p.snapshot_date = (select max(snapshot_date) from public.account_snapshot)
                 and upper(coalesce(i.security_type, '')) = any($1::text[])
                 and i.isin like 'US%'
                 and not exists (select 1 from public.etf_snapshot s where s.fund_isin = i.isin)
               group by i.t212_ticker, i.short_name, i.isin, i.name
               order by i.t212_ticker""",
            list(FUND_TYPES),
        )
    )


def provider_symbol(row: asyncpg.Record) -> str:
    """The exchange symbol of a Trading212 ticker such as `SPY_US_EQ` or `VOOd_EQ`."""
    return str(row["short_name"] or row["t212_ticker"].split("_", 1)[0]).upper()


def parse_mf_tickers(raw: bytes) -> dict[str, tuple[str, str, str]]:
    """symbol -> (registrant cik, series id, class id) from SEC `company_tickers_mf.json`."""
    data = json.loads(raw)
    fields = data.get("fields") or []
    try:
        ix = {name: fields.index(name) for name in ("cik", "seriesId", "classId", "symbol")}
    except ValueError as exc:
        raise EdgarError("company_tickers_mf.json changed shape") from exc
    out: dict[str, tuple[str, str, str]] = {}
    for row in data.get("data") or []:
        symbol = str(row[ix["symbol"]] or "").upper()
        if symbol:
            out[symbol] = (
                str(int(row[ix["cik"]])).zfill(10),
                str(row[ix["seriesId"]]),
                str(row[ix["classId"]]),
            )
    return out


async def refresh_fund_map(
    pool: asyncpg.Pool, edgar: EdgarClient, funds: list[asyncpg.Record]
) -> dict[str, FundMap]:
    """Suggested series mappings from SEC's file; owner decisions (confirmed/rejected) stay."""
    existing = {r["ticker"]: r for r in await pool.fetch("select * from public.nport_fund_map")}
    mapping: dict[str, tuple[str, str, str]] | None = None
    for fund in funds:
        ticker = fund["t212_ticker"]
        if ticker in existing:
            continue
        if mapping is None:
            mapping = parse_mf_tickers(await edgar.document(MF_TICKERS_URL))
        found = mapping.get(provider_symbol(fund))
        if found:
            await pool.execute(
                """insert into public.nport_fund_map
                     (ticker,registrant_cik,series_id,class_id,evidence)
                   values($1,$2,$3,$4,$5) on conflict(ticker) do nothing""",
                ticker,
                *found,
                f"SEC company_tickers_mf.json symbol {provider_symbol(fund)}",
            )
    return {
        r["ticker"]: FundMap(r["ticker"], r["registrant_cik"], r["series_id"], r["class_id"])
        for r in await pool.fetch(
            "select * from public.nport_fund_map where status <> 'rejected' and ticker = any($1)",
            [f["t212_ticker"] for f in funds],
        )
    }


def reconcile(report: NportReport, filing: Filing, fund: FundMap) -> tuple[str, str | None]:
    """('confirmed'|'unmatched'|'date_mismatch', detail) for a parsed filing against a fund."""
    if report.series_id != fund.series_id:
        return "unmatched", f"series {report.series_id} != mapped {fund.series_id}"
    if fund.class_id and report.class_ids and fund.class_id not in report.class_ids:
        return "unmatched", f"class {fund.class_id} not in the filing's classes"
    if filing.report_date and filing.report_date != report.report_period_end:
        return (
            "date_mismatch",
            f"EDGAR report date {filing.report_date} != filed {report.report_period_end}",
        )
    if report.report_period_end > filing.filing_date:
        return "date_mismatch", "report date is after the filing date"
    return "confirmed", None


async def map_holdings(
    pool: asyncpg.Pool, holdings: list[NportHolding]
) -> dict[int, dict[str, Any]]:
    """Canonical issuer/security/portfolio mapping per holding row, from identifiers only."""
    isins = sorted({h.isin for h in holdings if h.isin})
    leis = sorted({h.issuer_lei for h in holdings if h.issuer_lei})
    cusips = sorted({h.cusip for h in holdings if h.cusip})
    by_isin = {
        r["isin"]: r
        for r in await pool.fetch(
            "select isin, id, issuer_id from public.security where isin = any($1::text[])", isins
        )
    }
    by_lei = {
        r["lei"]: r["id"]
        for r in await pool.fetch(
            "select lei, id from public.issuer where lei = any($1::text[])", leis
        )
    }
    held_isin = {
        r["isin"]: r["t212_ticker"]
        for r in await pool.fetch(
            "select isin, t212_ticker from public.instrument where isin = any($1::text[])", isins
        )
    }
    cusip_ticker: dict[str, str] = {}
    for r in await pool.fetch("select t212_ticker, isin from public.instrument"):
        cusip = cusip_of_isin(r["isin"])
        if cusip in cusips:
            cusip_ticker.setdefault(str(cusip), r["t212_ticker"])
    figi_cusip = {
        r["cusip"].upper(): r["ticker"]
        for r in await pool.fetch(
            "select cusip, ticker from public.instrument_identifier where upper(cusip) = any($1)",
            cusips,
        )
    }
    link = {
        r["ticker"]: r
        for r in await pool.fetch(
            """select l.ticker, l.security_id, s.issuer_id from public.instrument_security_link l
               join public.security s on s.id = l.security_id where l.status = 'confirmed'"""
        )
    }
    out: dict[int, dict[str, Any]] = {}
    for h in holdings:
        candidates: list[tuple[str, int | None, int | None]] = []  # basis, issuer, security
        held: str | None = held_isin.get(h.isin) if h.isin else None
        if h.isin and h.isin in by_isin:
            row = by_isin[h.isin]
            candidates.append(("isin", row["issuer_id"], row["id"]))
        if h.issuer_lei and h.issuer_lei in by_lei:
            candidates.append(("lei", by_lei[h.issuer_lei], None))
        if h.cusip:
            for basis, table in (("cusip_isin", cusip_ticker), ("cusip_figi", figi_cusip)):
                ticker = table.get(h.cusip)
                if ticker:
                    held = held or ticker
                    if ticker in link:
                        candidates.append(
                            (basis, link[ticker]["issuer_id"], link[ticker]["security_id"])
                        )
        issuers = {c[1] for c in candidates if c[1] is not None}
        securities = {c[2] for c in candidates if c[2] is not None}
        # Conflicting identifier evidence is never resolved by guessing.
        if len(issuers) > 1 or len(securities) > 1:
            out[h.row_index] = {"held": held, "issuer": None, "security": None, "basis": None}
            continue
        pick = next((c for c in candidates if c[1] is not None), None)
        out[h.row_index] = {
            "held": held,
            "issuer": pick[1] if pick else None,
            "security": next(iter(securities), None),
            "basis": pick[0] if pick else None,
        }
    return out


async def store_report(
    pool: asyncpg.Pool,
    report: NportReport,
    filing: Filing,
    content: bytes,
    source_url: str,
    fund: FundMap,
    status: str,
    detail: str | None,
) -> int:
    mapped = await map_holdings(pool, report.holdings) if status == "confirmed" else {}
    known_at = dt.datetime.now(dt.UTC)
    async with pool.acquire() as conn, conn.transaction():
        amends = await conn.fetchval(
            """select document_version_id from public.nport_filing
               where series_id=$1 and report_period_end=$2 and accession <> $3
                 and filed <= $4 order by filed desc, accession desc limit 1""",
            report.series_id,
            report.report_period_end,
            filing.accession,
            filing.filing_date,
        )
        version_id = await link_public_document(
            conn,
            regulator="sec",
            external_id=filing.accession,
            stable_url=source_url,
            document_type=filing.form,
            content=content,
            known_at=known_at,
            filed_at=filing.filing_date,
            accepted_at=filing.accepted_at,
            published_at=filing.filing_date,
            amends_version_id=amends if filing.form.endswith("/A") else None,
            domain_table="nport_filing",
            record_key=filing.accession,
            quote=report.series_name,
        )
        await conn.execute(
            """insert into public.nport_filing
               (accession,form,registrant_cik,registrant_name,registrant_lei,series_id,
                series_name,series_lei,class_ids,fiscal_year_end,report_period_end,filed,is_final,
                total_assets,total_liabilities,net_assets,risk_metrics,class_returns,flows,
                holdings_count,reconcile_status,reconcile_detail,matched_ticker,fund_isin,
                source_url,document_version_id)
               values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::jsonb,
                      $18::jsonb,$19::jsonb,$20,$21,$22,$23,
                      (select isin from public.instrument where t212_ticker=$23),$24,$25)
               on conflict(accession) do update set
                 reconcile_status=excluded.reconcile_status,
                 reconcile_detail=excluded.reconcile_detail,
                 matched_ticker=excluded.matched_ticker, holdings_count=excluded.holdings_count,
                 document_version_id=excluded.document_version_id, loaded_at=now()""",
            filing.accession,
            filing.form,
            report.registrant_cik or fund.registrant_cik,
            report.registrant_name,
            report.registrant_lei,
            report.series_id,
            report.series_name,
            report.series_lei,
            list(report.class_ids),
            report.fiscal_year_end,
            report.report_period_end,
            filing.filing_date,
            report.is_final,
            report.total_assets,
            report.total_liabilities,
            report.net_assets,
            json.dumps(report.risk_metrics),
            json.dumps(report.class_returns),
            json.dumps(report.flows),
            len(report.holdings) if status == "confirmed" else 0,
            status,
            detail,
            fund.ticker if status == "confirmed" else None,
            source_url,
            version_id,
        )
        await conn.execute("delete from public.nport_holding where accession=$1", filing.accession)
        if status == "confirmed":
            await conn.executemany(
                """insert into public.nport_holding
                   (accession,row_index,issuer_name,issuer_lei,title,cusip,isin,other_id,balance,
                    units,currency,value_usd,pct_value,payoff_profile,asset_cat,issuer_cat,country,
                    fair_value_level,is_restricted,is_derivative,derivative_cat,issuer_id,
                    security_id,held_ticker,map_basis)
                   values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
                          $20,$21,$22,$23,$24,$25)""",
                [
                    (
                        filing.accession,
                        h.row_index,
                        h.issuer_name,
                        h.issuer_lei,
                        h.title,
                        h.cusip,
                        h.isin,
                        h.other_id,
                        h.balance,
                        h.units,
                        h.currency,
                        h.value_usd,
                        h.pct_value,
                        h.payoff_profile,
                        h.asset_cat,
                        h.issuer_cat,
                        h.country,
                        h.fair_value_level,
                        h.is_restricted,
                        h.is_derivative,
                        h.derivative_cat,
                        mapped[h.row_index]["issuer"],
                        mapped[h.row_index]["security"],
                        mapped[h.row_index]["held"],
                        mapped[h.row_index]["basis"],
                    )
                    for h in report.holdings
                ],
            )
            await conn.executemany(
                """insert into public.source_record_evidence
                     (domain_table,record_key,document_version_id,known_at)
                   values('nport_holding',$1,$2,$3) on conflict do nothing""",
                [
                    (f"{filing.accession}:{h.row_index}", version_id, known_at)
                    for h in report.holdings
                ],
            )
    return len(report.holdings)


async def _document(edgar: EdgarClient, filing: Filing) -> tuple[bytes, str]:
    for name in ("primary_doc.xml", filing.primary_document):
        if not name.lower().endswith(".xml") or "/" in name:
            continue
        url = filing.url(name)
        try:
            return await edgar.document(url, max_bytes=64 * 1024 * 1024), url
        except EdgarError:
            continue
    raise EdgarError(f"No N-PORT XML found for {filing.accession}")


async def collect_fund(
    ctx: JobContext, edgar: EdgarClient, fund: FundMap, today: dt.date
) -> dict[str, Any]:
    latest = await ctx.pool.fetchval(
        """select max(report_period_end) from public.nport_filing
           where series_id=$1 and reconcile_status='confirmed'""",
        fund.series_id,
    )
    if latest is not None and (today - latest).days <= FRESH_DAYS:
        return {"skipped": "stored report is current", "latest": latest.isoformat()}
    since = today - dt.timedelta(days=LOOKBACK_DAYS)
    candidates = sorted(
        (f for f in await edgar.filings(fund.registrant_cik, since=since) if f.form in NPORT_FORMS),
        key=lambda f: (f.report_date or f.filing_date, f.filing_date, f.accession),
        reverse=True,
    )
    stats: dict[str, Any] = {"candidates": len(candidates), "scanned": 0, "stored": 0}
    found_period: dt.date | None = None
    for filing in candidates[:MAX_SCAN_PER_FUND]:
        if found_period and filing.report_date and filing.report_date < found_period:
            break  # older periods are not needed once the latest one is stored
        if await ctx.pool.fetchval(
            "select exists(select 1 from public.nport_filing where accession=$1)",
            filing.accession,
        ):
            found_period = found_period or filing.report_date
            continue
        stats["scanned"] += 1
        try:
            content, url = await _document(edgar, filing)
            report = parse_nport(content)
        except (EdgarError, NportError, httpx.HTTPError) as exc:
            stats.setdefault("errors", []).append(f"{filing.accession}: {exc}")
            continue
        if report.series_id != fund.series_id:
            continue  # another series of the same registrant: not this fund
        status, detail = reconcile(report, filing, fund)
        await store_report(ctx.pool, report, filing, content, url, fund, status, detail)
        stats["stored"] += 1
        if status == "confirmed":
            found_period = report.report_period_end
        else:
            stats.setdefault("unreconciled", []).append(f"{filing.accession}: {detail}")
    return stats


async def run_nport(ctx: JobContext, edgar: EdgarClient, today: dt.date | None = None) -> None:
    today = today or dt.datetime.now(dt.UTC).date()
    async with ctx.step("funds") as step:
        funds = await gap_funds(ctx.pool)
        maps = await refresh_fund_map(ctx.pool, edgar, funds)
        step.detail.update(
            gap_funds=len(funds),
            mapped=len(maps),
            unmapped=sorted(f["t212_ticker"] for f in funds if f["t212_ticker"] not in maps),
        )
    for fund in maps.values():
        async with ctx.step(f"nport:{fund.ticker}") as step:
            step.detail.update(await collect_fund(ctx, edgar, fund, today))


async def nport_holdings_handler(ctx: JobContext) -> None:
    agent = get_settings().sec_user_agent
    if not agent:
        raise JobFailed("SEC_USER_AGENT is not set: the SEC requires a contact User-Agent")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as http:
        await run_nport(ctx, EdgarClient(http, f"{agent} StewardAI/{__version__}"))
