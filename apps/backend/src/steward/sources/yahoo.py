"""Yahoo Finance's chart API (`query1.finance.yahoo.com/v8/finance/chart/<symbol>`).

Unofficial and undocumented: Yahoo closed its public finance API in 2017 and this endpoint
can rate-limit, change or disappear without notice. No key. Tier 3; used for

- intraday 5-minute bars (`range=1d&interval=5m`): about 15 minutes delayed on Xetra, LSE and
  Euronext, close to real time on Copenhagen. The response `meta` carries the last price,
  its trade time (`regularMarketTime`), the previous close and the exchange time zone;
- daily bars (`interval=1d`) as the fallback when Stooq, the source of record, refuses or
  cannot be reached. Closes are split-adjusted, not dividend-adjusted.

Symbols: bare for US listings (`MSFT`), otherwise ticker + exchange suffix (`VWCE.DE` Xetra,
`CSPX.L` London, `ASML.AS` Amsterdam, `NOVO-B.CO` Copenhagen, `CEZ.PR` Prague); indices
start with `^` (`^GSPC`, `^STOXX`). Yahoo's `^PX` is an unrelated CBOE symbol, not Prague's
PX. An unknown symbol answers 404 "No data found, symbol may be delisted", which is a
visible gap (YahooNoData), not an error.

Requests are paced: bursts draw HTTP 429 "Too Many Requests"; a 429 is waited out and
retried a couple of times. A browser-like User-Agent is refused more often than an honest
one, so the caller's own User-Agent is sent.
"""

import asyncio
import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from steward.sources.market import PriceBar

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SOURCE_LABEL = "Yahoo Finance (unofficial)"
# Waits before retrying a 429 or a 5xx, in seconds.
RETRY_WAITS = (2.0, 6.0)
_sleep = asyncio.sleep


class YahooError(Exception):
    """The request failed or the answer could not be read (a gap with this reason)."""


class YahooNoData(YahooError):
    """Yahoo does not know the symbol or has no bars for it."""


@dataclass(frozen=True)
class YahooMeta:
    symbol: str
    currency: str | None
    exchange_name: str | None
    exchange_tz: str | None
    instrument_type: str | None
    price: Decimal | None
    price_time: dt.datetime | None
    previous_close: Decimal | None
    session_start: dt.datetime | None
    session_end: dt.datetime | None

    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.exchange_tz or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")


@dataclass(frozen=True)
class YahooBar:
    ts: dt.datetime
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: Decimal | None


@dataclass(frozen=True)
class YahooChart:
    meta: YahooMeta
    bars: list[YahooBar]
    granularity: str | None
    url: str
    status: int
    content_type: str | None
    # The response body exactly as received (stored as the original payload).
    body: str


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except InvalidOperation:
        return None
    return out if out.is_finite() else None


def _price(value: Any, decimals: int) -> Decimal | None:
    """A price rounded to Yahoo's `priceHint` decimals: Yahoo serialises prices through
    32-bit floats (185.92 arrives as 185.9199981689453), so the digits past the hint are
    noise, not precision."""
    out = _dec(value)
    return None if out is None else out.quantize(Decimal(1).scaleb(-decimals))


def _ts(value: Any) -> dt.datetime | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return dt.datetime.fromtimestamp(int(value), dt.UTC)


def _error(body: Any) -> YahooError | None:
    chart = body.get("chart") if isinstance(body, dict) else None
    if not isinstance(chart, dict):
        return YahooError("Yahoo returned an unexpected body")
    error = chart.get("error")
    if error:
        code = str(error.get("code", "")) if isinstance(error, dict) else ""
        text = str(error.get("description", error)) if isinstance(error, dict) else str(error)
        if code == "Not Found" or "no data found" in text.lower():
            return YahooNoData(text[:200])
        return YahooError(f"Yahoo error {code}: {text}"[:300])
    if not chart.get("result"):
        return YahooNoData("Yahoo returned no result")
    return None


def parse_chart(
    symbol: str,
    body: dict[str, Any],
    *,
    url: str = "",
    status: int = 200,
    content_type: str | None = "application/json",
    raw: str | None = None,
) -> YahooChart:
    """The chart response's meta and bars (bars without a close are skipped)."""
    problem = _error(body)
    if problem is not None:
        raise problem
    result = body["chart"]["result"][0]
    meta_raw = result.get("meta") or {}
    regular = (meta_raw.get("currentTradingPeriod") or {}).get("regular") or {}
    hint = meta_raw.get("priceHint")
    decimals = hint if isinstance(hint, int) and 0 <= hint <= 8 else 4
    meta = YahooMeta(
        symbol=str(meta_raw.get("symbol") or symbol),
        currency=meta_raw.get("currency"),
        exchange_name=meta_raw.get("fullExchangeName") or meta_raw.get("exchangeName"),
        exchange_tz=meta_raw.get("exchangeTimezoneName"),
        instrument_type=meta_raw.get("instrumentType"),
        price=_price(meta_raw.get("regularMarketPrice"), decimals),
        price_time=_ts(meta_raw.get("regularMarketTime")),
        # `previousClose` is the prior session's close; on multi-day ranges only
        # `chartPreviousClose` (the close before the range) is given.
        previous_close=_price(
            meta_raw.get("previousClose") or meta_raw.get("chartPreviousClose"), decimals
        ),
        session_start=_ts(regular.get("start")),
        session_end=_ts(regular.get("end")),
    )
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}

    def column(name: str) -> list[Any]:
        values = quote.get(name) or []
        return list(values) + [None] * (len(stamps) - len(values))

    opens, highs, lows, closes, volumes = (
        column(k) for k in ("open", "high", "low", "close", "volume")
    )
    bars = []
    for i, stamp in enumerate(stamps):
        ts = _ts(stamp)
        close = _price(closes[i], decimals)
        if ts is None or close is None or close <= 0:
            continue
        bars.append(
            YahooBar(
                ts=ts,
                open=_price(opens[i], decimals),
                high=_price(highs[i], decimals),
                low=_price(lows[i], decimals),
                close=close,
                volume=_dec(volumes[i]),
            )
        )
    bars.sort(key=lambda b: b.ts)
    return YahooChart(
        meta=meta,
        bars=bars,
        granularity=meta_raw.get("dataGranularity"),
        url=url,
        status=status,
        content_type=content_type,
        body=raw if raw is not None else json.dumps(body),
    )


async def yahoo_chart(
    http: httpx.AsyncClient, symbol: str, *, range_: str, interval: str, retry: bool = True
) -> YahooChart:
    """One chart request, retried after a 429 or a server error (unless `retry` is off, for
    a page waiting on the answer)."""
    url = CHART_URL.format(symbol=symbol)
    params = {"range": range_, "interval": interval, "includePrePost": "false"}
    response: httpx.Response | None = None
    waits = RETRY_WAITS if retry else ()
    for attempt in range(len(waits) + 1):
        response = await http.get(url, params=params)
        if response.status_code != 429 and response.status_code < 500:
            break
        if attempt < len(waits):
            await _sleep(waits[attempt])
    assert response is not None
    full_url = str(response.request.url) if response.request else url
    content_type = response.headers.get("content-type")
    if response.status_code == 429:
        raise YahooError("Yahoo rate limit (HTTP 429); retried on the next run")
    if response.status_code not in (200, 404):
        raise YahooError(f"Yahoo returned HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        if response.status_code == 404:
            raise YahooNoData("Yahoo returned HTTP 404 (unknown symbol)") from exc
        raise YahooError(f"Yahoo returned HTTP {response.status_code} without JSON") from exc
    return parse_chart(
        symbol,
        body,
        url=full_url,
        status=response.status_code,
        content_type=content_type,
        raw=response.text,
    )


async def yahoo_intraday(http: httpx.AsyncClient, symbol: str, *, retry: bool = True) -> YahooChart:
    """Today's (or the latest session's) 5-minute bars."""
    return await yahoo_chart(http, symbol, range_="1d", interval="5m", retry=retry)


# Yahoo's ranges for daily bars, shortest first: the smallest one covering the days wanted.
# The spans allow the few days of padding the history start carries (2 years + 5 days is
# still the 2y range; the bars before the start are dropped).
DAILY_RANGES = (("5d", 5), ("1mo", 31), ("3mo", 92), ("6mo", 183), ("1y", 372), ("2y", 740),
                ("5y", 1835), ("10y", 3660))  # fmt: skip


def daily_range(days: int) -> str:
    return next((name for name, span in DAILY_RANGES if span >= days), "max")


def daily_bars(chart: YahooChart, now: dt.datetime) -> list[PriceBar]:
    """Daily bars dated in the exchange's time zone. The current session's bar is left out
    while that session is still trading (its close is not a close yet)."""
    zone = chart.meta.zone()
    open_day: dt.date | None = None
    start, end = chart.meta.session_start, chart.meta.session_end
    if start is not None and end is not None and start <= now < end:
        open_day = start.astimezone(zone).date()
    out = []
    for bar in chart.bars:
        day = bar.ts.astimezone(zone).date()
        if day == open_day:
            continue
        out.append(
            PriceBar(
                symbol=chart.meta.symbol,
                date=day,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                source_timestamp=bar.ts,
            )
        )
    # One bar per day (Yahoo can repeat the last day as a live point).
    by_day = {b.date: b for b in out}
    return [by_day[d] for d in sorted(by_day)]


async def yahoo_daily(
    http: httpx.AsyncClient, symbol: str, start: dt.date, now: dt.datetime
) -> tuple[YahooChart, list[PriceBar]]:
    """Daily bars from `start` (the smallest Yahoo range covering it) up to the last
    completed session."""
    days = (now.date() - start).days + 1
    chart = await yahoo_chart(http, symbol, range_=daily_range(days), interval="1d")
    return chart, [b for b in daily_bars(chart, now) if b.date >= start]
