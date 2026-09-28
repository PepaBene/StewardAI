"""Recent quotes for the Holdings page and the market strip, read from stored data only.

- `holdings_prices`: every current holding's recent daily closes from `price_bar`, with the
  latest day's change, in one call (the Holdings page's sparklines). Each holding's series is
  the one the holding page uses (`steward.reference.price_series`: Stooq, else Yahoo). When
  intraday prices are on, the latest stored intraday quote rides along, so the page can show
  a fresher day change, labelled as such.
- `market_strip`: the reference FX rate of each held currency against the base currency
  (latest and previous published rate, from `fx_rate`), and the configured index quotes
  through a source chain: the latest daily close (Stooq, else Yahoo), a fresher intraday
  value from Yahoo when there is one, and for PX the Prague Stock Exchange's page. Each
  quote says its source, its kind (intraday or close) and its as-of time.

Nothing is estimated: a holding or index without stored bars, or a currency without two
published rates, is returned with nulls.
"""

import datetime as dt
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

import asyncpg
from pydantic import BaseModel, Field

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import JobsConfig, PriceSettings
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.fx.convert import HOME_CURRENCY, FxSourceName, normalise_currency
from steward.reference.intraday import PSE_SYMBOL, delay_note, quote_kind
from steward.reference.price_series import (
    SeriesRef,
    choose_series,
    holding_symbols,
    index_symbols,
    yahoo_mic,
)
from steward.reference.read import PricePoint
from steward.sources.yahoo import SOURCE_LABEL as YAHOO_LABEL

CHANGE_HELP = "Fraction (0.012 = +1.2 %) of the latest against the previous value."


def _change(latest: Decimal | None, previous: Decimal | None) -> float | None:
    if latest is None or previous is None or previous == 0:
        return None
    return float(latest / previous - 1)


def _opt(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


# ---------------------------------------------------------------------------
# Holdings: recent closes in one call
# ---------------------------------------------------------------------------


class IntradayQuote(BaseModel):
    """The latest stored Yahoo quote of a holding's listing (Yahoo Finance, unofficial)."""

    symbol: str
    source: str = YAHOO_LABEL
    kind: Literal["intraday", "close"] = Field(
        description="intraday: a last trade during the session; close: the session's close."
    )
    price: float
    price_time: dt.datetime = Field(description="When the price traded (UTC).")
    time_local: str = Field(description="HH:MM in the exchange's time zone.")
    exchange_tz: str | None
    session_date: dt.date = Field(description="Exchange-local day of the price.")
    previous_close: float | None
    change_pct: float | None = Field(description=CHANGE_HELP)
    currency: str | None
    delay_note: str
    fetched_at: dt.datetime


class HoldingPrices(BaseModel):
    ticker: str
    name: str | None
    symbol: str | None = Field(
        description="Symbol of the daily series (Stooq's, or Yahoo's when Stooq has none); "
        "null when none is known."
    )
    source: Literal["Stooq", "Yahoo"] | None = Field(
        default=None, description="Whose daily closes; null when none are stored."
    )
    as_of: dt.date | None = Field(description="Date of the latest stored close.")
    last_close: float | None
    previous_close: float | None = Field(description="The stored close before `as_of`.")
    day_change_pct: float | None = Field(description=CHANGE_HELP)
    bars: list[PricePoint] = Field(
        description="Daily closes from `days` before `as_of` to `as_of`, oldest first; empty "
        "when no prices are stored."
    )
    intraday: IntradayQuote | None = Field(
        default=None,
        description="The latest intraday quote when intraday prices are on and one is stored "
        "(it may be newer than `as_of`).",
    )


class HoldingsPrices(BaseModel):
    days: int
    holdings: list[HoldingPrices] = Field(
        description="Every open position of the latest snapshot, by value descending."
    )


async def _current_holdings(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """The latest sync's positions (as the Holdings page lists them), largest first."""
    rows: list[asyncpg.Record] = await pool.fetch(
        "select p.ticker, i.name from public.position_snapshot p"
        " left join public.instrument i on i.t212_ticker = p.ticker"
        " where p.snapshot_date = (select max(snapshot_date) from public.account_snapshot)"
        " order by p.value_base desc, p.ticker"
    )
    return rows


async def yahoo_quotes(
    pool: asyncpg.Pool, symbols: list[str], now: dt.datetime
) -> dict[str, IntradayQuote]:
    rows = await pool.fetch(
        "select * from public.market_quote where source = 'yahoo' and symbol = any($1::text[])",
        symbols,
    )
    out = {}
    for r in rows:
        zone = ZoneInfo(r["exchange_tz"] or "UTC")
        out[r["symbol"]] = IntradayQuote(
            symbol=r["symbol"],
            kind=quote_kind(r["price_time"], r["session_end"], now),
            price=float(r["price"]),
            price_time=r["price_time"],
            time_local=r["price_time"].astimezone(zone).strftime("%H:%M"),
            exchange_tz=r["exchange_tz"],
            session_date=r["session_date"],
            previous_close=_opt(r["previous_close"]),
            change_pct=_change(r["price"], r["previous_close"]),
            currency=r["currency"],
            delay_note=delay_note(yahoo_mic(r["symbol"])),
            fetched_at=r["fetched_at"],
        )
    return out


async def holdings_prices(
    pool: asyncpg.Pool, days: int, now: dt.datetime | None = None
) -> HoldingsPrices:
    now = now or dt.datetime.now(dt.UTC)
    holdings = await _current_holdings(pool)
    jobs = await load_active(pool, "jobs", JobsConfig)
    intraday_on = (jobs.body if jobs else JobsConfig()).prices.intraday_enabled
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool,
        [h["ticker"] for h in holdings],
        profiles.body if profiles else HoldingProfilesConfig(),
    )
    chosen = await choose_series(pool, symbols)
    refs = sorted({r for r in chosen.values() if r}, key=lambda r: (r.symbol, r.source))
    # The window's bars, plus the two latest whatever the window (the day change needs the
    # previous close even after a gap longer than the window).
    rows = await pool.fetch(
        "with wanted as (select * from unnest($1::text[], $2::text[]) as w(symbol, source)),"
        " ranked as (select b.symbol, b.source, b.date, b.close,"
        " row_number() over (partition by b.symbol, b.source order by b.date desc) as rn,"
        " max(b.date) over (partition by b.symbol, b.source) as latest from public.price_bar b"
        " join wanted w on w.symbol = b.symbol and w.source = b.source)"
        " select symbol, source, date, close, rn, date >= latest - $3::int as in_window"
        " from ranked where rn <= 2 or date >= latest - $3::int order by symbol, source, date",
        [r.symbol for r in refs],
        [r.source for r in refs],
        days,
    )
    bars: dict[SeriesRef, list[tuple[dt.date, Decimal]]] = {}
    latest: dict[SeriesRef, dict[int, tuple[dt.date, Decimal]]] = {}
    for r in rows:
        key = SeriesRef(r["symbol"], r["source"])
        if r["in_window"]:
            bars.setdefault(key, []).append((r["date"], r["close"]))
        if r["rn"] <= 2:
            latest.setdefault(key, {})[r["rn"]] = (r["date"], r["close"])
    quotes = (
        await yahoo_quotes(pool, sorted({s.yahoo for s in symbols.values() if s.yahoo}), now)
        if intraday_on
        else {}
    )

    out = []
    for h in holdings:
        ref = chosen.get(h["ticker"])
        own = symbols.get(h["ticker"])
        series = bars.get(ref, []) if ref else []
        ends = latest.get(ref, {}) if ref else {}
        last, previous = ends.get(1), ends.get(2)
        out.append(
            HoldingPrices(
                ticker=h["ticker"],
                name=h["name"],
                symbol=ref.symbol if ref else (own.stooq if own else None),
                source=ref.name if ref else None,
                as_of=last[0] if last else None,
                last_close=_opt(last[1]) if last else None,
                previous_close=_opt(previous[1]) if previous else None,
                day_change_pct=_change(
                    last[1] if last else None, previous[1] if previous else None
                ),
                bars=[PricePoint(date=d, close=float(c)) for d, c in series],
                intraday=quotes.get(own.yahoo) if own and own.yahoo else None,
            )
        )
    return HoldingsPrices(days=days, holdings=out)


# ---------------------------------------------------------------------------
# Market strip
# ---------------------------------------------------------------------------


class FxQuote(BaseModel):
    currency: str
    base_currency: str
    source: FxSourceName = Field(description="Whose reference rates: ČNB or the ECB.")
    rate: float | None = Field(description="Base-currency units per one unit of `currency`.")
    rate_date: dt.date | None
    previous_rate: float | None = Field(description="The published rate before `rate_date`.")
    previous_rate_date: dt.date | None
    change_pct: float | None = Field(description=CHANGE_HELP)


IndexSource = Literal["Stooq", "Yahoo", "Prague Stock Exchange"]


class IndexQuote(BaseModel):
    symbol: str
    label: str
    yahoo_symbol: str | None = Field(default=None, description="Yahoo's symbol of the index.")
    last_close: float | None = Field(
        description="The latest value: a close, or the intraday value when `kind` is intraday."
    )
    as_of: dt.date | None = Field(description="Day of the latest value (exchange-local).")
    as_of_time: dt.datetime | None = Field(
        default=None, description="When the latest value was computed (UTC), when known."
    )
    exchange_tz: str | None = None
    kind: Literal["intraday", "close"] | None = Field(
        default=None, description="intraday: a value during the session; close: a close."
    )
    source: IndexSource | None = Field(default=None, description="Where the latest value is from.")
    previous_close: float | None
    previous_date: dt.date | None
    change_pct: float | None = Field(description=CHANGE_HELP)


class MarketStrip(BaseModel):
    base_currency: str | None = Field(
        description="The latest snapshot's base currency (the active strategy's before the "
        "first sync)."
    )
    fx: list[FxQuote] = Field(
        description="Each currency held in the latest snapshot (positions and the account) "
        "other than the base currency, by code."
    )
    indices: list[IndexQuote] = Field(
        description="The Jobs config's `prices.market_strip`, in its order."
    )


async def _fx_quote(pool: asyncpg.Pool, source: FxSourceName, currency: str, base: str) -> FxQuote:
    """Rates on the two latest dates on which every rate the pair needs was published."""
    home = HOME_CURRENCY[source]
    needed = sorted({c for c in (currency, base) if c != home})
    rows = await pool.fetch(
        "select rate_date, currency, rate from public.fx_rate where source = $1"
        " and currency = any($2::text[]) and rate_date in (select distinct rate_date"
        " from public.fx_rate where source = $1 and currency = any($2::text[])"
        " order by rate_date desc limit 10) order by rate_date desc",
        source,
        needed,
    )
    by_date: dict[dt.date, dict[str, Decimal]] = {}
    for r in rows:
        by_date.setdefault(r["rate_date"], {})[r["currency"]] = r["rate"]
    quotes: list[tuple[dt.date, Decimal]] = []
    for day, rates in sorted(by_date.items(), reverse=True):
        if all(c in rates for c in needed):
            home_per = {home: Decimal(1), **rates}
            quotes.append((day, home_per[currency] / home_per[base]))
        if len(quotes) == 2:
            break
    latest = quotes[0] if quotes else None
    previous = quotes[1] if len(quotes) > 1 else None
    return FxQuote(
        currency=currency,
        base_currency=base,
        source=source,
        rate=_opt(latest[1]) if latest else None,
        rate_date=latest[0] if latest else None,
        previous_rate=_opt(previous[1]) if previous else None,
        previous_rate_date=previous[0] if previous else None,
        change_pct=_change(latest[1] if latest else None, previous[1] if previous else None),
    )


async def _index_quote(
    pool: asyncpg.Pool,
    settings: PriceSettings,
    symbol: str,
    label: str,
    yahoo_symbol: str | None,
    now: dt.datetime,
) -> IndexQuote:
    """The freshest of: the chosen daily series' last close (Stooq, else Yahoo), Yahoo's
    intraday quote (when newer than that close), and for PX the PSE page's value."""
    ref = (await choose_series(pool, {0: index_symbols(settings, symbol)}))[0]
    rows = (
        await pool.fetch(
            "select date, close from public.price_bar where symbol = $1 and source = $2"
            " order by date desc limit 2",
            ref.symbol,
            ref.source,
        )
        if ref
        else []
    )
    last = rows[0] if rows else None
    previous = rows[1] if len(rows) > 1 else None
    quote = IndexQuote(
        symbol=symbol,
        label=label,
        yahoo_symbol=yahoo_symbol,
        last_close=_opt(last["close"]) if last else None,
        as_of=last["date"] if last else None,
        kind="close" if last else None,
        source=ref.name if ref and last else None,
        previous_close=_opt(previous["close"]) if previous else None,
        previous_date=previous["date"] if previous else None,
        change_pct=_change(
            last["close"] if last else None, previous["close"] if previous else None
        ),
    )
    live_rows = []
    if yahoo_symbol and settings.intraday_enabled:
        live_rows.append(
            await pool.fetchrow(
                "select *, 'Yahoo' as label from public.market_quote where symbol = $1"
                " and source = 'yahoo'",
                yahoo_symbol,
            )
        )
    if symbol == PSE_SYMBOL:
        live_rows.append(
            await pool.fetchrow(
                "select *, 'Prague Stock Exchange' as label from public.market_quote"
                " where symbol = $1 and source = 'pse'",
                PSE_SYMBOL,
            )
        )
    for live in live_rows:
        if live is None:
            continue
        # A daily close stays the value of record for its own day.
        if quote.as_of is not None and live["session_date"] <= quote.as_of:
            continue
        kind = (
            quote_kind(live["price_time"], live["session_end"], now)
            if live["source"] == "yahoo"
            else live["kind"]
        )
        prev_value = live["previous_close"]
        quote = IndexQuote(
            symbol=symbol,
            label=label,
            yahoo_symbol=yahoo_symbol,
            last_close=float(live["price"]),
            as_of=live["session_date"],
            as_of_time=live["price_time"],
            exchange_tz=live["exchange_tz"],
            kind=kind,
            source=live["label"],
            previous_close=_opt(prev_value),
            previous_date=None,
            change_pct=_change(live["price"], prev_value),
        )
    return quote


async def market_strip(pool: asyncpg.Pool) -> MarketStrip:
    now = dt.datetime.now(dt.UTC)
    jobs = await load_active(pool, "jobs", JobsConfig)
    settings = (jobs.body if jobs else JobsConfig()).prices
    indices = [
        await _index_quote(pool, settings, i.symbol, i.label, i.yahoo(), now)
        for i in settings.market_strip
    ]

    account = await pool.fetchrow(
        "select snapshot_date, account_currency, base_currency, fx_source"
        " from public.account_snapshot order by snapshot_date desc limit 1"
    )
    if account is None:
        stored = await load_active(pool, "strategy", StrategyConfig)
        return MarketStrip(
            base_currency=stored.body.base_currency if stored else None, fx=[], indices=indices
        )

    base: str = account["base_currency"]
    source: FxSourceName = account["fx_source"]
    held = [account["account_currency"]] + [
        r["instrument_currency"]
        for r in await pool.fetch(
            "select distinct instrument_currency from public.position_snapshot"
            " where snapshot_date = $1",
            account["snapshot_date"],
        )
    ]
    currencies = sorted({normalise_currency(c)[0] for c in held} - {normalise_currency(base)[0]})
    return MarketStrip(
        base_currency=base,
        fx=[await _fx_quote(pool, source, c, base) for c in currencies],
        indices=indices,
    )
