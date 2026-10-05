"""Scheduled refreshes that spend free-tier provider credits: FMP profile and estimates,
Alpha Vantage annual statements and EPO OPS patent families (D4.2, D4.4, D5.4).

All three are opt-in twice over: the job schedule is off by default and the provider's
`ProviderJobSettings.enabled` must be true. Each reuses the ingestion function its manual route
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
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.free_sources import collect_alpha_vantage_statement
from steward.jobs.runner import JobContext
from steward.reference.identity import holding_tickers
from steward.settings import get_settings
from steward.sources.base import Priority
from steward.sources.company_research import collect_epo_patents
from steward.sources.fmp import collect_fmp_company
from steward.sources.free_market import FreeSourceError
from steward.sources.governor import QuotaExceeded

FMP_HOLDINGS_PER_RUN = 20  # two credits each: well inside the 125-per-endpoint daily cap
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


async def fmp_company_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with ctx.step("fmp_company") as step:
        jobs, profiles = await _configs(ctx.pool)
        provider = jobs.providers["fmp"]
        if not provider.enabled or not settings.fmp_api_key:
            step.detail["skipped"] = "providers.fmp is disabled or FMP_API_KEY is not set"
            return
        tickers = await holding_tickers(ctx.pool)
        # The free plan covers US listings under their EDGAR/Finnhub symbol; no mapping, no call.
        mapped = {
            t: p.sec_ticker for t in tickers if (p := profiles.for_ticker(t)) and p.sec_ticker
        }
        step.detail["unmapped"] = [t for t in tickers if t not in mapped]
        rows = await ctx.pool.fetch(
            "select ticker, max(last_seen_at) seen from public.company_research_observation"
            " where provider='fmp' and dataset='profile' and ticker = any($1::text[])"
            " group by ticker",
            list(mapped),
        )
        seen = {r["ticker"]: r["seen"] for r in rows}
        due = stalest_first(
            list(mapped),
            seen.get,
            now=dt.datetime.now(dt.UTC),
            refresh_hours=provider.refresh_hours,
            limit=FMP_HOLDINGS_PER_RUN,
        )
        results: dict[str, Any] = {}
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
        step.detail["refreshed"] = results


# ------------------------------------------------------------------------- Alpha Vantage


async def alpha_vantage_statements_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with ctx.step("alpha_vantage_statements") as step:
        jobs, profiles = await _configs(ctx.pool)
        provider = jobs.providers["alpha_vantage"]
        if not provider.enabled or not settings.alpha_vantage_api_key:
            step.detail["skipped"] = (
                "providers.alpha_vantage is disabled or ALPHA_VANTAGE_API_KEY is not set"
            )
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
        if (
            not provider.enabled
            or not settings.epo_ops_client_id
            or not settings.epo_ops_client_secret
        ):
            step.detail["skipped"] = "providers.epo_ops is disabled or OPS credentials are not set"
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
