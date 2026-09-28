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
from steward.config.event_rules import EventRulesConfig
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.fx.convert import FxRate, normalise_currency
from steward.jobs.runner import JobContext
from steward.news.escalation import queue_escalation
from steward.portfolio.sync import store_rates
from steward.reference.identity import holding_tickers
from steward.reference.intraday import PSE_SYMBOL, refresh_pse_px, store_payload
from steward.reference.price_series import (
    PriceSource,
    PriceSymbols,
    SeriesRef,
    choose_one,
    holding_symbols,
    index_symbols,
)
from steward.settings import get_settings
from steward.sources import yahoo
from steward.sources.market import (
    MarketDataError,
    PriceBar,
    frankfurter_series,
    public_holidays,
    stooq_currency,
    stooq_daily,
)

GAPS_KEY = "price_gaps"
CHECKS_KEY = "price_checks"
SOURCES_KEY = "price_sources"
FX_BACKFILL_KEY = "fx_history_backfill"
RECENT_DAYS = 10
BACKFILL_KEY = "price_backfill"
# A re-fetched close this far from the stored one means the source restated its history (a
# split or similar adjustment): the whole history is fetched again.
RESTATEMENT_PCT = Decimal(5)
# Close-to-close ratios that look like a share split (or reverse split) rather than a move.
SPLIT_RATIOS = (2, 3, 4, 5, 10, 20)
SPLIT_TOLERANCE = 0.02
NO_STOOQ_SYMBOL = "no Stooq symbol (set price_symbol in the holding profile)"


@dataclass
class PriceDeps:
    http: httpx.AsyncClient
    # Stooq's download key (STOOQ_API_KEY); without one Stooq refuses daily downloads.
    api_key: str | None = None
    today: dt.date = field(default_factory=lambda: dt.datetime.now(dt.UTC).date())
    now: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    # Waits between Yahoo requests (Yahoo answers bursts with HTTP 429); None: no wait.
    pace: Callable[[float], Awaitable[None]] | None = None


async def store_bars(
    pool: asyncpg.Pool,
    bars: list[PriceBar],
    source: PriceSource = "stooq",
    currency: str | None = None,
) -> int:
    await pool.executemany(
        "insert into public.price_bar (symbol, date, open, high, low, close, volume, source,"
        " currency) values ($1, $2, $3, $4, $5, $6, $7, $8, $9) on conflict (symbol, date, source)"
        " do update set open = excluded.open, high = excluded.high, low = excluded.low,"
        " close = excluded.close, volume = excluded.volume, currency = excluded.currency,"
        " fetched_at = now()",
        [
            (b.symbol, b.date, b.open, b.high, b.low, b.close, b.volume, source, currency)
            for b in bars
        ],
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
        "select max(date) from public.price_bar where symbol = $1 and source = $2",
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
        return 0, f"Stooq unreachable: {type(exc).__name__}: {exc}"[:300]
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
            return 0, f"Stooq restated history; refetch failed: {type(exc).__name__}: {exc}"[:300]
        if notes is not None:
            notes[symbol] = f"history restated by Stooq; refetched {len(bars)} bars"
    return await store_bars(pool, bars), None


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
        return 0, f"Yahoo unreachable: {type(exc).__name__}: {exc}"[:300]
    if not bars:
        return 0, None if last else f"Yahoo has no data for {symbol}"
    if backfill:
        await _save_state(pool, state_key, {"from": full_start})
    elif await _restated(pool, bars, "yahoo"):
        try:
            chart, bars = await _yahoo_daily(pool, deps, symbol, full_start)
        except (yahoo.YahooError, httpx.HTTPError, ValueError) as exc:
            return 0, f"Yahoo restated history; refetch failed: {type(exc).__name__}: {exc}"[:300]
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
    if symbols.stooq:
        count, gap = await collect_symbol(pool, deps, symbols.stooq, years, notes)
        counts[symbols.stooq] = count
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


async def _restated(
    pool: asyncpg.Pool, bars: list[PriceBar], source: PriceSource = "stooq"
) -> bool:
    """Whether any fetched close differs from the stored one on the same date by more
    than RESTATEMENT_PCT."""
    stored = {
        r["date"]: Decimal(r["close"])
        for r in await pool.fetch(
            "select date, close from public.price_bar where symbol = $1 and source = $3"
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
    quoted = row["currency"] if ref.source == "yahoo" else stooq_currency(ref.symbol)
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


async def run_price_history(ctx: JobContext, deps: PriceDeps) -> None:
    pool = ctx.pool
    async with ctx.step("load_config") as step:
        jobs = await load_active(pool, "jobs", JobsConfig)
        profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
        await ctx.record_config_version(jobs.id if jobs else None)
        await ctx.record_config_version(profiles.id if profiles else None)
        settings = (jobs.body if jobs else JobsConfig()).prices
        tickers = await holding_tickers(pool)
        holdings = await holding_symbols(
            pool, tickers, profiles.body if profiles else HoldingProfilesConfig()
        )
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
        for ticker, key, symbols in wanted:
            used, gap = await collect_series(
                pool,
                deps,
                symbols,
                settings.history_years,
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
        await fx_history(pool, deps, settings.history_years, step.detail)


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
