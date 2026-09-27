"""Recent quotes for the Holdings page and the market strip, read from stored data only.

- `holdings_prices`: every current holding's recent daily closes from `price_bar`, with the
  latest day's change, in one call (the Holdings page's sparklines). Symbols resolve exactly
  as for the holding page's price history (holding profile's `price_symbol`, else the one
  derived from OpenFIGI).
- `market_strip`: the reference FX rate of each held currency against the base currency
  (latest and previous published rate, from `fx_rate`), and the configured index quotes
  (latest and previous close, from `price_bar`).

Nothing is estimated: a holding or index without stored bars, or a currency without two
published rates, is returned with nulls.
"""

import datetime as dt
from decimal import Decimal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.fx.convert import HOME_CURRENCY, FxSourceName, normalise_currency
from steward.reference.prices import symbols_for
from steward.reference.read import PricePoint

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


class HoldingPrices(BaseModel):
    ticker: str
    name: str | None
    symbol: str | None = Field(description="Stooq symbol; null when none is known.")
    as_of: dt.date | None = Field(description="Date of the latest stored close.")
    last_close: float | None
    previous_close: float | None = Field(description="The stored close before `as_of`.")
    day_change_pct: float | None = Field(description=CHANGE_HELP)
    bars: list[PricePoint] = Field(
        description="Daily closes from `days` before `as_of` to `as_of`, oldest first; empty "
        "when no prices are stored."
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


async def holdings_prices(pool: asyncpg.Pool, days: int) -> HoldingsPrices:
    holdings = await _current_holdings(pool)
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await symbols_for(
        pool,
        [h["ticker"] for h in holdings],
        profiles.body if profiles else HoldingProfilesConfig(),
    )
    wanted = sorted({s for s in symbols.values() if s})
    # The window's bars, plus the two latest whatever the window (the day change needs the
    # previous close even after a gap longer than the window).
    rows = await pool.fetch(
        "with ranked as (select symbol, date, close,"
        " row_number() over (partition by symbol order by date desc) as rn,"
        " max(date) over (partition by symbol) as latest from public.price_bar"
        " where source = 'stooq' and symbol = any($1::text[]))"
        " select symbol, date, close, rn, date >= latest - $2::int as in_window from ranked"
        " where rn <= 2 or date >= latest - $2::int order by symbol, date",
        wanted,
        days,
    )
    bars: dict[str, list[tuple[dt.date, Decimal]]] = {}
    latest: dict[str, dict[int, tuple[dt.date, Decimal]]] = {}
    for r in rows:
        if r["in_window"]:
            bars.setdefault(r["symbol"], []).append((r["date"], r["close"]))
        if r["rn"] <= 2:
            latest.setdefault(r["symbol"], {})[r["rn"]] = (r["date"], r["close"])

    out = []
    for h in holdings:
        symbol = symbols.get(h["ticker"])
        series = bars.get(symbol, []) if symbol else []
        ends = latest.get(symbol, {}) if symbol else {}
        last, previous = ends.get(1), ends.get(2)
        out.append(
            HoldingPrices(
                ticker=h["ticker"],
                name=h["name"],
                symbol=symbol,
                as_of=last[0] if last else None,
                last_close=_opt(last[1]) if last else None,
                previous_close=_opt(previous[1]) if previous else None,
                day_change_pct=_change(
                    last[1] if last else None, previous[1] if previous else None
                ),
                bars=[PricePoint(date=d, close=float(c)) for d, c in series],
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


class IndexQuote(BaseModel):
    symbol: str
    label: str
    last_close: float | None
    as_of: dt.date | None = Field(description="Date of the latest stored close.")
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


async def _index_quote(pool: asyncpg.Pool, symbol: str, label: str) -> IndexQuote:
    rows = await pool.fetch(
        "select date, close from public.price_bar where symbol = $1 and source = 'stooq'"
        " order by date desc limit 2",
        symbol,
    )
    last = rows[0] if rows else None
    previous = rows[1] if len(rows) > 1 else None
    return IndexQuote(
        symbol=symbol,
        label=label,
        last_close=_opt(last["close"]) if last else None,
        as_of=last["date"] if last else None,
        previous_close=_opt(previous["close"]) if previous else None,
        previous_date=previous["date"] if previous else None,
        change_pct=_change(
            last["close"] if last else None, previous["close"] if previous else None
        ),
    )


async def market_strip(pool: asyncpg.Pool) -> MarketStrip:
    jobs = await load_active(pool, "jobs", JobsConfig)
    strip = (jobs.body if jobs else JobsConfig()).prices.market_strip
    indices = [await _index_quote(pool, i.symbol, i.label) for i in strip]

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
