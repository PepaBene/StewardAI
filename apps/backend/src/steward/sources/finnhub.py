"""Finnhub free tier: the earnings calendar (US issuers, some others)."""

import datetime as dt
from dataclasses import dataclass

import httpx

CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings"


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
    rows = response.json().get("earningsCalendar") or []
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
