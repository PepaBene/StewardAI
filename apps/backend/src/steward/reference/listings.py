"""The canonical listing writer (audit A17).

Nothing used to insert into `security_listing`, so `price_bar.listing_id`/`mic` stayed NULL,
corporate actions could never be recorded (they need a confirmed listing) and the EODHD and
Alpha Vantage collectors always skipped. This module derives, for a holding:

- the **security** (by ISIN, from the Trading212 instrument),
- its **home listing** (MIC from the home-market resolution, ticker from the OpenFIGI listing on
  that exchange, currency and lifecycle from the ESMA FIRDS record),
- the provider **symbols** that price this listing (`provider_identifier`: the Stooq
  `price_symbol`, the Yahoo/EODHD/Alpha Vantage symbols), which is how a price series (stored
  under the provider's own symbol, e.g. `cez.cz` or `CEZ.PR`) finds its listing even though the
  listing's ticker is the exchange ticker (`CEZ`),
- an `instrument_security_link` carrying the **match evidence**.

A link is written `confirmed` only when three independent records agree (the instrument's ISIN
has a FIRDS record on the home MIC, OpenFIGI names a listing ticker on that exchange, and the
FIRDS currency matches the instrument's), or, on a home market FIRDS does not cover (Tokyo, the
US, London), when OpenFIGI maps the ISIN to a listing on that exchange and the ISIN is of that
market's country; anything less is `suggested` and waits for the owner
(`confirm_listing`). An owner decision (confirm or reject) is never overwritten by a re-derive.
"""

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.reference.matching import Match, record_match_decision
from steward.reference.price_series import (
    COMPOSITE_BY_MIC,
    PriceSymbols,
    holding_symbols,
    series_mic,
)
from steward.sources.market import major_currency

PRICE_PROVIDERS = ("stooq", "yahoo", "eodhd", "alpha_vantage")
SYMBOL_TYPE = "symbol"
# Trading currency of home markets FIRDS does not cover (and of the larger EU ones, as a check).
MARKET_CURRENCY: dict[str, str] = {
    "XNAS": "USD", "XNYS": "USD", "ARCX": "USD", "BATS": "USD", "XTSE": "CAD", "XLON": "GBP",
    "XSWX": "CHF", "XTKS": "JPY", "XHKG": "HKD", "XKRX": "KRW", "XTAI": "TWD", "XASX": "AUD",
    "XSES": "SGD", "XSTO": "SEK", "XCSE": "DKK", "XOSL": "NOK", "XWAR": "PLN", "XPRA": "CZK",
    "XBUD": "HUF", "XETR": "EUR", "XFRA": "EUR", "XPAR": "EUR", "XAMS": "EUR", "XMIL": "EUR",
    "XMAD": "EUR", "XBRU": "EUR", "XDUB": "EUR", "XHEL": "EUR", "XLIS": "EUR", "XWBO": "EUR",
}  # fmt: skip
# Home markets outside ESMA FIRDS, by the ISIN country their own issuers carry. FIRDS can never
# hold their listings, so there OpenFIGI's mapping of the ISIN to a listing on the home
# exchange, from an issuer of that country, stands in for the FIRDS record.
NON_FIRDS_COUNTRY: dict[str, str] = {
    "XNAS": "US", "XNYS": "US", "ARCX": "US", "BATS": "US", "XTSE": "CA", "XLON": "GB",
    "XSWX": "CH", "XTKS": "JP", "XHKG": "HK", "XKRX": "KR", "XTAI": "TW", "XASX": "AU",
    "XSES": "SG",
}  # fmt: skip
# FIRDS files a venue's listings under its segment MICs (Xetra as XETA/XETU, not XETR).
FIRDS_SEGMENTS: dict[str, tuple[str, ...]] = {
    "XETR": ("XETR", "XETA", "XETB", "XETU", "XETS"),
    "XFRA": ("XFRA", "FRAA", "FRAB", "FRAU", "FRAS"),
}
# A fund has no home market: the line held is the one the broker trades, named by the
# Trading212 ticker suffix.
BROKER_VENUE: dict[str, str] = {"d": "XETR", "l": "XLON", "a": "XAMS", "p": "XPAR"}
FUND_TYPES = frozenset({"mutual fund", "etf", "etp", "unit trust", "open-end fund"})
MULTI_CURRENCY_VENUES = frozenset({"XLON"})
# Offshore domiciles whose companies list elsewhere (Nu Holdings: Cayman ISIN, NYSE only): the
# ISIN country names no exchange, so the market the shares list on is their home.
OFFSHORE_DOMICILES = frozenset({"KY", "BM", "VG", "GG", "JE", "IM", "BS", "PA", "MH", "LR", "CW"})


def broker_root(ticker: str) -> tuple[str, str | None]:
    """The exchange ticker and venue suffix of a Trading212 ticker ("SXRVd_EQ" -> SXRV, d)."""
    head = ticker.split("_", 1)[0]
    if "_US_" in ticker or not head[-1:].islower():
        return head, None
    return head[:-1], head[-1]


@dataclass(frozen=True)
class ListingCandidate:
    ticker: str  # the instrument (Trading212) ticker
    isin: str | None
    mic: str
    listing_ticker: str
    currency: str
    status: str  # active | terminated
    valid_from: dt.date | None
    valid_to: dt.date | None
    score: float
    evidence: dict[str, Any]
    symbols: PriceSymbols
    figi: str | None = None
    name: str = ""
    kind: str = "equity"
    issuer_lei: str | None = None
    confirmable: bool = False
    gaps: list[str] = field(default_factory=list)


def _date(value: dt.datetime | dt.date | None) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    return value


async def derive_candidate(
    pool: asyncpg.Pool | asyncpg.Connection,
    ticker: str,
    symbols: PriceSymbols,
    *,
    today: dt.date | None = None,
) -> ListingCandidate | None:
    """The home listing of a holding from the instrument, FIRDS and OpenFIGI, with evidence.
    None when the instrument has no home market or no listing ticker can be named."""
    today = today or dt.datetime.now(dt.UTC).date()
    inst = await pool.fetchrow(
        "select t212_ticker, isin, name, currency, home_mic, price_symbol, issuer_lei,"
        " security_type, share_class_figi from public.instrument where t212_ticker = $1",
        ticker,
    )
    if inst is None:
        return None
    root, suffix = broker_root(ticker)
    fund = str(inst["security_type"] or "").lower() in FUND_TYPES
    venue = BROKER_VENUE.get(suffix or "") if fund else None
    mic_or_none: str | None = venue or inst["home_mic"]
    if not mic_or_none:
        return None
    mic: str = mic_or_none
    # A market outside FIRDS keeps only stale records there (London's all end 2020-12-31, when
    # the UK left): they say nothing about the listing today.
    firds = (
        await pool.fetchrow(
            "select currency, status, terminated_at, first_trade_at from public.listing"
            " where isin = $1 and mic = any($2::text[])"
            " order by (terminated_at is null) desc, terminated_at desc limit 1",
            inst["isin"],
            list(FIRDS_SEGMENTS.get(mic, (mic,))),
        )
        if inst["isin"] and mic not in NON_FIRDS_COUNTRY
        else None
    )
    figi = await pool.fetchrow(
        "select figi, listing_ticker from public.instrument_identifier"
        " where ticker = $1 and exch_code = $2 and listing_ticker is not null"
        " order by (listing_ticker = $3) desc, (figi = composite_figi) desc, figi limit 1",
        ticker,
        COMPOSITE_BY_MIC.get(mic),
        root,
    )
    gaps: list[str] = []
    listing_ticker: str | None = figi["listing_ticker"] if figi else None
    if listing_ticker is None:
        gaps.append("no OpenFIGI listing ticker on the home exchange")
        stooq = symbols.stooq or inst["price_symbol"]
        if stooq:
            # The Stooq symbol's ticker part: a hint for the owner, never a confirmation.
            listing_ticker = stooq.rpartition(".")[0].upper() or None
    if listing_ticker is None:
        return None
    instrument_ccy = major_currency(inst["currency"])
    firds_ccy = major_currency(firds["currency"]) if firds and firds["currency"] else None
    # The instrument's currency is the line the broker trades (EUR on Xetra for a Tokyo share);
    # the home listing trades in its own market's currency.
    market_ccy = MARKET_CURRENCY.get(mic)
    # A fund's FIRDS currency is its base currency (USD for a Nasdaq-100 fund), not the
    # currency its line trades in: that is the broker's line itself (EUR on Xetra; London lists
    # a fund once per currency, each line under its own ticker, so there the ticker names it).
    if venue:
        currency = instrument_ccy or market_ccy
        currency_agrees = instrument_ccy is not None and (
            market_ccy == instrument_ccy or mic in MULTI_CURRENCY_VENUES
        )
    else:
        currency = firds_ccy or market_ccy or instrument_ccy
        currency_agrees = (
            firds_ccy is not None and instrument_ccy is not None and firds_ccy == instrument_ccy
        )
    if currency is None:
        return None
    isin_match = firds is not None
    # The fund line the broker holds: OpenFIGI lists the broker's own ticker on its venue, the
    # venue trades in the broker's currency, and FIRDS has the ISIN there where it covers it.
    fund_line = (
        venue is not None
        and figi is not None
        and figi["listing_ticker"] == root
        and currency_agrees
        and (isin_match or mic in NON_FIRDS_COUNTRY)
    )
    country = NON_FIRDS_COUNTRY.get(mic)
    # Outside FIRDS the broker's line trades in its own currency (EUR on Xetra for a Tokyo
    # share), so there is no second currency to agree with: the ISIN country takes its place.
    outside_firds = (
        country is not None
        and figi is not None
        and bool(inst["isin"])
        and (
            str(inst["isin"])[:2] == country
            # An offshore issuer's broker line is the listing itself when the tickers agree.
            or (str(inst["isin"])[:2] in OFFSHORE_DOMICILES and figi["listing_ticker"] == root)
        )
    )
    if not isin_match and not outside_firds and not fund_line:
        gaps.append("no FIRDS record for the instrument's ISIN on the home MIC")
    if venue is not None and not currency_agrees:
        gaps.append(f"{mic} trades in {market_ccy}, the broker's line in {instrument_ccy}")
    if venue is None and firds_ccy and instrument_ccy and firds_ccy != instrument_ccy:
        gaps.append(f"FIRDS currency {firds_ccy} differs from the instrument's {instrument_ccy}")
    terminated = _date(firds["terminated_at"]) if firds else None
    status = "terminated" if terminated is not None and terminated <= today else "active"
    score = round(
        0.45 * (isin_match or outside_firds or fund_line)
        + 0.3 * (figi is not None)
        + 0.15 * (currency_agrees or outside_firds)
        + 0.1 * bool(mic),
        2,
    )
    evidence: dict[str, Any] = {
        "derived_from": ["instrument", "firds", "openfigi"],
        "isin": inst["isin"],
        "isin_has_firds_record_on_home_mic": isin_match,
        "outside_firds_isin_country_matches": outside_firds,
        "fund_broker_line": fund_line,
        "openfigi_listing_ticker": listing_ticker if figi else None,
        "openfigi_figi": figi["figi"] if figi else None,
        "currency": {
            "firds": firds_ccy,
            "market": market_ccy,
            "instrument": instrument_ccy,
            "agrees": currency_agrees,
        },
        "home_mic": mic,
        "gaps": gaps,
    }
    return ListingCandidate(
        ticker=ticker,
        isin=inst["isin"],
        mic=mic,
        listing_ticker=listing_ticker,
        currency=currency,
        status=status,
        valid_from=_date(firds["first_trade_at"]) if firds else None,
        valid_to=terminated if status == "terminated" else None,
        score=score,
        evidence=evidence,
        symbols=symbols,
        figi=inst["share_class_figi"],
        name=inst["name"] or ticker,
        kind=inst["security_type"] or "equity",
        issuer_lei=inst["issuer_lei"],
        confirmable=(isin_match and figi is not None and currency_agrees and venue is None)
        or outside_firds
        or fund_line,
        gaps=gaps,
    )


async def _security_id(conn: asyncpg.Connection, c: ListingCandidate) -> int:
    issuer_id = None
    if c.issuer_lei:
        issuer_id = await conn.fetchval(
            "insert into public.issuer (legal_name, lei) values ($1, $2)"
            " on conflict (lei) where lei is not null do update set legal_name = issuer.legal_name"
            " returning id",
            c.name,
            c.issuer_lei,
        )
    found = None
    if c.isin:
        found = await conn.fetchval("select id from public.security where isin = $1", c.isin)
    if found is None and c.figi:
        found = await conn.fetchval("select id from public.security where figi = $1", c.figi)
    if found is not None:
        return int(found)
    return int(
        await conn.fetchval(
            "insert into public.security (issuer_id, isin, figi, kind, name, currency)"
            " values ($1, $2, $3, $4, $5, $6) returning id",
            issuer_id,
            c.isin,
            c.figi,
            c.kind,
            c.name,
            c.currency,
        )
    )


async def _listing_id(conn: asyncpg.Connection, security_id: int, c: ListingCandidate) -> int:
    evidence = json.dumps(c.evidence, default=str)
    row = await conn.fetchval(
        "select id from public.security_listing where security_id = $1 and mic = $2"
        " and ticker = $3 and coalesce(valid_from, date '0001-01-01')"
        " = coalesce($4::date, date '0001-01-01')",
        security_id,
        c.mic,
        c.listing_ticker,
        c.valid_from,
    )
    if row is not None:
        await conn.execute(
            "update public.security_listing set currency = $2, status = $3, valid_to = $4,"
            " evidence = $5::jsonb, source = 'derived' where id = $1",
            row,
            c.currency,
            c.status,
            c.valid_to,
            evidence,
        )
        return int(row)
    return int(
        await conn.fetchval(
            "insert into public.security_listing"
            " (security_id, mic, ticker, currency, status, valid_from, valid_to, source, evidence)"
            " values ($1, $2, $3, $4, $5, $6, $7, 'derived', $8::jsonb) returning id",
            security_id,
            c.mic,
            c.listing_ticker,
            c.currency,
            c.status,
            c.valid_from,
            c.valid_to,
            evidence,
        )
    )


async def _provider_symbols(
    conn: asyncpg.Connection, listing_id: int, c: ListingCandidate, status: str
) -> int:
    """Provider symbol -> listing mapping, one row per price provider symbol."""
    written = 0
    for provider in PRICE_PROVIDERS:
        symbol = getattr(c.symbols, provider)
        if not symbol:
            continue
        evidence = json.dumps(
            {"derived_from": "holding profile / instrument.price_symbol", "ticker": c.ticker}
        )
        row = await conn.fetchrow(
            "select id, match_status, listing_id from public.provider_identifier"
            " where provider = $1 and identifier_type = $2 and identifier = $3"
            " and valid_from is null",
            provider,
            SYMBOL_TYPE,
            symbol,
        )
        if row is None:
            await conn.execute(
                "insert into public.provider_identifier"
                " (provider, identifier_type, identifier, listing_id, match_status, score,"
                "  evidence) values ($1, $2, $3, $4, $5, $6, $7::jsonb)",
                provider,
                SYMBOL_TYPE,
                symbol,
                listing_id,
                status,
                c.score,
                evidence,
            )
        elif row["match_status"] != "rejected":
            # A confirmed mapping is never downgraded by a re-derive.
            keep = "confirmed" if row["match_status"] == "confirmed" else status
            await conn.execute(
                "update public.provider_identifier set listing_id = $2, match_status = $3,"
                " score = $4, evidence = $5::jsonb where id = $1",
                row["id"],
                listing_id,
                keep,
                c.score,
                evidence,
            )
        written += 1
    return written


@dataclass(frozen=True)
class DeriveResult:
    ticker: str
    status: str  # confirmed | suggested | unchanged | skipped
    listing_id: int | None
    reason: str | None = None
    score: float | None = None


async def write_listing(
    pool: asyncpg.Pool, c: ListingCandidate, *, decided_by: str | None = None
) -> DeriveResult:
    """Write the security, listing, provider symbol rows and link for one candidate. An existing
    owner decision on the link (confirmed or rejected by an owner) is kept untouched."""
    wanted = "confirmed" if c.confirmable else "suggested"
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "select pg_advisory_xact_lock(hashtext('listing'), hashtext($1))", c.ticker
        )
        link = await conn.fetchrow(
            "select status, decided_by, decided_at, listing_id, security_id from"
            " public.instrument_security_link where ticker = $1 for update",
            c.ticker,
        )
        # A decision by the owner or by the AI approver / autopilot (which leaves decided_by
        # empty but always stamps decided_at) is kept; a derive never writes decided_at.
        if link is not None and (link["decided_by"] is not None or link["decided_at"] is not None):
            return DeriveResult(
                c.ticker, "unchanged", link["listing_id"], "decision is kept", c.score
            )
        if link is not None and link["status"] == "rejected":
            return DeriveResult(c.ticker, "unchanged", link["listing_id"], "rejected link kept")
        security_id = await _security_id(conn, c)
        listing_id = await _listing_id(conn, security_id, c)
        await _provider_symbols(conn, listing_id, c, wanted)
        evidence = json.dumps({**c.evidence, "auto_status": wanted}, default=str)
        await conn.execute(
            "insert into public.instrument_security_link"
            " (ticker, security_id, listing_id, status, score, evidence, decided_at, updated_at)"
            " values ($1, $2, $3, $4, $5, $6::jsonb, null, now())"
            " on conflict (ticker) do update set security_id = excluded.security_id,"
            " listing_id = excluded.listing_id, status = excluded.status,"
            " score = excluded.score, evidence = excluded.evidence, updated_at = now()",
            c.ticker,
            security_id,
            listing_id,
            wanted,
            c.score,
            evidence,
        )
    await record_match_decision(
        pool,
        Match(None, c.score, wanted, tuple(k for k, v in _reasons(c).items() if v)),
        instrument_ticker=c.ticker,
        security_id=security_id,
        listing_id=listing_id,
        decided_by=decided_by,
    )
    return DeriveResult(c.ticker, wanted, listing_id, "; ".join(c.gaps) or None, c.score)


def _reasons(c: ListingCandidate) -> dict[str, bool]:
    return {
        "isin_firds_record": bool(c.evidence.get("isin_has_firds_record_on_home_mic")),
        "openfigi_listing_ticker": c.evidence.get("openfigi_listing_ticker") is not None,
        "currency_agrees": bool((c.evidence.get("currency") or {}).get("agrees")),
        "home_mic": True,
    }


async def derive_listings(
    pool: asyncpg.Pool,
    tickers: list[str],
    profiles: HoldingProfilesConfig,
    *,
    today: dt.date | None = None,
) -> list[DeriveResult]:
    """Derive and write the listing of every ticker (idempotent)."""
    symbols = await holding_symbols(pool, tickers, profiles)
    out: list[DeriveResult] = []
    for ticker in tickers:
        candidate = await derive_candidate(pool, ticker, symbols[ticker], today=today)
        if candidate is None:
            out.append(
                DeriveResult(ticker, "skipped", None, "no home market or listing ticker known")
            )
            continue
        out.append(await write_listing(pool, candidate))
    return out


class ListingDecisionError(Exception):
    def __init__(self, message: str, *, not_found: bool = False) -> None:
        super().__init__(message)
        self.not_found = not_found


async def confirm_listing(
    pool: asyncpg.Pool,
    ticker: str,
    decision: str,
    owner_id: str | None,
    reason: str,
    *,
    listing_id: int | None = None,
) -> dict[str, Any]:
    """The owner's decision on a holding's listing: confirm (optionally naming a different
    existing listing of the same security) or reject. Provider symbol rows follow the decision."""
    if decision not in ("confirmed", "rejected"):
        raise ListingDecisionError("decision must be confirmed or rejected")
    async with pool.acquire() as conn, conn.transaction():
        link = await conn.fetchrow(
            "select security_id, listing_id from public.instrument_security_link"
            " where ticker = $1 for update",
            ticker,
        )
        if link is None:
            raise ListingDecisionError(f"no derived listing for {ticker}", not_found=True)
        target = listing_id or link["listing_id"]
        if target is None:
            raise ListingDecisionError("the link names no listing to decide on")
        belongs = await conn.fetchval(
            "select exists(select 1 from public.security_listing where id = $1"
            " and ($2::bigint is null or security_id = $2))",
            target,
            link["security_id"],
        )
        if not belongs:
            raise ListingDecisionError("that listing does not belong to the holding's security")
        await conn.execute(
            "update public.instrument_security_link set listing_id = $2, status = $3,"
            " score = $4, decided_at = now(), decided_by = $5::uuid, updated_at = now(),"
            " evidence = evidence || jsonb_build_object('owner_reason', $6::text)"
            " where ticker = $1",
            ticker,
            target,
            decision,
            1 if decision == "confirmed" else 0,
            owner_id,
            reason,
        )
        await conn.execute(
            "update public.provider_identifier set match_status = $2"
            " where listing_id = $1 and identifier_type = $3",
            target,
            decision,
            SYMBOL_TYPE,
        )
    await record_match_decision(
        pool,
        Match(None, 1 if decision == "confirmed" else 0, decision, (reason,)),
        instrument_ticker=ticker,
        security_id=link["security_id"],
        listing_id=target,
        decided_by=owner_id,
    )
    return {"ticker": ticker, "listing_id": target, "status": decision}


async def resolve_series_listing(
    pool: asyncpg.Pool | asyncpg.Connection, symbol: str, source: str
) -> tuple[int | None, str | None]:
    """(listing id, MIC) of the confirmed listing a price series belongs to, through the
    provider symbol mapping; for Stooq also through the instrument's `price_symbol`. When no
    confirmed listing is known the MIC is still taken from a Yahoo symbol's suffix, because
    the exchange session of a series should not wait for the owner."""
    row = await pool.fetchrow(
        "select l.id, l.mic from public.provider_identifier p"
        " join public.security_listing l on l.id = p.listing_id"
        " where p.provider = $1 and p.identifier_type = $2 and p.identifier = $3"
        " and p.match_status = 'confirmed' order by p.id desc limit 1",
        source,
        SYMBOL_TYPE,
        symbol,
    )
    if row is not None:
        return int(row["id"]), row["mic"]
    if source == "stooq":
        legacy = await pool.fetchrow(
            "select l.id, l.mic from public.instrument i"
            " join public.instrument_security_link m on m.ticker = i.t212_ticker"
            " and m.status = 'confirmed' and m.listing_id is not null"
            " join public.security_listing l on l.id = m.listing_id"
            " where i.price_symbol = $1 limit 1",
            symbol,
        )
        if legacy is not None:
            return int(legacy["id"]), legacy["mic"]
    return None, series_mic(symbol, source)


async def holding_symbol_names(
    pool: asyncpg.Pool | asyncpg.Connection, ticker: str, profiles: HoldingProfilesConfig
) -> set[str]:
    """Every symbol a holding's listing is known by: the provider symbols that price it, the
    instrument's `price_symbol`, the confirmed listing's exchange ticker and the provider symbol
    rows mapped to it. A corporate action may be recorded under any of them (the listing ticker
    `CEZ` and the Stooq symbol `cez.cz` name the same listing)."""
    symbols = (await holding_symbols(pool, [ticker], profiles))[ticker]
    names = {ref.symbol for ref in symbols.refs()}
    names.add(ticker)
    price_symbol = await pool.fetchval(
        "select price_symbol from public.instrument where t212_ticker = $1", ticker
    )
    if price_symbol:
        names.add(price_symbol)
    rows = await pool.fetch(
        "select l.ticker as listing_ticker, p.identifier from public.instrument_security_link m"
        " join public.security_listing l on l.id = m.listing_id"
        " left join public.provider_identifier p on p.listing_id = l.id"
        " and p.identifier_type = $2 and p.match_status <> 'rejected'"
        " where m.ticker = $1 and m.status = 'confirmed'",
        ticker,
        SYMBOL_TYPE,
    )
    for row in rows:
        names.add(row["listing_ticker"])
        if row["identifier"]:
            names.add(row["identifier"])
    return names


async def listing_overview(pool: asyncpg.Pool, ticker: str) -> dict[str, Any] | None:
    """The derived listing of a holding with its match evidence and provider symbols."""
    link = await pool.fetchrow(
        "select m.status, m.score, m.evidence, m.decided_at, m.decided_by, m.updated_at,"
        " l.id as listing_id, l.mic, l.ticker as listing_ticker, l.currency,"
        " l.status as listing_status, l.valid_from, l.valid_to, s.id as security_id, s.isin"
        " from public.instrument_security_link m"
        " left join public.security_listing l on l.id = m.listing_id"
        " left join public.security s on s.id = m.security_id where m.ticker = $1",
        ticker,
    )
    if link is None:
        return None
    symbols = await pool.fetch(
        "select provider, identifier, match_status from public.provider_identifier"
        " where listing_id = $1 and identifier_type = $2 order by provider, identifier",
        link["listing_id"],
        SYMBOL_TYPE,
    )
    evidence = link["evidence"]
    return {
        "ticker": ticker,
        "status": link["status"],
        "score": float(link["score"]) if link["score"] is not None else None,
        "decided_at": link["decided_at"],
        "listing": None
        if link["listing_id"] is None
        else {
            "id": link["listing_id"],
            "mic": link["mic"],
            "ticker": link["listing_ticker"],
            "currency": link["currency"],
            "status": link["listing_status"],
            "valid_from": link["valid_from"],
            "valid_to": link["valid_to"],
            "security_id": link["security_id"],
            "isin": link["isin"],
        },
        "evidence": json.loads(evidence) if isinstance(evidence, str) else evidence,
        "provider_symbols": [dict(r) for r in symbols],
    }
