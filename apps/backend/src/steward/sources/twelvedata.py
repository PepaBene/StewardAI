"""Twelve Data Basic (free) `time_series`, used only to probe which exchanges a key can read.

Twelve Data answers HTTP 200 with a JSON `code`/`status` envelope for most failures, so the
outcome is read from the body: `status: ok` with values is coverage; `code 400` "symbol not
found" is missing coverage; `code 401` a key problem; `code 403` or wording about a higher
plan/grow/pro plan is a technical (plan) denial; `code 429` the minute/day credit limit.
Global trial symbols (AAPL, MSFT, EUR/USD) work on any key and say nothing about a portfolio
symbol, so a probe must use an actual held symbol (see `reference.exchange_probes`).
"""

from dataclasses import dataclass
from typing import Any

import httpx

from steward.sources.base import AccessState

TIME_SERIES_URL = "https://api.twelvedata.com/time_series"


class TwelveDataError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class TimeSeriesProbe:
    state: AccessState
    detail: str
    currency: str | None = None
    exchange: str | None = None
    values: int = 0


_PLAN_WORDS = ("grow plan", "pro plan", "higher plan", "upgrade", "available exclusively")


def classify_time_series(body: Any) -> TimeSeriesProbe:
    """Typed outcome of one `time_series` response body."""
    if not isinstance(body, dict):
        return TimeSeriesProbe(AccessState.TEMPORARILY_FAILED, "unexpected response schema")
    code = body.get("code")
    message = str(body.get("message") or "")[:200]
    lowered = message.casefold()
    if body.get("status") == "error" or code is not None:
        if code == 429 or "run out of api credits" in lowered or "api credits" in lowered:
            return TimeSeriesProbe(AccessState.QUOTA_EXHAUSTED, message or "credit limit")
        # The docs (twelvedata.com/docs, "Error Codes", read 2026-10-03) define 403 as "API key
        # lacks permissions for the requested resource (upgrade required)": a plan denial whose
        # text contains "API key", so the code is read before any wording.
        if code == 403 or (code in (400, 404) and any(w in lowered for w in _PLAN_WORDS)):
            return TimeSeriesProbe(AccessState.PAID_ONLY, message or "plan excludes this symbol")
        if code == 401 or "apikey" in lowered or "api key" in lowered:
            return TimeSeriesProbe(AccessState.ACCESS_UNVERIFIED, message or "key rejected")
        if code in (400, 404):
            return TimeSeriesProbe(AccessState.NOT_COVERED, message or "symbol not found")
        return TimeSeriesProbe(AccessState.TEMPORARILY_FAILED, message or f"error code {code}")
    values = body.get("values")
    raw_meta = body.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    if isinstance(values, list) and values:
        return TimeSeriesProbe(
            AccessState.AVAILABLE,
            "time series returned for the portfolio symbol",
            currency=meta.get("currency"),
            exchange=meta.get("exchange"),
            values=len(values),
        )
    return TimeSeriesProbe(AccessState.NOT_COVERED, "empty time series")


async def probe_time_series(
    http: httpx.AsyncClient, api_key: str, symbol: str, exchange: str | None = None
) -> TimeSeriesProbe:
    params = {"symbol": symbol, "interval": "1day", "outputsize": "5", "apikey": api_key}
    if exchange:
        params["exchange"] = exchange
    try:
        response = await http.get(TIME_SERIES_URL, params=params)
    except httpx.HTTPError as exc:
        # The key is a query parameter: report the error class only.
        raise TwelveDataError(f"Twelve Data request failed ({type(exc).__name__})") from exc
    if response.status_code == 429:
        return TimeSeriesProbe(AccessState.QUOTA_EXHAUSTED, "HTTP 429")
    if response.status_code in (401, 403):
        return TimeSeriesProbe(
            AccessState.ACCESS_UNVERIFIED if response.status_code == 401 else AccessState.PAID_ONLY,
            f"HTTP {response.status_code}",
        )
    if response.status_code >= 500:
        return TimeSeriesProbe(AccessState.TEMPORARILY_FAILED, f"HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        return TimeSeriesProbe(AccessState.TEMPORARILY_FAILED, "response was not JSON")
    return classify_time_series(body)
