"""The earnings thesis check job (`earnings_check`, params {"ticker", "report_date"?, "url"?}).

locate -> fetch and archive -> extract (model, document only, no tools) -> validate in code
-> store facts -> verdicts from verified facts. Extraction and judgement are separate:
the verdict step never sees the page, only validated figures.

Also `thesis_verdicts` (params {"ticker"?}): re-judge every KPI from the stored facts with
the active theses config, no fetching and no model calls. That is how an edited threshold
reaches the verdicts without waiting for the next release.
"""

import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import asyncpg
import httpx

from steward import __version__
from steward.ai import AINotConfigured, Usage
from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import EarningsCheckSettings, JobsConfig
from steward.config.prompts import load_prompts
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig
from steward.config.verification import VerificationConfig
from steward.documents.fetch import FetchError, default_headers, fetch_document
from steward.earnings import metrics as metric_names
from steward.earnings.extract import SYSTEM_PROMPT as EXTRACTION_SYSTEM
from steward.earnings.extract import ExtractionError, ExtractionRequest, Extractor, LLMExtractor
from steward.earnings.ir_scan import locate_on_pages
from steward.earnings.locate import (
    SEARCH_SYSTEM,
    LLMSearchLocator,
    Located,
    LocateError,
    Locator,
    SearchRequest,
)
from steward.earnings.periods import label_year_for_end, likely_reported_period
from steward.earnings.schema import ExtractedFigure
from steward.earnings.store import archive, load_known, load_owner_rejected, store_facts
from steward.earnings.validate import ValidationContext, validate
from steward.earnings.verdicts import run_verdicts
from steward.earnings.xbrl_figures import figures_from_facts, filing_of
from steward.jobs.runner import JobContext, JobFailed
from steward.llm.cache import ResultCache
from steward.llm.client import create_llm
from steward.llm.routing import routing_client
from steward.notify.breach import alert_breaches
from steward.settings import get_settings
from steward.sources.edgar import EdgarClient, EdgarError
from steward.storage import EvidenceStore, SupabaseStorage

logger = logging.getLogger(__name__)

PRAGUE = ZoneInfo("Europe/Prague")
_METRIC = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass
class EarningsDeps:
    http: httpx.AsyncClient
    storage: EvidenceStore
    extractor: Extractor
    edgar: EdgarClient | None
    locator: Locator | None


@dataclass(frozen=True)
class LoadedConfig:
    theses: ThesesConfig
    theses_version_id: int | None
    profiles: HoldingProfilesConfig
    verification: VerificationConfig
    jobs: JobsConfig


def local_today() -> dt.date:
    return dt.datetime.now(PRAGUE).date()


async def load_config(ctx: JobContext) -> LoadedConfig:
    pool = ctx.pool
    theses = await load_active(pool, "theses", ThesesConfig)
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    verification = await load_active(pool, "verification", VerificationConfig)
    jobs = await load_active(pool, "jobs", JobsConfig)
    for stored in (theses, profiles, verification, jobs):
        await ctx.record_config_version(stored.id if stored else None)
    return LoadedConfig(
        theses=theses.body if theses else ThesesConfig(),
        theses_version_id=theses.id if theses else None,
        profiles=profiles.body if profiles else HoldingProfilesConfig(),
        verification=verification.body if verification else VerificationConfig(),
        jobs=jobs.body if jobs else JobsConfig(),
    )


def _required_ticker(params: dict[str, Any]) -> str:
    ticker = params.get("ticker")
    if not isinstance(ticker, str) or not ticker:
        raise JobFailed("this job needs a 'ticker' parameter")
    return ticker


async def earnings_check_handler(ctx: JobContext) -> None:
    """Entry point for the scheduler and the queue consumer."""
    settings = get_settings()
    if settings.supabase_url is None or settings.supabase_secret_key is None:
        raise JobFailed("SUPABASE_URL and SUPABASE_SECRET_KEY are needed to archive evidence")
    try:
        client = routing_client(create_llm(settings))
    except AINotConfigured as exc:
        raise JobFailed(str(exc)) from exc
    stored_jobs = await load_active(ctx.pool, "jobs", JobsConfig)
    cfg = (stored_jobs.body if stored_jobs else JobsConfig()).earnings_check
    if cfg.result_cache_days > 0:
        client.cache = ResultCache(
            ctx.pool,
            "earnings_check",
            ttl_days=cfg.result_cache_days,
            search_ttl_days=cfg.result_cache_days,
        )
    prompts = await load_prompts(ctx.pool)
    async with httpx.AsyncClient(
        timeout=60.0, headers={"User-Agent": f"StewardAI/{__version__}"}
    ) as http:
        edgar = EdgarClient(http, settings.sec_user_agent) if settings.sec_user_agent else None
        deps = EarningsDeps(
            http=http,
            storage=SupabaseStorage(
                http, settings.supabase_url, settings.supabase_secret_key.get_secret_value()
            ),
            extractor=LLMExtractor(
                client,
                cfg.extraction_model,
                cfg.effort,
                prompts.text("earnings_extraction", EXTRACTION_SYSTEM, cfg.extraction_model),
            ),
            edgar=edgar,
            locator=LLMSearchLocator(
                client,
                cfg.locate_model,
                cfg.max_searches,
                prompts.text("earnings_locate", SEARCH_SYSTEM, cfg.locate_model),
                effort=cfg.locate_effort,
            ),
        )
        await earnings_check(ctx, deps)


async def thesis_verdicts_handler(ctx: JobContext) -> None:
    async with ctx.step("load_config") as step:
        config = await load_config(ctx)
        step.detail["theses"] = len(config.theses.theses)
    ticker = ctx.params.get("ticker")
    async with ctx.step("verdicts") as step:
        if config.theses_version_id is None:
            step.detail["skipped"] = "no theses config saved"
            return
        summary = await run_verdicts(
            ctx.pool,
            config.theses,
            config.theses_version_id,
            ctx.job_id,
            [ticker] if isinstance(ticker, str) and ticker else None,
            config.jobs.earnings_check.kpi_history_periods,
        )
        step.detail.update(summary.as_detail())


class _Budget:
    """Model calls plus document fetches, capped per run by the Jobs config."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def spend(self, what: str) -> None:
        self.used += 1
        if self.used > self.limit:
            raise JobFailed(f"step cap of {self.limit} reached before {what}")


async def _instrument(pool: asyncpg.Pool, ticker: str) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "select t212_ticker, name, cik, next_report_date, last_report_date"
        " from public.instrument where t212_ticker = $1",
        ticker,
    )


def _report_date(params: dict[str, Any], instrument: asyncpg.Record | None) -> dt.date:
    raw = params.get("report_date")
    if isinstance(raw, str) and raw:
        try:
            return dt.date.fromisoformat(raw)
        except ValueError as exc:
            raise JobFailed(f"report_date {raw!r} is not a date") from exc
    today = local_today()
    if instrument is not None:
        for key in ("next_report_date", "last_report_date"):
            value = instrument[key]
            if value is not None and value <= today:
                return value  # type: ignore[no-any-return]
    return today


async def resolve_cik(
    pool: asyncpg.Pool, edgar: EdgarClient | None, profile: HoldingProfile, instrument: Any
) -> str | None:
    if profile.sec_cik:
        from_profile = profile.sec_cik.zfill(10)
        if instrument is not None and not instrument["cik"]:
            # Insider and filing collection read the instrument's CIK, not the profile's.
            await pool.execute(
                "update public.instrument set cik = $2 where t212_ticker = $1 and cik is null",
                profile.ticker,
                from_profile,
            )
        return from_profile
    if instrument is not None and instrument["cik"]:
        return str(instrument["cik"])
    if edgar is None:
        return None
    try:
        cik = await edgar.cik_for_ticker(profile.edgar_ticker())
    except (EdgarError, httpx.HTTPError):
        return None
    if cik and instrument is not None:
        await pool.execute(
            "update public.instrument set cik = $2 where t212_ticker = $1 and cik is null",
            profile.ticker,
            cik,
        )
    return cik


def _kpi_metrics(theses: ThesesConfig, ticker: str) -> tuple[list[str], dict[str, str]]:
    thesis = next((t for t in theses.theses if t.ticker == ticker), None)
    if thesis is None:
        return [], {}
    return [k.metric for k in thesis.kpis], {k.metric: k.description for k in thesis.kpis}


def is_sec_url(url: str) -> bool:
    """Only SEC hosts get the SEC User-Agent (it carries the owner's contact address)."""
    host = (urlsplit(url).hostname or "").lower()
    return host == "sec.gov" or host.endswith(".sec.gov")


def _record_failed_usage(step: Any, usage: Usage | None) -> None:
    """A failed AI call still costs what its response used; record it on the step."""
    if usage is not None and (usage.input_tokens or usage.output_tokens or usage.searches):
        step.usage = usage.step_usage()


def _usable(
    figures: list[ExtractedFigure], requested: set[str]
) -> tuple[list[ExtractedFigure], int]:
    kept = [
        f
        for f in figures
        if f.metric in requested and _METRIC.match(f.metric) and 1900 <= f.fiscal_year <= 2200
    ]
    return kept, len(figures) - len(kept)


async def earnings_check(ctx: JobContext, deps: EarningsDeps) -> None:
    pool = ctx.pool
    ticker = _required_ticker(ctx.params)

    async with ctx.step("load_config") as step:
        config = await load_config(ctx)
        settings: EarningsCheckSettings = config.jobs.earnings_check
        profile = config.profiles.for_ticker(ticker) or HoldingProfile(ticker=ticker)
        instrument = await _instrument(pool, ticker)
        report_date = _report_date(ctx.params, instrument)
        expected = likely_reported_period(report_date, profile.fiscal_year_end_month)
        step.detail.update(
            report_date=report_date,
            fiscal_year_end_month=profile.fiscal_year_end_month,
            has_profile=config.profiles.for_ticker(ticker) is not None,
        )
    budget = _Budget(settings.max_steps)
    company = profile.company_name or (instrument["name"] if instrument else "") or ticker

    async with ctx.step("locate") as step:
        located = await _locate(ctx, deps, profile, instrument, report_date, expected, budget, step)
        if located is None:
            raise JobFailed(
                f"no results document found for {ticker} around {report_date}. Set sec_cik "
                "(EDGAR), results_urls or ir_url in the holding profile, or run the check "
                "with a document URL."
            )
        step.detail.update(url=located.url, source=located.source, note=located.detail)

    async with ctx.step("fetch_and_archive") as step:
        budget.spend("fetching the document")
        headers = (
            deps.edgar.headers
            if deps.edgar is not None and is_sec_url(located.url)
            else default_headers()
        )
        try:
            document = await fetch_document(
                deps.http,
                located.url,
                max_bytes=settings.max_document_mb * 1_000_000,
                headers=headers,
            )
            text = await document.text_async()
        except FetchError as exc:
            raise JobFailed(str(exc)) from exc
        if len(text) < 200:
            raise JobFailed(
                f"{located.url} has almost no text ({len(text)} characters); it may be "
                "rendered by JavaScript. Point the profile at the PDF or the newswire copy."
            )
        archived = await archive(pool, deps.storage, document, ctx.job_id)
        step.detail.update(
            content_type=document.content_type,
            bytes=len(document.data),
            sha256=archived.sha256,
            evidence_path=archived.path,
            text_chars=len(text),
        )

    kpi_metrics, kpi_descriptions = _kpi_metrics(config.theses, ticker)
    wanted = metric_names.metrics_to_extract(kpi_metrics)

    async with ctx.step("extract") as step:
        budget.spend("the extraction call")
        from_xbrl = await _filing_figures(deps, settings, located.url, ticker, text, set(wanted))
        remaining = [m for m in wanted if m not in {f.metric for f in from_xbrl}]
        request = ExtractionRequest(
            ticker=ticker,
            company_name=company,
            report_date=report_date,
            fye_month=profile.fiscal_year_end_month,
            expected_period=expected,
            metrics=[(m, metric_names.describe(m, kpi_descriptions)) for m in remaining],
            reporting_currency=profile.reporting_currency,
        )
        extracted: list[ExtractedFigure] = list(from_xbrl)
        if remaining:
            try:
                result, usage = await deps.extractor.extract(document, text, request)
            except ExtractionError as exc:
                _record_failed_usage(step, exc.usage)
                raise JobFailed(str(exc)) from exc
            step.usage = usage.step_usage()
            extracted += result.figures
            step.detail.update(
                model=usage.model, document_kind=result.document_kind, not_found=result.not_found
            )
        figures, dropped = _usable(extracted, set(wanted))
        figures = [
            _in_year_of_end(
                fig,
                profile.fiscal_year_end_month,
                config.verification.period_end_tolerance_days,
            )
            for fig in figures
        ]
        step.detail.update(
            figures=len(extracted), from_xbrl=len(from_xbrl), dropped_unrequested=dropped
        )

    async with ctx.step("validate_and_store") as step:
        known = await load_known(pool, ticker)
        owner_rejected = await load_owner_rejected(pool, ticker)
        validated = validate(
            figures,
            ValidationContext(
                document_text=text,
                report_date=report_date,
                fye_month=profile.fiscal_year_end_month,
                reporting_currency=profile.reporting_currency,
                config=config.verification,
                known=known,
                owner_rejected=owner_rejected,
            ),
        )
        summary = await store_facts(
            pool, ticker, validated, archived, ctx.job_id, ctx.config_version_ids
        )
        step.detail.update(summary.as_detail())

    async with ctx.step("verdicts") as step:
        if config.theses_version_id is None:
            step.detail["skipped"] = "no theses config saved"
        else:
            verdicts = await run_verdicts(
                pool,
                config.theses,
                config.theses_version_id,
                ctx.job_id,
                [ticker],
                config.jobs.earnings_check.kpi_history_periods,
            )
            step.detail.update(verdicts.as_detail())
            breaches = await alert_breaches(
                pool, ctx.job_id, dt.datetime.now(dt.UTC), get_settings().app_url
            )
            if breaches:
                step.detail["alerts"] = breaches
        if instrument is not None:
            await pool.execute(
                "update public.instrument set last_report_date = $2 where t212_ticker = $1"
                " and (last_report_date is null or last_report_date < $2)",
                ticker,
                report_date,
            )


def _in_year_of_end(fig: ExtractedFigure, fye_month: int, tolerance_days: int) -> ExtractedFigure:
    """The figure with its fiscal year in the year-it-ends-in convention (see `periods`)."""
    try:
        stated = dt.date.fromisoformat((fig.period_end or "")[:10])
    except ValueError:
        return fig
    year = label_year_for_end(fig.fiscal_year, fig.fiscal_period, fye_month, stated, tolerance_days)
    return fig if year == fig.fiscal_year else fig.model_copy(update={"fiscal_year": year})


async def _filing_figures(
    deps: EarningsDeps,
    settings: EarningsCheckSettings,
    url: str,
    ticker: str,
    text: str,
    wanted: set[str],
) -> list[ExtractedFigure]:
    """The standard figures of a 10-Q / 10-K document, from its XBRL (setting-gated); empty
    for anything else, so the model extraction runs as before."""
    filing = filing_of(url)
    if not settings.xbrl_for_filings or deps.edgar is None or filing is None:
        return []
    cik, accession = filing
    try:
        facts = await deps.edgar.company_facts(cik)
    except (EdgarError, httpx.HTTPError):
        return []
    return figures_from_facts(facts, accession, text, wanted)


async def _locate(
    ctx: JobContext,
    deps: EarningsDeps,
    profile: HoldingProfile,
    instrument: Any,
    report_date: dt.date,
    expected: tuple[int, str],
    budget: _Budget,
    step: Any,
) -> Located | None:
    url = ctx.params.get("url")
    if isinstance(url, str) and url:
        return Located(url, "param")

    attempts: list[str] = []
    cik = await resolve_cik(ctx.pool, deps.edgar, profile, instrument)
    if cik and deps.edgar is not None:
        try:
            found = await deps.edgar.find_earnings_release(cik, report_date)
        except (EdgarError, httpx.HTTPError) as exc:
            attempts.append(f"edgar: {exc}")
        else:
            if found is not None:
                filing, doc_url = found
                return Located(doc_url, "edgar", f"{filing.form} filed {filing.filing_date}")
            attempts.append(f"edgar: no 8-K 2.02 or 6-K for CIK {cik} near {report_date}")
    elif deps.edgar is None:
        attempts.append("edgar: SEC_USER_AGENT not set")
    else:
        attempts.append("edgar: no CIK")

    domains = profile.allowed_domains()
    pages = [str(u) for u in profile.results_urls]
    if pages:
        found_on_page, notes = await locate_on_pages(
            deps.http, pages, expected[0], expected[1], report_date, domains
        )
        if found_on_page is not None:
            return found_on_page
        attempts.extend(f"ir page {n}" for n in notes)
    if deps.locator is not None and domains:
        budget.spend("the search call")
        try:
            located, usage = await deps.locator.locate(
                SearchRequest(
                    ticker=profile.ticker,
                    company_name=profile.company_name,
                    report_date=report_date,
                    expected_period=expected,
                    domains=domains,
                    hints=[str(u) for u in profile.results_urls]
                    + ([str(profile.ir_url)] if profile.ir_url else []),
                )
            )
        except LocateError as exc:
            _record_failed_usage(step, exc.usage)
            attempts.append(f"search: {exc}")
        else:
            step.usage = usage.step_usage()
            if located is not None:
                return located
            attempts.append("search: no matching document")
    else:
        attempts.append("search: no domains in the holding profile")
    step.detail["attempts"] = attempts
    return None
