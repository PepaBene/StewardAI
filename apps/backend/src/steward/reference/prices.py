"""Market history jobs.

`price_history` (weekdays after the close):
- Stooq daily bars for every holding's symbol (the holding profile's `price_symbol`, else the
  one derived from OpenFIGI) and the configured benchmarks: a backfill of `history_years` the
  first time, the recent days after that. A symbol Stooq does not know is a visible gap (kept
  with its reason for the holding page and the Activity page), never a failed job.
- Plausibility: on a sync day, the latest close is compared with Trading212's price; a gap
  beyond the configured tolerance is flagged (timing differences are expected intraday).
- FX history: the ECB's rates for `history_years` from Frankfurter in one request (once), and
  a daily consistency check of the Data Portal's latest rates against Frankfurter's.

- Price-move trigger: when a holding's latest close moved beyond the event rules' threshold
  (default +-8 %) against the previous close, an event escalation is queued once for that
  holding and day. Without Stooq data, Trading212's prices on consecutive sync days are used.

`holiday_refresh` (twice a year, and on demand): public holidays of the configured
countries for this and next year from Nager.Date.
"""

import datetime as dt
import json
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
from steward.fx.convert import FxRate
from steward.jobs.runner import JobContext
from steward.news.escalation import queue_escalation
from steward.portfolio.sync import store_rates
from steward.reference.identity import holding_tickers
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
FX_BACKFILL_KEY = "fx_history_backfill"
RECENT_DAYS = 10
# A re-fetched close this far from the stored one means Stooq restated its history (a split
# or similar adjustment): the whole history is fetched again.
RESTATEMENT_PCT = Decimal(5)
# Close-to-close ratios that look like a share split (or reverse split) rather than a move.
SPLIT_RATIOS = (2, 3, 4, 5, 10, 20)
SPLIT_TOLERANCE = 0.02


@dataclass
class PriceDeps:
    http: httpx.AsyncClient
    today: dt.date = field(default_factory=lambda: dt.datetime.now(dt.UTC).date())


async def store_bars(pool: asyncpg.Pool, bars: list[PriceBar]) -> int:
    await pool.executemany(
        "insert into public.price_bar (symbol, date, open, high, low, close, volume, source)"
        " values ($1, $2, $3, $4, $5, $6, $7, 'stooq') on conflict (symbol, date, source)"
        " do update set open = excluded.open, high = excluded.high, low = excluded.low,"
        " close = excluded.close, volume = excluded.volume, fetched_at = now()",
        [(b.symbol, b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars],
    )
    return len(bars)


async def symbols_for(
    pool: asyncpg.Pool, tickers: list[str], profiles: HoldingProfilesConfig
) -> dict[str, str | None]:
    """Ticker -> Stooq symbol (profile override first), None when there is none."""
    stored = {
        r["t212_ticker"]: r["price_symbol"]
        for r in await pool.fetch(
            "select t212_ticker, price_symbol from public.instrument"
            " where t212_ticker = any($1::text[])",
            tickers,
        )
    }
    out: dict[str, str | None] = {}
    for ticker in tickers:
        profile = profiles.for_ticker(ticker)
        out[ticker] = (profile.price_symbol if profile else None) or stored.get(ticker)
    return out


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


async def collect_symbol(
    pool: asyncpg.Pool,
    deps: PriceDeps,
    symbol: str,
    years: int,
    notes: dict[str, str] | None = None,
) -> tuple[int, str | None]:
    """(bars stored, gap reason or None).

    The recent days are re-fetched each run; when they disagree with the stored closes
    (Stooq adjusted its history for a split), the full history is fetched and overwrites
    the stored bars, so drawdown, volatility and beta keep working on one adjusted series.
    """
    last = await pool.fetchval(
        "select max(date) from public.price_bar where symbol = $1 and source = 'stooq'", symbol
    )
    full_start = deps.today - dt.timedelta(days=365 * years + 5)
    start = last - dt.timedelta(days=RECENT_DAYS) if last else full_start
    try:
        bars = await stooq_daily(deps.http, symbol, start, deps.today)
    except (MarketDataError, httpx.HTTPError) as exc:
        return 0, f"Stooq unreachable: {type(exc).__name__}: {exc}"[:300]
    if not bars:
        return 0, None if last else f"Stooq has no data for {symbol}"
    if last and await _restated(pool, bars):
        try:
            bars = await stooq_daily(deps.http, symbol, full_start, deps.today)
        except (MarketDataError, httpx.HTTPError) as exc:
            # Storing only the restated recent bars would put a false jump in the series.
            return 0, f"Stooq restated history; refetch failed: {type(exc).__name__}: {exc}"[:300]
        if notes is not None:
            notes[symbol] = f"history restated by Stooq; refetched {len(bars)} bars"
    return await store_bars(pool, bars), None


async def _restated(pool: asyncpg.Pool, bars: list[PriceBar]) -> bool:
    """Whether any fetched close differs from the stored one on the same date by more
    than RESTATEMENT_PCT."""
    stored = {
        r["date"]: Decimal(r["close"])
        for r in await pool.fetch(
            "select date, close from public.price_bar where symbol = $1 and source = 'stooq'"
            " and date = any($2::date[])",
            bars[0].symbol,
            [b.date for b in bars],
        )
    }
    return any(
        b.date in stored and abs(b.close / stored[b.date] - 1) * 100 > RESTATEMENT_PCT for b in bars
    )


async def plausibility(
    pool: asyncpg.Pool,
    ticker: str,
    symbol: str,
    tolerance_pct: float,
    notes: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Latest Stooq close vs Trading212's price on the same day, when both exist and are
    quoted in the same currency (a line traded in another currency than the Stooq symbol
    is skipped, with a note, rather than flagged every day)."""
    row = await pool.fetchrow(
        "select p.snapshot_date, p.current_price, p.instrument_currency, b.close"
        " from public.position_snapshot p join public.price_bar b on b.symbol = $2"
        " and b.date = p.snapshot_date and b.source = 'stooq'"
        " where p.ticker = $1 order by p.snapshot_date desc limit 1",
        ticker,
        symbol,
    )
    if row is None or not row["current_price"]:
        return None
    quoted = stooq_currency(symbol)
    if quoted is None or row["instrument_currency"] != quoted:
        if notes is not None:
            notes[ticker] = (
                f"not compared: Trading212 quotes {row['instrument_currency']},"
                f" {symbol} is {quoted or 'in an unknown currency'}"
            )
        return None
    diff = (Decimal(row["close"]) / Decimal(row["current_price"]) - 1) * 100
    return {
        "date": row["snapshot_date"].isoformat(),
        "stooq_close": float(row["close"]),
        "trading212_price": float(row["current_price"]),
        "difference_pct": round(float(diff), 3),
        "flagged": abs(diff) > Decimal(str(tolerance_pct)),
    }


async def fx_history(
    pool: asyncpg.Pool, deps: PriceDeps, years: int, detail: dict[str, Any]
) -> None:
    state = await _state(pool, FX_BACKFILL_KEY)
    if not state.get("done"):
        start = deps.today - dt.timedelta(days=365 * years + 5)
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
    symbol: str | None,
    today: dt.date,
    notes: dict[str, str] | None = None,
) -> tuple[dt.date, float, str] | None:
    """(date, close-to-close % move, price source) of the latest close, if recent.

    Trading212's prices are not split-adjusted, so a fallback move whose ratio is a common
    split ratio (2:1, 1:10, ...) is skipped with a note instead of raising a price move.
    """
    rows = []
    source = symbol or "trading212"
    if symbol:
        rows = await pool.fetch(
            "select date, close from public.price_bar where symbol = $1 and source = 'stooq'"
            " order by date desc limit 2",
            symbol,
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
        symbols = await symbols_for(
            pool, tickers, profiles.body if profiles else HoldingProfilesConfig()
        )
        step.detail.update(symbols=symbols, benchmarks=settings.benchmark_symbols)

    gaps: dict[str, dict[str, Any]] = {}
    checks: dict[str, Any] = {}
    async with ctx.step("prices") as step:
        counts: dict[str, int] = {}
        restated: dict[str, str] = {}
        unchecked: dict[str, str] = {}
        wanted = [(t, s) for t, s in symbols.items()] + [
            (None, b) for b in settings.benchmark_symbols
        ]
        for ticker, symbol in wanted:
            key = ticker or symbol
            assert key is not None
            if symbol is None:
                gaps[key] = {"reason": "no Stooq symbol (set price_symbol in the holding profile)"}
                continue
            count, gap = await collect_symbol(pool, deps, symbol, settings.history_years, restated)
            counts[symbol] = count
            if gap:
                gaps[key] = {"symbol": symbol, "reason": gap}
            elif ticker is not None:
                check = await plausibility(
                    pool, ticker, symbol, settings.plausibility_tolerance_pct, unchecked
                )
                if check:
                    checks[ticker] = check
        step.detail.update(stored=counts, gaps=gaps)
        if restated:
            step.detail["restated"] = restated
        if unchecked:
            step.detail["plausibility_skipped"] = unchecked
        step.detail["flagged"] = {t: c for t, c in checks.items() if c["flagged"]}
        now = dt.datetime.now(dt.UTC).isoformat()
        await _save_state(pool, GAPS_KEY, {"at": now, "gaps": gaps})
        await _save_state(pool, CHECKS_KEY, {"at": now, "checks": checks})

    async with ctx.step("price_moves") as step:
        rules = await load_active(pool, "event_rules", EventRulesConfig)
        await ctx.record_config_version(rules.id if rules else None)
        threshold = (rules.body if rules else EventRulesConfig()).price_move_threshold_pct
        moves = []
        splits: dict[str, str] = {}
        for ticker, symbol in symbols.items():
            move = await latest_move(pool, ticker, symbol, deps.today, splits)
            if move is None:
                continue
            day, pct, used = move
            if abs(pct) <= threshold:
                continue
            job = await queue_escalation(
                pool,
                {
                    "trigger": "price_move",
                    "ticker": ticker,
                    "date": day.isoformat(),
                    "move_pct": round(pct, 4),
                    "symbol": used,
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
        await run_price_history(ctx, PriceDeps(http=http))


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
            for year in (today.year, today.year + 1):
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
