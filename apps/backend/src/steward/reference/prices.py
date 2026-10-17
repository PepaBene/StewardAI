"""Market history jobs.

`price_history` (weekdays after the close):
- Daily bars for every holding (the holding profile's `price_symbol`, else the one derived
  from OpenFIGI), the configured benchmarks and the market strip's indices, through a
  source chain: Stooq first (the source of record); when Stooq refuses, cannot be reached or
  has no symbol, Yahoo Finance's daily bars (unofficial; `yahoo_daily_fallback`) under the
  Yahoo symbol (`steward.reference.price_series`). A backfill of `history_years` the first
  time, the recent days after that. A symbol neither source has is a visible gap (kept with
  its reason for the holding page and the Activity page), never a failed job. Which source
  each holding's series came from is kept too (`price_sources`).
- PX: when Stooq has no `^px` close, the Prague Stock Exchange's PX page (once a day).
- Plausibility: on a sync day, the latest close is compared with Trading212's price; a gap
  beyond the configured tolerance is flagged (timing differences are expected intraday).
- FX history: the ECB's rates for `history_years` from Frankfurter in one request (once, and
  again if `history_years` grows), and a daily consistency check of the Data Portal's latest
  rates against Frankfurter's.

- Price-move trigger: when a holding's latest close moved beyond the event rules' threshold
  (default +-8 %) against the previous close, an event escalation is queued once for that
  holding and day. Without stored daily closes, Trading212's prices on consecutive sync days
  are used.

`holiday_refresh` (twice a year, and on demand): public holidays of the configured
countries for last, this and next year from Nager.Date (last year's so a rate gap reaching
back over New Year is still judged against its holidays).
"""

import asyncio
import datetime as dt
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.company.peer_coverage import price_symbol, uncovered_peers
from steward.config.event_rules import EventRulesConfig
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.fx.convert import FxRate, normalise_currency
from steward.fx.sources import FxSourceError, fetch_cnb_year
from steward.jobs.runner import JobContext
from steward.news.escalation import queue_escalation
from steward.portfolio.sync import store_rates
from steward.reference.identity import bench_tickers, holding_tickers
from steward.reference.intraday import PSE_SYMBOL, refresh_pse_px, store_payload
from steward.reference.listings import derive_listings, resolve_series_listing
from steward.reference.price_series import (
    PriceSource,
    PriceSymbols,
    SeriesRef,
    choose_one,
    describe,
    holding_symbols,
    index_symbols,
    series_mic,
)
from steward.settings import get_settings
from steward.sources import yahoo
from steward.sources.base import AccessResult, AccessState
from steward.sources.credit_group import CreditPart, refund, reserve_group
from steward.sources.free_market import (
    FreeSourceError,
    alpha_daily,
    eodhd_daily,
    eodhd_exchange_symbols,
)
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.sources.market import (
    MarketDataError,
    PriceBar,
    frankfurter_series,
    infer_quote_currency,
    public_holidays,
    quote_unit_of,
    stooq_currency,
    stooq_daily,
)
from steward.sources.store import CoverageUpdate, record_access, update_coverage

GAPS_KEY = "price_gaps"
CHECKS_KEY = "price_checks"
SOURCES_KEY = "price_sources"
FX_BACKFILL_KEY = "fx_history_backfill"
CNB_BACKFILL_KEY = "cnb_history_backfill"
RECENT_DAYS = 10
BACKFILL_KEY = "price_backfill"
# A re-fetched close this far from the stored one means the source restated its history (a
# split or similar adjustment): the whole history is fetched again.
RESTATEMENT_PCT = Decimal(5)
# Close-to-close ratios that look like a share split (or reverse split) rather than a move.
SPLIT_RATIOS = (2, 3, 4, 5, 10, 20)
SPLIT_TOLERANCE = 0.02
NO_STOOQ_SYMBOL = "no Stooq symbol (set price_symbol in the holding profile)"
# Alpha Vantage's free tier is 25 requests a day for the whole account. The price fallback may
# never spend the last ALPHA_VANTAGE_ACCOUNT_RESERVE of them: Company Overview (the valuation
# fallback) draws on the same account and must not be starved by a Stooq outage (B24).
ALPHA_VANTAGE_ACCOUNT_RESERVE = 5


@dataclass
class PriceDeps:
    http: httpx.AsyncClient
    # Stooq's download key (STOOQ_API_KEY); without one Stooq refuses daily downloads.
    api_key: str | None = None
    today: dt.date = field(default_factory=lambda: dt.datetime.now(dt.UTC).date())
    now: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    # Waits between Yahoo requests (Yahoo answers bursts with HTTP 429); None: no wait.
    pace: Callable[[float], Awaitable[None]] | None = None
    # Circuit breaker for the primary source: after this many Stooq transport failures in a row
    # in one run, the rest of the run goes straight to the fallbacks instead of waiting out a
    # timeout per symbol. A Stooq answer (data, or "no data") resets the count.
    stooq_breaker_after: int = 4
    stooq_failures: int = 0


async def store_bars(
    pool: asyncpg.Pool,
    bars: list[PriceBar],
    source: PriceSource = "stooq",
    currency: str | None = None,
    bar_type: str = "adjusted",
    listing_id: int | None = None,
    mic: str | None = None,
    quote_unit: str | None = None,
) -> int:
    """Store daily bars. `currency` is the quote currency code as the source reports it (GBX or
    GBp for pence); `quote_unit` its canonical unit (derived from the currency when omitted).
    The listing and exchange come from the confirmed provider-symbol mapping (any source, under
    the provider's own symbol), else from the symbol's suffix. A bar carries the source's own
    timestamp when it has one."""
    if not bars:
        return 0
    unit = quote_unit or quote_unit_of(currency)
    resolved: dict[str, tuple[int | None, str | None]] = {}
    rows = []
    for b in bars:
        if b.symbol not in resolved:
            found_listing, found_mic = await resolve_series_listing(pool, b.symbol, source)
            resolved[b.symbol] = (found_listing, found_mic)
        found_listing, found_mic = resolved[b.symbol]
        rows.append(
            (
                b.symbol,
                b.date,
                b.open,
                b.high,
                b.low,
                b.close,
                b.volume,
                source,
                currency,
                listing_id if listing_id is not None else found_listing,
                mic or found_mic or series_mic(b.symbol, source),
                unit,
                bar_type,
                b.source_timestamp,
            )
        )
    await pool.executemany(
        "insert into public.price_bar (symbol, date, open, high, low, close, volume, source,"
        " currency, session_date, listing_id, mic, quote_unit, bar_type, access_state,"
        " source_timestamp) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$2,$10,$11,$12,$13,'available',$14)"
        " on conflict (symbol,date,source,bar_type)"
        " do update set open = excluded.open, high = excluded.high, low = excluded.low,"
        " close = excluded.close, volume = excluded.volume, currency = excluded.currency,"
        " session_date=excluded.session_date,"
        " listing_id=coalesce(excluded.listing_id,price_bar.listing_id),"
        " mic=coalesce(excluded.mic,price_bar.mic),quote_unit=excluded.quote_unit,"
        " bar_type=excluded.bar_type,access_state=excluded.access_state,"
        " source_timestamp=coalesce(excluded.source_timestamp,price_bar.source_timestamp),"
        " fetched_at = now()",
        rows,
    )
    return len(bars)


async def symbols_for(
    pool: asyncpg.Pool, tickers: list[str], profiles: HoldingProfilesConfig
) -> dict[str, str | None]:
    """Ticker -> Stooq symbol (profile override first), None when there is none."""
    return {t: s.stooq for t, s in (await holding_symbols(pool, tickers, profiles)).items()}


async def _state(pool: asyncpg.Pool, key: str) -> dict[str, Any]:
    raw = await pool.fetchval("select value from public.sync_state where key = $1", key)
    return dict(json.loads(raw)) if raw else {}


async def _save_state(pool: asyncpg.Pool, key: str, value: dict[str, Any]) -> None:
    await pool.execute(
        "insert into public.sync_state (key, value) values ($1, $2::jsonb)"
        " on conflict (key) do update set value = excluded.value",
        key,
        json.dumps(value, default=str),
    )


async def _backfill_start(
    pool: asyncpg.Pool, deps: PriceDeps, ref: SeriesRef, years: int
) -> tuple[dt.date | None, dt.date, bool, str]:
    """(latest stored date, full-history start, whether a backfill is due, state key)."""
    last = await pool.fetchval(
        "select max(date) from public.price_bar where symbol = $1 and source = $2"
        " and bar_type = 'adjusted'",
        ref.symbol,
        ref.source,
    )
    full_start = deps.today - dt.timedelta(days=365 * years + 5)
    # How far back the stored history was requested. History stored before this was
    # recorded was requested for the history_years of its time: taken as the current one.
    state_key = (
        f"{BACKFILL_KEY}:{ref.symbol}"
        if ref.source == "stooq"
        else f"{BACKFILL_KEY}:{ref.source}:{ref.symbol}"
    )
    recorded = (await _state(pool, state_key)).get("from")
    if last and not recorded:
        await _save_state(pool, state_key, {"from": full_start})
    covered_from = dt.date.fromisoformat(recorded) if recorded else full_start
    # A first run, or a longer history_years than the stored history was fetched for.
    backfill = last is None or covered_from > full_start
    return last, full_start, backfill, state_key


async def collect_symbol(
    pool: asyncpg.Pool,
    deps: PriceDeps,
    symbol: str,
    years: int,
    notes: dict[str, str] | None = None,
) -> tuple[int, str | None]:
    """Stooq: (bars stored, gap reason or None).

    The recent days are re-fetched each run; when they disagree with the stored closes
    (Stooq adjusted its history for a split), the full history is fetched and overwrites
    the stored bars, so drawdown, volatility and beta keep working on one adjusted series.
    """
    ref = SeriesRef(symbol, "stooq")
    last, full_start, backfill, state_key = await _backfill_start(pool, deps, ref, years)
    start = last - dt.timedelta(days=RECENT_DAYS) if last and not backfill else full_start
    try:
        bars = await stooq_daily(deps.http, symbol, start, deps.today, api_key=deps.api_key)
    except (MarketDataError, httpx.HTTPError) as exc:
        return 0, f"Stooq unreachable: {describe(exc)}"[:300]
    if not bars:
        return 0, None if last else f"Stooq has no data for {symbol}"
    if backfill:
        await _save_state(pool, state_key, {"from": full_start})
    elif await _restated(pool, bars):
        try:
            bars = await stooq_daily(
                deps.http, symbol, full_start, deps.today, api_key=deps.api_key
            )
        except (MarketDataError, httpx.HTTPError) as exc:
            # Storing only the restated recent bars would put a false jump in the series.
            return 0, f"Stooq restated history; refetch failed: {describe(exc)}"[:300]
        if notes is not None:
            notes[symbol] = f"history restated by Stooq; refetched {len(bars)} bars"
    return await store_bars(pool, bars, "stooq", stooq_currency(symbol)), None


async def _yahoo_daily(
    pool: asyncpg.Pool, deps: PriceDeps, symbol: str, start: dt.date
) -> tuple[yahoo.YahooChart, list[PriceBar]]:
    if deps.pace is not None:
        await deps.pace(1.0)
    chart, bars = await yahoo.yahoo_daily(deps.http, symbol, start, deps.now)
    await store_payload(
        pool, "yahoo", symbol, "chart_1d", chart.url, chart.status, chart.content_type, chart.body
    )
    # Stored under the symbol asked for (Yahoo echoes it, but the case could differ).
    return chart, [replace(b, symbol=symbol) for b in bars]


async def collect_yahoo(
    pool: asyncpg.Pool,
    deps: PriceDeps,
    symbol: str,
    years: int,
    notes: dict[str, str] | None = None,
) -> tuple[int, str | None]:
    """Yahoo daily bars (the fallback): (bars stored, gap reason or None). Same backfill and
    restatement rules as Stooq; the quote currency Yahoo reports is stored with the bars."""
    ref = SeriesRef(symbol, "yahoo")
    last, full_start, backfill, state_key = await _backfill_start(pool, deps, ref, years)
    start = last - dt.timedelta(days=RECENT_DAYS) if last and not backfill else full_start
    try:
        chart, bars = await _yahoo_daily(pool, deps, symbol, start)
    except yahoo.YahooNoData:
        return 0, None if last else f"Yahoo has no data for {symbol}"
    except (yahoo.YahooError, httpx.HTTPError, ValueError) as exc:
        return 0, f"Yahoo unreachable: {describe(exc)}"[:300]
    if not bars:
        return 0, None if last else f"Yahoo has no data for {symbol}"
    if backfill:
        await _save_state(pool, state_key, {"from": full_start})
    elif await _restated(pool, bars, "yahoo"):
        try:
            chart, bars = await _yahoo_daily(pool, deps, symbol, full_start)
        except (yahoo.YahooError, httpx.HTTPError, ValueError) as exc:
            return 0, f"Yahoo restated history; refetch failed: {describe(exc)}"[:300]
        if notes is not None:
            notes[symbol] = f"history restated by Yahoo; refetched {len(bars)} bars"
    return await store_bars(pool, bars, "yahoo", chart.meta.currency), None


async def collect_series(
    pool: asyncpg.Pool,
    deps: PriceDeps,
    symbols: PriceSymbols,
    years: int,
    yahoo_fallback: bool,
    notes: dict[str, str],
    counts: dict[str, int],
    yahoo_hint: str = "set yahoo_symbol in the holding profile",
) -> tuple[dict[str, Any], str | None]:
    """The source chain for one holding or index: Stooq, else Yahoo. Returns (which source
    delivered, with why Stooq did not; gap reason when neither did)."""
    stooq_gap = NO_STOOQ_SYMBOL
    if symbols.stooq and deps.stooq_failures >= deps.stooq_breaker_after:
        stooq_gap = f"Stooq skipped: unreachable {deps.stooq_failures} times in a row this run"
    elif symbols.stooq:
        count, gap = await collect_symbol(pool, deps, symbols.stooq, years, notes)
        counts[symbols.stooq] = count
        deps.stooq_failures = deps.stooq_failures + 1 if gap and "unreachable" in gap else 0
        if gap is None:
            return {"source": "stooq", "symbol": symbols.stooq}, None
        stooq_gap = gap
    if not yahoo_fallback:
        return {}, stooq_gap
    if not symbols.yahoo:
        return {}, f"{stooq_gap}; no Yahoo symbol ({yahoo_hint})"
    count, gap = await collect_yahoo(pool, deps, symbols.yahoo, years, notes)
    counts[symbols.yahoo] = count
    if gap is not None:
        return {}, f"{stooq_gap}; {gap}"[:400]
    return {"source": "yahoo", "symbol": symbols.yahoo, "stooq_gap": stooq_gap}, None


def _gate_closed(row: asyncpg.Record) -> bool:
    """A fallback endpoint is used only while it is audited `available`, not hard-blocked and
    outside a provider Retry-After window (a quota message sets one until the next day)."""
    retry_at = row["retry_at"]
    return bool(
        row["state"] != "available"
        or row["hard_blocked"]
        or (retry_at is not None and retry_at > dt.datetime.now(dt.UTC))
    )


def _scaled_adjusted(rows: list[Any], symbol: str) -> list[PriceBar]:
    """Adjusted-basis bars from a provider's adjusted close: the open/high/low are scaled by the
    same factor as the close, so a bar's shape is kept on the adjusted scale."""
    out = []
    for row in rows:
        if row.adjusted_close is None or row.close <= 0 or row.adjusted_close <= 0:
            continue
        factor = row.adjusted_close / row.close
        out.append(
            PriceBar(
                symbol,
                row.day,
                row.open * factor,
                row.high * factor,
                row.low * factor,
                row.adjusted_close,
                row.volume,
            )
        )
    return out


async def _not_covered(
    pool: asyncpg.Pool, provider: str, dataset: str, key: str, reason: str
) -> None:
    await update_coverage(
        pool,
        CoverageUpdate(provider, dataset, key, AccessResult(AccessState.NOT_COVERED, reason)),
    )


async def collect_eodhd_fallbacks(
    pool: asyncpg.Pool,
    deps: PriceDeps,
    tickers: list[str],
    profiles: HoldingProfilesConfig,
    provider_settings: Any,
    api_key: str | None,
    detail: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Use verified EODHD symbols only for profile-mapped held listings with source gaps.

    One atomic `eod` budget is shared by exchange-list checks and data calls, capped at the
    documented 20/day even if configuration is accidentally higher. The series is stored under
    the EODHD symbol: `close` as the raw basis and `adjusted_close` (split and dividend
    adjusted) as the adjusted basis readers use.
    """
    if not api_key:
        return {}
    gates = await pool.fetch(
        """select endpoint,state,hard_blocked,retry_at from public.provider_access
           where provider='eodhd' and endpoint in ('eod','symbol_search')"""
    )
    if len(gates) != 2 or any(_gate_closed(r) for r in gates):
        detail["eodhd"] = "skipped: both EODHD endpoints must pass the free-key access audit"
        return {}
    budget = Budget(
        daily=Decimal(min(provider_settings.daily_quota or 20, 20)),
        monthly=Decimal(provider_settings.monthly_quota)
        if provider_settings.monthly_quota
        else None,
        reserve=Decimal(provider_settings.reserve_credits),
    )
    governor = PostgresRequestGovernor(pool)
    listings: dict[str, list[dict[str, Any]]] = {}
    results: dict[str, dict[str, Any]] = {}
    for ticker in tickers:
        profile = profiles.for_ticker(ticker)
        symbol = profile.eodhd_symbol if profile else None
        if not symbol:
            continue
        code, dot, exchange = symbol.rpartition(".")
        if not dot or not code or not exchange:
            detail[ticker] = {"eodhd": "symbol must include its exchange suffix"}
            continue
        mapping = await pool.fetchrow(
            """select m.listing_id,l.mic,l.currency,i.currency as instrument_currency
               from public.instrument_security_link m
               join public.security_listing l on l.id=m.listing_id
               join public.instrument i on i.t212_ticker=m.ticker
               where m.ticker=$1 and m.status='confirmed' and l.status='active'""",
            ticker,
        )
        if mapping is None:
            detail[ticker] = {"eodhd": "skipped: confirmed canonical listing match is missing"}
            continue
        if exchange not in listings:
            try:
                await governor.reserve("eodhd", "eod", Decimal(1), budget)
                listings[exchange] = await eodhd_exchange_symbols(deps.http, api_key, exchange)
            except QuotaExceeded as exc:
                detail[ticker] = {"eodhd": f"skipped: {exc}"}
                break
            except (FreeSourceError, httpx.HTTPError) as exc:
                status_code = exc.status_code if isinstance(exc, FreeSourceError) else None
                result = _provider_error_result(exc)
                await record_access(
                    pool,
                    "eodhd",
                    "symbol_search",
                    result,
                    status_code=status_code,
                )
                detail[ticker] = {"eodhd": "exchange-list request failed"}
                continue
            else:
                await record_access(
                    pool,
                    "eodhd",
                    "symbol_search",
                    AccessResult(AccessState.AVAILABLE, "exchange list retrieved"),
                    credits=1,
                )
        confirmed = any(
            str(row.get("Code") or row.get("code") or "").casefold() == code.casefold()
            for row in listings[exchange]
        )
        if not confirmed:
            await _not_covered(
                pool, "eodhd", "eod", ticker, "exact exchange symbol absent from provider list"
            )
            detail[ticker] = {"eodhd": "symbol not found on the selected exchange"}
            continue
        try:
            await governor.reserve("eodhd", "eod", Decimal(1), budget)
            daily = await eodhd_daily(deps.http, api_key, symbol)
        except QuotaExceeded as exc:
            detail[ticker] = {"eodhd": f"skipped: {exc}"}
            break
        except (FreeSourceError, httpx.HTTPError) as exc:
            status_code = exc.status_code if isinstance(exc, FreeSourceError) else None
            await record_access(
                pool,
                "eodhd",
                "eod",
                _provider_error_result(exc),
                status_code=status_code,
            )
            detail[ticker] = {"eodhd": "daily request failed; previous price history retained"}
            continue
        if not daily:
            detail[ticker] = {"eodhd": "verified symbol has no EOD data"}
            continue
        quote_currency, unit_basis = infer_quote_currency(
            mapping["mic"], mapping["currency"], mapping["instrument_currency"]
        )
        raw_bars = [
            PriceBar(symbol, bar.day, bar.open, bar.high, bar.low, bar.close, bar.volume)
            for bar in daily
        ]
        adjusted_bars = _scaled_adjusted(daily, symbol)
        await store_bars(
            pool,
            raw_bars,
            "eodhd",
            quote_currency,
            "raw",
            mapping["listing_id"],
            mapping["mic"],
        )
        await store_bars(
            pool,
            adjusted_bars,
            "eodhd",
            quote_currency,
            "adjusted",
            mapping["listing_id"],
            mapping["mic"],
        )
        await record_access(
            pool,
            "eodhd",
            "eod",
            AccessResult(AccessState.AVAILABLE, "verified EOD bars received"),
            credits=1,
            response_bytes=None,
        )
        results[ticker] = {
            "source": "eodhd",
            "symbol": symbol,
            "bars": len(daily),
            "adjusted_bars": len(adjusted_bars),
            "basis": "raw_and_adjusted" if adjusted_bars else "raw",
            "quote_unit": quote_unit_of(quote_currency),
            "quote_unit_basis": unit_basis,
            "history_limit": "free plan: up to one year",
        }
    return results


async def collect_alpha_vantage_fallbacks(
    pool: asyncpg.Pool,
    deps: PriceDeps,
    tickers: list[str],
    profiles: HoldingProfilesConfig,
    provider_settings: Any,
    api_key: str | None,
    detail: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Fill held price gaps from explicitly mapped Alpha Vantage symbols.

    The account-wide 25-call budget is shared with Company Overview, source audits and
    statement probes. The account credit and the endpoint credit are reserved together in one
    transaction (a refused endpoint credit never burns the account credit), the price fallback
    leaves ALPHA_VANTAGE_ACCOUNT_RESERVE calls to Company Overview, and a reservation is
    refunded when the request never reached the provider. Both access gates must be available
    so an unverified or paid endpoint is never called.
    """
    if not api_key:
        return {}
    gates = await pool.fetch(
        """select endpoint,state,hard_blocked,retry_at from public.provider_access
           where provider='alpha_vantage' and endpoint in ('daily_adjusted','account_free')"""
    )
    by_endpoint = {row["endpoint"]: row for row in gates}
    if len(by_endpoint) != 2 or any(_gate_closed(row) for row in by_endpoint.values()):
        detail["alpha_vantage"] = "skipped: daily and shared-budget gates must be available"
        return {}
    endpoint_budget = Budget(
        daily=Decimal(min(provider_settings.daily_quota or 25, 25)),
        monthly=Decimal(provider_settings.monthly_quota)
        if provider_settings.monthly_quota
        else None,
        reserve=Decimal(provider_settings.reserve_credits),
    )
    account_budget = Budget(
        daily=Decimal(25),
        reserve=Decimal(max(ALPHA_VANTAGE_ACCOUNT_RESERVE, provider_settings.reserve_credits)),
    )
    results: dict[str, dict[str, Any]] = {}
    for ticker in tickers:
        profile = profiles.for_ticker(ticker)
        provider_symbol = profile.alpha_vantage_symbol if profile else None
        if not provider_symbol:
            continue
        mapping = await pool.fetchrow(
            """select m.listing_id,l.mic,l.currency,i.currency as instrument_currency
               from public.instrument_security_link m
               join public.security_listing l on l.id=m.listing_id
               join public.instrument i on i.t212_ticker=m.ticker
               where m.ticker=$1 and m.status='confirmed' and l.status='active'""",
            ticker,
        )
        if mapping is None:
            detail[ticker] = {"alpha_vantage": "skipped: confirmed canonical listing is missing"}
            continue
        reservation = None
        try:
            reservation = await reserve_group(
                pool,
                [
                    CreditPart("alpha_vantage", "account_free", Decimal(1), account_budget),
                    CreditPart("alpha_vantage", "daily_adjusted", Decimal(1), endpoint_budget),
                ],
            )
            payload, daily = await alpha_daily(deps.http, api_key, provider_symbol)
        except QuotaExceeded as exc:
            detail[ticker] = {"alpha_vantage": f"skipped: {exc}"}
            break
        except (FreeSourceError, httpx.HTTPError) as exc:
            if reservation is not None and not getattr(exc, "provider_charged", False):
                await refund(pool, reservation)
            state = getattr(exc, "state", None)
            if state is AccessState.NOT_COVERED:
                await _not_covered(pool, "alpha_vantage", "daily_adjusted", ticker, str(exc)[:200])
            else:
                await record_access(
                    pool,
                    "alpha_vantage",
                    "daily_adjusted",
                    _provider_error_result(exc),
                    status_code=exc.status_code if isinstance(exc, FreeSourceError) else None,
                )
            detail[ticker] = {
                "alpha_vantage": f"request failed ({state.value if state else 'error'});"
                " previous price history retained"
            }
            continue
        if not daily:
            detail[ticker] = {"alpha_vantage": "no mapped history returned"}
            continue
        quote_currency, unit_basis = infer_quote_currency(
            mapping["mic"], mapping["currency"], mapping["instrument_currency"]
        )
        raw_bars = [
            PriceBar(provider_symbol, row.day, row.open, row.high, row.low, row.close, row.volume)
            for row in daily
        ]
        adjusted_bars = _scaled_adjusted(daily, provider_symbol)
        await store_bars(
            pool,
            raw_bars,
            "alpha_vantage",
            quote_currency,
            "raw",
            mapping["listing_id"],
            mapping["mic"],
        )
        await store_bars(
            pool,
            adjusted_bars,
            "alpha_vantage",
            quote_currency,
            "adjusted",
            mapping["listing_id"],
            mapping["mic"],
        )
        await record_access(
            pool,
            "alpha_vantage",
            "daily_adjusted",
            AccessResult(AccessState.AVAILABLE, "raw and adjusted daily bars received"),
            credits=1,
            response_bytes=len(json.dumps(payload)),
        )
        results[ticker] = {
            "source": "alpha_vantage",
            "symbol": provider_symbol,
            "bars": len(daily),
            "basis": "raw_and_adjusted",
            "adjustment_source": "Alpha Vantage adjusted close",
            "quote_unit": quote_unit_of(quote_currency),
            "quote_unit_basis": unit_basis,
        }
    return results


def _next_utc_midnight(now: dt.datetime) -> dt.datetime:
    return dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time.min, dt.UTC)


def _provider_error_result(exc: Exception, now: dt.datetime | None = None) -> AccessResult:
    """The typed access outcome of a provider failure. A provider's own HTTP-200 message wins
    (`exc.state`: an Alpha Vantage "premium endpoint" is PAID_ONLY, "25 requests per day" is
    QUOTA_EXHAUSTED); otherwise the HTTP status decides."""
    now = now or dt.datetime.now(dt.UTC)
    code = exc.status_code if isinstance(exc, FreeSourceError) else None
    text = str(exc).casefold()
    typed: AccessState | None = getattr(exc, "state", None)
    if typed is AccessState.PAID_ONLY:
        return AccessResult(
            typed, "provider reports a premium-only endpoint; hard-blocked pending a plan review"
        )
    if typed is AccessState.QUOTA_EXHAUSTED:
        return AccessResult(
            typed,
            "provider free quota message",
            retry_at=_next_utc_midnight(now) if "day" in text else now + dt.timedelta(minutes=2),
        )
    if typed is AccessState.ACCESS_UNVERIFIED:
        return AccessResult(typed, "provider rejected the key; hard-blocked pending a key check")
    if code in (401, 403):
        state = (
            AccessState.PAID_ONLY
            if any(term in text for term in ("premium", "upgrade", "subscription required"))
            else AccessState.ACCESS_UNVERIFIED
        )
        detail = "provider denied access; endpoint hard-blocked pending configuration review"
    elif code == 429:
        state, detail = AccessState.QUOTA_EXHAUSTED, "provider free quota exhausted"
    else:
        state, detail = AccessState.TEMPORARILY_FAILED, "provider request failed"
    return AccessResult(state, detail)


async def _restated(
    pool: asyncpg.Pool, bars: list[PriceBar], source: PriceSource = "stooq"
) -> bool:
    """Whether any fetched close differs from the stored one on the same date by more
    than RESTATEMENT_PCT."""
    stored = {
        r["date"]: Decimal(r["close"])
        for r in await pool.fetch(
            "select date, close from public.price_bar where symbol = $1 and source = $3"
            " and bar_type = 'adjusted'"
            " and date = any($2::date[])",
            bars[0].symbol,
            [b.date for b in bars],
            source,
        )
    }
    return any(
        b.date in stored and abs(b.close / stored[b.date] - 1) * 100 > RESTATEMENT_PCT for b in bars
    )


def _same_currency(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and normalise_currency(a) == normalise_currency(b)


async def plausibility(
    pool: asyncpg.Pool,
    ticker: str,
    symbol: str | SeriesRef,
    tolerance_pct: float,
    notes: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Latest stored close (the holding's chosen series) vs Trading212's price on the same
    day, when both exist and are quoted in the same currency (a line traded in another
    currency than the series is skipped, with a note, rather than flagged every day)."""
    ref = symbol if isinstance(symbol, SeriesRef) else SeriesRef(symbol, "stooq")
    row = await pool.fetchrow(
        "select p.snapshot_date, p.current_price, p.instrument_currency, b.close, b.currency"
        " from public.position_snapshot p join public.price_bar b on b.symbol = $2"
        " and b.date = p.snapshot_date and b.source = $3"
        " where p.ticker = $1 order by p.snapshot_date desc limit 1",
        ticker,
        ref.symbol,
        ref.source,
    )
    if row is None or not row["current_price"]:
        return None
    quoted = row["currency"] or (stooq_currency(ref.symbol) if ref.source == "stooq" else None)
    if not _same_currency(quoted, row["instrument_currency"]):
        if notes is not None:
            notes[ticker] = (
                f"not compared: Trading212 quotes {row['instrument_currency']},"
                f" {ref.symbol} is {quoted or 'in an unknown currency'}"
            )
        return None
    diff = (Decimal(row["close"]) / Decimal(row["current_price"]) - 1) * 100
    return {
        "date": row["snapshot_date"].isoformat(),
        "stooq_close": float(row["close"]),
        "close": float(row["close"]),
        "source": ref.name,
        "symbol": ref.symbol,
        "trading212_price": float(row["current_price"]),
        "difference_pct": round(float(diff), 3),
        "flagged": abs(diff) > Decimal(str(tolerance_pct)),
    }


async def cnb_history(
    pool: asyncpg.Pool, deps: PriceDeps, start: dt.date, detail: dict[str, Any]
) -> None:
    """ČNB's fixings back to `start`, one yearly file per year, once (and again when `start`
    moves earlier). The sync stores only each day's fixing, so a CZK book had ČNB rates from
    its first sync only, and every series converted to CZK (the benchmark in the risk report)
    was cut to those few weeks. A rate already stored is never overwritten."""
    state = await _state(pool, CNB_BACKFILL_KEY)
    done_from = dt.date.fromisoformat(state["from"]) if state.get("from") else None
    if state.get("done") and done_from is not None and done_from <= start:
        return
    stored = 0
    for year in range(start.year, deps.today.year + 1):
        try:
            rates = await fetch_cnb_year(deps.http, year)
        except (FxSourceError, httpx.HTTPError) as exc:
            detail["cnb_backfill_error"] = f"{year}: {str(exc)[:200]}"
            return  # retried on the next run; nothing is marked done
        await store_rates(pool, [r for r in rates if r.rate_date >= start], overwrite=False)
        stored += len(rates)
    await _save_state(pool, CNB_BACKFILL_KEY, {"done": True, "from": start, "rates": stored})
    detail["cnb_backfill"] = {"from": start.isoformat(), "rates": stored}


async def fx_history(
    pool: asyncpg.Pool, deps: PriceDeps, years: int, detail: dict[str, Any]
) -> None:
    state = await _state(pool, FX_BACKFILL_KEY)
    start = deps.today - dt.timedelta(days=365 * years + 5)
    # Once, and again when history_years grows past the range fetched before. Frankfurter
    # returns a whole multi-year range in one response (no range cap).
    done_from = dt.date.fromisoformat(state["from"]) if state.get("from") else None
    if not state.get("done") or (done_from is not None and done_from > start):
        try:
            rates = await frankfurter_series(deps.http, start, deps.today)
        except (MarketDataError, httpx.HTTPError, ValueError) as exc:
            detail["fx_backfill_error"] = str(exc)[:300]
            return
        # Never overwrite a rate the Data Portal delivered: Frankfurter only fills history.
        await store_rates(pool, [_frankfurter(r) for r in rates], overwrite=False)
        await _save_state(pool, FX_BACKFILL_KEY, {"done": True, "from": start, "rates": len(rates)})
        detail["fx_backfill"] = {"from": start.isoformat(), "rates": len(rates)}
    await cnb_history(pool, deps, start, detail)
    # Consistency: the Data Portal's latest stored rates against Frankfurter's.
    latest = await pool.fetch(
        "select rate_date, currency, published_rate from public.fx_rate where source = 'ecb'"
        " and provider = 'ecb-data-portal' and rate_date = (select max(rate_date) from"
        " public.fx_rate where source = 'ecb' and provider = 'ecb-data-portal')"
    )
    if not latest:
        return
    day = latest[0]["rate_date"]
    try:
        check = {
            (r.rate_date, r.currency): r.published_rate
            for r in await frankfurter_series(deps.http, day, day)
        }
    except (MarketDataError, httpx.HTTPError, ValueError) as exc:
        detail["fx_consistency_error"] = str(exc)[:300]
        return
    mismatches = [
        f"{r['currency']}: portal {r['published_rate']}"
        f" vs Frankfurter {check[(day, r['currency'])]}"
        for r in latest
        if (day, r["currency"]) in check and check[(day, r["currency"])] != r["published_rate"]
    ]
    detail["fx_consistency"] = {"date": day.isoformat(), "mismatches": mismatches}


def _frankfurter(rate: FxRate) -> FxRate:
    return replace(rate, provider="frankfurter")


def _looks_like_split(ratio: float) -> bool:
    return any(
        abs(ratio * n - 1) < SPLIT_TOLERANCE or abs(ratio / n - 1) < SPLIT_TOLERANCE
        for n in SPLIT_RATIOS
    )


async def latest_move(
    pool: asyncpg.Pool,
    ticker: str,
    symbol: str | SeriesRef | None,
    today: dt.date,
    notes: dict[str, str] | None = None,
) -> tuple[dt.date, float, str] | None:
    """(date, close-to-close % move, price source) of the latest close, if recent.

    Trading212's prices are not split-adjusted, so a fallback move whose ratio is a common
    split ratio (2:1, 1:10, ...) is skipped with a note instead of raising a price move.
    """
    ref = SeriesRef(symbol, "stooq") if isinstance(symbol, str) else symbol
    rows = []
    source = ref.symbol if ref else "trading212"
    if ref:
        rows = await pool.fetch(
            "select date, close from public.price_bar where symbol = $1 and source = $2"
            " and bar_type = 'adjusted'"
            " order by date desc limit 2",
            ref.symbol,
            ref.source,
        )
    if len(rows) < 2:
        source = "trading212"
        rows = await pool.fetch(
            "select snapshot_date as date, current_price as close from public.position_snapshot"
            " where ticker = $1 order by snapshot_date desc limit 2",
            ticker,
        )
    if len(rows) < 2 or not rows[1]["close"]:
        return None
    day = rows[0]["date"]
    if day < today - dt.timedelta(days=4):
        return None  # history backfill, not today's move
    ratio = Decimal(rows[0]["close"]) / Decimal(rows[1]["close"])
    if source == "trading212" and _looks_like_split(float(ratio)):
        if notes is not None:
            notes[ticker] = f"price ratio {float(ratio):.4f} on {day} looks like a split; skipped"
        return None
    return day, float((ratio - 1) * 100), source


async def _accept_fallbacks(
    pool: asyncpg.Pool,
    results: dict[str, dict[str, Any]],
    holdings: dict[str, PriceSymbols],
    gaps: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    detail: dict[str, Any],
) -> None:
    """Record a fallback's source and clear the holding's gap only when the readers will
    actually serve the stored series (A2): `choose_series` must pick it. A series that was
    stored but is not served (an older Stooq/Yahoo series is fresher, or the symbol the
    reader looks up differs) leaves the gap visible with the reason."""
    for ticker, result in results.items():
        ref = await choose_one(pool, holdings[ticker])
        if ref is not None and ref.source == result.get("source"):
            result["stooq_gap"] = (gaps.pop(ticker, None) or {}).get("reason")
            sources[ticker] = result
            result["served"] = True
            continue
        result["served"] = False
        note = (
            f"{result.get('source')} bars stored under {result.get('symbol')} but readers serve"
            f" {ref.name + ' ' + ref.symbol if ref else 'no series'}; gap kept"
        )
        result["note"] = note
        if ticker in gaps:
            gaps[ticker]["reason"] = f"{gaps[ticker].get('reason', '')}; {note}"[:400]
        detail.setdefault("unserved", {})[ticker] = note


async def run_price_history(ctx: JobContext, deps: PriceDeps) -> None:
    pool = ctx.pool
    async with ctx.step("load_config") as step:
        jobs = await load_active(pool, "jobs", JobsConfig)
        profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
        await ctx.record_config_version(jobs.id if jobs else None)
        await ctx.record_config_version(profiles.id if profiles else None)
        settings = (jobs.body if jobs else JobsConfig()).prices
        tickers = await holding_tickers(pool)
        bench = await bench_tickers(pool, tickers)
        holdings = await holding_symbols(
            pool, [*tickers, *bench], profiles.body if profiles else HoldingProfilesConfig()
        )
        step.detail["bench"] = bench
        step.detail.update(
            symbols={t: s.stooq for t, s in holdings.items()},
            yahoo_symbols={t: s.yahoo for t, s in holdings.items()},
            benchmarks=settings.benchmark_symbols,
            yahoo_daily_fallback=settings.yahoo_daily_fallback,
        )
        # Benchmarks and the market strip's indices, each symbol once.
        indices = list(
            dict.fromkeys(settings.benchmark_symbols + [i.symbol for i in settings.market_strip])
        )
        step.detail["market_strip"] = [i.symbol for i in settings.market_strip]

    async with ctx.step("listings") as step:
        # The canonical listing of each holding (and its provider symbols) before bars are
        # stored, so every bar can carry listing_id and mic (A17). A failure here is a note,
        # never a failed price job.
        try:
            derived = await derive_listings(
                pool,
                list(holdings),
                profiles.body if profiles else HoldingProfilesConfig(),
                today=deps.today,
            )
        except asyncpg.PostgresError as exc:
            step.detail["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        else:
            step.detail["listings"] = {r.ticker: r.status for r in derived}
            step.detail["needs_owner_confirmation"] = [
                r.ticker for r in derived if r.status == "suggested"
            ]

    gaps: dict[str, dict[str, Any]] = {}
    checks: dict[str, Any] = {}
    sources: dict[str, dict[str, Any]] = {}
    async with ctx.step("prices") as step:
        counts: dict[str, int] = {}
        restated: dict[str, str] = {}
        unchecked: dict[str, str] = {}
        wanted: list[tuple[str | None, str, PriceSymbols]] = [
            (t, t, s) for t, s in holdings.items()
        ] + [(None, b, index_symbols(settings, b)) for b in indices]
        # Proxy ETFs for the ex-ante risk model's short-history backfill (quant plan Q0.3).
        wanted += [
            (None, p.symbol, PriceSymbols(stooq=p.symbol, yahoo=p.yahoo()))
            for p in settings.proxy_series
            if p.symbol not in indices
        ]
        # Confirmed peers not held: a close for their multiples in the peer comparison.
        peers = await uncovered_peers(pool)
        step.detail["peers"] = peers
        wanted += [
            (None, price_symbol(s), PriceSymbols(stooq=price_symbol(s), yahoo=s)) for s in peers
        ]
        for ticker, key, symbols in wanted:
            used, gap = await collect_series(
                pool,
                deps,
                symbols,
                settings.history_years if ticker else settings.reference_history_years,
                settings.yahoo_daily_fallback,
                restated,
                counts,
                "set yahoo_symbol in the holding profile"
                if ticker
                else "Yahoo has no such index"
                if key == PSE_SYMBOL
                else "set its Yahoo symbol in Config: Jobs: Prices: Market strip",
            )
            if used:
                sources[key] = used
            if gap:
                gaps[key] = {"symbol": symbols.stooq, "yahoo_symbol": symbols.yahoo, "reason": gap}
            if ticker is not None and not gap:
                ref = await choose_one(pool, symbols)
                check = (
                    await plausibility(
                        pool, ticker, ref, settings.plausibility_tolerance_pct, unchecked
                    )
                    if ref
                    else None
                )
                if check:
                    checks[ticker] = check
        step.detail.update(stored=counts, gaps=gaps)
        fallbacks = {k: v for k, v in sources.items() if v.get("source") != "stooq"}
        if fallbacks:
            step.detail["fallbacks"] = fallbacks
        if restated:
            step.detail["restated"] = restated
        if unchecked:
            step.detail["plausibility_skipped"] = unchecked
        step.detail["flagged"] = {t: c for t, c in checks.items() if c["flagged"]}
        now = dt.datetime.now(dt.UTC).isoformat()
        await _save_state(pool, GAPS_KEY, {"at": now, "gaps": gaps})
        await _save_state(pool, CHECKS_KEY, {"at": now, "checks": checks})
        await _save_state(pool, SOURCES_KEY, {"at": now, "sources": sources})

    provider_credentials = get_settings()
    alpha_key = provider_credentials.alpha_vantage_api_key
    if alpha_key and any(ticker in gaps for ticker in holdings):
        async with ctx.step("alpha_vantage_fallback") as step:
            alpha = await collect_alpha_vantage_fallbacks(
                pool,
                deps,
                [ticker for ticker in holdings if ticker in gaps],
                profiles.body if profiles else HoldingProfilesConfig(),
                (jobs.body if jobs else JobsConfig()).providers["alpha_vantage"],
                alpha_key.get_secret_value(),
                step.detail,
            )
            await _accept_fallbacks(pool, alpha, holdings, gaps, sources, step.detail)
            step.detail["fallbacks"] = alpha
            now = dt.datetime.now(dt.UTC).isoformat()
            await _save_state(pool, GAPS_KEY, {"at": now, "gaps": gaps})
            await _save_state(pool, SOURCES_KEY, {"at": now, "sources": sources})

    eodhd_key = provider_credentials.eodhd_api_key
    if eodhd_key and any(ticker in gaps for ticker in holdings):
        async with ctx.step("eodhd_fallback") as step:
            eodhd = await collect_eodhd_fallbacks(
                pool,
                deps,
                [ticker for ticker in holdings if ticker in gaps],
                profiles.body if profiles else HoldingProfilesConfig(),
                (jobs.body if jobs else JobsConfig()).providers["eodhd"],
                eodhd_key.get_secret_value(),
                step.detail,
            )
            await _accept_fallbacks(pool, eodhd, holdings, gaps, sources, step.detail)
            step.detail["fallbacks"] = eodhd
            now = dt.datetime.now(dt.UTC).isoformat()
            await _save_state(pool, GAPS_KEY, {"at": now, "gaps": gaps})
            await _save_state(pool, SOURCES_KEY, {"at": now, "sources": sources})

    if PSE_SYMBOL in indices and sources.get(PSE_SYMBOL, {}).get("source") != "stooq":
        # No PX close from Stooq (Yahoo has no PX): the Prague Stock Exchange's own page.
        async with ctx.step("pse_px") as step:
            if await refresh_pse_px(pool, deps.http, deps.now, step.detail):
                sources[PSE_SYMBOL] = {
                    "source": "pse",
                    "stooq_gap": (gaps.pop(PSE_SYMBOL, None) or {}).get("reason"),
                }
                now = dt.datetime.now(dt.UTC).isoformat()
                await _save_state(pool, GAPS_KEY, {"at": now, "gaps": gaps})
                await _save_state(pool, SOURCES_KEY, {"at": now, "sources": sources})

    async with ctx.step("price_moves") as step:
        rules = await load_active(pool, "event_rules", EventRulesConfig)
        await ctx.record_config_version(rules.id if rules else None)
        threshold = (rules.body if rules else EventRulesConfig()).price_move_threshold_pct
        moves = []
        splits: dict[str, str] = {}
        for ticker, symbols in holdings.items():
            ref = await choose_one(pool, symbols)
            move = await latest_move(pool, ticker, ref, deps.today, splits)
            if move is None:
                continue
            day, pct, used_symbol = move
            if abs(pct) <= threshold:
                continue
            job = await queue_escalation(
                pool,
                {
                    "trigger": "price_move",
                    "ticker": ticker,
                    "date": day.isoformat(),
                    "move_pct": round(pct, 4),
                    "symbol": used_symbol,
                },
                ("date", day.isoformat()),
            )
            moves.append(f"{ticker} {pct:+.2f}% on {day} (job {job or 'already queued'})")
        step.detail.update(threshold_pct=threshold, moves=moves)
        if splits:
            step.detail["split_skipped"] = splits

    async with ctx.step("fx_history") as step:
        await fx_history(pool, deps, settings.reference_history_years, step.detail)


async def price_history_handler(ctx: JobContext) -> None:
    async with httpx.AsyncClient(
        timeout=60.0, headers={"User-Agent": f"StewardAI/{__version__}"}
    ) as http:
        key = get_settings().stooq_api_key
        deps = PriceDeps(
            http=http, api_key=key.get_secret_value() if key else None, pace=asyncio.sleep
        )
        await run_price_history(ctx, deps)


async def run_holiday_refresh(ctx: JobContext, http: httpx.AsyncClient, today: dt.date) -> None:
    pool = ctx.pool
    async with ctx.step("load_config") as step:
        jobs = await load_active(pool, "jobs", JobsConfig)
        await ctx.record_config_version(jobs.id if jobs else None)
        countries = (jobs.body if jobs else JobsConfig()).calendar.holiday_countries
        step.detail["countries"] = countries
    for country in countries:
        async with ctx.step(f"holidays:{country}") as step:
            stored = 0
            for year in range(today.year - 1, today.year + 2):  # last, this and next year
                try:
                    holidays = await public_holidays(http, country, year)
                except (MarketDataError, httpx.HTTPError) as exc:
                    step.detail[f"error_{year}"] = str(exc)[:300]
                    continue
                async with pool.acquire() as conn, conn.transaction():
                    await conn.execute(
                        "delete from public.holiday where calendar = $1 and source = 'nager'"
                        " and extract(year from date) = $2",
                        country,
                        year,
                    )
                    await conn.executemany(
                        "insert into public.holiday (calendar, date, name, local_name, scope,"
                        " regions, source) values ($1, $2, $3, $4, $5, $6, 'nager')"
                        " on conflict do nothing",
                        [
                            (
                                h.country,
                                h.date,
                                h.name,
                                h.local_name,
                                "national" if h.national else "regional",
                                h.regions,
                            )
                            for h in holidays
                        ],
                    )
                stored += len(holidays)
            step.detail["stored"] = stored


async def holiday_refresh_handler(ctx: JobContext) -> None:
    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": f"StewardAI/{__version__}"}
    ) as http:
        await run_holiday_refresh(ctx, http, dt.datetime.now(dt.UTC).date())
