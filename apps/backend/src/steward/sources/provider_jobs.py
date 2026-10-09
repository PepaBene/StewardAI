"""Scheduled refreshes that spend free-tier provider credits: FMP profile and estimates,
Alpha Vantage annual statements and EPO OPS patent families (D4.2, D4.4, D5.4).

Each runs on its schedule whenever its provider's API key is set (a missing key skips the run
with a reason, never an error). Each reuses the ingestion function its manual route
already calls, spends credits only through the Postgres governor at background priority (the
owner's manual requests may use the reserve a scheduled run leaves), works through the stalest
holdings first and stops at the first quota, gate or authentication refusal, so a run can never
push a provider past its free allowance. A holding without the explicit symbol mapping a
provider needs is skipped, never guessed.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Sequence
from typing import Any, Literal

import asyncpg
import httpx

from steward import __version__
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import JobsConfig, ProviderJobSettings
from steward.config.store import load_active
from steward.free_sources import collect_alpha_vantage_statement
from steward.jobs.runner import JobContext
from steward.reference.identity import holding_tickers
from steward.settings import get_settings
from steward.sources.base import Priority
from steward.sources.companies_house import collect_companies_house
from steward.sources.company_research import collect_epo_patents
from steward.sources.edinet import collect_edinet_day
from steward.sources.fmp import (
    COMPENSATION_REFRESH_DAYS,
    collect_fmp_company,
    collect_fmp_compensation,
)
from steward.sources.free_market import FreeSourceError
from steward.sources.governor import QuotaExceeded
from steward.sources.twelvedata import TwelveDataError, collect_corporate_actions

FMP_HOLDINGS_PER_RUN = 20  # two credits each: well inside the 125-per-endpoint daily cap
COMPANIES_HOUSE_HOLDINGS_PER_RUN = 20  # two requests each, far below the 600 per 5 minutes
EDINET_DAYS_PER_RUN = 7  # one request a day of filings
EDINET_FIRST_LOOKBACK_DAYS = 3
TWELVE_DATA_HOLDINGS_PER_RUN = 6  # two credits each: 12 of the 40-a-day-per-endpoint cap
TWELVE_DATA_LOOKBACK_DAYS = 365
ALPHA_STATEMENTS_PER_RUN = 4  # two ledger credits each of the shared 25 a day
EPO_HOLDINGS_PER_RUN = 3
AlphaFunction = Literal["INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW"]
_ALPHA_STATEMENT = {
    "INCOME_STATEMENT": "income",
    "BALANCE_SHEET": "balance_sheet",
    "CASH_FLOW": "cash_flow",
}


def stalest_first[T](
    candidates: Sequence[T],
    last_done: Callable[[T], dt.datetime | None],
    *,
    now: dt.datetime,
    refresh_hours: int,
    limit: int,
) -> list[T]:
    """Candidates not refreshed within `refresh_hours`, never-refreshed first, then oldest."""
    cutoff = now - dt.timedelta(hours=refresh_hours)
    never = dt.datetime.min.replace(tzinfo=dt.UTC)
    ranked = sorted(((last_done(c) or never, c) for c in candidates), key=lambda pair: pair[0])
    return [c for last, c in ranked if last < cutoff][: max(limit, 0)]


async def _configs(pool: asyncpg.Pool) -> tuple[JobsConfig, HoldingProfilesConfig]:
    jobs = await load_active(pool, "jobs", JobsConfig)
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    return (
        jobs.body if jobs else JobsConfig(),
        profiles.body if profiles else HoldingProfilesConfig(),
    )


async def _save_state(pool: asyncpg.Pool, key: str, value: dict[str, Any]) -> None:
    await pool.execute(
        "insert into public.sync_state (key, value) values ($1, $2::jsonb)"
        " on conflict (key) do update set value = excluded.value",
        key,
        json.dumps(value, default=str),
    )


# ------------------------------------------------------------------------------------- FMP


def fmp_symbols(profiles: HoldingProfilesConfig, tickers: Sequence[str]) -> dict[str, str]:
    """Holding -> FMP symbol: the profile's `fmp_symbol`, else its `sec_ticker`; none, no call."""
    out: dict[str, str] = {}
    for ticker in tickers:
        profile = profiles.for_ticker(ticker)
        symbol = profile and (profile.fmp_symbol or profile.sec_ticker)
        if symbol:
            out[ticker] = symbol
    return out


async def fmp_company_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with ctx.step("fmp_company") as step:
        jobs, profiles = await _configs(ctx.pool)
        provider = jobs.providers["fmp"]
        if not settings.fmp_api_key:
            step.detail["skipped"] = "FMP_API_KEY is not set"
            return
        tickers = await holding_tickers(ctx.pool)
        # The free plan covers US listings under their EDGAR/Finnhub symbol (or an explicit
        # `fmp_symbol`); no mapping, no call.
        mapped = fmp_symbols(profiles, tickers)
        step.detail["unmapped"] = [t for t in tickers if t not in mapped]
        rows = await ctx.pool.fetch(
            "select ticker, max(last_seen_at) seen from public.company_research_observation"
            " where provider='fmp' and dataset='profile' and ticker = any($1::text[])"
            " group by ticker",
            list(mapped),
        )
        seen = {r["ticker"]: r["seen"] for r in rows}
        # A holding whose compensation was never checked is due whatever its profile's age, or a
        # dataset added later would wait for every profile to go stale first.
        compensation_checked = {
            r["entity_key"]
            for r in await ctx.pool.fetch(
                "select entity_key from public.coverage_status"
                " where provider='fmp' and dataset='executive_compensation'"
                " and entity_key = any($1::text[])",
                list(mapped),
            )
        }
        seen = {t: s for t, s in seen.items() if t in compensation_checked}
        due = stalest_first(
            list(mapped),
            seen.get,
            now=dt.datetime.now(dt.UTC),
            refresh_hours=provider.refresh_hours,
            limit=FMP_HOLDINGS_PER_RUN,
        )
        results: dict[str, Any] = {}
        compensation: dict[str, int] = {}
        async with httpx.AsyncClient(
            timeout=30, headers={"User-Agent": f"StewardAI/{__version__}"}
        ) as http:
            for ticker in due:
                try:
                    results[ticker] = await collect_fmp_company(
                        ctx.pool,
                        http,
                        ticker=ticker,
                        symbol=mapped[ticker],
                        api_key=settings.fmp_api_key.get_secret_value(),
                        provider_settings=provider,
                    )
                except (QuotaExceeded, RuntimeError) as exc:
                    step.detail["stopped"] = f"{type(exc).__name__}: {exc}"[:200]
                    break
                except FreeSourceError as exc:
                    results[ticker] = f"failed: {exc.status_code or 'error'}"
                    if exc.status_code in {401, 403, 429}:
                        step.detail["stopped"] = f"HTTP {exc.status_code}"
                        break
                except httpx.HTTPError as exc:
                    results[ticker] = f"failed: {type(exc).__name__}"
                # Proxy-statement pay changes once a year: refresh it monthly, not every run.
                try:
                    stale = await ctx.pool.fetchval(
                        "select coalesce("
                        "max(last_seen_at) < now() - make_interval(days => $2), true)"
                        " from public.company_research_observation"
                        " where provider='fmp' and dataset='executive_compensation' and ticker=$1",
                        ticker,
                        COMPENSATION_REFRESH_DAYS,
                    )
                    if stale:
                        compensation[ticker] = await collect_fmp_compensation(
                            ctx.pool,
                            http,
                            ticker=ticker,
                            symbol=mapped[ticker],
                            api_key=settings.fmp_api_key.get_secret_value(),
                            provider_settings=provider,
                        )
                except (QuotaExceeded, RuntimeError) as exc:
                    step.detail["compensation_stopped"] = f"{type(exc).__name__}: {exc}"[:200]
                except (FreeSourceError, httpx.HTTPError):
                    compensation[ticker] = -1
        step.detail["refreshed"] = results
        step.detail["compensation_rows"] = compensation


# ------------------------------------------------------------------------- Alpha Vantage


async def alpha_vantage_statements_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with ctx.step("alpha_vantage_statements") as step:
        jobs, profiles = await _configs(ctx.pool)
        provider = jobs.providers["alpha_vantage"]
        if not settings.alpha_vantage_api_key:
            step.detail["skipped"] = "ALPHA_VANTAGE_API_KEY is not set"
            return
        tickers = await holding_tickers(ctx.pool)
        # The manual route's two preconditions: an explicit symbol mapping and a confirmed
        # canonical listing.
        confirmed = {
            r["ticker"]
            for r in await ctx.pool.fetch(
                "select distinct ticker from public.instrument_security_link"
                " where status='confirmed' and listing_id is not null and ticker = any($1::text[])",
                tickers,
            )
        }
        symbols = {
            t: p.alpha_vantage_symbol
            for t in tickers
            if t in confirmed and (p := profiles.for_ticker(t)) and p.alpha_vantage_symbol
        }
        step.detail["unmapped"] = [t for t in tickers if t not in symbols]
        rows = await ctx.pool.fetch(
            "select ticker, statement, max(known_at) seen from public.financial_statement_fact"
            " where source='alpha_vantage' and ticker = any($1::text[]) group by ticker, statement",
            list(symbols),
        )
        seen = {(r["ticker"], r["statement"]): r["seen"] for r in rows}
        functions: tuple[AlphaFunction, ...] = ("INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW")
        pairs = [(t, fn) for t in symbols for fn in functions]
        due = stalest_first(
            pairs,
            lambda pair: seen.get((pair[0], _ALPHA_STATEMENT[pair[1]])),
            now=dt.datetime.now(dt.UTC),
            refresh_hours=provider.refresh_hours,
            limit=min(ALPHA_STATEMENTS_PER_RUN, max(provider.daily_quota or 25, 0) // 2),
        )
        results: dict[str, Any] = {}
        async with httpx.AsyncClient(
            timeout=30, headers={"User-Agent": f"StewardAI/{__version__}"}
        ) as http:
            for ticker, function in due:
                try:
                    results[f"{ticker}:{function}"] = await collect_alpha_vantage_statement(
                        ctx.pool,
                        http,
                        ticker=ticker,
                        symbol=symbols[ticker],
                        function=function,
                        api_key=settings.alpha_vantage_api_key.get_secret_value(),
                        priority=Priority.BACKGROUND,
                        reserve_credits=max(provider.reserve_credits, 1),
                    )
                except (QuotaExceeded, RuntimeError) as exc:
                    step.detail["stopped"] = f"{type(exc).__name__}: {exc}"[:200]
                    break
                except (FreeSourceError, httpx.HTTPError) as exc:
                    # Any Alpha Vantage failure may be the daily limit: do not spend more.
                    results[f"{ticker}:{function}"] = f"failed: {type(exc).__name__}"
                    step.detail["stopped"] = "provider error"
                    break
        step.detail["refreshed"] = results


# ---------------------------------------------------------------------------------- EPO OPS


def epo_state_key(ticker: str) -> str:
    return f"epo_patents:{ticker}"


async def epo_patents_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with ctx.step("epo_patents") as step:
        jobs, profiles = await _configs(ctx.pool)
        provider = jobs.providers["epo_ops"]
        if not settings.epo_ops_client_id or not settings.epo_ops_client_secret:
            step.detail["skipped"] = "EPO OPS credentials are not set"
            return
        from steward.graph.jobs import load_graph_holdings

        names = {h["ticker"]: h["names"] for h in await load_graph_holdings(ctx.pool)}
        aliases: dict[str, tuple[str, ...]] = {}
        for ticker, resolved in names.items():
            profile = profiles.for_ticker(ticker)
            own = [profile.company_name, *profile.aliases] if profile else []
            merged = tuple(dict.fromkeys(a.strip() for a in [*own, *resolved] if a and a.strip()))
            if merged:
                aliases[ticker] = merged
        step.detail["without_alias"] = [t for t in names if t not in aliases]
        rows = await ctx.pool.fetch(
            "select key, value from public.sync_state where key = any($1::text[])",
            [epo_state_key(t) for t in aliases],
        )
        done: dict[str, dt.datetime | None] = {}
        for row in rows:
            value = json.loads(row["value"]) if isinstance(row["value"], str) else row["value"]
            try:
                done[row["key"]] = dt.datetime.fromisoformat(str(value.get("at")))
            except (AttributeError, ValueError):
                done[row["key"]] = None
        due = stalest_first(
            list(aliases),
            lambda t: done.get(epo_state_key(t)),
            now=dt.datetime.now(dt.UTC),
            refresh_hours=provider.refresh_hours,
            limit=EPO_HOLDINGS_PER_RUN,
        )
        results: dict[str, Any] = {}
        async with httpx.AsyncClient(
            timeout=45, headers={"User-Agent": f"StewardAI/{__version__}"}
        ) as http:
            for ticker in due:
                try:
                    results[ticker] = await collect_epo_patents(
                        ctx.pool,
                        http,
                        ticker=ticker,
                        applicant_aliases=aliases[ticker],
                        client_id=settings.epo_ops_client_id.get_secret_value(),
                        client_secret=settings.epo_ops_client_secret.get_secret_value(),
                    )
                except (QuotaExceeded, RuntimeError) as exc:
                    step.detail["stopped"] = f"{type(exc).__name__}: {exc}"[:200]
                    break
                except (FreeSourceError, httpx.HTTPError) as exc:
                    results[ticker] = f"failed: {type(exc).__name__}"
                    step.detail["stopped"] = "provider error"
                    break
                await _save_state(
                    ctx.pool, epo_state_key(ticker), {"at": dt.datetime.now(dt.UTC).isoformat()}
                )
        step.detail["refreshed"] = results


# ------------------------------------------------------- Companies House, EDINET, Twelve Data


def _provider_settings(jobs: JobsConfig, name: str) -> ProviderJobSettings:
    """The provider's settings; a config saved before the provider existed has none (off)."""
    return jobs.providers.get(name) or ProviderJobSettings()


def companies_house_numbers(
    profiles: HoldingProfilesConfig, tickers: Sequence[str]
) -> dict[str, str]:
    out: dict[str, str] = {}
    for ticker in tickers:
        profile = profiles.for_ticker(ticker)
        if profile and profile.companies_house_number:
            out[ticker] = profile.companies_house_number
    return out


def edinet_holdings(profiles: HoldingProfilesConfig, tickers: Sequence[str]) -> dict[str, str]:
    """EDINET code -> holding ticker."""
    out: dict[str, str] = {}
    for ticker in tickers:
        profile = profiles.for_ticker(ticker)
        if profile and profile.edinet_code:
            out[profile.edinet_code] = ticker
    return out


def edinet_days_to_fetch(
    last_day: dt.date | None, today: dt.date, *, limit: int = EDINET_DAYS_PER_RUN
) -> list[dt.date]:
    """Weekdays (Japan time) from the last fetched day, which is read again because a day's list
    can still grow, up to `today`; the first run looks back `EDINET_FIRST_LOOKBACK_DAYS`. At most
    `limit` days, oldest first, so a long gap is worked off over several runs."""
    start = last_day or today - dt.timedelta(days=EDINET_FIRST_LOOKBACK_DAYS)
    days = [start + dt.timedelta(days=n) for n in range((today - start).days + 1)]
    return [d for d in days if d.weekday() < 5][: max(limit, 0)]


async def _state_times(pool: asyncpg.Pool, keys: list[str], field: str = "at") -> dict[str, Any]:
    rows = await pool.fetch(
        "select key, value from public.sync_state where key = any($1::text[])", keys
    )
    out: dict[str, Any] = {}
    for row in rows:
        value = json.loads(row["value"]) if isinstance(row["value"], str) else row["value"]
        out[row["key"]] = value.get(field) if isinstance(value, dict) else None
    return out


def _as_datetime(value: object) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None


async def companies_house_filings_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with ctx.step("companies_house_filings") as step:
        jobs, profiles = await _configs(ctx.pool)
        provider = _provider_settings(jobs, "companies_house")
        if not settings.companies_house_api_key:
            step.detail["skipped"] = "COMPANIES_HOUSE_API_KEY is not set"
            return
        tickers = await holding_tickers(ctx.pool)
        mapped = companies_house_numbers(profiles, tickers)
        step.detail["unmapped"] = [t for t in tickers if t not in mapped]
        rows = await ctx.pool.fetch(
            "select ticker, max(last_seen_at) seen from public.company_research_observation"
            " where provider='companies_house' and dataset='profile' and ticker = any($1::text[])"
            " group by ticker",
            list(mapped),
        )
        seen = {r["ticker"]: r["seen"] for r in rows}
        due = stalest_first(
            list(mapped),
            seen.get,
            now=dt.datetime.now(dt.UTC),
            refresh_hours=provider.refresh_hours,
            limit=COMPANIES_HOUSE_HOLDINGS_PER_RUN,
        )
        results: dict[str, Any] = {}
        async with httpx.AsyncClient(
            timeout=30, headers={"User-Agent": f"StewardAI/{__version__}"}
        ) as http:
            for ticker in due:
                try:
                    results[ticker] = await collect_companies_house(
                        ctx.pool,
                        http,
                        ticker=ticker,
                        number=mapped[ticker],
                        api_key=settings.companies_house_api_key.get_secret_value(),
                        provider_settings=provider,
                    )
                except (QuotaExceeded, RuntimeError) as exc:
                    step.detail["stopped"] = f"{type(exc).__name__}: {exc}"[:200]
                    break
                except FreeSourceError as exc:
                    results[ticker] = f"failed: {exc.status_code or 'error'}"
                    if exc.status_code in {401, 403, 429}:
                        step.detail["stopped"] = f"HTTP {exc.status_code}"
                        break
                except httpx.HTTPError as exc:
                    results[ticker] = f"failed: {type(exc).__name__}"
        step.detail["refreshed"] = results


EDINET_STATE_KEY = "edinet_filings:last_day"


async def edinet_filings_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with ctx.step("edinet_filings") as step:
        jobs, profiles = await _configs(ctx.pool)
        provider = _provider_settings(jobs, "edinet")
        if not settings.edinet_api_key:
            step.detail["skipped"] = "EDINET_API_KEY is not set"
            return
        tickers = await holding_tickers(ctx.pool)
        holdings = edinet_holdings(profiles, tickers)
        step.detail["unmapped"] = [t for t in tickers if t not in holdings.values()]
        if not holdings:
            step.detail["skipped"] = "no holding has an edinet_code"
            return
        raw_last = (await _state_times(ctx.pool, [EDINET_STATE_KEY], "day")).get(EDINET_STATE_KEY)
        try:
            last_day = dt.date.fromisoformat(str(raw_last)) if raw_last else None
        except ValueError:
            last_day = None
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
        results: dict[str, Any] = {}
        async with httpx.AsyncClient(
            timeout=45, headers={"User-Agent": f"StewardAI/{__version__}"}
        ) as http:
            for day in edinet_days_to_fetch(last_day, today):
                try:
                    results[day.isoformat()] = await collect_edinet_day(
                        ctx.pool,
                        http,
                        day=day,
                        holdings=holdings,
                        api_key=settings.edinet_api_key.get_secret_value(),
                        provider_settings=provider,
                    )
                except (QuotaExceeded, RuntimeError) as exc:
                    step.detail["stopped"] = f"{type(exc).__name__}: {exc}"[:200]
                    break
                except (FreeSourceError, httpx.HTTPError) as exc:
                    results[day.isoformat()] = f"failed: {type(exc).__name__}"
                    step.detail["stopped"] = "provider error"
                    break
                await _save_state(ctx.pool, EDINET_STATE_KEY, {"day": day.isoformat()})
        step.detail["fetched_days"] = results


def twelve_data_state_key(ticker: str) -> str:
    return f"twelve_data_actions:{ticker}"


async def twelve_data_corporate_actions_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with ctx.step("twelve_data_corporate_actions") as step:
        jobs, _profiles = await _configs(ctx.pool)
        provider = _provider_settings(jobs, "twelve_data")
        if not settings.twelve_data_api_key:
            step.detail["skipped"] = "TWELVE_DATA_API_KEY is not set"
            return
        tickers = await holding_tickers(ctx.pool)
        # The route's precondition: a confirmed canonical listing; the symbol is its ticker.
        rows = await ctx.pool.fetch(
            "select distinct on (m.ticker) m.ticker, m.listing_id, l.ticker as symbol"
            " from public.instrument_security_link m"
            " join public.security_listing l on l.id = m.listing_id"
            " where m.status='confirmed' and m.ticker = any($1::text[])",
            tickers,
        )
        listings = {r["ticker"]: (r["listing_id"], r["symbol"]) for r in rows}
        step.detail["without_confirmed_listing"] = [t for t in tickers if t not in listings]
        done = await _state_times(ctx.pool, [twelve_data_state_key(t) for t in listings])
        now = dt.datetime.now(dt.UTC)
        due = stalest_first(
            list(listings),
            lambda t: _as_datetime(done.get(twelve_data_state_key(t))),
            now=now,
            refresh_hours=provider.refresh_hours,
            limit=TWELVE_DATA_HOLDINGS_PER_RUN,
        )
        results: dict[str, Any] = {}
        async with httpx.AsyncClient(
            timeout=30, headers={"User-Agent": f"StewardAI/{__version__}"}
        ) as http:
            for ticker in due:
                listing_id, symbol = listings[ticker]
                try:
                    results[ticker] = await collect_corporate_actions(
                        ctx.pool,
                        http,
                        listing_id=listing_id,
                        symbol=symbol,
                        api_key=settings.twelve_data_api_key.get_secret_value(),
                        since=now.date() - dt.timedelta(days=TWELVE_DATA_LOOKBACK_DAYS),
                    )
                except (QuotaExceeded, RuntimeError) as exc:
                    # HardBlocked (a paid-plan latch) is a QuotaExceeded: stop, never retry.
                    step.detail["stopped"] = f"{type(exc).__name__}: {exc}"[:200]
                    break
                except FreeSourceError as exc:
                    results[ticker] = f"failed: {exc.status_code or 'error'}"
                    if exc.status_code in {401, 403, 429}:
                        step.detail["stopped"] = f"HTTP {exc.status_code}"
                        break
                    continue
                except (TwelveDataError, httpx.HTTPError) as exc:
                    results[ticker] = f"failed: {type(exc).__name__}"
                    continue
                await _save_state(ctx.pool, twelve_data_state_key(ticker), {"at": now.isoformat()})
        step.detail["refreshed"] = results
