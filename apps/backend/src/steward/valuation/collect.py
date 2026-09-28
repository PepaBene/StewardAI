"""`valuation_refresh` (daily by default; params {"ticker"?, "force"?}): the fundamentals
behind P/E, P/B and dividend yield, and the ETF providers' fund-level figures.

Per holding (ETFs excepted, they are valued at fund level):

- SEC: the issuer's latest periodic report (10-Q, 10-K, 20-F, 40-F) is looked up in its
  submissions. When it differs from the one the stored figures were built on, or those are
  older than `full_refresh_days`, companyfacts is read again. When companyfacts carries no
  EPS from that latest report (it can lag, or miss a filing's financial statements as it
  did TSMC's FY2025 20-F), the report's own XBRL instance is parsed instead.
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
from steward.jobs.runner import JobContext
from steward.reference.identity import holding_tickers
from steward.reference.prices import symbols_for
from steward.settings import get_settings
from steward.sources.edgar import EdgarClient, EdgarError, Filing, parse_submissions
from steward.sources.finnhub import FinnhubError, basic_financials
from steward.sources.fund_characteristics import (
    FundDataError,
    FundFigures,
    ishares_characteristics,
    vanguard_characteristics,
)
from steward.sources.xbrl import (
    EsefFiling,
    XbrlError,
    esef_document,
    esef_filings,
    parse_instance,
    parse_xbrl_json,
)
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

PERIODIC_FORMS = {"10-Q", "10-K", "20-F", "40-F", "10-Q/A", "10-K/A", "20-F/A", "40-F/A"}
STATUS_KEY = "valuation_status"
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
        "select source, document_ref, fetched_at, figures from public.fundamentals"
        " where ticker = $1",
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


def latest_periodic(filings: list[Filing]) -> Filing | None:
    periodic = [f for f in filings if f.form in PERIODIC_FORMS]
    return max(periodic, key=lambda f: (f.filing_date, f.accession)) if periodic else None


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
    submissions = await edgar.submissions(cik)
    latest = latest_periodic(parse_submissions(cik, submissions))
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
            instance_url, content = await edgar.instance_document(latest)
            parsed = parse_instance(content, ALL_CONCEPTS)
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
            raw["instance"] = {"url": instance_url, "facts": [x.raw for x in parsed]}
            detail["instance_fallback"] = (
                f"companyfacts lacks {latest.form} {latest.accession}: read {len(parsed)} facts "
                "from its XBRL instance"
            )
    figures = compute("sec", facts, tc.preferred, deps.today)
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
        detail["esef"] = "unchanged"
        return
    facts: list[Fact] = []
    raw: dict[str, Any] = {"filings": [], "facts": []}
    for filing in picked:
        doc = await esef_document(deps.http, filing)
        parsed = parse_xbrl_json(doc, ALL_CONCEPTS)
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
        ]
        raw["filings"].append({**filing.raw, "json_url": filing.json_url})
        raw["facts"] += [{**x.raw, "filing": filing.id} for x in parsed]
    if not any(f.concept in SHARES_CONCEPTS for f in facts):
        facts += _stored_shares(tc.stored.get("sec"))
    figures = compute("esef", facts, tc.preferred, deps.today)
    if figures is None:
        detail["esef"] = "no EPS in the ESEF filings"
        return
    await store_fundamentals(
        pool, tc.ticker, "esef", figures, raw, latest.id, latest.report_url or latest.json_url
    )
    detail["esef"] = _summary(figures)


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
                except (EdgarError, XbrlError, httpx.HTTPError, ValueError) as exc:
                    step.detail["sec_error"] = entry["errors"]["sec"] = str(exc)[:300]
                    await record_failure(pool, ticker, "sec", str(exc))
            elif deps.edgar is not None:
                step.detail["sec"] = "no SEC CIK (not an EDGAR filer)"
            tc.stored = await _stored(pool, ticker)
            if tc.lei:
                try:
                    await refresh_esef(tc, deps, settings, force, step.detail, pool)
                except (XbrlError, httpx.HTTPError, ValueError) as exc:
                    step.detail["esef_error"] = entry["errors"]["esef"] = str(exc)[:300]
                    await record_failure(pool, ticker, "esef", str(exc))
            else:
                step.detail["esef"] = "no issuer LEI (reference data)"
            if deps.finnhub_key and settings.finnhub_cross_check:
                try:
                    await refresh_finnhub(tc, deps, step.detail, pool)
                except (FinnhubError, httpx.HTTPError) as exc:
                    step.detail["finnhub_error"] = entry["errors"]["finnhub"] = str(exc)[:300]
            entry["sec"] = step.detail.get("sec")
            entry["esef"] = step.detail.get("esef")
        holdings_status[ticker] = entry
    await _save_status(pool, status)


async def valuation_refresh_handler(ctx: JobContext) -> None:
    settings = get_settings()
    finnhub = settings.finnhub_api_key.get_secret_value() if settings.finnhub_api_key else None
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
        await run_valuation(ctx, ValuationDeps(http=http, edgar=edgar, finnhub_key=finnhub))
