"""Intraday prices (Yahoo Finance, unofficial) and the latest index quotes.

`intraday_prices` (every `intraday_interval_minutes` on weekdays, Config -> Jobs -> Prices):
- for every holding's Yahoo symbol and each market strip index with one, polls Yahoo's
  5-minute chart only while that listing's market is open (its regular session from
  SESSIONS, plus a grace period for the delayed last bars), on exchange days of the
  configured calendar (weekends and confirmed closures are skipped);
- stores the bars (`intraday_bar`), the latest quote (`market_quote`: last price, trade time,
  previous close, exchange time zone) and the original response (`market_payload`);
- prunes bars older than `intraday_retention_days`.
A symbol Yahoo refuses or does not know is a recorded gap (`sync_state` `intraday_status`),
never a failed job; readers then show daily closes with a note.

A holding page also fetches on demand (`holding_intraday`): when intraday is on and the
stored quote is older than `intraday_cache_seconds` during the session (or missing).

Nothing is estimated: the portfolio's intraday path values only the holdings with bars that
day (the snapshot's quantities times each 5-minute close, in the base currency at the
stored reference rates) and says which holdings it covers.
"""

import datetime as dt
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

import asyncpg
import httpx
from pydantic import BaseModel, Field

from steward import __version__
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import CalendarSettings, JobsConfig, PriceSettings
from steward.config.store import load_active
from steward.fx.convert import FxRate, FxSourceName, FxTable, MissingRateError
from steward.jobs.runner import JobContext
from steward.reference.calendar import exchange_open
from steward.reference.identity import holding_tickers
from steward.reference.price_series import (
    PriceSymbols,
    choose_one,
    holding_symbols,
    yahoo_mic,
)
from steward.sources import yahoo
from steward.sources.pse import PX_ISIN, PseError, pse_index

SOURCE_LABEL = yahoo.SOURCE_LABEL
STATUS_KEY = "intraday_status"
PSE_SYMBOL = "^px"
# The PSE page is read at most this often (and only when Stooq has no PX close).
PSE_MIN_AGE = dt.timedelta(hours=20)
# Delayed bars keep arriving for a while after the close.
CLOSE_GRACE = dt.timedelta(minutes=30)
# A request between two symbols (Yahoo answers bursts with HTTP 429).
PACE_SECONDS = 1.0

QuoteKind = Literal["intraday", "close"]


@dataclass(frozen=True)
class Session:
    tz: str
    open: dt.time
    close: dt.time


def _s(tz: str, open_: str, close: str) -> Session:
    return Session(tz, dt.time.fromisoformat(open_), dt.time.fromisoformat(close))


# Regular (continuous) trading sessions by operating MIC, local time.
SESSIONS: dict[str, Session] = {
    "XNYS": _s("America/New_York", "09:30", "16:00"),
    "XNAS": _s("America/New_York", "09:30", "16:00"),
    "ARCX": _s("America/New_York", "09:30", "16:00"),
    "BATS": _s("America/New_York", "09:30", "16:00"),
    "XASE": _s("America/New_York", "09:30", "16:00"),
    "XLON": _s("Europe/London", "08:00", "16:30"),
    "XETR": _s("Europe/Berlin", "09:00", "17:30"),
    "XFRA": _s("Europe/Berlin", "08:00", "20:00"),
    "XAMS": _s("Europe/Amsterdam", "09:00", "17:30"),
    "XPAR": _s("Europe/Paris", "09:00", "17:30"),
    "XBRU": _s("Europe/Brussels", "09:00", "17:30"),
    "XLIS": _s("Europe/Lisbon", "08:00", "16:30"),
    "XMIL": _s("Europe/Rome", "09:00", "17:30"),
    "XMAD": _s("Europe/Madrid", "09:00", "17:30"),
    "XSWX": _s("Europe/Zurich", "09:00", "17:30"),
    "XCSE": _s("Europe/Copenhagen", "09:00", "17:00"),
    "XSTO": _s("Europe/Stockholm", "09:00", "17:30"),
    "XHEL": _s("Europe/Helsinki", "10:00", "18:30"),
    "XOSL": _s("Europe/Oslo", "09:00", "16:20"),
    "XWBO": _s("Europe/Vienna", "09:00", "17:30"),
    "XDUB": _s("Europe/Dublin", "08:00", "16:30"),
    "XPRA": _s("Europe/Prague", "09:00", "16:20"),
    "XWAR": _s("Europe/Warsaw", "09:00", "17:00"),
    "XTKS": _s("Asia/Tokyo", "09:00", "15:30"),
}

# The delay seen on Yahoo's feed per market (observed 2026-09-28); others are not verified.
DELAY_NOTE: dict[str, str] = {
    "XETR": "~15 min delayed",
    "XLON": "~15 min delayed",
    "XAMS": "~15 min delayed",
    "XCSE": "near real time",
}
DEFAULT_DELAY_NOTE = "delayed up to ~15 min"


def delay_note(mic: str | None) -> str:
    return DELAY_NOTE.get(mic or "", DEFAULT_DELAY_NOTE)


def market_open(
    mic: str | None, now: dt.datetime, calendar: CalendarSettings, grace: dt.timedelta = CLOSE_GRACE
) -> bool:
    """Whether `now` falls in the listing's session (plus `grace` after the close) on an
    exchange day. An unknown market is treated as open (the job's own hours bound it)."""
    session = SESSIONS.get(mic or "")
    if session is None:
        return True
    zone = ZoneInfo(session.tz)
    local = now.astimezone(zone)
    if not exchange_open(calendar, mic, local.date()):
        return False
    start = dt.datetime.combine(local.date(), session.open, zone)
    end = dt.datetime.combine(local.date(), session.close, zone) + grace
    return start <= local <= end


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


async def store_payload(
    conn: asyncpg.Pool | asyncpg.Connection,
    source: str,
    symbol: str,
    kind: str,
    url: str,
    status: int,
    content_type: str | None,
    body: str,
) -> None:
    await conn.execute(
        "insert into public.market_payload (source, symbol, kind, url, http_status,"
        " content_type, body, fetched_at) values ($1, $2, $3, $4, $5, $6, $7, now())"
        " on conflict (source, symbol, kind) do update set url = excluded.url,"
        " http_status = excluded.http_status, content_type = excluded.content_type,"
        " body = excluded.body, fetched_at = excluded.fetched_at",
        source,
        symbol,
        kind,
        url,
        status,
        content_type,
        body,
    )


def _aligned(bars: list[yahoo.YahooBar]) -> list[yahoo.YahooBar]:
    """Yahoo appends the latest tick at its own time (08:06:12) after the last 5-minute
    bar: that point is the quote, not a bar."""
    return [b for b in bars if b.ts.second == 0 and b.ts.minute % 5 == 0]


async def store_chart(pool: asyncpg.Pool, symbol: str, chart: yahoo.YahooChart) -> int:
    """The 5-minute bars, the latest quote and the original response, in one transaction."""
    meta = chart.meta
    bars = _aligned(chart.bars)
    async with pool.acquire() as conn, conn.transaction():
        await store_payload(
            conn,
            "yahoo",
            symbol,
            "chart_5m",
            chart.url,
            chart.status,
            chart.content_type,
            chart.body,
        )
        await conn.executemany(
            "insert into public.intraday_bar (symbol, source, ts, open, high, low, close,"
            " volume, fetched_at) values ($1, 'yahoo', $2, $3, $4, $5, $6, $7, now())"
            " on conflict (symbol, source, ts) do update set open = excluded.open,"
            " high = excluded.high, low = excluded.low, close = excluded.close,"
            " volume = excluded.volume, fetched_at = excluded.fetched_at",
            [(symbol, b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        )
        if meta.price is not None and meta.price > 0 and meta.price_time is not None:
            await conn.execute(
                "insert into public.market_quote (symbol, source, kind, price, price_time,"
                " session_date, previous_close, currency, exchange_name, exchange_tz,"
                " session_start, session_end, payload_kind, fetched_at) values ($1, 'yahoo',"
                " 'intraday', $2, $3, $4, $5, $6, $7, $8, $9, $10, 'chart_5m', now())"
                " on conflict (symbol, source) do update set kind = excluded.kind,"
                " price = excluded.price, price_time = excluded.price_time,"
                " session_date = excluded.session_date, previous_close = excluded.previous_close,"
                " currency = excluded.currency, exchange_name = excluded.exchange_name,"
                " exchange_tz = excluded.exchange_tz, session_start = excluded.session_start,"
                " session_end = excluded.session_end, payload_kind = excluded.payload_kind,"
                " fetched_at = excluded.fetched_at",
                symbol,
                meta.price,
                meta.price_time,
                meta.price_time.astimezone(meta.zone()).date(),
                meta.previous_close if meta.previous_close and meta.previous_close > 0 else None,
                meta.currency,
                meta.exchange_name,
                meta.exchange_tz,
                meta.session_start,
                meta.session_end,
            )
    return len(bars)


async def record_status(
    pool: asyncpg.Pool, symbol: str, now: dt.datetime, error: str | None
) -> None:
    """The latest fetch outcome per symbol, merged into one `sync_state` row."""
    entry = {"at": now.isoformat(), "ok": error is None, "error": error}
    await pool.execute(
        "insert into public.sync_state (key, value) values ($1, $2::jsonb)"
        " on conflict (key) do update set value = public.sync_state.value || excluded.value",
        STATUS_KEY,
        json.dumps({symbol: entry}),
    )


async def fetch_symbol(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    symbol: str,
    now: dt.datetime,
    *,
    retry: bool = True,
) -> tuple[int, str | None]:
    """(bars stored, error or None) for one symbol; never raises for a source failure."""
    try:
        chart = await yahoo.yahoo_intraday(http, symbol, retry=retry)
    except (yahoo.YahooError, httpx.HTTPError, ValueError) as exc:
        reason = f"{type(exc).__name__}: {exc}"[:300]
        await record_status(pool, symbol, now, reason)
        return 0, reason
    count = await store_chart(pool, symbol, chart)
    await record_status(pool, symbol, now, None)
    return count, None


async def prune(pool: asyncpg.Pool, days: int, now: dt.datetime) -> int:
    result: str = await pool.execute(
        "delete from public.intraday_bar where ts < $1", now - dt.timedelta(days=days)
    )
    return int(result.split()[-1]) if result.split()[-1].isdigit() else 0


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


@dataclass
class IntradayDeps:
    http: httpx.AsyncClient
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC)
    pace: Callable[[float], Awaitable[None]] | None = None


async def wanted_symbols(
    pool: asyncpg.Pool, settings: PriceSettings, profiles: HoldingProfilesConfig
) -> dict[str, str]:
    """Yahoo symbol -> what it is for (a ticker or an index label), each symbol once."""
    tickers = await holding_tickers(pool)
    symbols = await holding_symbols(pool, tickers, profiles)
    out: dict[str, str] = {}
    for ticker, s in symbols.items():
        if s.yahoo:
            out.setdefault(s.yahoo, ticker)
    for item in settings.market_strip:
        if (y := item.yahoo()) is not None:
            out.setdefault(y, item.label)
    return out


async def run_intraday(ctx: JobContext, deps: IntradayDeps) -> None:
    pool = ctx.pool
    now = deps.now()
    async with ctx.step("load_config") as step:
        jobs = await load_active(pool, "jobs", JobsConfig)
        profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
        await ctx.record_config_version(jobs.id if jobs else None)
        await ctx.record_config_version(profiles.id if profiles else None)
        config = jobs.body if jobs else JobsConfig()
        settings = config.prices
        step.detail.update(enabled=settings.intraday_enabled, source=SOURCE_LABEL)
        if not settings.intraday_enabled:
            step.detail["skipped"] = "intraday prices are off (Config -> Jobs -> Prices)"
            return
        symbols = await wanted_symbols(
            pool, settings, profiles.body if profiles else HoldingProfilesConfig()
        )
        open_now = {
            s: f for s, f in symbols.items() if market_open(yahoo_mic(s), now, config.calendar)
        }
        step.detail.update(
            symbols=symbols, open=sorted(open_now), closed=sorted(set(symbols) - set(open_now))
        )

    async with ctx.step("intraday") as step:
        stored: dict[str, int] = {}
        errors: dict[str, str] = {}
        for i, symbol in enumerate(sorted(open_now)):
            if i and deps.pace is not None:
                await deps.pace(PACE_SECONDS)
            count, error = await fetch_symbol(pool, deps.http, symbol, now)
            if error:
                errors[symbol] = error
            else:
                stored[symbol] = count
        step.detail.update(stored=stored, errors=errors)

    async with ctx.step("prune") as step:
        step.detail["deleted"] = await prune(pool, settings.intraday_retention_days, now)
        step.detail["retention_days"] = settings.intraday_retention_days


async def intraday_prices_handler(ctx: JobContext) -> None:
    import asyncio

    async with httpx.AsyncClient(
        timeout=20.0, headers={"User-Agent": f"StewardAI/{__version__}"}
    ) as http:
        await run_intraday(ctx, IntradayDeps(http=http, pace=asyncio.sleep))


# ---------------------------------------------------------------------------
# PX: the Prague Stock Exchange's page, once a day
# ---------------------------------------------------------------------------


async def refresh_pse_px(
    pool: asyncpg.Pool, http: httpx.AsyncClient, now: dt.datetime, detail: dict[str, Any]
) -> None:
    """Read PX off the PSE detail page unless it was read in the last PSE_MIN_AGE."""
    last = await pool.fetchval(
        "select fetched_at from public.market_quote where symbol = $1 and source = 'pse'",
        PSE_SYMBOL,
    )
    if last is not None and now - last < PSE_MIN_AGE:
        detail["pse_px"] = f"cached (read {last.isoformat()})"
        return
    try:
        fetched = await pse_index(http, PX_ISIN)
    except (PseError, httpx.HTTPError) as exc:
        detail["pse_px_error"] = f"{type(exc).__name__}: {exc}"[:300]
        return
    value = fetched.value
    kind: QuoteKind = "close" if value.is_close(now) else "intraday"
    async with pool.acquire() as conn, conn.transaction():
        await store_payload(
            conn,
            "pse",
            PSE_SYMBOL,
            "pse_detail",
            fetched.url,
            fetched.status,
            fetched.content_type,
            fetched.body,
        )
        await conn.execute(
            "insert into public.market_quote (symbol, source, kind, price, price_time,"
            " session_date, previous_close, currency, exchange_name, exchange_tz,"
            " payload_kind, fetched_at) values ($1, 'pse', $2, $3, $4, $5, $6, 'CZK',"
            " 'Prague Stock Exchange', 'Europe/Prague', 'pse_detail', now())"
            " on conflict (symbol, source) do update set kind = excluded.kind,"
            " price = excluded.price, price_time = excluded.price_time,"
            " session_date = excluded.session_date, previous_close = excluded.previous_close,"
            " fetched_at = excluded.fetched_at",
            PSE_SYMBOL,
            kind,
            value.value,
            value.at,
            value.at.date(),
            value.previous_close,
        )
    detail["pse_px"] = {"value": float(value.value), "at": value.at.isoformat(), "kind": kind}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


class IntradayBarOut(BaseModel):
    ts: dt.datetime = Field(description="Start of the 5-minute bar (UTC).")
    time: str = Field(description="Start of the bar in exchange time, HH:MM.")
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None


class DailyFallback(BaseModel):
    date: dt.date
    close: float
    source: str = Field(description="Stooq or Yahoo (daily closes).")


class HoldingIntraday(BaseModel):
    ticker: str
    symbol: str | None = Field(description="Yahoo symbol; null when none is known.")
    enabled: bool = Field(description="Intraday prices are on (Config -> Jobs -> Prices).")
    available: bool = Field(description="Intraday bars or a last price are stored.")
    source: str = SOURCE_LABEL
    kind: QuoteKind | None = Field(
        description="intraday: a last trade during the session; close: the session's close."
    )
    last_price: float | None
    last_trade_at: dt.datetime | None = Field(description="Time of the last price (UTC).")
    last_trade_time: str | None = Field(description="Time of the last price, exchange time.")
    exchange_tz: str | None
    exchange_name: str | None
    currency: str | None = Field(description="Quote currency as Yahoo reports it (GBp = pence).")
    session_date: dt.date | None = Field(description="Exchange-local day of the last price.")
    previous_close: float | None
    change: float | None
    change_pct: float | None = Field(description="Fraction against the previous close.")
    delay_note: str | None
    fetched_at: dt.datetime | None
    bars: list[IntradayBarOut] = Field(description="The session's 5-minute bars, oldest first.")
    note: str | None = Field(
        description="Why intraday prices are missing or stale (the page shows daily closes)."
    )
    daily: DailyFallback | None = Field(description="The latest stored daily close.")


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def quote_kind(
    price_time: dt.datetime, session_end: dt.datetime | None, now: dt.datetime
) -> QuoteKind:
    """close: the session is over and the price is from its end; else intraday."""
    if session_end is not None and now >= session_end:
        if price_time >= session_end - dt.timedelta(minutes=10):
            return "close"
    return "intraday"


async def _quote(pool: asyncpg.Pool, symbol: str) -> asyncpg.Record | None:
    row: asyncpg.Record | None = await pool.fetchrow(
        "select * from public.market_quote where symbol = $1 and source = 'yahoo'", symbol
    )
    return row


def needs_fetch(
    quote: asyncpg.Record | None,
    now: dt.datetime,
    cache: dt.timedelta,
    mic: str | None,
    calendar: CalendarSettings,
) -> bool:
    if quote is None:
        return True
    age = now - quote["fetched_at"]
    if age < cache:
        return False
    # In the session: refresh past the cache age; outside it, only a quote from before the
    # last session's end (fetched mid-session) is worth refreshing.
    if market_open(mic, now, calendar):
        return True
    end = quote["session_end"]
    return end is not None and quote["fetched_at"] < end + CLOSE_GRACE <= now


async def _daily(pool: asyncpg.Pool, symbols: PriceSymbols) -> DailyFallback | None:
    ref = await choose_one(pool, symbols)
    if ref is None:
        return None
    row = await pool.fetchrow(
        "select date, close from public.price_bar where symbol = $1 and source = $2"
        " order by date desc limit 1",
        ref.symbol,
        ref.source,
    )
    return (
        DailyFallback(date=row["date"], close=float(row["close"]), source=ref.name) if row else None
    )


async def holding_intraday(
    pool: asyncpg.Pool,
    ticker: str,
    *,
    now: dt.datetime | None = None,
    http: httpx.AsyncClient | None = None,
) -> HoldingIntraday:
    """The holding's latest session in 5-minute bars, fetched first when `http` is given
    and the stored quote is stale."""
    now = now or dt.datetime.now(dt.UTC)
    jobs = await load_active(pool, "jobs", JobsConfig)
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    config = jobs.body if jobs else JobsConfig()
    settings = config.prices
    symbols = (
        await holding_symbols(
            pool, [ticker], profiles.body if profiles else HoldingProfilesConfig()
        )
    )[ticker]
    daily = await _daily(pool, symbols)
    symbol = symbols.yahoo
    empty: dict[str, Any] = dict(
        ticker=ticker,
        symbol=symbol,
        enabled=settings.intraday_enabled,
        available=False,
        kind=None,
        last_price=None,
        last_trade_at=None,
        last_trade_time=None,
        exchange_tz=None,
        exchange_name=None,
        currency=None,
        session_date=None,
        previous_close=None,
        change=None,
        change_pct=None,
        delay_note=None,
        fetched_at=None,
        bars=[],
        daily=daily,
    )
    if not settings.intraday_enabled:
        return HoldingIntraday(**empty, note="Intraday prices are off (Config → Jobs → Prices).")
    if symbol is None:
        return HoldingIntraday(
            **empty, note="No Yahoo symbol for this holding (set it in the holding profile)."
        )
    mic = yahoo_mic(symbol)
    quote = await _quote(pool, symbol)
    fetch_error: str | None = None
    if http is not None and needs_fetch(
        quote, now, dt.timedelta(seconds=settings.intraday_cache_seconds), mic, config.calendar
    ):
        _count, fetch_error = await fetch_symbol(pool, http, symbol, now, retry=False)
        quote = await _quote(pool, symbol)
    if fetch_error is None:
        status = await pool.fetchval(
            "select value -> $2 from public.sync_state where key = $1", STATUS_KEY, symbol
        )
        entry = json.loads(status) if status else None
        if entry and not entry.get("ok"):
            fetch_error = entry.get("error")
    if quote is None:
        note: str | None = (
            f"Yahoo Finance did not answer ({fetch_error}); showing daily closes."
            if fetch_error
            else "No intraday prices collected yet; showing daily closes."
        )
        return HoldingIntraday(**empty, note=note)

    zone = ZoneInfo(quote["exchange_tz"] or "UTC")
    session_date: dt.date = quote["session_date"]
    day_start = dt.datetime.combine(session_date, dt.time(0), zone)
    rows = await pool.fetch(
        "select ts, open, high, low, close, volume from public.intraday_bar"
        " where symbol = $1 and source = 'yahoo' and ts >= $2 and ts < $3 order by ts",
        symbol,
        day_start,
        day_start + dt.timedelta(days=1),
    )
    price: Decimal = quote["price"]
    previous: Decimal | None = quote["previous_close"]
    note = None
    if fetch_error:
        note = (
            f"Yahoo Finance did not answer on the latest try ({fetch_error}); the prices shown "
            f"were fetched {quote['fetched_at'].astimezone(zone).strftime('%d.%m %H:%M')} "
            "exchange time."
        )
    return HoldingIntraday(
        ticker=ticker,
        symbol=symbol,
        enabled=True,
        available=True,
        kind=quote_kind(quote["price_time"], quote["session_end"], now),
        last_price=float(price),
        last_trade_at=quote["price_time"],
        last_trade_time=quote["price_time"].astimezone(zone).strftime("%H:%M"),
        exchange_tz=quote["exchange_tz"],
        exchange_name=quote["exchange_name"],
        currency=quote["currency"],
        session_date=session_date,
        previous_close=_f(previous),
        change=float(price - previous) if previous else None,
        change_pct=float(price / previous - 1) if previous else None,
        delay_note=delay_note(mic),
        fetched_at=quote["fetched_at"],
        bars=[
            IntradayBarOut(
                ts=r["ts"],
                time=r["ts"].astimezone(zone).strftime("%H:%M"),
                open=_f(r["open"]),
                high=_f(r["high"]),
                low=_f(r["low"]),
                close=float(r["close"]),
                volume=_f(r["volume"]),
            )
            for r in rows
        ],
        note=note,
        daily=daily,
    )


# ---------------------------------------------------------------------------
# The portfolio's intraday path
# ---------------------------------------------------------------------------


class PortfolioIntradayPoint(BaseModel):
    ts: dt.datetime
    value: float = Field(description="Covered holdings' value in the base currency.")


class PortfolioIntradayCoverage(BaseModel):
    holdings: int = Field(description="Positions in the latest snapshot.")
    covered: int = Field(description="Positions with intraday bars on `day`.")
    covered_weight: float | None = Field(
        description="Share of the snapshot's value (base currency) the covered positions hold."
    )
    missing: list[str] = Field(description="Tickers without intraday bars on `day`.")


class PortfolioIntraday(BaseModel):
    day: dt.date | None = Field(description="UTC day of the path (the latest with bars).")
    base_currency: str | None
    fx_source: FxSourceName | None
    fx_rate_date: dt.date | None = Field(description="Oldest reference rate used.")
    positions_as_of: dt.date | None = Field(description="Snapshot whose quantities are used.")
    source: str = SOURCE_LABEL
    enabled: bool
    points: list[PortfolioIntradayPoint] = Field(
        description="Every 5-minute timestamp any covered holding traded at, oldest first. "
        "A holding counts at its previous close before its first bar of the day."
    )
    previous_value: float | None = Field(
        description="Covered holdings at their previous closes (base currency)."
    )
    last_value: float | None
    change_pct: float | None = Field(description="Fraction, last value against previous.")
    coverage: PortfolioIntradayCoverage
    note: str | None


async def _fx(pool: asyncpg.Pool, source: FxSourceName, day: dt.date) -> FxTable:
    rows = await pool.fetch(
        "select distinct on (currency) source, rate_date, currency, rate, published_amount,"
        " published_rate from public.fx_rate where source = $1 and rate_date <= $2"
        " order by currency, rate_date desc",
        source,
        day,
    )
    return FxTable(
        source,
        [
            FxRate(
                source=r["source"],
                rate_date=r["rate_date"],
                currency=r["currency"],
                rate=r["rate"],
                published_amount=r["published_amount"],
                published_rate=r["published_rate"],
            )
            for r in rows
        ],
    )


async def portfolio_intraday(
    pool: asyncpg.Pool, until: dt.datetime | None = None
) -> PortfolioIntraday:
    until = until or dt.datetime.now(dt.UTC)
    jobs = await load_active(pool, "jobs", JobsConfig)
    enabled = (jobs.body if jobs else JobsConfig()).prices.intraday_enabled
    account = await pool.fetchrow(
        "select snapshot_date, base_currency, fx_source from public.account_snapshot"
        " where snapshot_date <= $1::date order by snapshot_date desc limit 1",
        until.date(),
    )
    empty_cov = PortfolioIntradayCoverage(holdings=0, covered=0, covered_weight=None, missing=[])
    blank: dict[str, Any] = dict(
        base_currency=None,
        fx_source=None,
        fx_rate_date=None,
        positions_as_of=None,
        enabled=enabled,
        points=[],
        previous_value=None,
        last_value=None,
        change_pct=None,
    )
    if account is None:
        return PortfolioIntraday(
            day=None, coverage=empty_cov, note="No portfolio snapshot yet.", **blank
        )
    positions = await pool.fetch(
        "select ticker, quantity, instrument_currency, value_base from public.position_snapshot"
        " where snapshot_date = $1 order by value_base desc",
        account["snapshot_date"],
    )
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool,
        [p["ticker"] for p in positions],
        profiles.body if profiles else HoldingProfilesConfig(),
    )
    yahoo_of = {t: s.yahoo for t, s in symbols.items() if s.yahoo}
    wanted = sorted(set(yahoo_of.values()))
    day: dt.date | None = await pool.fetchval(
        "select (max(ts) at time zone 'UTC')::date from public.intraday_bar where source = 'yahoo'"
        " and symbol = any($1::text[]) and ts <= $2 and ts >= $2 - interval '10 days'",
        wanted,
        until,
    )
    base = account["base_currency"]
    blank.update(
        base_currency=base, fx_source=account["fx_source"], positions_as_of=account["snapshot_date"]
    )
    if day is None:
        note: str | None = (
            "Intraday prices are off (Config → Jobs → Prices)."
            if not enabled
            else "No intraday prices stored for this period; daily closes only."
        )
        return PortfolioIntraday(
            day=None,
            coverage=PortfolioIntradayCoverage(
                holdings=len(positions),
                covered=0,
                covered_weight=0.0 if positions else None,
                missing=[p["ticker"] for p in positions],
            ),
            note=note,
            **blank,
        )
    start = dt.datetime.combine(day, dt.time(0), dt.UTC)
    end = min(start + dt.timedelta(days=1), until + dt.timedelta(microseconds=1))
    bar_rows = await pool.fetch(
        "select symbol, ts, close from public.intraday_bar where source = 'yahoo'"
        " and symbol = any($1::text[]) and ts >= $2 and ts < $3 order by ts",
        wanted,
        start,
        end,
    )
    bars: dict[str, list[tuple[dt.datetime, Decimal]]] = {}
    for r in bar_rows:
        bars.setdefault(r["symbol"], []).append((r["ts"], r["close"]))
    quotes = {
        r["symbol"]: r
        for r in await pool.fetch(
            "select symbol, currency, previous_close, session_date from public.market_quote"
            " where source = 'yahoo' and symbol = any($1::text[])",
            wanted,
        )
    }
    fx = await _fx(pool, account["fx_source"], day)

    @dataclass
    class Line:
        ticker: str
        quantity: Decimal
        rate: Decimal
        previous: Decimal | None
        series: list[tuple[dt.datetime, Decimal]]

    lines: list[Line] = []
    missing: list[str] = []
    rate_dates: list[dt.date] = []
    covered_value = Decimal(0)
    total_value = sum((Decimal(p["value_base"]) for p in positions), Decimal(0))
    for p in positions:
        symbol = yahoo_of.get(p["ticker"])
        series = bars.get(symbol, []) if symbol else []
        if not series:
            missing.append(p["ticker"])
            continue
        quote = quotes.get(symbol)
        currency = (quote["currency"] if quote else None) or p["instrument_currency"]
        try:
            conversion = fx.conversion(currency, base)
        except MissingRateError:
            missing.append(p["ticker"])
            continue
        if conversion.rate_date:
            rate_dates.append(conversion.rate_date)
        previous = await _previous_close(pool, symbol, quote, day) if symbol else None
        lines.append(Line(p["ticker"], Decimal(p["quantity"]), conversion.rate, previous, series))
        covered_value += Decimal(p["value_base"])

    stamps = sorted({ts for line in lines for ts, _ in line.series})
    points = []
    cursor = {line.ticker: 0 for line in lines}
    current: dict[str, Decimal | None] = {line.ticker: line.previous for line in lines}
    for ts in stamps:
        total = Decimal(0)
        for line in lines:
            i = cursor[line.ticker]
            while i < len(line.series) and line.series[i][0] <= ts:
                current[line.ticker] = line.series[i][1]
                i += 1
            cursor[line.ticker] = i
            now_price = current[line.ticker]
            price = now_price if now_price is not None else line.series[0][1]
            total += line.quantity * price * line.rate
        points.append(PortfolioIntradayPoint(ts=ts, value=float(total)))
    previous_closes = [line.previous for line in lines]
    previous_value = (
        sum(
            (
                line.quantity * p * line.rate
                for line, p in zip(lines, previous_closes, strict=True)
                if p is not None
            ),
            Decimal(0),
        )
        if lines and all(p is not None for p in previous_closes)
        else None
    )
    last_value = points[-1].value if points else None
    change = (
        last_value / float(previous_value) - 1
        if last_value is not None and previous_value
        else None
    )
    blank.update(
        fx_rate_date=min(rate_dates) if rate_dates else None,
        points=points,
        previous_value=float(previous_value) if previous_value is not None else None,
        last_value=last_value,
        change_pct=change,
    )
    note = None
    if missing:
        note = f"{len(lines)} of {len(positions)} holdings have intraday prices for {day}."
    return PortfolioIntraday(
        day=day,
        coverage=PortfolioIntradayCoverage(
            holdings=len(positions),
            covered=len(lines),
            covered_weight=float(covered_value / total_value) if total_value else None,
            missing=missing,
        ),
        note=note,
        **blank,
    )


async def _previous_close(
    pool: asyncpg.Pool, symbol: str, quote: asyncpg.Record | None, day: dt.date
) -> Decimal | None:
    """The close before `day`: the quote's previous close when the quote is from `day`,
    else the last stored 5-minute close before `day`, else Yahoo's daily close."""
    if quote is not None and quote["session_date"] == day and quote["previous_close"]:
        return Decimal(quote["previous_close"])
    start = dt.datetime.combine(day, dt.time(0), dt.UTC)
    value = await pool.fetchval(
        "select close from public.intraday_bar where symbol = $1 and source = 'yahoo'"
        " and ts < $2 and ts >= $2 - interval '5 days' order by ts desc limit 1",
        symbol,
        start,
    )
    if value is None:
        value = await pool.fetchval(
            "select close from public.price_bar where symbol = $1 and source = 'yahoo'"
            " and date < $2 order by date desc limit 1",
            symbol,
            day,
        )
    return Decimal(value) if value is not None else None
