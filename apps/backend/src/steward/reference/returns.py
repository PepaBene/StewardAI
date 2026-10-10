"""Price and total-return artifacts of the holdings' chosen series (audit A16).

`returns_refresh` (weekdays after `price_history`) computes, for every holding's chosen series
(`choose_series`: the one readers serve, whichever source it came from) and each standard window
ending today, the raw price return, the adjusted price return and the reinvested total return,
and stores them as versioned `price_return_series` rows. The read routes (`GET /returns`,
`GET /prices/diagnostics`) never write: they compute the same figures on demand
(`free_sources.compute_returns`) or read what the job stored.

The series' quote currency is known at write time (Stooq's from its market suffix, others from
the source), the exchange sessions come from the exchange calendar (`calendar.expected_sessions`)
and the corporate actions are those of the listing under any of its provider symbols.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.store import load_active
from steward.free_sources import (
    calculate_and_store_returns,
    compute_returns,
)
from steward.jobs.runner import JobContext
from steward.portfolio.reconstruct import rebuild_portfolio_history
from steward.reference.identity import holding_tickers
from steward.reference.listings import resolve_series_listing
from steward.reference.price_series import (
    PriceSymbols,
    SeriesRef,
    choose_one,
    holding_symbols,
)
from steward.sources.market import major_currency, stooq_currency

# Trailing windows, in calendar days: 1 month, 3 months, 6 months, 1 year.
WINDOW_DAYS: tuple[int, ...] = (30, 91, 182, 365)


@dataclass(frozen=True)
class ReturnInputs:
    ticker: str
    ref: SeriesRef
    currency: str
    mic: str | None
    listing_id: int | None
    action_symbols: list[str]
    ads_ratio: Decimal | None


async def series_currency(
    pool: asyncpg.Pool | asyncpg.Connection, ref: SeriesRef, instrument_currency: str | None
) -> str | None:
    """The quote currency code of a stored series: what its bars say, else the Stooq market's,
    else the instrument's trading currency."""
    stored = await pool.fetchval(
        "select currency from public.price_bar where symbol=$1 and source=$2"
        " and currency is not null order by date desc limit 1",
        ref.symbol,
        ref.source,
    )
    return (
        stored
        or (stooq_currency(ref.symbol) if ref.source == "stooq" else None)
        or instrument_currency
    )


async def return_inputs(
    pool: asyncpg.Pool,
    ticker: str,
    symbols: PriceSymbols,
    profiles: HoldingProfilesConfig,
) -> ReturnInputs | None:
    """What `compute_returns` needs for a holding's chosen series; None when nothing is stored
    or the series has no usable quote currency."""
    ref = await choose_one(pool, symbols)
    if ref is None:
        return None
    instrument = await pool.fetchrow(
        "select currency, price_symbol from public.instrument where t212_ticker=$1", ticker
    )
    currency = await series_currency(pool, ref, instrument["currency"] if instrument else None)
    if currency is None:
        return None
    listing_id, mic = await resolve_series_listing(pool, ref.symbol, ref.source)
    names = [r.symbol for r in symbols.refs()]
    if instrument and instrument["price_symbol"]:
        names.append(instrument["price_symbol"])
    profile = profiles.for_ticker(ticker)
    ads = profile.share_basis.ads_ratio if profile else None
    return ReturnInputs(
        ticker=ticker,
        ref=ref,
        currency=currency,
        mic=mic,
        listing_id=listing_id,
        action_symbols=sorted(set(names)),
        ads_ratio=Decimal(str(ads)) if ads is not None else None,
    )


async def actions_complete(
    pool: asyncpg.Pool | asyncpg.Connection, symbols: list[str], end: dt.date
) -> bool:
    """Whether the owner verified corporate-action coverage through `end` for the holding."""
    row = await pool.fetchrow(
        "select state, latest_period from public.coverage_status"
        " where provider='owner_review' and dataset='corporate_actions'"
        " and entity_key = any($1::text[]) order by latest_period desc nulls last limit 1",
        symbols,
    )
    return bool(
        row is not None
        and row["state"] == "available"
        and row["latest_period"] is not None
        and row["latest_period"] >= end
    )


async def ecb_fx_to_quote(
    pool: asyncpg.Pool | asyncpg.Connection,
    ex_dates: dict[dt.date, str],
    quote_currency: str,
) -> tuple[dict[dt.date, Decimal], dict[str, str]]:
    """Dividend-currency -> quote-currency rates at each ex-date from the stored ECB reference
    rates (the latest fixing on or before the ex-date): ({ex_date: rate}, {ex_date: fixing
    date}). Missing rates are simply absent, which makes that dividend unavailable."""
    rates: dict[dt.date, Decimal] = {}
    fixings: dict[str, str] = {}
    quote = major_currency(quote_currency)
    for day, dividend_currency in ex_dates.items():
        major = major_currency(dividend_currency)
        if major is None or quote is None or major == quote:
            continue

        async def eur_per_unit(code: str, day: dt.date = day) -> tuple[Decimal, dt.date] | None:
            if code == "EUR":
                return Decimal(1), day
            row = await pool.fetchrow(
                "select rate, rate_date from public.fx_rate where source='ecb' and currency=$1"
                " and rate_date <= $2 order by rate_date desc limit 1",
                code,
                day,
            )
            return (Decimal(row["rate"]), row["rate_date"]) if row else None

        numerator = await eur_per_unit(major)
        denominator = await eur_per_unit(quote)
        if numerator and denominator and denominator[0] > 0:
            rates[day] = numerator[0] / denominator[0]
            fixings[day.isoformat()] = min(numerator[1], denominator[1]).isoformat()
    return rates, fixings


async def _foreign_dividends(
    pool: asyncpg.Pool, inputs: ReturnInputs, start: dt.date, end: dt.date
) -> dict[dt.date, str]:
    rows = await pool.fetch(
        "select ex_date, currency from public.corporate_action where status='confirmed'"
        " and action_type='cash_dividend' and currency is not null"
        " and ex_date between $2 and $3"
        " and (symbol = any($1::text[]) or ($4::bigint is not null and listing_id=$4))",
        inputs.action_symbols,
        start,
        end,
        inputs.listing_id,
    )
    return {r["ex_date"]: r["currency"] for r in rows}


async def compute_for(
    pool: asyncpg.Pool,
    inputs: ReturnInputs,
    start: dt.date,
    end: dt.date,
    *,
    store: bool,
    withholding_rate: Decimal = Decimal(0),
) -> dict[str, object]:
    """One window of one holding's chosen series; `store` is False on the read routes."""
    dividend_currencies = await _foreign_dividends(pool, inputs, start, end)
    fx, fixings = await ecb_fx_to_quote(pool, dividend_currencies, inputs.currency)
    kwargs: dict[str, Any] = dict(
        symbol=inputs.ref.symbol,
        start=start,
        end=end,
        source=inputs.ref.source,
        currency=inputs.currency,
        actions_complete=await actions_complete(pool, inputs.action_symbols, end),
        mic=inputs.mic,
        listing_id=inputs.listing_id,
        action_symbols=inputs.action_symbols,
        ads_ratio=inputs.ads_ratio,
        withholding_rate=withholding_rate,
        fx_to_quote=fx or None,
    )
    if store:
        output = await calculate_and_store_returns(pool, **kwargs)
    else:
        output = (await compute_returns(pool, **kwargs)).output
    if fixings:
        output["fx_fixing_dates"] = fixings
    return output


async def run_returns_refresh(ctx: JobContext, today: dt.date) -> None:
    pool = ctx.pool
    async with ctx.step("load_config") as step:
        profiles_stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
        await ctx.record_config_version(profiles_stored.id if profiles_stored else None)
        profiles = profiles_stored.body if profiles_stored else HoldingProfilesConfig()
        tickers = await holding_tickers(pool)
        symbols = await holding_symbols(pool, tickers, profiles)
        step.detail["holdings"] = len(tickers)
    async with ctx.step("returns") as step:
        stored: dict[str, Any] = {}
        skipped: dict[str, str] = {}
        failed: dict[str, str] = {}
        for ticker in tickers:
            inputs = await return_inputs(pool, ticker, symbols[ticker], profiles)
            if inputs is None:
                skipped[ticker] = "no stored price series or no quote currency"
                continue
            windows: dict[str, Any] = {}
            for days in WINDOW_DAYS:
                start = today - dt.timedelta(days=days)
                try:
                    out = await compute_for(pool, inputs, start, today, store=True)
                except asyncpg.PostgresError as exc:
                    failed[ticker] = f"{type(exc).__name__}: {str(exc)[:160]}"
                    break
                windows[f"{days}d"] = {
                    "coverage": float(out["coverage"]),  # type: ignore[arg-type]
                    "expected": out["expected_sessions"],
                    "price_adjusted": None
                    if out["price_adjusted"] is None
                    else float(out["price_adjusted"]),  # type: ignore[arg-type]
                    "total_return_reason": out["total_return_reason"],
                }
            else:
                stored[ticker] = {
                    "series": f"{inputs.ref.name} {inputs.ref.symbol}",
                    "windows": windows,
                }
        step.detail.update(stored=stored, skipped=skipped, failed=failed)


async def returns_refresh_handler(ctx: JobContext) -> None:
    await run_returns_refresh(ctx, dt.datetime.now(dt.UTC).date())
    # Same inputs (the chosen closes), same cadence: keep the pre-snapshot history in step.
    await rebuild_portfolio_history(ctx)
