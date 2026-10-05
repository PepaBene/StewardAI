"""Twelve Data Basic (free) `time_series`, used only to probe which exchanges a key can read.

Twelve Data answers HTTP 200 with a JSON `code`/`status` envelope for most failures, so the
outcome is read from the body: `status: ok` with values is coverage; `code 400` "symbol not
found" is missing coverage; `code 401` a key problem; `code 403` or wording about a higher
plan/grow/pro plan is a technical (plan) denial; `code 429` the minute/day credit limit.
Global trial symbols (AAPL, MSFT, EUR/USD) work on any key and say nothing about a portfolio
symbol, so a probe must use an actual held symbol (see `reference.exchange_probes`).
"""

import asyncio
import datetime as dt
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg
import httpx

from steward.sources.base import AccessResult, AccessState
from steward.sources.governor import Budget

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
        if code == 401 or "apikey" in lowered or "api key" in lowered:
            return TimeSeriesProbe(AccessState.ACCESS_UNVERIFIED, message or "key rejected")
        if code == 403 or (code in (400, 404) and any(w in lowered for w in _PLAN_WORDS)):
            return TimeSeriesProbe(AccessState.PAID_ONLY, message or "plan excludes this symbol")
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


# ----------------------------------------------------------------- dividends and splits (D3.5)
#
# Basic plan: 8 credits a minute, 800 a day. Each request costs one credit; the collector's own
# hard stop is 4 a minute and 40 a day per endpoint (80 of the 800, a tenth), below what the
# exchange probes may use, and the pacing keeps a run under the per-minute cap. The first call of
# a fresh key is the probe: a 403 or plan wording latches the endpoint `paid_only`.
# Field names (`dividends[].ex_date/amount`, `splits[].date/from_factor/to_factor`, `meta.currency`)
# follow Twelve Data's public documentation and have NOT been checked against a live response.

DIVIDENDS_URL = "https://api.twelvedata.com/dividends"
SPLITS_URL = "https://api.twelvedata.com/splits"
ACTIONS_BUDGET = Budget(per_minute=Decimal(4), daily=Decimal(40))
ACTIONS_PACE_SECONDS = 15.5  # four requests a minute
ACTION_SOURCE = "twelve_data"


@dataclass(frozen=True, slots=True)
class ProposedAction:
    action_type: str  # "cash_dividend" | "split"
    ex_date: dt.date
    cash_amount: Decimal | None
    ratio: Decimal | None
    raw: dict[str, Any]


def classify_envelope(response: httpx.Response) -> AccessResult | None:
    """A 200 body that is really an error (Twelve Data's usual failure shape). 403 or plan
    wording is `paid_only`, a credit limit is `quota_exhausted`, a key problem is
    `access_unverified`, an unknown symbol is `not_covered` (per symbol, not per endpoint)."""
    try:
        body = response.json()
    except ValueError:
        return AccessResult(AccessState.TEMPORARILY_FAILED, "response was not JSON")
    if not isinstance(body, dict) or not (body.get("status") == "error" or "code" in body):
        return None
    probe = classify_time_series(body)
    return AccessResult(probe.state, probe.detail)


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def parse_actions(dividends: object, splits: object, *, since: dt.date) -> list[ProposedAction]:
    """Dividend and split proposals on or after `since`. A split's ratio is shares after per
    share before (`to_factor / from_factor`: 4-for-1 is 4); one without both factors is dropped
    rather than guessed from the provider's own `ratio` field. A dividend without a positive
    amount is dropped."""
    out: list[ProposedAction] = []
    if not isinstance(dividends, dict) or not isinstance(dividends.get("dividends", []), list):
        raise TwelveDataError("Twelve Data dividends schema changed")
    if not isinstance(splits, dict) or not isinstance(splits.get("splits", []), list):
        raise TwelveDataError("Twelve Data splits schema changed")
    for row in dividends.get("dividends", []):
        if not isinstance(row, dict):
            continue
        try:
            ex_date = dt.date.fromisoformat(str(row.get("ex_date")))
        except ValueError:
            continue
        amount = _decimal(row.get("amount"))
        if ex_date >= since and amount is not None and amount > 0:
            out.append(ProposedAction("cash_dividend", ex_date, amount, None, dict(row)))
    for row in splits.get("splits", []):
        if not isinstance(row, dict):
            continue
        try:
            ex_date = dt.date.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        before, after = _decimal(row.get("from_factor")), _decimal(row.get("to_factor"))
        if ex_date >= since and before and after and before > 0 and after > 0:
            out.append(ProposedAction("split", ex_date, None, after / before, dict(row)))
    return out


def meta_currency(body: object) -> str | None:
    meta = body.get("meta") if isinstance(body, dict) else None
    currency = meta.get("currency") if isinstance(meta, dict) else None
    return currency if isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency) else None


async def store_suggested_actions(
    pool: asyncpg.Pool,
    *,
    listing_id: int,
    symbol: str,
    actions: list[ProposedAction],
    currency: str | None,
) -> int:
    """Insert each proposal as `suggested`. An existing row (same listing, type, ex-date and
    source) is never touched, so an owner decision is never changed; a proposal becomes
    `confirmed` only through the decision route."""
    stored = 0
    for action in actions:
        url = f"{SPLITS_URL if action.action_type == 'split' else DIVIDENDS_URL}?symbol={symbol}"
        status = await pool.fetchval(
            """insert into public.corporate_action
               (listing_id,symbol,action_type,ex_date,ratio,cash_amount,currency,source,source_url,
                status,raw)
               values($1,$2,$3,$4,$5,$6,$7,$8,$9,'suggested',$10::jsonb)
               on conflict(listing_id,action_type,ex_date,source,source_url) do nothing
               returning status""",
            listing_id,
            symbol,
            action.action_type,
            action.ex_date,
            action.ratio,
            action.cash_amount,
            currency if action.action_type == "cash_dividend" else None,
            ACTION_SOURCE,
            url,
            json.dumps(
                {
                    "per": "listed_line",
                    "amount_basis_unverified": True,
                    "provider_row": action.raw,
                    "provider_currency": currency,
                },
                default=str,
            ),
        )
        stored += 1 if status else 0
    return stored


async def collect_corporate_actions(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    listing_id: int,
    symbol: str,
    api_key: str,
    since: dt.date,
    pace_seconds: float = ACTIONS_PACE_SECONDS,
) -> dict[str, int]:
    """Dividends then splits of one listing symbol (two credits) into `corporate_action` as
    `suggested`. Quota and paid-plan denials propagate (QuotaExceeded / HardBlocked)."""
    from steward.sources.governed_http import governed_get

    headers = {"Authorization": f"apikey {api_key}"}
    bodies: list[Any] = []
    for index, (endpoint, url, params) in enumerate(
        (
            ("corporate_actions", DIVIDENDS_URL, {"symbol": symbol, "range": "1y"}),
            ("splits", SPLITS_URL, {"symbol": symbol, "range": "1y"}),
        )
    ):
        if index and pace_seconds:
            await asyncio.sleep(pace_seconds)
        response = await governed_get(
            pool,
            http,
            provider="twelve_data",
            endpoint=endpoint,
            url=url,
            params=params,
            headers=headers,
            budget=ACTIONS_BUDGET,
            body_denial=classify_envelope,
        )
        bodies.append(response.json() if response is not None else {})
    dividends = {"dividends": []} if not bodies[0] else bodies[0]
    splits = {"splits": []} if not bodies[1] else bodies[1]
    actions = parse_actions(dividends, splits, since=since)
    stored = await store_suggested_actions(
        pool,
        listing_id=listing_id,
        symbol=symbol,
        actions=actions,
        currency=meta_currency(dividends),
    )
    return {"proposals": len(actions), "new": stored}
