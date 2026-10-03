"""Finnhub free tier: the earnings calendar (US issuers, some others) and basic financials
(`/stock/metric`: P/E, P/B, EPS TTM, dividend yield), used only as a tier-2 cross-check of
the valuation computed from filings. Observed live with a free key (2026-10-03): `/quote`,
`/stock/metric` and `/calendar/earnings` answer for US listings and for Prague (`CEZ.PR`), and
HTTP 403 `{"error":"You don't have access to this resource."}` for Frankfurt, London,
Amsterdam, Swiss, Paris, Tokyo, Copenhagen, Milan and Toronto symbols; an unknown symbol on a
covered market is HTTP 200 with an all-zero quote (`"c":0,...,"d":null,"t":0`); an invalid key is
HTTP 401 `{"error":"Invalid API key."}`; successful answers carry `x-ratelimit-limit: 60`."""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings"
METRIC_URL = "https://finnhub.io/api/v1/stock/metric"


QUOTE_URL = "https://finnhub.io/api/v1/quote"


class FinnhubError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class EarningsDate:
    symbol: str
    date: dt.date
    source: str


async def next_earnings_date(
    http: httpx.AsyncClient, api_key: str, symbol: str, start: dt.date, end: dt.date
) -> EarningsDate | None:
    # The key goes in a header, never the URL, so it cannot reach an access log.
    response = await http.get(
        CALENDAR_URL,
        params={"from": start.isoformat(), "to": end.isoformat(), "symbol": symbol},
        headers={"X-Finnhub-Token": api_key},
    )
    if response.status_code != 200:
        raise FinnhubError(f"Finnhub returned HTTP {response.status_code}", response.status_code)
    try:
        body = response.json()
    except ValueError as exc:
        raise FinnhubError("Finnhub sent no JSON") from exc
    if not isinstance(body, dict):
        raise FinnhubError("Finnhub calendar schema changed")
    rows = body.get("earningsCalendar") or []
    dates = sorted(
        dt.date.fromisoformat(r["date"])
        for r in rows
        if r.get("date") and str(r.get("symbol", "")).upper() == symbol.upper()
    )
    upcoming = [d for d in dates if d >= start]
    if not upcoming:
        return None
    source = f"{CALENDAR_URL}?symbol={symbol}&from={start.isoformat()}&to={end.isoformat()}"
    return EarningsDate(symbol, upcoming[0], source)


async def basic_financials(http: httpx.AsyncClient, api_key: str, symbol: str) -> dict[str, Any]:
    """`/stock/metric?metric=all` for `symbol`: the response as Finnhub sends it. An
    unknown symbol (or one the plan does not cover) comes back with an empty `metric`."""
    response = await http.get(
        METRIC_URL,
        params={"symbol": symbol, "metric": "all"},
        headers={"X-Finnhub-Token": api_key},
    )
    if response.status_code != 200:
        raise FinnhubError(f"Finnhub returned HTTP {response.status_code}", response.status_code)
    try:
        data: dict[str, Any] = response.json()
    except ValueError as exc:
        raise FinnhubError("Finnhub sent no JSON") from exc
    return data


@dataclass(frozen=True)
class FinnhubQuote:
    symbol: str
    price: Decimal
    previous_close: Decimal | None
    traded_at: dt.datetime | None


async def quote(http: httpx.AsyncClient, api_key: str, symbol: str) -> FinnhubQuote | None:
    """`/quote` for `symbol`. None when Finnhub answers with its all-zero placeholder, which is
    how it says "no such symbol on a market this key can see" (missing coverage, not a denial).
    A technical denial is an HTTP error: 401 (key), 403 (the plan excludes the exchange), 429."""
    response = await http.get(
        QUOTE_URL, params={"symbol": symbol}, headers={"X-Finnhub-Token": api_key}
    )
    if response.status_code != 200:
        raise FinnhubError(f"Finnhub returned HTTP {response.status_code}", response.status_code)
    try:
        body = response.json()
    except ValueError as exc:
        raise FinnhubError("Finnhub sent no JSON") from exc
    if not isinstance(body, dict):
        raise FinnhubError("Finnhub quote schema changed")
    try:
        price = Decimal(str(body.get("c") or 0))
        previous = Decimal(str(body.get("pc") or 0))
    except InvalidOperation as exc:
        raise FinnhubError("Finnhub quote schema changed") from exc
    stamp = body.get("t")
    if price <= 0:
        return None
    return FinnhubQuote(
        symbol=symbol,
        price=price,
        previous_close=previous if previous > 0 else None,
        traded_at=(
            dt.datetime.fromtimestamp(int(stamp), dt.UTC)
            if isinstance(stamp, int | float) and stamp > 0
            else None
        ),
    )
