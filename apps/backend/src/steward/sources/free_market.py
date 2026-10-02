"""Optional free-plan market and fundamentals adapters.

Each parser is strict about provider error envelopes and leaves key/quota policy to the
shared provider-access gate. A sample or trial symbol must never be promoted as coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from steward.sources.base import AccessState


class FreeSourceError(Exception):
    """A provider failure. `state` is the typed access outcome when the provider's own message
    makes it unambiguous (an Alpha Vantage HTTP-200 body that says "premium endpoint" is
    PAID_ONLY; one that says "25 requests per day" is QUOTA_EXHAUSTED), else None and the
    caller classifies by `status_code`."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        *,
        state: AccessState | None = None,
        provider_charged: bool = True,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.state = state
        # False when the request provably never reached the provider (network failure), so a
        # reserved credit can be refunded.
        self.provider_charged = provider_charged


_QUOTA_WORDS = (
    "rate limit",
    "requests per day",
    "calls per day",
    "calls per minute",
    "call frequency",
    "api call volume",
    "daily limit",
    "per-minute",
    "too many requests",
)
_PAID_WORDS = (
    "premium endpoint",
    "premium feature",
    "premium plan",
    "this is a premium",
    "paid tier",
    "subscribe to any of the premium",
    "upgrade your plan",
)
_KEY_WORDS = ("apikey", "api key", "unauthorized")


def classify_alpha_message(text: str, *, key: str = "Information") -> AccessState:
    """Typed access state of an Alpha Vantage HTTP-200 envelope message.

    Quota wording is checked before premium wording on purpose: the daily-limit text says "...
    subscribe to any of the premium plans ... to remove all daily rate limits", which is a
    quota, not a paid-only endpoint."""
    lowered = text.casefold()
    if any(word in lowered for word in _QUOTA_WORDS):
        return AccessState.QUOTA_EXHAUSTED
    if any(word in lowered for word in _PAID_WORDS):
        return AccessState.PAID_ONLY
    if any(word in lowered for word in _KEY_WORDS):
        return AccessState.ACCESS_UNVERIFIED
    if key == "Error Message":
        return AccessState.NOT_COVERED
    return AccessState.TEMPORARILY_FAILED


def raise_alpha_envelope(payload: dict[str, Any]) -> None:
    """Raise a typed FreeSourceError for Alpha Vantage's error/denial envelopes (HTTP 200)."""
    for key in ("Error Message", "Information", "Note"):
        if payload.get(key):
            text = str(payload[key])
            raise FreeSourceError(text[:240], state=classify_alpha_message(text, key=key))


def _decimal(value: Any, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FreeSourceError(f"invalid {field}") from exc
    if not number.is_finite():
        raise FreeSourceError(f"invalid {field}")
    return number


@dataclass(frozen=True, slots=True)
class DailyBar:
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adjusted_close: Decimal | None
    volume: Decimal | None
    dividend: Decimal | None = None
    split_coefficient: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SentimentArticle:
    url: str
    title: str
    summary: str | None
    published_at: date | None
    ticker: str
    relevance: Decimal
    sentiment: Decimal
    raw: dict[str, Any]


def parse_alpha_news(payload: dict[str, Any], symbol: str) -> list[SentimentArticle]:
    raise_alpha_envelope(payload)
    feed = payload.get("feed")
    if not isinstance(feed, list):
        raise FreeSourceError("Alpha Vantage news schema changed")
    items: list[SentimentArticle] = []
    for article in feed:
        if not isinstance(article, dict) or not article.get("url") or not article.get("title"):
            continue
        scores = article.get("ticker_sentiment")
        if not isinstance(scores, list):
            continue
        match = next(
            (
                item
                for item in scores
                if isinstance(item, dict)
                and str(item.get("ticker", "")).casefold() == symbol.casefold()
            ),
            None,
        )
        if match is None:
            continue
        try:
            relevance = _decimal(match.get("relevance_score"), "ticker relevance")
            sentiment = _decimal(match.get("ticker_sentiment_score"), "ticker sentiment")
            if not 0 <= relevance <= 1 or not -1 <= sentiment <= 1:
                continue
            raw_time = str(article.get("time_published") or "")[:8]
            published = (
                date(int(raw_time[:4]), int(raw_time[4:6]), int(raw_time[6:8]))
                if len(raw_time) == 8
                else None
            )
        except (FreeSourceError, ValueError):
            continue
        items.append(
            SentimentArticle(
                url=str(article["url"]),
                title=str(article["title"]),
                summary=str(article.get("summary") or "") or None,
                published_at=published,
                ticker=symbol,
                relevance=relevance,
                sentiment=sentiment,
                raw=article,
            )
        )
    return items


def parse_alpha_series(payload: dict[str, Any]) -> list[tuple[date, Decimal]]:
    """Normalize Alpha Vantage commodity and FX daily/periodic observations."""
    raise_alpha_envelope(payload)
    raw_rows = payload.get("data")
    if not isinstance(raw_rows, list):
        raw_rows = next((v for k, v in payload.items() if k.startswith("Time Series")), None)
    rows: list[tuple[date, Decimal]] = []
    if isinstance(raw_rows, dict):
        for day, fields in raw_rows.items():
            if not isinstance(fields, dict):
                continue
            value = fields.get("4. close") or fields.get("1. open")
            if value is None:
                continue
            try:
                rows.append((date.fromisoformat(day), _decimal(value, "series value")))
            except (ValueError, FreeSourceError):
                continue
    elif isinstance(raw_rows, list):
        for item in raw_rows:
            if not isinstance(item, dict) or item.get("value") in (None, "."):
                continue
            try:
                rows.append(
                    (date.fromisoformat(str(item["date"])), _decimal(item["value"], "series value"))
                )
            except (KeyError, ValueError, FreeSourceError):
                continue
    else:
        raise FreeSourceError("Alpha Vantage series schema changed")
    return sorted(set(rows))


async def alpha_news_sentiment(
    http: httpx.AsyncClient, api_key: str, symbol: str
) -> dict[str, Any]:
    try:
        response = await http.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "NEWS_SENTIMENT",
                "tickers": symbol,
                "sort": "LATEST",
                "limit": 50,
                "apikey": api_key,
            },
        )
    except httpx.HTTPError as exc:
        raise FreeSourceError(
            f"Alpha Vantage network error ({type(exc).__name__})", provider_charged=False
        ) from exc
    if response.status_code != 200:
        raise FreeSourceError(f"Alpha Vantage HTTP {response.status_code}", response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise FreeSourceError("Alpha Vantage sent invalid JSON") from exc
    parse_alpha_news(payload, symbol)
    result: dict[str, Any] = payload
    return result


def parse_alpha_daily(payload: dict[str, Any]) -> list[DailyBar]:
    """Parse TIME_SERIES_DAILY_ADJUSTED with its provider units preserved."""
    raise_alpha_envelope(payload)
    series = payload.get("Time Series (Daily)")
    if not isinstance(series, dict):
        raise FreeSourceError("Alpha Vantage daily series unavailable")
    bars: list[DailyBar] = []
    try:
        for raw_day, row in series.items():
            bars.append(
                DailyBar(
                    day=date.fromisoformat(raw_day),
                    open=_decimal(row["1. open"], "open"),
                    high=_decimal(row["2. high"], "high"),
                    low=_decimal(row["3. low"], "low"),
                    close=_decimal(row["4. close"], "close"),
                    adjusted_close=_decimal(row["5. adjusted close"], "adjusted close"),
                    volume=_decimal(row["6. volume"], "volume"),
                    dividend=_decimal(row["7. dividend amount"], "dividend"),
                    split_coefficient=_decimal(row["8. split coefficient"], "split coefficient"),
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise FreeSourceError("Alpha Vantage daily schema changed") from exc
    return sorted(bars, key=lambda b: b.day)


async def alpha_daily(
    http: httpx.AsyncClient, api_key: str, symbol: str
) -> tuple[dict[str, Any], list[DailyBar]]:
    try:
        response = await http.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": symbol,
                "outputsize": "compact",
                "apikey": api_key,
            },
        )
    except httpx.HTTPError as exc:
        raise FreeSourceError(
            f"Alpha Vantage network error ({type(exc).__name__})", provider_charged=False
        ) from exc
    if response.status_code != 200:
        raise FreeSourceError(f"Alpha Vantage HTTP {response.status_code}", response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise FreeSourceError("Alpha Vantage sent invalid JSON") from exc
    return payload, parse_alpha_daily(payload)


async def eodhd_exchange_symbols(
    http: httpx.AsyncClient, api_key: str, exchange: str
) -> list[dict[str, Any]]:
    try:
        response = await http.get(
            f"https://eodhd.com/api/exchange-symbol-list/{exchange}",
            params={"api_token": api_key, "fmt": "json"},
        )
    except httpx.HTTPError as exc:
        raise FreeSourceError(
            f"EODHD network error ({type(exc).__name__})", provider_charged=False
        ) from exc
    if response.status_code != 200:
        raise FreeSourceError(
            f"EODHD HTTP {response.status_code}: {response.text[:160]}", response.status_code
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise FreeSourceError("EODHD sent invalid JSON") from exc
    if not isinstance(payload, list):
        raise FreeSourceError("EODHD exchange symbol schema changed")
    return [item for item in payload if isinstance(item, dict)]


async def eodhd_daily(http: httpx.AsyncClient, api_key: str, symbol: str) -> list[DailyBar]:
    try:
        response = await http.get(
            f"https://eodhd.com/api/eod/{symbol}",
            params={"api_token": api_key, "fmt": "json", "period": "d"},
        )
    except httpx.HTTPError as exc:
        raise FreeSourceError(
            f"EODHD network error ({type(exc).__name__})", provider_charged=False
        ) from exc
    if response.status_code != 200:
        raise FreeSourceError(
            f"EODHD HTTP {response.status_code}: {response.text[:160]}", response.status_code
        )
    try:
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError
        return [
            DailyBar(
                date.fromisoformat(str(r["date"])),
                _decimal(r["open"], "open"),
                _decimal(r["high"], "high"),
                _decimal(r["low"], "low"),
                _decimal(r["close"], "close"),
                # EODHD's adjusted_close is adjusted for splits and dividends. Whether its
                # plain `close` is split-adjusted is unverified without a live payload, so
                # total return checks the raw series against confirmed splits before use.
                _decimal(r["adjusted_close"], "adjusted close")
                if r.get("adjusted_close") is not None
                else None,
                _decimal(r["volume"], "volume") if r.get("volume") is not None else None,
            )
            for r in payload
            if isinstance(r, dict)
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise FreeSourceError("EODHD daily schema changed or access denied") from exc


async def alpha_statement(
    http: httpx.AsyncClient, api_key: str, symbol: str, function: str
) -> dict[str, Any]:
    if function not in {"INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW"}:
        raise ValueError("unsupported Alpha Vantage statement")
    try:
        response = await http.get(
            "https://www.alphavantage.co/query",
            params={"function": function, "symbol": symbol, "apikey": api_key},
        )
    except httpx.HTTPError as exc:
        raise FreeSourceError(
            f"Alpha Vantage network error ({type(exc).__name__})", provider_charged=False
        ) from exc
    if response.status_code != 200:
        raise FreeSourceError(f"Alpha Vantage HTTP {response.status_code}", response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise FreeSourceError("Alpha Vantage sent invalid JSON") from exc
    raise_alpha_envelope(payload)
    if not isinstance(payload.get("annualReports"), list):
        raise FreeSourceError("Alpha Vantage statement unavailable or schema changed")
    result: dict[str, Any] = payload
    return result


async def finnhub_peers(http: httpx.AsyncClient, api_key: str, symbol: str) -> list[str]:
    try:
        response = await http.get(
            "https://finnhub.io/api/v1/stock/peers",
            params={"symbol": symbol, "token": api_key},
        )
    except httpx.HTTPError as exc:
        raise FreeSourceError(
            f"Finnhub network error ({type(exc).__name__})", provider_charged=False
        ) from exc
    if response.status_code != 200:
        raise FreeSourceError(f"Finnhub peers HTTP {response.status_code}", response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise FreeSourceError("Finnhub peers response was not JSON") from exc
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise FreeSourceError("Finnhub peers schema changed")
    return [item for item in dict.fromkeys(payload) if item.casefold() != symbol.casefold()]


async def finnhub_earnings_surprises(
    http: httpx.AsyncClient, api_key: str, symbol: str
) -> list[dict[str, Any]]:
    try:
        response = await http.get(
            "https://finnhub.io/api/v1/stock/earnings",
            params={"symbol": symbol, "limit": 4, "token": api_key},
        )
    except httpx.HTTPError as exc:
        raise FreeSourceError(
            f"Finnhub network error ({type(exc).__name__})", provider_charged=False
        ) from exc
    if response.status_code != 200:
        raise FreeSourceError(f"Finnhub earnings HTTP {response.status_code}", response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise FreeSourceError("Finnhub earnings response was not JSON") from exc
    if not isinstance(payload, list):
        raise FreeSourceError("Finnhub earnings schema changed")
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if not item.get("period") or item.get("actual") is None or item.get("estimate") is None:
            continue
        try:
            period = date.fromisoformat(str(item["period"]))
            actual = _decimal(item["actual"], "actual EPS")
            estimate = _decimal(item["estimate"], "estimated EPS")
            surprise = (
                _decimal(item["surprise"], "EPS surprise")
                if item.get("surprise") is not None
                else actual - estimate
            )
        except (ValueError, FreeSourceError):
            continue
        rows.append(
            {
                "period": period,
                "actual": actual,
                "estimate": estimate,
                "surprise": surprise,
                "surprise_percent": item.get("surprisePercent"),
                "raw": item,
            }
        )
    return rows


def parse_alpha_statement_facts(payload: dict[str, Any], *, statement: str) -> list[dict[str, Any]]:
    """Flatten Alpha Vantage annual statements into reported field observations.

    Provider totals remain distinct from SEC/ESEF facts through their source, accession,
    currency unit, and provider-reported basis. Non-numeric metadata is not published as a
    metric; the original report payload remains attached for reconciliation.
    """
    if statement not in {"income", "balance_sheet", "cash_flow"}:
        raise ValueError("unsupported statement kind")
    reports = payload.get("annualReports")
    if not isinstance(reports, list):
        raise FreeSourceError("Alpha Vantage annual reports unavailable")
    out: list[dict[str, Any]] = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        raw_end = report.get("fiscalDateEnding")
        try:
            period_end = date.fromisoformat(str(raw_end))
        except (TypeError, ValueError):
            continue
        raw_currency = str(report.get("reportedCurrency") or "").upper()
        currency = raw_currency if len(raw_currency) == 3 and raw_currency.isalpha() else None
        for concept, raw_value in report.items():
            if concept in {"fiscalDateEnding", "reportedDate", "reportedCurrency"}:
                continue
            try:
                value = _decimal(raw_value, concept)
            except FreeSourceError:
                continue
            field_name = concept.casefold()
            unit = (
                f"{currency}/shares"
                if currency and ("pershare" in field_name or "eps" in field_name)
                else "shares"
                if "shares" in field_name
                else "provider_percent"
                if "margin" in field_name or "percent" in field_name
                else currency or "pure"
            )
            out.append(
                {
                    "concept": f"alpha-vantage:{concept}",
                    "label": concept,
                    "statement": statement,
                    "period_end": period_end,
                    "currency": str(currency).upper() if currency else None,
                    "unit": unit,
                    "value": value,
                    "published_at": report.get("reportedDate"),
                    "raw": report,
                }
            )
    return out
