"""Per-exchange quote probes (plan D3.4).

`exchange_probes` (monthly, and on demand) asks Finnhub's free `/quote` and Twelve Data's Basic
`/time_series` about an actual held symbol of each exchange the portfolio trades on, and records
the answer per (provider, dataset, MIC) in `coverage_status`, keeping two different things apart:

- **missing coverage** (`not_covered`): the provider answered but knows nothing about the symbol
  on that exchange (Finnhub's all-zero quote, Twelve Data "symbol not found");
- **technical denial** (`paid_only`, `access_unverified`, `quota_exhausted`): the key or plan
  is refused (HTTP 401/403, a plan message, a credit limit).

A plan that excludes one exchange must not close the endpoint for the others, so an
exchange-scoped denial is recorded only per MIC; endpoint-level outcomes (a rejected key, a
quota, a transient error, a working answer) also go through `record_access`. Trial symbols are
never used: a global demo symbol proves nothing about the portfolio's.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.reference.identity import holding_tickers
from steward.reference.price_series import holding_symbols, yahoo_mic
from steward.settings import get_settings
from steward.sources import finnhub as finnhub_source
from steward.sources import twelvedata
from steward.sources.base import AccessResult, AccessState
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.sources.store import (
    CoverageUpdate,
    provider_gate,
    record_access,
    update_coverage,
)

FINNHUB_BUDGET = Budget(per_minute=Decimal(30))
# Twelve Data Basic: 8 credits a minute, 800 a day; a monthly probe needs a handful.
TWELVE_BUDGET = Budget(per_minute=Decimal(8), daily=Decimal(100))


@dataclass(frozen=True)
class ProbeTarget:
    mic: str
    ticker: str
    finnhub_symbol: str | None
    twelve_symbol: str | None


@dataclass(frozen=True)
class ProbeOutcome:
    provider: str
    dataset: str
    mic: str
    symbol: str
    state: AccessState
    detail: str
    endpoint_level: bool


def classify_finnhub(
    quote: finnhub_source.FinnhubQuote | None, error: finnhub_source.FinnhubError | None
) -> tuple[AccessState, str, bool]:
    """(state, detail, endpoint-level) of one Finnhub /quote probe. 403 is the plan excluding
    this exchange (exchange-scoped); 401 the key (endpoint-level)."""
    if error is not None:
        code = error.status_code
        if code == 403:
            return AccessState.PAID_ONLY, "the free plan does not include this exchange", False
        if code == 401:
            return AccessState.ACCESS_UNVERIFIED, "Finnhub rejected the key", True
        if code == 429:
            return AccessState.QUOTA_EXHAUSTED, "Finnhub rate limit", True
        return AccessState.TEMPORARILY_FAILED, str(error)[:200], True
    if quote is None:
        return AccessState.NOT_COVERED, "Finnhub returned an empty quote for the symbol", False
    return AccessState.AVAILABLE, "quote returned for the portfolio symbol", True


async def probe_targets(
    pool: asyncpg.Pool, tickers: list[str], profiles: HoldingProfilesConfig
) -> list[ProbeTarget]:
    """One held symbol per exchange (the first holding on it)."""
    symbols = await holding_symbols(pool, tickers, profiles)
    rows = await pool.fetch(
        "select i.t212_ticker, i.home_mic,"
        " (select x.listing_ticker from public.instrument_identifier x"
        "   where x.ticker = i.t212_ticker and x.listing_ticker is not null"
        "   order by (x.figi = x.composite_figi) desc, x.figi limit 1) as listing_ticker"
        " from public.instrument i where i.t212_ticker = any($1::text[])"
        " order by i.t212_ticker",
        tickers,
    )
    targets: dict[str, ProbeTarget] = {}
    for r in rows:
        yahoo = symbols[r["t212_ticker"]].yahoo
        mic = r["home_mic"] or (yahoo_mic(yahoo) if yahoo else None)
        if not mic or mic in targets:
            continue
        targets[mic] = ProbeTarget(
            mic=mic,
            ticker=r["t212_ticker"],
            # Finnhub and Yahoo share the exchange-suffix convention (SAP.DE, VOD.L).
            finnhub_symbol=yahoo,
            twelve_symbol=r["listing_ticker"],
        )
    return list(targets.values())


async def _record(pool: asyncpg.Pool, outcome: ProbeOutcome, status_code: int | None) -> None:
    await update_coverage(
        pool,
        CoverageUpdate(
            outcome.provider,
            outcome.dataset,
            outcome.mic,
            AccessResult(outcome.state, f"{outcome.symbol}: {outcome.detail}"[:300]),
        ),
    )
    if outcome.endpoint_level:
        await record_access(
            pool,
            outcome.provider,
            outcome.dataset,
            AccessResult(outcome.state, outcome.detail),
            status_code=status_code,
            credits=1 if outcome.state is AccessState.AVAILABLE else 0,
        )


async def run_exchange_probes(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    targets: list[ProbeTarget],
    *,
    finnhub_key: str | None,
    twelve_key: str | None,
    detail: dict[str, Any],
) -> list[ProbeOutcome]:
    governor = PostgresRequestGovernor(pool)
    outcomes: list[ProbeOutcome] = []
    for provider, dataset, key in (
        ("finnhub", "quote", finnhub_key),
        ("twelve_data", "time_series", twelve_key),
    ):
        if not key:
            detail[provider] = "skipped: no key configured"
            continue
        gate = await provider_gate(pool, provider, dataset)
        if not gate.open:
            detail[provider] = f"skipped: {gate.reason}"
            continue
        budget = FINNHUB_BUDGET if provider == "finnhub" else TWELVE_BUDGET
        for target in targets:
            symbol = target.finnhub_symbol if provider == "finnhub" else target.twelve_symbol
            if not symbol:
                continue
            try:
                await governor.reserve(provider, dataset, Decimal(1), budget)
            except QuotaExceeded as exc:
                detail[provider] = f"stopped: {exc}"
                break
            status_code: int | None = None
            if provider == "finnhub":
                try:
                    quote = await finnhub_source.quote(http, key, symbol)
                    state, why, endpoint_level = classify_finnhub(quote, None)
                except finnhub_source.FinnhubError as exc:
                    status_code = exc.status_code
                    state, why, endpoint_level = classify_finnhub(None, exc)
                except httpx.HTTPError as exc:
                    state, why, endpoint_level = (
                        AccessState.TEMPORARILY_FAILED,
                        f"network error ({type(exc).__name__})",
                        True,
                    )
            else:
                try:
                    probe = await twelvedata.probe_time_series(
                        http, key, symbol, exchange=target.mic
                    )
                    state, why = probe.state, probe.detail
                except twelvedata.TwelveDataError as exc:
                    state, why = AccessState.TEMPORARILY_FAILED, str(exc)
                # Missing coverage and a plan denial are scoped to the exchange; a key problem,
                # a credit limit, a transient failure or a working answer is the endpoint's.
                endpoint_level = state not in (AccessState.NOT_COVERED, AccessState.PAID_ONLY)
            outcome = ProbeOutcome(
                provider, dataset, target.mic, symbol, state, why, endpoint_level
            )
            await _record(pool, outcome, status_code)
            outcomes.append(outcome)
            if state in (AccessState.ACCESS_UNVERIFIED, AccessState.QUOTA_EXHAUSTED):
                detail[provider] = f"stopped: {state.value}"
                break
    return outcomes


async def exchange_probes_handler(ctx: JobContext) -> None:
    pool = ctx.pool
    settings = get_settings()
    async with ctx.step("probe") as step:
        profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
        body = profiles.body if profiles else HoldingProfilesConfig()
        tickers = await holding_tickers(pool)
        targets = await probe_targets(pool, tickers, body)
        step.detail["exchanges"] = [t.mic for t in targets]
        async with httpx.AsyncClient(
            timeout=20.0, headers={"User-Agent": f"StewardAI/{__version__}"}
        ) as http:
            outcomes = await run_exchange_probes(
                pool,
                http,
                targets,
                finnhub_key=(
                    settings.finnhub_api_key.get_secret_value()
                    if settings.finnhub_api_key
                    else None
                ),
                twelve_key=(
                    settings.twelve_data_api_key.get_secret_value()
                    if settings.twelve_data_api_key
                    else None
                ),
                detail=step.detail,
            )
        step.detail["outcomes"] = {f"{o.provider}:{o.mic}": o.state.value for o in outcomes}
        step.detail["checked_at"] = dt.datetime.now(dt.UTC).isoformat()
