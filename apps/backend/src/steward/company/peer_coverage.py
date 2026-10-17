"""Confirmed peers the owner does not hold: which ones, and the key their data is stored under.

A peer is confirmed as a plain exchange symbol (MSFT, META), while the covered universe is the
owner's Trading212 instruments (MSFT_US_EQ). So a peer comparison matched nothing: a held peer
(META is held as FB_US_EQ) was not recognised, and a peer not held had no statements or prices,
leaving every confirmed peer "not in the covered universe". Here a symbol maps to the held
instrument trading under it, and an unheld US symbol to a peer key in Trading212's own form
(MSFT_US_EQ): the SEC XBRL job stores its statements and the price job its closes under that key,
without the peer becoming an instrument the portfolio, allocator or calendars would act on.
"""

import re

import asyncpg

# A US listing symbol as Finnhub and the peer proposals give it; a suffixed symbol (GSK.L,
# 2303.TW, BBAS3.SA) is a foreign listing outside the SEC's statements.
US_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,5}$")
US_SUFFIX = "_US_EQ"


def peer_key(symbol: str) -> str:
    return f"{symbol.upper()}{US_SUFFIX}"


def peer_symbol(key: str) -> str | None:
    """The US symbol of a peer key, None for anything else."""
    if not key.endswith(US_SUFFIX):
        return None
    symbol = key.removesuffix(US_SUFFIX)
    return symbol if US_SYMBOL.match(symbol) else None


def price_symbol(symbol: str) -> str:
    """The Stooq symbol the price job stores a US peer's closes under."""
    return f"{symbol.lower()}.us"


def symbol_index(rows: list[tuple[str, str | None]]) -> dict[str, str]:
    """Upper-cased trading symbol -> universe ticker, from (ticker, price_symbol) rows.

    The price symbol names the listing (FB_US_EQ trades as meta.us); a ticker's own prefix
    (GOOGL_US_EQ) is the fallback when no price symbol is stored.
    """
    out: dict[str, str] = {}
    for ticker, _ in rows:
        if ticker.endswith(US_SUFFIX):
            out.setdefault(ticker.removesuffix(US_SUFFIX).upper(), ticker)
    for ticker, stooq in rows:
        if stooq and stooq.lower().endswith(".us"):
            out[stooq[: -len(".us")].upper()] = ticker
    return out


async def universe_symbols(pool: asyncpg.Pool) -> dict[str, str]:
    rows = await pool.fetch("select ticker, price_symbol from public.investment_universe")
    return symbol_index([(r["ticker"], r["price_symbol"]) for r in rows])


def resolve(stored: str, universe: set[str], by_symbol: dict[str, str]) -> str:
    """The ticker a stored peer is loaded under: a universe ticker as stored, the held
    instrument trading under the symbol, a US peer key, or the symbol as stored."""
    if stored in universe:
        return stored
    if stored.upper() in by_symbol:
        return by_symbol[stored.upper()]
    return peer_key(stored) if US_SYMBOL.match(stored.upper()) else stored


async def uncovered_peers(pool: asyncpg.Pool) -> list[str]:
    """The US symbols confirmed as a peer of a covered company that the owner does not hold."""
    by_symbol = await universe_symbols(pool)
    universe = set(by_symbol.values())
    rows = await pool.fetch(
        "select distinct upper(peer_ticker) as s from public.peer_set p"
        " join public.investment_universe u on u.ticker = p.ticker"
        " where p.status = 'confirmed'"
    )
    return sorted(
        r["s"]
        for r in rows
        if US_SYMBOL.match(r["s"]) and r["s"] not in by_symbol and r["s"] not in universe
    )
