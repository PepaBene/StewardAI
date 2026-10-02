"""`valuation_refresh` (daily by default; params {"ticker"?, "force"?}): the fundamentals
behind P/E, P/B and dividend yield, and the ETF providers' fund-level figures.

Per holding (ETFs excepted, they are valued at fund level):

- SEC: the issuer's latest periodic report (10-Q, 10-K, 20-F, 40-F) is looked up in its
  filing list. When it differs from the one the stored figures were built on, or those are
  older than `full_refresh_days`, companyfacts is read again. When companyfacts carries no
  EPS from that latest report (it can lag, or miss a filing's financial statements as it
  did TSMC's FY2025 20-F), the report's own XBRL instance is parsed instead. The share
  count for book value per share may come from the SEC XBRL job's `shares_outstanding`
  (class-summed cover-page figure) when it is the one closest to the balance-sheet date.
- ESEF (filings.xbrl.org), for issuers with an LEI: the latest filing within
  `esef_max_age_months`, plus the latest annual report when that is an interim one (so a
  half year can be rolled into a trailing year). Novo Nordisk's quarterly reports make its
  EPS fresher than its annual-only 20-F XBRL; ASML's ESEF gives its IFRS figures next to
  the US GAAP 20-F.
- Finnhub `/stock/metric` as a tier-2 cross-check, only with FINNHUB_API_KEY.

Figures and the verbatim source facts go to `fundamentals`; fund figures to
`fund_valuation`. Any one source failing is recorded on its step and in the
`valuation_status` sync state (shown on the holding page), never a failed job.
"""

import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import FundValuationSource, JobsConfig, ValuationSettings
from steward.config.store import load_active
from steward.earnings.check import resolve_cik
from steward.fundamentals.statements import (
    FilingMeta,
    apply_reconciliation,
    esef_statement_facts,
    filing_evidence,
    internal_checks,
    resolve_issuer_id,
    store_statement_facts,
)
from steward.jobs.runner import JobContext
from steward.reference.identity import holding_tickers
from steward.reference.prices import symbols_for
from steward.settings import get_settings
from steward.sources.alphavantage import AlphaVantageError, company_overview
from steward.sources.base import AccessState
from steward.sources.edgar import (
    EdgarClient,
    EdgarError,
    Filing,
    latest_periodic,
    xbrl_documents,
)
from steward.sources.esef import (
    EsefFact,
    EsefFiling,
    amended_esef,
    esef_document,
    esef_filings,
    parse_xbrl_json,
)
from steward.sources.finnhub import FinnhubError, basic_financials
from steward.sources.fund_characteristics import (
    FundDataError,
    FundFigures,
    ishares_characteristics,
    vanguard_characteristics,
)
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.sources.xbrl import XbrlError, parse_instance, plain_facts
from steward.valuation.common import (
    fund_for_ticker,
    fx_currencies,
    fx_source,
    instrument_info,
    price_currency,
)
from steward.valuation.fundamentals import (
    ALL_CONCEPTS,
    EPS_BASIC_CONCEPTS,
    EPS_CONCEPTS,
    SHARES_CONCEPTS,
    Fact,
    SourceFigures,
    companyfacts_facts,
    companyfacts_subset,
    compute,
)

logger = logging.getLogger(__name__)

STATUS_KEY = "valuation_status"
ALPHA_VANTAGE_QUOTA_KEY = "alphavantage_quota"
ALPHA_VANTAGE_DAILY_LIMIT = 25
FINNHUB_FIELDS = (
    "peTTM",
    "peBasicExclExtraTTM",
    "pbQuarterly",
    "pbAnnual",
    "epsTTM",
    "epsBasicExclExtraItemsTTM",
    "bookValuePerShareQuarterly",
    "currentDividendYieldTTM",
    "dividendPerShareTTM",
)


@dataclass
class ValuationDeps:
    http: httpx.AsyncClient
    edgar: EdgarClient | None
    finnhub_key: str | None = None
    alpha_vantage_key: str | None = None
    today: dt.date = field(default_factory=lambda: dt.datetime.now(dt.UTC).date())


@dataclass
class TickerContext:
    ticker: str
    profile: HoldingProfile
    preferred: list[str]
    lei: str | None
    stored: dict[str, asyncpg.Record]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


async def store_fundamentals(
    pool: asyncpg.Pool,
    ticker: str,
    source: str,
    figures: SourceFigures | dict[str, Any],
    raw: dict[str, Any],
    document_ref: str | None,
    document_url: str,
) -> None:
    body = figures.model_dump(mode="json") if isinstance(figures, SourceFigures) else figures
    eps = body.get("eps_ttm") or {}
    await pool.execute(
        "insert into public.fundamentals (ticker, source, basis, currency, document_ref,"
        " document_url, period_end, figures, raw) values ($1, $2, $3, $4, $5, $6, $7,"
        " $8::jsonb, $9::jsonb) on conflict (ticker, source) do update set"
        " basis = excluded.basis, currency = excluded.currency,"
        " document_ref = excluded.document_ref, document_url = excluded.document_url,"
        " period_end = excluded.period_end, figures = excluded.figures, raw = excluded.raw,"
        " fetched_at = now(), last_attempt_at = now(), last_error = null",
        ticker,
        source,
        body.get("basis"),
        eps.get("currency"),
        document_ref,
        document_url,
        dt.date.fromisoformat(eps["period_end"]) if eps.get("period_end") else None,
        json.dumps(body, default=str),
        json.dumps(raw, default=str),
    )


async def record_failure(pool: asyncpg.Pool, ticker: str, source: str, error: str) -> None:
    await pool.execute(
        "update public.fundamentals set last_error = $3, last_attempt_at = now()"
        " where ticker = $1 and source = $2",
        ticker,
        source,
        error[:500],
    )


async def store_fund(pool: asyncpg.Pool, key: str, figures: FundFigures) -> None:
    await pool.execute(
        "insert into public.fund_valuation (fund_key, provider, as_of, pe, pb, index_pe,"
        " index_pb, dividend_yield, dividend_yield_kind, methodology, source_url, raw)"
        " values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)"
        " on conflict (fund_key, as_of) do update set provider = excluded.provider,"
        " pe = excluded.pe, pb = excluded.pb, index_pe = excluded.index_pe,"
        " index_pb = excluded.index_pb, dividend_yield = excluded.dividend_yield,"
        " dividend_yield_kind = excluded.dividend_yield_kind,"
        " methodology = excluded.methodology, source_url = excluded.source_url,"
        " raw = excluded.raw, fetched_at = now()",
        key,
        figures.provider,
        figures.as_of,
        figures.pe,
        figures.pb,
        figures.index_pe,
        figures.index_pb,
        figures.dividend_yield,
        figures.dividend_yield_kind,
        figures.methodology,
        figures.source_url,
        json.dumps(figures.raw, default=str),
    )


async def _stored(pool: asyncpg.Pool, ticker: str) -> dict[str, asyncpg.Record]:
    rows = await pool.fetch(
        "select source, document_ref, fetched_at, figures, last_attempt_at, last_error"
        " from public.fundamentals where ticker = $1",
        ticker,
    )
    return {r["source"]: r for r in rows}


async def _load_status(pool: asyncpg.Pool) -> dict[str, Any]:
    raw = await pool.fetchval("select value from public.sync_state where key = $1", STATUS_KEY)
    return dict(json.loads(raw)) if raw else {}


async def _save_status(pool: asyncpg.Pool, status: dict[str, Any]) -> None:
    await pool.execute(
        "insert into public.sync_state (key, value) values ($1, $2::jsonb)"
        " on conflict (key) do update set value = excluded.value",
        STATUS_KEY,
        json.dumps(status, default=str),
    )


# ---------------------------------------------------------------------------
# Funds
# ---------------------------------------------------------------------------


async def fetch_fund(http: httpx.AsyncClient, fund: FundValuationSource) -> FundFigures:
    if fund.provider == "ishares":
        return await ishares_characteristics(http, str(fund.url))
    assert fund.port_id is not None  # validated by the config
    return await vanguard_characteristics(http, str(fund.url), fund.port_id, fund.consumer_id)


async def refresh_funds(
    ctx: JobContext, deps: ValuationDeps, settings: ValuationSettings, status: dict[str, Any]
) -> None:
    funds_status: dict[str, Any] = status.setdefault("funds", {})
    for fund in settings.funds:
        async with ctx.step(f"fund:{fund.key}") as step:
            step.detail["provider"] = fund.provider
            try:
                figures = await fetch_fund(deps.http, fund)
            except (FundDataError, httpx.HTTPError) as exc:
                step.detail["error"] = str(exc)[:300]
                funds_status[fund.key] = {
                    "ok": False,
                    "error": str(exc)[:300],
                    "checked_at": dt.datetime.now(dt.UTC),
                }
                continue
            await store_fund(ctx.pool, fund.key, figures)
            step.detail.update(
                as_of=figures.as_of.isoformat(),
                pe=float(figures.pe) if figures.pe is not None else None,
                pb=float(figures.pb) if figures.pb is not None else None,
            )
            funds_status[fund.key] = {
                "ok": True,
                "error": None,
                "checked_at": dt.datetime.now(dt.UTC),
            }


# ---------------------------------------------------------------------------
# SEC
# ---------------------------------------------------------------------------


def latest_report(filings: list[Filing]) -> Filing | None:
    """The latest periodic report: the 10-Q filed after the latest annual report, else that
    annual report (10-K, 20-F, 40-F)."""
    annual, quarter = latest_periodic(filings)
    return quarter or annual


# shares_outstanding.method (the SEC XBRL job) -> the concept the figure was read from.
TABLE_SHARE_CONCEPTS = {
    "dei_companyfacts": "dei:EntityCommonStockSharesOutstanding",
    "dei_instance": "dei:EntityCommonStockSharesOutstanding",
    "dei_instance_classes": "dei:EntityCommonStockSharesOutstanding",
    "us_gaap_common_stock": "us-gaap:CommonStockSharesOutstanding",
    "ifrs_number_of_shares": "ifrs-full:NumberOfSharesOutstanding",
}


async def table_shares(pool: asyncpg.Pool, ticker: str) -> list[Fact]:
    """The SEC XBRL job's latest shares-outstanding figure (`shares_outstanding`) as a share
    fact for book value per share. It sums share classes from the filing instance, which
    companyfacts cannot (Alphabet, Novo Nordisk). Not used when the holding profile narrows
    the counted classes (that figure is not all shares) or when it was entered manually.
    `_shares_for` then picks whichever count is closest to the balance-sheet date."""
    row = await pool.fetchrow(
        "select shares, as_of, method, classes, accession, form, filed, source_url"
        " from public.shares_outstanding where ticker = $1"
        " order by as_of desc, fetched_at desc limit 1",
        ticker,
    )
    if row is None or row["method"] not in TABLE_SHARE_CONCEPTS:
        return []
    classes = json.loads(row["classes"]) if isinstance(row["classes"], str) else row["classes"]
    if any(c.get("counted") is False for c in classes or []):
        return []
    return [
        Fact(
            concept=TABLE_SHARE_CONCEPTS[row["method"]],
            start=None,
            end=row["as_of"],
            value=Decimal(row["shares"]),
            unit="shares",
            source="sec_xbrl",
            ref=row["accession"] or "",
            filed=row["filed"],
            form=row["form"],
            url=row["source_url"],
        )
    ]


def _due(
    stored: asyncpg.Record | None,
    latest_ref: str | None,
    settings: ValuationSettings,
    today: dt.date,
    force: bool,
) -> str | None:
    """Why the source must be read again, or None when the stored figures stand."""
    if force:
        return "forced"
    if stored is None:
        return "never read"
    if latest_ref and stored["document_ref"] != latest_ref:
        return f"new filing {latest_ref}"
    age = today - stored["fetched_at"].date()
    if age.days >= settings.full_refresh_days:
        return f"last read {age.days} days ago"
    return None


async def refresh_sec(
    tc: TickerContext,
    deps: ValuationDeps,
    settings: ValuationSettings,
    cik: str,
    force: bool,
    detail: dict[str, Any],
    pool: asyncpg.Pool,
) -> None:
    assert deps.edgar is not None
    edgar = deps.edgar
    latest = latest_report(await edgar.filings(cik))
    if latest is None:
        detail["sec"] = "no periodic reports (10-Q, 10-K, 20-F) on EDGAR"
        return
    detail["latest_filing"] = f"{latest.form} {latest.accession} ({latest.filing_date})"
    reason = _due(tc.stored.get("sec"), latest.accession, settings, deps.today, force)
    if reason is None:
        detail["sec"] = "unchanged"
        return
    detail["sec_reason"] = reason
    company_facts = await edgar.company_facts(cik)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    facts = companyfacts_facts(company_facts, url=url)
    raw: dict[str, Any] = {
        "companyfacts": companyfacts_subset(company_facts, ALL_CONCEPTS),
        "latest_filing": {
            "form": latest.form,
            "accession": latest.accession,
            "filing_date": latest.filing_date.isoformat(),
            "report_date": latest.report_date.isoformat() if latest.report_date else None,
            "url": latest.index_url,
        },
    }
    eps_refs = {f.ref for f in facts if f.concept in (*EPS_CONCEPTS, *EPS_BASIC_CONCEPTS)}
    if latest.accession not in eps_refs:
        # companyfacts has nothing (or only the cover page) from the latest report.
        try:
            instance_name, _labels = xbrl_documents(latest, await edgar.filing_files(latest))
            if instance_name is None:
                raise XbrlError(f"{latest.form} {latest.accession} has no XBRL instance")
            instance_url = latest.url(instance_name)
            parsed = plain_facts(parse_instance(await edgar.document(instance_url), ALL_CONCEPTS))
        except (EdgarError, XbrlError, httpx.HTTPError) as exc:
            detail["instance_error"] = str(exc)[:300]
        else:
            filed = latest.filing_date
            facts += [
                Fact(
                    concept=x.concept,
                    start=x.start,
                    end=x.end,
                    value=x.value,
                    unit=x.unit,
                    source="sec_instance",
                    ref=latest.accession,
                    filed=filed,
                    form=latest.form,
                    url=instance_url,
                )
                for x in parsed
            ]
            raw["instance"] = {
                "url": instance_url,
                "facts": [{"contextRef": x.fact.context.id, "xml": x.fact.xml} for x in parsed],
            }
            detail["instance_fallback"] = (
                f"companyfacts lacks {latest.form} {latest.accession}: read {len(parsed)} facts "
                "from its XBRL instance"
            )
    shares = await table_shares(pool, tc.ticker)
    figures = compute("sec", [*facts, *shares], tc.preferred, deps.today)
    if figures is None:
        detail["sec"] = "no EPS in the SEC filings"
        return
    if "instance" in raw:
        figures.notes.append(
            f"SEC companyfacts lags the latest {latest.form} ({latest.accession}); its figures "
            "were read from the filing's XBRL instance."
        )
    await store_fundamentals(
        pool, tc.ticker, "sec", figures, raw, latest.accession, latest.index_url
    )
    detail["sec"] = _summary(figures)


def _summary(figures: SourceFigures) -> dict[str, Any]:
    eps = figures.eps_ttm
    return {
        "basis": figures.basis,
        "eps_ttm": eps.value if eps else None,
        "currency": eps.currency if eps else None,
        "period_end": eps.period_end.isoformat() if eps else None,
        "method": eps.method if eps else None,
    }


def _alpha_vantage_needed(stored: dict[str, asyncpg.Record]) -> bool:
    """Request a single overview only when filings cannot supply at least one key ratio."""
    sources = []
    for name in ("sec", "esef"):
        row = stored.get(name)
        if row is not None:
            try:
                body = (
                    json.loads(row["figures"])
                    if isinstance(row["figures"], str)
                    else row["figures"]
                )
                sources.append(SourceFigures.model_validate(body))
            except (ValueError, TypeError):
                continue
    if not sources:
        return True

    def has(source: SourceFigures, name: str) -> bool:
        if name == "eps_steps":
            latest = max(source.eps_steps, key=lambda step: step.period_end, default=None)
            return bool(
                latest
                and latest.eps > 0
                and any(
                    previous.eps > 0
                    and 300 <= (latest.period_end - previous.period_end).days <= 430
                    for previous in source.eps_steps
                    if previous.period_end < latest.period_end
                )
            )
        value = getattr(source, name, None)
        return value is not None and getattr(value, "value", None) is not None

    needed = (
        ("eps_ttm",),
        ("book_value_per_share",),
        ("dividend_per_share",),
        ("revenue_ttm", "shares_outstanding"),
        ("revenue_ttm", "gross_profit_ttm"),
        ("revenue_ttm", "operating_income_ttm"),
        ("revenue_ttm", "net_income_ttm"),
        ("net_income_ttm", "equity_value", "equity_previous_value"),
        ("eps_steps",),
    )
    return any(
        not any(all(has(source, key) for key in group) for source in sources) for group in needed
    )


# ---------------------------------------------------------------------------
# ESEF
# ---------------------------------------------------------------------------


def pick_esef(
    filings: list[EsefFiling], fiscal_year_end_month: int, today: dt.date, max_age_months: int
) -> list[EsefFiling]:
    """The latest recent filing, plus the latest annual one when the latest is interim."""
    oldest = today - dt.timedelta(days=int(max_age_months * 30.44))
    recent = [f for f in filings if f.period_end >= oldest]
    if not recent:
        return []
    latest = recent[0]
    picked = [latest]
    if latest.period_end.month != fiscal_year_end_month:
        annual = next(
            (
                f
                for f in filings
                if f.period_end.month == fiscal_year_end_month and f.period_end < latest.period_end
            ),
            None,
        )
        if annual is not None:
            picked.append(annual)
    return picked


def _stored_shares(stored: asyncpg.Record | None) -> list[Fact]:
    """The SEC figures' share count, for an ESEF interim report that tags none."""
    if stored is None:
        return []
    body = (
        json.loads(stored["figures"]) if isinstance(stored["figures"], str) else stored["figures"]
    )
    bvps = body.get("book_value_per_share") or {}
    return [
        Fact(
            concept=i["concept"],
            start=None,
            end=dt.date.fromisoformat(i["end"]),
            value=Decimal(str(i["value"])),
            unit=i["unit"],
            source=i["source"],
            ref=i["ref"],
            filed=dt.date.fromisoformat(i["filed"]) if i.get("filed") else None,
            form=i.get("form"),
            url=i.get("url"),
        )
        for i in bvps.get("inputs") or []
        if i.get("unit") == "shares"
    ]


async def refresh_esef(
    tc: TickerContext,
    deps: ValuationDeps,
    settings: ValuationSettings,
    force: bool,
    detail: dict[str, Any],
    pool: asyncpg.Pool,
) -> None:
    assert tc.lei is not None
    filings = await esef_filings(deps.http, tc.lei)
    if not filings:
        detail["esef"] = "no ESEF filings on filings.xbrl.org"
        return
    picked = pick_esef(
        filings, tc.profile.fiscal_year_end_month, deps.today, settings.esef_max_age_months
    )
    if not picked:
        detail["esef"] = (
            f"latest ESEF filing on filings.xbrl.org is for {filings[0].period_end}, older than "
            f"{settings.esef_max_age_months} months: not used"
        )
        return
    latest = picked[0]
    reason = _due(tc.stored.get("esef"), latest.id, settings, deps.today, force)
    if reason is None:
        has_history = await pool.fetchval(
            """select exists(select 1 from public.financial_statement_fact
               where ticker=$1 and accession=$2 and source='esef')""",
            tc.ticker,
            latest.id,
        )
        if has_history:
            detail["esef"] = "unchanged"
            return
        reason = "populate periodized ESEF statement history"
    facts: list[Fact] = []
    raw: dict[str, Any] = {"filings": [], "facts": []}
    for filing in picked:
        doc = await esef_document(deps.http, filing)
        parsed = parse_xbrl_json(doc, None, include_dimensions=True, include_text_blocks=True)
        filed = filing.added.date() if filing.added else None
        facts += [
            Fact(
                concept=x.concept,
                start=x.start,
                end=x.end,
                value=x.value,
                unit=x.unit,
                source="esef",
                ref=filing.id,
                filed=filed,
                form="ESEF",
                url=filing.report_url,
            )
            for x in parsed
            if x.value is not None and not x.dimensions and x.concept in ALL_CONCEPTS
        ]
        await _store_esef_statement_facts(
            pool,
            tc.ticker,
            filing,
            parsed,
            content_hash=hashlib.sha256(
                json.dumps(doc, sort_keys=True, default=str).encode()
            ).hexdigest(),
            amends=amended_esef(filings, filing),
            year_end_month=(
                tc.profile.fiscal_year_end_month
                if "fiscal_year_end_month" in tc.profile.model_fields_set
                else None
            ),
        )
        raw["filings"].append({**filing.raw, "json_url": filing.json_url})
        raw["facts"] += [{**x.raw, "filing": filing.id} for x in parsed]
    if not any(f.concept in SHARES_CONCEPTS for f in facts):
        facts += await table_shares(pool, tc.ticker) or _stored_shares(tc.stored.get("sec"))
    figures = compute("esef", facts, tc.preferred, deps.today)
    if figures is None:
        detail["esef"] = "no EPS in the ESEF filings"
        return
    await store_fundamentals(
        pool, tc.ticker, "esef", figures, raw, latest.id, latest.report_url or latest.json_url
    )
    detail["esef"] = _summary(figures)


async def _store_esef_statement_facts(
    pool: asyncpg.Pool,
    ticker: str,
    filing: EsefFiling,
    facts: list[EsefFact],
    *,
    content_hash: str,
    amends: EsefFiling | None = None,
    year_end_month: int | None = None,
) -> dict[str, int]:
    """Keep the filing's numeric dimensional facts and cited note blocks point in time.

    `known_at` is the date filings.xbrl.org added the report; when it gives none, the moment
    this filing was first stored (carried over on every later run, never reset)."""
    first_seen = dt.datetime.now(dt.UTC)
    async with pool.acquire() as conn, conn.transaction():
        known_at = filing.added or first_seen
        url = filing.report_url or filing.json_url
        evidence_id = await filing_evidence(
            conn,
            regulator="filings.xbrl.org",
            external_id=filing.id,
            stable_url=url,
            document_type="ESEF",
            content_hash=content_hash,
            filed_at=filing.added,
            accepted_at=None,
            known_at=known_at,
            quote=f"ESEF report {filing.id} for the period ending {filing.period_end}: {ticker}",
            amends_external_id=amends.id if amends else None,
        )
        meta = FilingMeta(
            ticker=ticker,
            accession=filing.id,
            form="ESEF",
            filing_date=(filing.added or first_seen).date(),
            filing_url=url,
            source_url=url,
            accepted_at=None,
            cik=None,
            issuer_id=await resolve_issuer_id(conn, ticker, None),
            evidence_id=evidence_id,
            amends_accession=amends.id if amends else None,
        )
        rows = esef_statement_facts(
            facts,
            meta,
            year_end_month=year_end_month,
            published_at=filing.added,
            known_at=known_at,
        )
        rows = apply_reconciliation(rows, internal_checks(rows))
        return await store_statement_facts(conn, ticker, "esef", filing.id, rows)


# ---------------------------------------------------------------------------
# Finnhub cross-check
# ---------------------------------------------------------------------------


async def refresh_finnhub(
    tc: TickerContext, deps: ValuationDeps, detail: dict[str, Any], pool: asyncpg.Pool
) -> None:
    assert deps.finnhub_key is not None
    symbol = tc.profile.edgar_ticker()
    data = await basic_financials(deps.http, deps.finnhub_key, symbol)
    metric = data.get("metric") or {}
    if not metric:
        detail["finnhub"] = f"no basic financials for {symbol} on the free plan"
        return
    picked = {k: metric.get(k) for k in FINNHUB_FIELDS}
    yield_pct = picked.get("currentDividendYieldTTM")
    body = {
        "source": "finnhub",
        "basis": None,
        "symbol": symbol,
        "pe": picked.get("peTTM") or picked.get("peBasicExclExtraTTM"),
        "pb": picked.get("pbQuarterly") or picked.get("pbAnnual"),
        "eps_ttm": picked.get("epsTTM") or picked.get("epsBasicExclExtraItemsTTM"),
        "book_value_per_share": picked.get("bookValuePerShareQuarterly"),
        "dividend_yield": yield_pct / 100 if isinstance(yield_pct, int | float) else None,
    }
    await store_fundamentals(
        pool,
        tc.ticker,
        "finnhub",
        body,
        {"symbol": data.get("symbol"), "metric": picked},
        symbol,
        f"https://finnhub.io/api/v1/stock/metric?symbol={symbol}&metric=all",
    )
    detail["finnhub"] = {"pe": body["pe"], "pb": body["pb"]}


async def _reserve_alpha_vantage_request(pool: asyncpg.Pool, today: dt.date) -> bool:
    """Reserve against Alpha Vantage's account-wide free 25-request daily quota.

    Valuation jobs are serialized by job type using a database advisory lock, so this
    sync-state counter cannot be incremented concurrently by two valuation runs.
    """
    try:
        await PostgresRequestGovernor(pool).reserve(
            "alpha_vantage",
            "account_free",
            Decimal(1),
            Budget(daily=Decimal(ALPHA_VANTAGE_DAILY_LIMIT)),
            now=dt.datetime.combine(today, dt.time.min, tzinfo=dt.UTC),
        )
    except QuotaExceeded:
        return False
    return True


def _has_overview(stored: asyncpg.Record | None) -> bool:
    """Whether the stored Alpha Vantage row holds an actual Company Overview."""
    if stored is None:
        return False
    figures = stored["figures"]
    if isinstance(figures, str):
        try:
            figures = json.loads(figures)
        except ValueError:
            return False
    return isinstance(figures, dict) and bool(figures.get("Symbol"))


def _transient(exc: Exception) -> bool:
    """A failure that says nothing about the symbol: network, quota, 5xx, premium message."""
    if isinstance(exc, httpx.HTTPError):
        return True
    if isinstance(exc, AlphaVantageError):
        # NOT_COVERED ("Error Message": the provider does not know the symbol) is about the
        # symbol; every other typed state, and any 429 / 5xx, says nothing about it.
        return (
            not exc.provider_charged
            or (exc.state is not None and exc.state is not AccessState.NOT_COVERED)
            or (exc.status_code is not None and exc.status_code >= 429)
        )
    return False


async def _alpha_vantage_failed(
    pool: asyncpg.Pool,
    tc: TickerContext,
    symbol: str,
    error: str,
    detail: dict[str, Any],
    *,
    transient: bool = False,
) -> None:
    """A failed or empty overview never replaces a good one (B31). With good data stored the
    failure is only recorded on the row (the figures and `fetched_at` stay, so the cache keeps
    serving them). With none stored, a transient failure writes nothing (the next run asks
    again) and a failure about the symbol itself (unknown to Alpha Vantage) is cached, so the
    symbol is not requested again every day."""
    detail["alphavantage_error"] = error[:300]
    if _has_overview(tc.stored.get("alphavantage")):
        await record_failure(pool, tc.ticker, "alphavantage", error)
        return
    if transient:
        return
    await store_fundamentals(
        pool,
        tc.ticker,
        "alphavantage",
        {},
        {"error": error[:300]},
        symbol,
        f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={symbol}",
    )
    await record_failure(pool, tc.ticker, "alphavantage", error)


async def refresh_alpha_vantage(
    tc: TickerContext,
    deps: ValuationDeps,
    settings: ValuationSettings,
    force: bool,
    detail: dict[str, Any],
    pool: asyncpg.Pool,
) -> None:
    """Cache one Company Overview for ratio-level gap filling; SEC/ESEF remain primary."""
    if not settings.alpha_vantage_fallback or not deps.alpha_vantage_key:
        return
    if tc.profile.valuation_source != "auto":
        detail["alphavantage"] = "skipped: valuation source is forced in the holding profile"
        return
    symbol = tc.profile.alpha_vantage_symbol or tc.profile.edgar_ticker()
    stored = tc.stored.get("alphavantage")
    reason = _due(stored, None, settings, deps.today, force)
    if reason is None:
        detail["alphavantage"] = "cached"
        return
    if (
        not force
        and stored is not None
        and stored["last_error"]
        and stored["last_attempt_at"].date() >= deps.today
    ):
        detail["alphavantage"] = "failed today: retried tomorrow"
        return
    if not await _reserve_alpha_vantage_request(pool, deps.today):
        detail["alphavantage"] = "daily free-tier limit reached (25 requests)"
        return
    url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={symbol}"
    try:
        data = await company_overview(deps.http, deps.alpha_vantage_key, symbol)
    except (AlphaVantageError, httpx.HTTPError) as exc:
        await _alpha_vantage_failed(pool, tc, symbol, str(exc), detail, transient=_transient(exc))
        return
    # Empty objects commonly mean that the symbol has no overview on the free plan.
    if not data or not data.get("Symbol"):
        detail["alphavantage"] = f"no Company Overview for {symbol}"
        await _alpha_vantage_failed(
            pool, tc, symbol, f"no Company Overview returned for {symbol}", detail
        )
        return
    await store_fundamentals(pool, tc.ticker, "alphavantage", data, data, symbol, url)
    detail["alphavantage"] = {"symbol": symbol, "as_of": data.get("LatestQuarter")}


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


def preferred_currencies(
    reporting: str | None, price: str | None, convertible: set[str]
) -> list[str]:
    """Per-share figures are taken in the reporting currency when a stored reference rate
    converts the price into it (or it is the price's currency), else in the price's
    currency (e.g. TSMC's USD convenience translation: there is no TWD reference rate)."""
    out: list[str] = []
    price_iso = "GBP" if price in ("GBX", "GBp") else price
    if reporting and (
        reporting == price_iso or (reporting in convertible and price_iso in convertible)
    ):
        out.append(reporting)
    if price_iso:
        out.append(price_iso)
    return list(dict.fromkeys(out))


async def run_valuation(ctx: JobContext, deps: ValuationDeps) -> None:
    pool = ctx.pool
    async with ctx.step("load_config") as step:
        jobs_stored = await load_active(pool, "jobs", JobsConfig)
        profiles_stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
        await ctx.record_config_version(jobs_stored.id if jobs_stored else None)
        await ctx.record_config_version(profiles_stored.id if profiles_stored else None)
        jobs = jobs_stored.body if jobs_stored else JobsConfig()
        profiles = profiles_stored.body if profiles_stored else HoldingProfilesConfig()
        settings = jobs.valuation
        if not settings.enabled and ctx.trigger == "schedule":
            step.detail["skipped"] = "valuation is off in the Jobs config"
            return
        only = ctx.params.get("ticker")
        force = bool(ctx.params.get("force"))
        tickers = [only] if isinstance(only, str) and only else await holding_tickers(pool)
        info = await instrument_info(pool, tickers)
        symbols = await symbols_for(pool, tickers, profiles)
        convertible = await fx_currencies(pool, await fx_source(pool))
        step.detail.update(tickers=tickers, force=force)
        if deps.edgar is None:
            step.detail["sec"] = "SEC_USER_AGENT is not set: SEC fundamentals are skipped"

    status = await _load_status(pool)
    if not only:
        await refresh_funds(ctx, deps, settings, status)

    holdings_status: dict[str, Any] = status.setdefault("holdings", {})
    for ticker in tickers:
        inst = info.get(ticker)
        if fund_for_ticker(jobs, ticker) is not None or (inst is not None and inst.is_fund):
            continue
        profile = profiles.for_ticker(ticker) or HoldingProfile(ticker=ticker)
        stored = await _stored(pool, ticker)
        price_ccy = price_currency(symbols.get(ticker), inst.currency if inst else None)
        reporting = profile.reporting_currency
        tc = TickerContext(
            ticker=ticker,
            profile=profile,
            preferred=preferred_currencies(reporting, price_ccy, convertible),
            lei=inst.issuer_lei if inst else None,
            stored=stored,
        )
        entry: dict[str, Any] = {"checked_at": dt.datetime.now(dt.UTC), "errors": {}}
        async with ctx.step(f"fundamentals:{ticker}") as step:
            step.detail["preferred_currencies"] = tc.preferred
            cik = None
            if deps.edgar is not None:
                instrument = await pool.fetchrow(
                    "select t212_ticker, cik from public.instrument where t212_ticker = $1",
                    ticker,
                )
                cik = await resolve_cik(pool, deps.edgar, profile, instrument)
            if cik is not None:
                try:
                    await refresh_sec(tc, deps, settings, cik, force, step.detail, pool)
                except (
                    EdgarError,
                    XbrlError,
                    httpx.HTTPError,
                    ValueError,
                    asyncpg.PostgresError,
                ) as exc:
                    step.detail["sec_error"] = entry["errors"]["sec"] = str(exc)[:300]
                    await record_failure(pool, ticker, "sec", str(exc))
            elif deps.edgar is not None:
                step.detail["sec"] = "no SEC CIK (not an EDGAR filer)"
            tc.stored = await _stored(pool, ticker)
            if tc.lei:
                try:
                    await refresh_esef(tc, deps, settings, force, step.detail, pool)
                except (
                    XbrlError,
                    httpx.HTTPError,
                    ValueError,
                    asyncpg.PostgresError,
                ) as exc:
                    step.detail["esef_error"] = entry["errors"]["esef"] = str(exc)[:300]
                    await record_failure(pool, ticker, "esef", str(exc))
            else:
                step.detail["esef"] = "no issuer LEI (reference data)"
            tc.stored = await _stored(pool, ticker)
            if _alpha_vantage_needed(tc.stored):
                try:
                    await refresh_alpha_vantage(tc, deps, settings, force, step.detail, pool)
                except (AlphaVantageError, httpx.HTTPError, asyncpg.PostgresError) as exc:
                    error = str(exc)[:300]
                    step.detail["alphavantage_error"] = entry["errors"]["alphavantage"] = error
            if "alphavantage_error" in step.detail:
                entry["errors"].setdefault("alphavantage", step.detail["alphavantage_error"])
            entry["alphavantage"] = step.detail.get("alphavantage")
            if deps.finnhub_key and settings.finnhub_cross_check:
                try:
                    await refresh_finnhub(tc, deps, step.detail, pool)
                except (FinnhubError, httpx.HTTPError, asyncpg.PostgresError) as exc:
                    step.detail["finnhub_error"] = entry["errors"]["finnhub"] = str(exc)[:300]
            entry["sec"] = step.detail.get("sec")
            entry["esef"] = step.detail.get("esef")
        holdings_status[ticker] = entry
    await _save_status(pool, status)


async def valuation_refresh_handler(ctx: JobContext) -> None:
    settings = get_settings()
    finnhub = settings.finnhub_api_key.get_secret_value() if settings.finnhub_api_key else None
    alpha_vantage = None
    if settings.alpha_vantage_api_key:
        alpha_vantage = settings.alpha_vantage_api_key.get_secret_value()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, read=120.0),
        headers={"User-Agent": f"StewardAI/{__version__} (personal portfolio tool)"},
        follow_redirects=True,
    ) as http:
        edgar = None
        if settings.sec_user_agent:
            try:
                edgar = EdgarClient(http, settings.sec_user_agent)
            except EdgarError:
                logger.warning("SEC_USER_AGENT has no contact address: SEC fundamentals skipped")
        await run_valuation(
            ctx,
            ValuationDeps(
                http=http,
                edgar=edgar,
                finnhub_key=finnhub,
                alpha_vantage_key=alpha_vantage,
            ),
        )
