"""Repeatable Phase-0 endpoint audit; a catalogue page is never access evidence."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from steward import __version__
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.settings import Settings, get_settings
from steward.sources.base import AccessResult, AccessState, access_from_http, retry_after_at
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.sources.store import record_access


@dataclass(frozen=True, slots=True)
class Probe:
    provider: str
    endpoint: str
    url: str
    params: dict[str, str]
    credential: str | None = None
    credential_param: str | None = None
    expected_symbol: str | None = None
    auth_secret: str | None = None


def _dec(value: int | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def classify_probe(status: int, body: Any, expected_symbol: str | None = None) -> AccessResult:
    text = str(body).casefold()
    paid = any(word in text for word in ("premium", "upgrade", "subscription required"))
    if status == 200 and isinstance(body, dict):
        message = (
            " ".join(
                str(body.get(key, ""))
                for key in ("Error Message", "Note", "Information", "message", "error")
            )
            .strip()
            .casefold()
        )
        if message:
            if any(word in message for word in ("premium", "upgrade", "subscription required")):
                return AccessResult(AccessState.PAID_ONLY, "provider reports a paid-tier endpoint")
            if any(word in message for word in ("call frequency", "rate limit", "quota exceeded")):
                return AccessResult(AccessState.QUOTA_EXHAUSTED, "provider rate or quota limit")
            if any(
                word in message for word in ("invalid api key", "apikey is invalid", "unauthorized")
            ):
                return AccessResult(AccessState.ACCESS_UNVERIFIED, "credential rejected")
            if any(word in message for word in ("no data", "symbol not found", "not found")):
                return AccessResult(AccessState.NOT_COVERED, "provider returned no matching data")
            return AccessResult(
                AccessState.TEMPORARILY_FAILED, "provider returned an error response"
            )
    result = access_from_http(
        status, has_rows=body not in ({}, [], None, ""), paid_tier_denial=paid
    )
    if result.state == AccessState.AVAILABLE and expected_symbol:
        item = body[0] if isinstance(body, list) and body and isinstance(body[0], dict) else body
        symbol = (
            str(item.get("symbol") or item.get("Symbol") or "") if isinstance(item, dict) else ""
        )
        if symbol and symbol.casefold() != expected_symbol.casefold():
            return AccessResult(
                AccessState.DISPUTED, f"symbol mismatch: expected {expected_symbol}"
            )
    if result.state == AccessState.AVAILABLE and "sample" in text:
        return AccessResult(AccessState.ACCESS_UNVERIFIED, "sample-only response")
    return result


def probes(settings: Settings) -> list[Probe]:
    def secret(value: Any) -> str | None:
        return value.get_secret_value() if value else None

    return [
        Probe(
            "alpha_vantage",
            "company_overview",
            "https://www.alphavantage.co/query",
            {"function": "OVERVIEW", "symbol": "IBM"},
            secret(settings.alpha_vantage_api_key),
            "apikey",
            "IBM",
        ),
        Probe(
            "alpha_vantage",
            "daily_adjusted",
            "https://www.alphavantage.co/query",
            {"function": "TIME_SERIES_DAILY_ADJUSTED", "symbol": "IBM", "outputsize": "compact"},
            secret(settings.alpha_vantage_api_key),
            "apikey",
            "IBM",
        ),
        Probe(
            "alpha_vantage",
            "financial_statements",
            "https://www.alphavantage.co/query",
            {"function": "INCOME_STATEMENT", "symbol": "IBM"},
            secret(settings.alpha_vantage_api_key),
            "apikey",
            "IBM",
        ),
        Probe(
            "alpha_vantage",
            "commodity",
            "https://www.alphavantage.co/query",
            {"function": "WTI", "interval": "daily"},
            secret(settings.alpha_vantage_api_key),
            "apikey",
        ),
        Probe(
            "alpha_vantage",
            "fx_daily",
            "https://www.alphavantage.co/query",
            {
                "function": "FX_DAILY",
                "from_symbol": "EUR",
                "to_symbol": "USD",
                "outputsize": "compact",
            },
            secret(settings.alpha_vantage_api_key),
            "apikey",
        ),
        Probe(
            "alpha_vantage",
            "news_sentiment",
            "https://www.alphavantage.co/query",
            {"function": "NEWS_SENTIMENT", "tickers": "IBM"},
            secret(settings.alpha_vantage_api_key),
            "apikey",
        ),
        Probe(
            "eodhd",
            "eod",
            "https://eodhd.com/api/eod/AAPL.US",
            {"fmt": "json"},
            secret(settings.eodhd_api_key),
            "api_token",
        ),
        Probe(
            "eodhd",
            "symbol_search",
            "https://eodhd.com/api/exchange-symbol-list/US",
            {"fmt": "json"},
            secret(settings.eodhd_api_key),
            "api_token",
        ),
        Probe(
            "world_bank",
            "indicators",
            "https://api.worldbank.org/v2/country/CA/indicator/NY.GDP.MKTP.CD",
            {"format": "json"},
        ),
        Probe(
            "oecd",
            "sdmx",
            "https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_STES@DF_FINMARK,/.M.IR3TIB",
            {"startPeriod": "2025"},
        ),
        Probe(
            "fmp",
            "profile",
            "https://financialmodelingprep.com/stable/profile",
            {"symbol": "AAPL"},
            secret(settings.fmp_api_key),
            "apikey",
            "AAPL",
        ),
        Probe(
            "fmp",
            "analyst_estimates",
            "https://financialmodelingprep.com/stable/analyst-estimates",
            {"symbol": "AAPL"},
            secret(settings.fmp_api_key),
            "apikey",
        ),
        Probe(
            "fmp",
            "sector_snapshot",
            "https://financialmodelingprep.com/stable/sector-performance-snapshot",
            {},
            secret(settings.fmp_api_key),
            "apikey",
        ),
        Probe(
            "diffbot",
            "organization",
            "https://kg.diffbot.com/kg/v3/dql",
            {"query": 'type:Organization homepageUri:"example.com"', "size": "1"},
            secret(settings.diffbot_api_key),
            "token",
        ),
        Probe(
            "people_data_labs",
            "company_enrich",
            "https://api.peopledatalabs.com/v5/company/enrich",
            {"website": "openai.com"},
            secret(settings.people_data_labs_api_key),
            "api_key",
        ),
        Probe(
            "finnhub",
            "earnings_calendar",
            "https://finnhub.io/api/v1/calendar/earnings",
            {},
            secret(settings.finnhub_api_key),
            "token",
        ),
        Probe(
            "finnhub",
            "quote",
            "https://finnhub.io/api/v1/quote",
            {"symbol": "AAPL"},
            secret(settings.finnhub_api_key),
            "token",
            "AAPL",
        ),
        Probe(
            "finnhub",
            "peers",
            "https://finnhub.io/api/v1/stock/peers",
            {"symbol": "AAPL"},
            secret(settings.finnhub_api_key),
            "token",
        ),
        Probe(
            "finnhub",
            "earnings_surprises",
            "https://finnhub.io/api/v1/stock/earnings",
            {"symbol": "AAPL", "limit": "4"},
            secret(settings.finnhub_api_key),
            "token",
        ),
        Probe(
            "finnhub",
            "ownership",
            "https://finnhub.io/api/v1/stock/ownership",
            {"symbol": "AAPL"},
            secret(settings.finnhub_api_key),
            "token",
        ),
        Probe(
            "finnhub",
            "etf_holdings",
            "https://finnhub.io/api/v1/etf/holdings",
            {"symbol": "SPY"},
            secret(settings.finnhub_api_key),
            "token",
        ),
        Probe(
            "finnhub",
            "index_constituents",
            "https://finnhub.io/api/v1/index/constituents",
            {"symbol": "^GSPC"},
            secret(settings.finnhub_api_key),
            "token",
        ),
        Probe(
            "finnhub",
            "forward_estimates",
            "https://finnhub.io/api/v1/stock/revenue-estimate",
            {"symbol": "AAPL"},
            secret(settings.finnhub_api_key),
            "token",
        ),
        Probe(
            "twelve_data",
            "time_series",
            "https://api.twelvedata.com/time_series",
            {"symbol": "AAPL", "interval": "1day", "outputsize": "1"},
            secret(settings.twelve_data_api_key),
            "apikey",
        ),
        Probe(
            "twelve_data",
            "quote",
            "https://api.twelvedata.com/quote",
            {"symbol": "AAPL"},
            secret(settings.twelve_data_api_key),
            "apikey",
            "AAPL",
        ),
        Probe(
            "twelve_data",
            "corporate_actions",
            "https://api.twelvedata.com/dividends",
            {"symbol": "AAPL"},
            secret(settings.twelve_data_api_key),
            "apikey",
        ),
        Probe(
            "twelve_data",
            "splits",
            "https://api.twelvedata.com/splits",
            {"symbol": "AAPL"},
            secret(settings.twelve_data_api_key),
            "apikey",
        ),
        Probe(
            "bis",
            "sdmx",
            "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_TC/2.0/.",
            {"startPeriod": "2025"},
        ),
        Probe(
            "ecb",
            "sdmx",
            "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV",
            {"format": "csvdata", "lastNObservations": "1"},
        ),
        Probe(
            "un_comtrade",
            "trade",
            "https://comtradeapi.un.org/data/v1/get/C/A/HS",
            {"reporterCode": "124", "period": "2025", "cmdCode": "27"},
            secret(settings.un_comtrade_api_key),
            "subscription-key",
        ),
        Probe(
            "epo_ops",
            "patent_search",
            "https://ops.epo.org/3.2/auth/accesstoken",
            {},
            secret(settings.epo_ops_client_id),
            auth_secret=secret(settings.epo_ops_client_secret),
        ),
    ]


async def run_source_audit(ctx: JobContext, http: httpx.AsyncClient, settings: Settings) -> None:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    jobs = stored.body if stored else JobsConfig()
    if stored:
        await ctx.record_config_version(stored.id)
    governor = PostgresRequestGovernor(ctx.pool)
    for probe in probes(settings):
        async with ctx.step(f"{probe.provider}:{probe.endpoint}") as step:
            config_hash = hashlib.sha256(
                f"{probe.credential or ''}:{probe.auth_secret or ''}".encode()
            ).hexdigest()
            gate = await ctx.pool.fetchrow(
                """select state,hard_blocked,retry_at,configuration_hash
                   from public.provider_access where provider=$1 and endpoint=$2""",
                probe.provider,
                probe.endpoint,
            )
            now = dt.datetime.now(dt.UTC)
            if gate and gate["retry_at"] and gate["retry_at"] > now:
                step.detail.update(state=gate["state"], skipped="provider Retry-After window")
                continue
            if (
                gate
                and gate["hard_blocked"]
                and gate["configuration_hash"] == config_hash
                and gate["state"] in (AccessState.PAID_ONLY, AccessState.ACCESS_UNVERIFIED)
            ):
                step.detail.update(state=gate["state"], skipped="same credential is hard-blocked")
                continue
            await ctx.pool.execute(
                """update public.provider_access set configuration_hash=$3
                   where provider=$1 and endpoint=$2""",
                probe.provider,
                probe.endpoint,
                config_hash,
            )
            if (
                not probe.url
                or (probe.credential_param and not probe.credential)
                or (probe.provider == "epo_ops" and (not probe.credential or not probe.auth_secret))
            ):
                result = AccessResult(AccessState.ACCESS_UNVERIFIED, "credential not configured")
                await record_access(ctx.pool, probe.provider, probe.endpoint, result)
                step.detail["state"] = result.state
                continue
            provider_settings = jobs.providers[probe.provider]
            try:
                if probe.provider == "alpha_vantage":
                    await governor.reserve(
                        "alpha_vantage",
                        "account_free",
                        Decimal(1),
                        Budget(daily=Decimal(25)),
                    )
                await governor.reserve(
                    probe.provider,
                    probe.endpoint,
                    Decimal(1),
                    Budget(
                        daily=_dec(provider_settings.daily_quota),
                        monthly=_dec(provider_settings.monthly_quota),
                        reserve=Decimal(provider_settings.reserve_credits),
                    ),
                )
            except QuotaExceeded as exc:
                result = AccessResult(AccessState.QUOTA_EXHAUSTED, str(exc))
                await record_access(ctx.pool, probe.provider, probe.endpoint, result)
                step.detail.update(state=result.state, skipped="free request budget exhausted")
                continue
            params = dict(probe.params)
            if probe.credential_param and probe.credential:
                params[probe.credential_param] = probe.credential
            started = time.monotonic()
            try:
                if probe.provider == "epo_ops":
                    response = await http.post(
                        probe.url,
                        data={"grant_type": "client_credentials"},
                        auth=(probe.credential or "", probe.auth_secret or ""),
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                else:
                    response = await http.get(probe.url, params=params)
                try:
                    body: Any = response.json()
                except ValueError:
                    body = response.text[:2000]
                result = classify_probe(response.status_code, body, probe.expected_symbol)
                if response.status_code == 429:
                    result = AccessResult(
                        result.state,
                        result.detail,
                        retry_after_at(response.headers.get("Retry-After")),
                        response.headers.get("X-Request-ID"),
                    )
                elapsed = round((time.monotonic() - started) * 1000)
                await record_access(
                    ctx.pool,
                    probe.provider,
                    probe.endpoint,
                    result,
                    status_code=response.status_code,
                    elapsed_ms=elapsed,
                    response_bytes=len(response.content),
                    credits=1,
                )
                response_item = (
                    body[0]
                    if isinstance(body, list) and body and isinstance(body[0], dict)
                    else body
                )
                response_symbol = (
                    str(response_item.get("symbol") or response_item.get("Symbol") or "")
                    if isinstance(response_item, dict)
                    else ""
                )
                matched = (
                    response_symbol.casefold() == probe.expected_symbol.casefold()
                    if probe.expected_symbol and response_symbol
                    else None
                )
                response_fields = (
                    sorted(response_item.keys()) if isinstance(response_item, dict) else []
                )
                await ctx.pool.execute(
                    """insert into public.provider_benchmark_result
                       (provider,endpoint,corpus_key,latency_ms,state,matched,accurate,
                        incremental_coverage,reference_source,mismatch_reason,response_fields)
                       values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)""",
                    probe.provider,
                    probe.endpoint,
                    probe.expected_symbol or "endpoint_probe",
                    elapsed,
                    result.state,
                    matched,
                    None,
                    None,
                    None,
                    result.detail,
                    json.dumps(response_fields),
                )
                step.detail.update(
                    state=result.state,
                    status_code=response.status_code,
                    elapsed_ms=elapsed,
                    response_bytes=len(response.content),
                )
            except httpx.HTTPError as exc:
                result = AccessResult(AccessState.TEMPORARILY_FAILED, type(exc).__name__)
                await record_access(ctx.pool, probe.provider, probe.endpoint, result)
                step.detail.update(state=result.state, error=type(exc).__name__)


async def source_audit_handler(ctx: JobContext) -> None:
    settings = get_settings()
    headers = {"User-Agent": f"StewardAI/{__version__}"}
    async with httpx.AsyncClient(timeout=30, headers=headers) as http:
        await run_source_audit(ctx, http, settings)
