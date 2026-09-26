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
# reports its GBX lines). Unknown suffixes give None.
STOOQ_CURRENCY: dict[str, str] = {"us": "USD", "uk": "GBX", "de": "EUR", "jp": "JPY", "pl": "PLN"}


def stooq_currency(symbol: str) -> str | None:
    _, _, suffix = symbol.rpartition(".")
    return STOOQ_CURRENCY.get(suffix.lower())


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


async def stooq_daily(
    http: httpx.AsyncClient, symbol: str, start: dt.date, end: dt.date
) -> list[PriceBar]:
    response = await http.get(
        STOOQ_URL,
        params={
            "s": symbol,
            "i": "d",
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
        },
    )
    if response.status_code != 200:
        raise MarketDataError(f"Stooq returned HTTP {response.status_code}")
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
    return parse_frankfurter(response.json())


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
    return parse_nager(country, response.json())
