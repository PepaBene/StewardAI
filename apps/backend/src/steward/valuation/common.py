"""Shared lookups for the valuation job and its read models."""

import datetime as dt
from dataclasses import dataclass

import asyncpg

from steward.config.jobs import FundValuationSource, JobsConfig
from steward.fx.convert import HOME_CURRENCY, FxRate, FxSourceName, FxTable
from steward.sources.market import stooq_currency

# Trading212 / OpenFIGI security types of exchange-traded funds.
FUND_TYPES = {"ETP", "ETF", "Mutual Fund", "Unit"}
# OpenFIGI security types of depositary receipts (their per-share figures need a ratio).
DR_TYPES = {"ADR", "GDR", "Depositary Receipt"}


def price_currency(symbol: str | None, instrument_currency: str | None) -> str | None:
    """The currency a stored close is in: the Stooq market's (GBX for London), else the
    instrument's trading currency."""
    return (stooq_currency(symbol) if symbol else None) or instrument_currency


def fund_for_ticker(jobs: JobsConfig, ticker: str) -> FundValuationSource | None:
    return next((f for f in jobs.valuation.funds if ticker in f.tickers), None)


@dataclass(frozen=True)
class InstrumentInfo:
    ticker: str
    name: str | None
    currency: str | None
    security_type: str | None
    issuer_lei: str | None
    is_fund: bool
    is_depositary: bool


async def instrument_info(pool: asyncpg.Pool, tickers: list[str]) -> dict[str, InstrumentInfo]:
    rows = await pool.fetch(
        "select i.t212_ticker, i.name, i.currency, i.security_type, i.issuer_lei,"
        " exists (select 1 from public.instrument_identifier d where d.ticker = i.t212_ticker"
        "   and (d.security_type = any($2::text[]) or d.security_type2 = any($2::text[])))"
        "   as depositary"
        " from public.instrument i where i.t212_ticker = any($1::text[])",
        tickers,
        sorted(DR_TYPES),
    )
    out = {}
    for r in rows:
        kind = r["security_type"] or ""
        out[r["t212_ticker"]] = InstrumentInfo(
            ticker=r["t212_ticker"],
            name=r["name"],
            currency=r["currency"],
            security_type=r["security_type"],
            issuer_lei=r["issuer_lei"],
            is_fund=kind in FUND_TYPES,
            is_depositary=kind in DR_TYPES or bool(r["depositary"]),
        )
    return out


async def fx_source(pool: asyncpg.Pool) -> FxSourceName:
    """The rates the latest sync converted with (ČNB for a CZK base), ECB before any."""
    source = await pool.fetchval(
        "select fx_source from public.account_snapshot order by snapshot_date desc limit 1"
    )
    return "cnb" if source == "cnb" else "ecb"


async def fx_table(pool: asyncpg.Pool, source: FxSourceName, on: dt.date) -> FxTable:
    """The latest stored rate of every currency on or before `on`."""
    rows = await pool.fetch(
        "select distinct on (currency) source, rate_date, currency, rate, published_amount,"
        " published_rate, provider from public.fx_rate where source = $1 and rate_date <= $2"
        " order by currency, rate_date desc",
        source,
        on,
    )
    return FxTable(source, [FxRate(**dict(r)) for r in rows])


async def fx_currencies(pool: asyncpg.Pool, source: FxSourceName) -> set[str]:
    rows = await pool.fetch(
        "select distinct currency from public.fx_rate where source = $1", source
    )
    return {r["currency"] for r in rows} | {HOME_CURRENCY[source]}
