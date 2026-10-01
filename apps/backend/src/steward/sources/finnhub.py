"""Finnhub free tier: the earnings calendar (US issuers, some others) and basic financials
(`/stock/metric`: P/E, P/B, EPS TTM, dividend yield), used only as a tier-2 cross-check of
the valuation computed from filings. The free plan covers US listings (incl. ADRs)."""

import datetime as dt
from dataclasses import dataclass
from typing import Any

import httpx

CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings"
METRIC_URL = "https://finnhub.io/api/v1/stock/metric"


class FinnhubError(Exception):
    pass


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
        raise FinnhubError(f"Finnhub returned HTTP {response.status_code}")
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
        raise FinnhubError(f"Finnhub returned HTTP {response.status_code}")
    try:
        data: dict[str, Any] = response.json()
    except ValueError as exc:
        raise FinnhubError("Finnhub sent no JSON") from exc
    return data
