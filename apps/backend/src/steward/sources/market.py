"""Prices, FX history and public holidays from free sources.

- Stooq (`stooq.com/q/d/l/?s=<symbol>&i=d`): daily OHLCV as CSV for equities (`aapl.us`,
  `vod.uk`, `sap.de`), indices (`^spx`, `^dax`), FX and more. Unofficial and personal use;
  tier 2. A symbol it does not know answers "No data", which is a visible gap, not an error.
- Frankfurter (`api.frankfurter.dev/v1`): the ECB reference rates, including a whole date
  range in one request. It republishes ECB data, so it adds redundancy, not corroboration.
- Nager.Date (`date.nager.at/api/v3`): public holidays per country and year.
"""

import csv
import datetime as dt
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

import httpx

from steward.fx.convert import FxRate

STOOQ_URL = "https://stooq.com/q/d/l/"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1"
NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"


# Quote currency of a Stooq symbol by market suffix (London quotes in pence, as Trading212
# reports its GBX lines). Unknown suffixes give None. Amsterdam (nl), Copenhagen (dk) and
# Prague (cz) follow Stooq's country-suffix scheme; see openfigi.STOOQ_SUFFIX.
STOOQ_CURRENCY: dict[str, str] = {
    "us": "USD", "uk": "GBX", "de": "EUR", "jp": "JPY", "pl": "PLN", "nl": "EUR", "dk": "DKK",
    "cz": "CZK",
}  # fmt: skip


def stooq_currency(symbol: str) -> str | None:
    _, dot, suffix = symbol.rpartition(".")
    return STOOQ_CURRENCY.get(suffix.lower()) if dot else None


# Minor-unit quote codes a provider or Trading212 uses -> (ISO currency, major units per minor
# unit, canonical unit code). Pence are quoted as GBX by Stooq/Trading212 and GBp by Yahoo.
MINOR_QUOTE_UNITS: dict[str, tuple[str, Decimal, str]] = {
    "GBX": ("GBP", Decimal("0.01"), "GBX"),
    "GBp": ("GBP", Decimal("0.01"), "GBX"),
    "ZAc": ("ZAR", Decimal("0.01"), "ZAC"),
    "ZAC": ("ZAR", Decimal("0.01"), "ZAC"),
    "ILa": ("ILS", Decimal("0.01"), "ILA"),
    "ILA": ("ILS", Decimal("0.01"), "ILA"),
}
# Exchanges whose providers conventionally quote in a minor unit (London pence, Johannesburg
# cents, Tel Aviv agorot). Used only when a source reports no unit of its own.
MINOR_UNIT_BY_MIC: dict[str, str] = {"XLON": "GBX", "XJSE": "ZAC", "XTAE": "ILA"}


def quote_unit_of(currency: str | None) -> str | None:
    """The canonical quote unit of a quote currency code: `GBp`/`GBX` -> `GBX` (pence),
    `GBP` -> `GBP` (pounds), `eur` -> `EUR`. None when no currency is known."""
    if not currency:
        return None
    code = currency.strip()
    if code in MINOR_QUOTE_UNITS:
        return MINOR_QUOTE_UNITS[code][2]
    return code.upper() or None


def major_currency(currency: str | None) -> str | None:
    """The ISO currency behind a quote code (pence -> GBP)."""
    if not currency:
        return None
    code = currency.strip()
    return MINOR_QUOTE_UNITS[code][0] if code in MINOR_QUOTE_UNITS else code.upper()


def unit_factor(currency: str | None) -> Decimal:
    """Major units per quoted unit (0.01 for pence, 1 otherwise)."""
    code = (currency or "").strip()
    return MINOR_QUOTE_UNITS[code][1] if code in MINOR_QUOTE_UNITS else Decimal(1)


def infer_quote_currency(
    mic: str | None, listing_currency: str | None, instrument_currency: str | None
) -> tuple[str | None, str]:
    """(quote currency code, basis) for a provider that reports no unit of its own (EODHD,
    Alpha Vantage), so a London line is not stored as pounds when the provider quotes pence.

    1. The instrument's own code when it is the minor unit of the listing currency
       (Trading212 reports London lines as GBX): `instrument`.
    2. The exchange's conventional minor unit (XLON -> GBX) when the listing currency is that
       unit's major currency: `exchange_convention` (unverified against a live payload).
    3. The listing currency as is: `listing_currency`."""
    major = major_currency(listing_currency)
    if major is None:
        return None, "unknown"
    if (
        instrument_currency in MINOR_QUOTE_UNITS
        and MINOR_QUOTE_UNITS[instrument_currency][0] == major
    ):
        return instrument_currency, "instrument"
    minor = MINOR_UNIT_BY_MIC.get(mic or "")
    if minor is not None and MINOR_QUOTE_UNITS[minor][0] == major:
        return minor, "exchange_convention"
    return major, "listing_currency"


class MarketDataError(Exception):
    pass


@dataclass(frozen=True)
class PriceBar:
    symbol: str
    date: dt.date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: Decimal | None
    # When the source stamped the bar (Yahoo: the bar's timestamp); None when it gives none.
    source_timestamp: dt.datetime | None = None


def _dec(value: str | None) -> Decimal | None:
    if value is None or value.strip() == "":
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


def parse_stooq(symbol: str, text: str) -> list[PriceBar]:
    """Stooq's CSV (Date,Open,High,Low,Close[,Volume]). "No data" gives []."""
    stripped = text.strip()
    if not stripped or stripped.lower().startswith("no data") or "Date" not in stripped[:40]:
        return []
    bars = []
    for row in csv.DictReader(io.StringIO(stripped)):
        close = _dec(row.get("Close"))
        try:
            date = dt.date.fromisoformat(row.get("Date", ""))
        except ValueError:
            continue
        if close is None or close <= 0:
            continue
        bars.append(
            PriceBar(
                symbol=symbol,
                date=date,
                open=_dec(row.get("Open")),
                high=_dec(row.get("High")),
                low=_dec(row.get("Low")),
                close=close,
                volume=_dec(row.get("Volume")),
            )
        )
    bars.sort(key=lambda b: b.date)
    return bars


# Answers Stooq gives in place of the CSV that are refusals, not "this symbol has no data":
# the download needs an `apikey` (since 2026) or the daily hit quota is used up.
STOOQ_REFUSALS = ("get your apikey", "exceeded the daily hits limit")


async def stooq_daily(
    http: httpx.AsyncClient,
    symbol: str,
    start: dt.date,
    end: dt.date,
    api_key: str | None = None,
) -> list[PriceBar]:
    """Daily bars in [start, end] (`d1`/`d2`); Stooq returns the whole range in one CSV."""
    params = {"s": symbol, "i": "d", "d1": start.strftime("%Y%m%d"), "d2": end.strftime("%Y%m%d")}
    if api_key:
        params["apikey"] = api_key
    response = await http.get(STOOQ_URL, params=params)
    if response.status_code != 200:
        raise MarketDataError(f"Stooq returned HTTP {response.status_code}")
    head = response.text.lstrip()[:200].lower()
    if head.startswith("<") or "html" in response.headers.get("content-type", "").lower():
        # Observed live (2026-10-03): HTTP 200 and an HTML page whose script computes a
        # proof of work in the browser (`/__verify`), otherwise a connection reset. It is a
        # block for non-browser clients, not an empty history, and is not worked around here.
        raise MarketDataError("Stooq served an HTML bot-check page, not CSV (blocked)")
    if not head.startswith("date") and any(r in head for r in STOOQ_REFUSALS):
        # Not an empty history: storing nothing here would look like "no data" forever.
        raise MarketDataError(f"Stooq refused the download: {response.text.strip()[:120]}")
    return parse_stooq(symbol, response.text)


def parse_frankfurter(body: dict[str, Any]) -> list[FxRate]:
    """A Frankfurter time series (base EUR) as ECB-source rates: EUR per one unit, with the
    published figure (units per 1 EUR) kept alongside, exactly like the Data Portal."""
    if body.get("base") != "EUR":
        raise MarketDataError("Frankfurter series must have base EUR")
    series = body.get("rates", {})
    if "date" in body and not isinstance(next(iter(series.values()), None), dict):
        series = {body["date"]: series}
    rates = []
    for day, values in series.items():
        date = dt.date.fromisoformat(day)
        for currency, value in values.items():
            published = Decimal(str(value))
            if published <= 0:
                continue
            with localcontext() as ctx:
                ctx.prec = 28
                rate = Decimal(1) / published
            rates.append(
                FxRate(
                    source="ecb",
                    rate_date=date,
                    currency=currency.upper(),
                    rate=rate,
                    published_amount=Decimal(1),
                    published_rate=published,
                )
            )
    return rates


async def frankfurter_series(http: httpx.AsyncClient, start: dt.date, end: dt.date) -> list[FxRate]:
    response = await http.get(
        f"{FRANKFURTER_URL}/{start.isoformat()}..{end.isoformat()}", params={"base": "EUR"}
    )
    if response.status_code != 200:
        raise MarketDataError(f"Frankfurter returned HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise MarketDataError("Frankfurter sent no JSON") from exc
    if not isinstance(body, dict):
        raise MarketDataError("Frankfurter schema changed")
    return parse_frankfurter(body)


@dataclass(frozen=True)
class PublicHoliday:
    country: str
    date: dt.date
    name: str
    local_name: str | None
    national: bool
    regions: list[str]
    types: list[str]


def parse_nager(country: str, body: list[dict[str, Any]]) -> list[PublicHoliday]:
    if not isinstance(body, list):
        raise MarketDataError("Nager.Date schema changed: expected a list")
    if any(not isinstance(h, dict) or "date" not in h for h in body):
        raise MarketDataError("Nager.Date schema changed: holiday without a date")
    return [
        PublicHoliday(
            country=country,
            date=dt.date.fromisoformat(h["date"]),
            name=h.get("name", ""),
            local_name=h.get("localName"),
            national=bool(h.get("global", True)),
            regions=list(h.get("counties") or []),
            types=list(h.get("types") or []),
        )
        for h in body
    ]


async def public_holidays(http: httpx.AsyncClient, country: str, year: int) -> list[PublicHoliday]:
    response = await http.get(NAGER_URL.format(year=year, country=country))
    if response.status_code == 404:
        return []
    if response.status_code != 200:
        raise MarketDataError(f"Nager.Date returned HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise MarketDataError("Nager.Date sent no JSON") from exc
    return parse_nager(country, body)
