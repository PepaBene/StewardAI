"""Which stored daily price series a holding or an index uses, and its Yahoo symbol.

Daily closes come from two sources, each under its own symbol in `price_bar`:

- Stooq (`cez.cz`), the source of record;
- Yahoo Finance (`CEZ.PR`, unofficial), collected only when Stooq refuses, cannot be reached
  or has no symbol for the holding.

A series is never stitched from both (their adjustments differ, so a stitch could show a
false jump). Every reader picks one whole series per holding with `choose_series`: Stooq's
when it is at least as recent as Yahoo's, else Yahoo's. While Stooq works, Yahoo is not
collected, so Stooq wins; once Stooq stops delivering, the Yahoo series (backfilled over
the configured history on its first run) takes over, and Stooq wins again when it catches up.

Yahoo symbols are derived from the listing: the Stooq symbol's ticker and market suffix
(`novo-b.dk` -> `NOVO-B.CO`), else the OpenFIGI listing ticker on the home market (MIC).
The holding profile's `yahoo_symbol` overrides both.
"""

import datetime as dt
import json
from dataclasses import dataclass
from typing import Literal

import asyncpg

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import PriceSettings

PriceSource = Literal["stooq", "yahoo"]
SOURCE_NAME: dict[PriceSource, str] = {"stooq": "Stooq", "yahoo": "Yahoo"}

# Stooq market suffix -> Yahoo suffix (US listings are bare on Yahoo).
YAHOO_SUFFIX_BY_STOOQ: dict[str, str] = {
    "us": "", "uk": ".L", "de": ".DE", "nl": ".AS", "dk": ".CO", "cz": ".PR", "jp": ".T",
    "pl": ".WA",
}  # fmt: skip
# Operating MIC -> Yahoo suffix.
YAHOO_SUFFIX_BY_MIC: dict[str, str] = {
    "XNAS": "", "XNYS": "", "ARCX": "", "BATS": "", "XASE": "", "XLON": ".L", "XETR": ".DE",
    "XFRA": ".F", "XAMS": ".AS", "XCSE": ".CO", "XPRA": ".PR", "XTKS": ".T", "XWAR": ".WA",
    "XPAR": ".PA", "XBRU": ".BR", "XLIS": ".LS", "XMIL": ".MI", "XMAD": ".MC", "XSWX": ".SW",
    "XSTO": ".ST", "XHEL": ".HE", "XOSL": ".OL", "XWBO": ".VI", "XDUB": ".IR",
}  # fmt: skip
# Operating MIC -> OpenFIGI composite exchange code (where the listing ticker is read).
COMPOSITE_BY_MIC: dict[str, str] = {
    "XNAS": "US", "XNYS": "US", "ARCX": "US", "BATS": "US", "XASE": "US", "XLON": "LN",
    "XETR": "GR", "XFRA": "GR", "XAMS": "NA", "XCSE": "DC", "XPRA": "CP", "XTKS": "JP",
    "XWAR": "PW", "XPAR": "FP", "XBRU": "BB", "XLIS": "PL", "XMIL": "IM", "XMAD": "SM",
    "XSWX": "SW", "XSTO": "SS", "XHEL": "FH", "XOSL": "NO", "XWBO": "AV", "XDUB": "ID",
}  # fmt: skip
# Yahoo suffix -> the MIC whose session a listing trades in (US: NYSE and Nasdaq share hours).
MIC_BY_YAHOO_SUFFIX: dict[str, str] = {
    "": "XNYS", ".L": "XLON", ".DE": "XETR", ".F": "XFRA", ".AS": "XAMS", ".CO": "XCSE",
    ".PR": "XPRA", ".T": "XTKS", ".WA": "XWAR", ".PA": "XPAR", ".BR": "XBRU", ".LS": "XLIS",
    ".MI": "XMIL", ".MC": "XMAD", ".SW": "XSWX", ".ST": "XSTO", ".HE": "XHEL", ".OL": "XOSL",
    ".VI": "XWBO", ".IR": "XDUB",
}  # fmt: skip
# Index -> the exchange whose session it is computed in.
INDEX_MIC: dict[str, str] = {
    "^GSPC": "XNYS", "^DJI": "XNYS", "^IXIC": "XNAS", "^STOXX": "XETR", "^GDAXI": "XETR",
    "^FTSE": "XLON", "^FCHI": "XPAR", "^AEX": "XAMS", "^N225": "XTKS",
}  # fmt: skip


def yahoo_from_stooq(stooq: str | None) -> str | None:
    """`novo-b.dk` -> `NOVO-B.CO`, `msft.us` -> `MSFT`; None for indices and unknown markets."""
    if not stooq or stooq.startswith("^"):
        return None
    ticker, _, suffix = stooq.rpartition(".")
    if not ticker or suffix not in YAHOO_SUFFIX_BY_STOOQ:
        return None
    return ticker.upper() + YAHOO_SUFFIX_BY_STOOQ[suffix]


def yahoo_from_listing(ticker: str | None, mic: str | None) -> str | None:
    """A listing ticker on its market: ("CEZ", "XPRA") -> `CEZ.PR`; ("BRK/B", "XNYS") -> `BRK-B`."""
    if not ticker or not mic or mic not in YAHOO_SUFFIX_BY_MIC:
        return None
    clean = ticker.strip().upper().replace("/", "-").replace(" ", "-")
    return clean + YAHOO_SUFFIX_BY_MIC[mic]


def yahoo_mic(symbol: str) -> str | None:
    """The MIC whose trading session a Yahoo symbol follows (None: unknown)."""
    if symbol.startswith("^"):
        return INDEX_MIC.get(symbol)
    _, dot, suffix = symbol.rpartition(".")
    return MIC_BY_YAHOO_SUFFIX.get(f".{suffix}" if dot else "")


@dataclass(frozen=True)
class PriceSymbols:
    stooq: str | None
    yahoo: str | None


@dataclass(frozen=True)
class SeriesRef:
    symbol: str
    source: PriceSource

    @property
    def name(self) -> str:
        return SOURCE_NAME[self.source]


async def holding_symbols(
    pool: asyncpg.Pool | asyncpg.Connection, tickers: list[str], profiles: HoldingProfilesConfig
) -> dict[str, PriceSymbols]:
    """Ticker -> its Stooq and Yahoo symbols (the holding profile's overrides first)."""
    rows = await pool.fetch(
        "select i.t212_ticker, i.price_symbol, i.home_mic,"
        " (select x.listing_ticker from public.instrument_identifier x"
        "   where x.ticker = i.t212_ticker and x.exch_code = $2::jsonb->>i.home_mic"
        "   and x.listing_ticker is not null order by (x.figi = x.composite_figi) desc, x.figi"
        "   limit 1) as listing_ticker"
        " from public.instrument i where i.t212_ticker = any($1::text[])",
        tickers,
        json.dumps(COMPOSITE_BY_MIC),
    )
    stored = {r["t212_ticker"]: r for r in rows}
    out: dict[str, PriceSymbols] = {}
    for ticker in tickers:
        profile = profiles.for_ticker(ticker)
        row = stored.get(ticker)
        stooq = (profile.price_symbol if profile else None) or (
            row["price_symbol"] if row else None
        )
        yahoo = (
            (profile.yahoo_symbol if profile else None)
            or yahoo_from_stooq(stooq)
            or (yahoo_from_listing(row["listing_ticker"], row["home_mic"]) if row else None)
        )
        out[ticker] = PriceSymbols(stooq=stooq, yahoo=yahoo)
    return out


def index_symbols(settings: PriceSettings, symbol: str) -> PriceSymbols:
    return PriceSymbols(stooq=symbol, yahoo=settings.index_yahoo(symbol))


async def latest_dates(
    pool: asyncpg.Pool | asyncpg.Connection, refs: list[SeriesRef]
) -> dict[SeriesRef, dt.date]:
    if not refs:
        return {}
    rows = await pool.fetch(
        "select symbol, source, max(date) as latest from public.price_bar"
        " where (symbol, source) in (select * from unnest($1::text[], $2::text[]))"
        " group by symbol, source",
        [r.symbol for r in refs],
        [r.source for r in refs],
    )
    return {SeriesRef(r["symbol"], r["source"]): r["latest"] for r in rows}


def pick(symbols: PriceSymbols, latest: dict[SeriesRef, dt.date]) -> SeriesRef | None:
    """Stooq's series when it is at least as recent as Yahoo's, else Yahoo's."""
    stooq = SeriesRef(symbols.stooq, "stooq") if symbols.stooq else None
    yahoo = SeriesRef(symbols.yahoo, "yahoo") if symbols.yahoo else None
    stooq_at = latest.get(stooq) if stooq else None
    yahoo_at = latest.get(yahoo) if yahoo else None
    if stooq and stooq_at and (yahoo_at is None or stooq_at >= yahoo_at):
        return stooq
    if yahoo and yahoo_at:
        return yahoo
    return None


async def choose_series[K](
    pool: asyncpg.Pool | asyncpg.Connection, wanted: dict[K, PriceSymbols]
) -> dict[K, SeriesRef | None]:
    """For each key, the one stored series its readers use (None: nothing stored)."""
    refs: set[SeriesRef] = set()
    for p in wanted.values():
        if p.stooq:
            refs.add(SeriesRef(p.stooq, "stooq"))
        if p.yahoo:
            refs.add(SeriesRef(p.yahoo, "yahoo"))
    latest = await latest_dates(pool, sorted(refs, key=lambda r: (r.symbol, r.source)))
    return {key: pick(p, latest) for key, p in wanted.items()}


async def choose_one(
    pool: asyncpg.Pool | asyncpg.Connection, symbols: PriceSymbols
) -> SeriesRef | None:
    return (await choose_series(pool, {0: symbols}))[0]
