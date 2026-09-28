"""The Prague Stock Exchange's index detail page (`pse.cz/en/indices/index-values/detail/<ISIN>`).

No free API carries the PX index (Yahoo's `^PX` is an unrelated CBOE symbol), so when Stooq's
`^px` cannot be had, the PX value is read off PSE's public detail page: its "Intraday data"
box (date and time, current value, absolute change). The site publishes no API or terms of
use and its robots.txt disallows only `/api/`; the page is read at most once a day, after the
close, and cached. Outside trading hours the page shows the last trading day's closing data.
Tier 1 publisher, but a scraped page: a layout change is a visible gap, never a guess.
"""

import datetime as dt
import html
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx

DETAIL_URL = "https://www.pse.cz/en/indices/index-values/detail/{isin}"
PX_ISIN = "XC0009698371"
PRAGUE = ZoneInfo("Europe/Prague")
# PSE's continuous trading ends 16:20; the index's closing value is published by 16:35.
CLOSE_AFTER = dt.time(16, 20)


class PseError(Exception):
    pass


@dataclass(frozen=True)
class PseIndexValue:
    isin: str
    value: Decimal
    # The page's "Date & Time" (Prague time), as an aware datetime.
    at: dt.datetime
    change_abs: Decimal | None
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None

    @property
    def previous_close(self) -> Decimal | None:
        if self.change_abs is None:
            return None
        previous = self.value - self.change_abs
        return previous if previous > 0 else None

    def is_close(self, now: dt.datetime) -> bool:
        """The value is a session's close: from an earlier day, or after the close."""
        local = now.astimezone(PRAGUE)
        return self.at.date() < local.date() or self.at.time() >= CLOSE_AFTER


def _number(text: str) -> Decimal | None:
    cleaned = html.unescape(text).replace("\xa0", "").replace(" ", "").replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _item(page: str, title: str) -> str | None:
    """The value cell following an "Intraday data" item title."""
    pattern = (
        r'detail-chart-info-item-content-title">\s*' + re.escape(title) + r"\s*</div>\s*"
        r'<div class="detail-chart-info-item-content-value[^"]*"[^>]*>(.*?)</div>'
    )
    match = re.search(pattern, page, re.S)
    if match is None:
        return None
    return re.sub(r"<[^>]+>", " ", match.group(1)).strip()


def _row(page: str, label: str) -> Decimal | None:
    match = re.search(r"<td>\s*" + re.escape(label) + r"\s*</td>\s*<td>([^<]+)</td>", page)
    return _number(match.group(1)) if match else None


def parse_detail(isin: str, page: str) -> PseIndexValue:
    stamp = _item(page, "Date &amp; Time") or _item(page, "Date & Time")
    value_text = _item(page, "Current value")
    if not stamp or not value_text:
        raise PseError("PSE page layout changed: no Intraday data box")
    match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s*\|\s*(\d{2}):(\d{2})(?::(\d{2}))?", stamp)
    if match is None:
        raise PseError(f"PSE page: unreadable date {stamp[:40]!r}")
    day, month, year, hour, minute, second = match.groups()
    at = dt.datetime(
        int(year), int(month), int(day), int(hour), int(minute), int(second or 0), tzinfo=PRAGUE
    )
    value = _number(value_text.split()[0]) if value_text.split() else None
    if value is None or value <= 0:
        raise PseError(f"PSE page: unreadable value {value_text[:40]!r}")
    change_text = _item(page, "Change Abs.")
    change = _number(change_text.split()[0]) if change_text and change_text.split() else None
    return PseIndexValue(
        isin=isin,
        value=value,
        at=at,
        change_abs=change,
        open=_row(page, "Open value"),
        high=_row(page, "Daily High"),
        low=_row(page, "Daily Low"),
    )


@dataclass(frozen=True)
class PseFetch:
    value: PseIndexValue
    url: str
    status: int
    content_type: str | None
    body: str


async def pse_index(http: httpx.AsyncClient, isin: str = PX_ISIN) -> PseFetch:
    url = DETAIL_URL.format(isin=isin)
    response = await http.get(url)
    if response.status_code != 200:
        raise PseError(f"PSE returned HTTP {response.status_code}")
    return PseFetch(
        value=parse_detail(isin, response.text),
        url=url,
        status=response.status_code,
        content_type=response.headers.get("content-type"),
        body=response.text,
    )
