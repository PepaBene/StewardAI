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
    http: httpx.AsyncClient,
    symbol: str,
    *,
    range_: str,
    interval: str,
    retry: bool = True,
    period: tuple[dt.date, dt.date] | None = None,
) -> YahooChart:
    """One chart request, retried after a 429 or a server error (unless `retry` is off, for
    a page waiting on the answer). `period` (first day, last day) replaces `range_`: Yahoo
    answers `range=max` with monthly bars whatever `interval` says, but honours dated
    `period1`/`period2` with daily ones."""
    url = CHART_URL.format(symbol=symbol)
    params: dict[str, str | int] = {"interval": interval, "includePrePost": "false"}
    if period is None:
        params["range"] = range_
    else:
        zero = dt.time.min
        params["period1"] = int(dt.datetime.combine(period[0], zero, dt.UTC).timestamp())
        params["period2"] = int(
            dt.datetime.combine(period[1] + dt.timedelta(days=1), zero, dt.UTC).timestamp()
        )
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
    name = daily_range(days)
    # Beyond ten years the only named range is "max", which Yahoo serves as monthly bars.
    period = (start, now.date()) if name == "max" else None
    chart = await yahoo_chart(http, symbol, range_=name, interval="1d", period=period)
    return chart, [b for b in daily_bars(chart, now) if b.date >= start]


# ------------------------------------------------------------ dividend events (insight I1.3)
#
# `events=div` adds `events.dividends` to the chart answer: one entry per ex-date (`date`, a UNIX
# time) with the cash `amount` per share in the chart's `meta.currency`. Yahoo adjusts these
# amounts for later splits, which Twelve Data does not, so a cross-check may disagree after a
# split; the disagreement is listed rather than resolved. Requests go through `governed_http`
# (provider `yahoo`, endpoint `chart_dividends`): the first call is the access probe, and the rows
# are stored as `suggested` corporate actions only.

DIVIDEND_SOURCE = "yahoo"
DIVIDEND_RANGE = "10y"
DIVIDEND_BUDGET_PER_MINUTE = 6
DIVIDEND_BUDGET_DAILY = 120


@dataclass(frozen=True)
class YahooDividend:
    ex_date: dt.date
    amount: Decimal
    raw: dict[str, Any]


async def yahoo_symbol_for(pool: Any, ticker: str) -> tuple[int, str, str] | None:
    """(listing id, listing ticker, Yahoo symbol) of a holding with a confirmed listing: the
    profile's `yahoo_symbol`, else derived from the Stooq symbol or the listing. None: no call."""
    from steward.config.holding_profiles import HoldingProfilesConfig
    from steward.config.store import load_active
    from steward.reference.price_series import yahoo_from_listing, yahoo_from_stooq

    row = await pool.fetchrow(
        "select m.listing_id, l.ticker as listing_ticker, l.mic, i.price_symbol"
        " from public.instrument_security_link m"
        " join public.security_listing l on l.id = m.listing_id"
        " join public.instrument i on i.t212_ticker = m.ticker"
        " where m.ticker = $1 and m.status = 'confirmed'",
        ticker,
    )
    if row is None:
        return None
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profile = (stored.body if stored else HoldingProfilesConfig()).for_ticker(ticker)
    ysym = (
        (profile.yahoo_symbol if profile else None)
        or yahoo_from_stooq(row["price_symbol"])
        or yahoo_from_listing(row["listing_ticker"], row["mic"])
    )
    if not ysym:
        return None
    return row["listing_id"], row["listing_ticker"], ysym


def iso_currency(code: str | None) -> str | None:
    """Yahoo's `GBp` (pence) is stored as the Trading212 code `GBX`; anything else upper-cased."""
    if not code:
        return None
    if code == "GBp":
        return "GBX"
    up = code.upper()
    return up if len(up) == 3 and up.isalpha() else None


def parse_dividend_events(body: Any) -> tuple[str | None, list[YahooDividend]]:
    """(currency, dividends) from a chart body with `events=div`. A body with no `events`
    has no dividends on record (an empty list, not an error)."""
    problem = _error(body)
    if problem is not None:
        raise problem
    result = body["chart"]["result"][0]
    meta = result.get("meta") or {}
    events = ((result.get("events") or {}).get("dividends")) or {}
    out: list[YahooDividend] = []
    if isinstance(events, dict):
        for row in events.values():
            if not isinstance(row, dict):
                continue
            stamp = _ts(row.get("date"))
            amount = _dec(row.get("amount"))
            if stamp is None or amount is None or amount <= 0:
                continue
            out.append(YahooDividend(stamp.date(), amount, dict(row)))
    out.sort(key=lambda d: d.ex_date)
    return iso_currency(meta.get("currency")), out


async def collect_yahoo_dividends(
    pool: Any,
    http: httpx.AsyncClient,
    *,
    listing_id: int,
    symbol: str,
    yahoo_symbol: str,
) -> dict[str, int]:
    """Ten years of dividend events of one listing into `corporate_action` as `suggested`
    (source `yahoo`). An existing row is never touched, so an owner decision stays."""
    from steward.sources.governed_http import governed_get
    from steward.sources.governor import Budget

    response = await governed_get(
        pool,
        http,
        provider="yahoo",
        endpoint="chart_dividends",
        url=CHART_URL.format(symbol=yahoo_symbol),
        params={"range": DIVIDEND_RANGE, "interval": "1mo", "events": "div"},
        budget=Budget(
            per_minute=Decimal(DIVIDEND_BUDGET_PER_MINUTE), daily=Decimal(DIVIDEND_BUDGET_DAILY)
        ),
    )
    if response is None:
        return {"proposals": 0, "new": 0}
    try:
        body = response.json()
    except ValueError as exc:
        raise YahooError("Yahoo returned no JSON") from exc
    currency, dividends = parse_dividend_events(body)
    url = f"{CHART_URL.format(symbol=yahoo_symbol)}?events=div&range={DIVIDEND_RANGE}"
    stored = 0
    for d in dividends:
        status = await pool.fetchval(
            """insert into public.corporate_action
               (listing_id,symbol,action_type,ex_date,cash_amount,currency,source,source_url,status,raw)
               values($1,$2,'cash_dividend',$3,$4,$5,$6,$7,'suggested',$8::jsonb)
               on conflict(listing_id,action_type,ex_date,source,source_url) do nothing
               returning status""",
            listing_id,
            symbol,
            d.ex_date,
            d.amount,
            currency,
            DIVIDEND_SOURCE,
            url,
            json.dumps(
                {
                    "per": "listed_line",
                    "amount_basis": "split-adjusted by Yahoo",
                    "provider_row": d.raw,
                    "provider_currency": currency,
                    "yahoo_symbol": yahoo_symbol,
                },
                default=str,
            ),
        )
        stored += 1 if status else 0
    return {"proposals": len(dividends), "new": stored}
