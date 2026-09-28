"""Czech National Bank (ČNB) money-market rates from its keyless text files. Tier 1.

- CZEONIA, the CZK overnight reference rate (weighted average of unsecured overnight
  interbank deposits, including deposits at the ČNB deposit facility; administered by the
  ČNB since 1 July 2020, published D+1 by about 9:30):
  - `yearly.txt?calendarYear=YYYY`: header `Date|Volume (CZK mio)|Rate`, then one row per
    business day, e.g. `24 Sep 2026|20740|3.56`. A year with no data yet answers 200 with an
    empty body.
  - `daily.txt`: the latest fixing: a date line (`24 Sep 2026`), then
    `Volume (CZK mio)|Rate` and one row.
- The 2-week repo rate (the ČNB's main policy rate): one file with its whole history,
  `VALID_FROM|CNB_REPO_RATE_IN_%` then `YYYYMMDD|rate` per change (a UTF-8 BOM first).

Rates are in percent per annum. Numbers use a decimal point in the English files; a decimal
comma is accepted too.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx

CZEONIA_PAGE = (
    "https://www.cnb.cz/en/financial-markets/money-market/czeonia-reference-interest-rate/"
)
CZEONIA_YEARLY_URL = CZEONIA_PAGE + "yearly.txt"
CZEONIA_DAILY_URL = CZEONIA_PAGE + "daily.txt"
REPO_RATE_URL = "https://www.cnb.cz/en/faq/.galleries/development_of_the_cnb_2w_repo_rate.txt"

RETRIES = 2


class CnbError(Exception):
    pass


@dataclass(frozen=True)
class RatePoint:
    date: dt.date
    rate: Decimal  # percent per annum
    volume: Decimal | None = None  # CZEONIA only: CZK millions


def _decimal(text: str) -> Decimal:
    try:
        return Decimal(text.strip().replace(",", "."))
    except InvalidOperation as exc:
        raise CnbError(f"ČNB file has an unreadable number: {text!r}") from exc


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.lstrip("﻿").splitlines() if line.strip()]


def _date(text: str) -> dt.date:
    try:
        return dt.datetime.strptime(text.strip(), "%d %b %Y").date()
    except ValueError as exc:
        raise CnbError(f"ČNB file has an unexpected date: {text!r}") from exc


def parse_czeonia_yearly(text: str) -> list[RatePoint]:
    """Every row of a CZEONIA yearly file, oldest first. An empty file gives []."""
    lines = _lines(text)
    if not lines:
        return []
    if lines[0].startswith("<"):
        raise CnbError("ČNB answered with an HTML page instead of the CZEONIA file")
    header = [h.strip().lower() for h in lines[0].split("|")]
    if "date" not in header or "rate" not in header:
        raise CnbError(f"CZEONIA yearly file has an unexpected header: {lines[0]!r}")
    date_i, rate_i = header.index("date"), header.index("rate")
    volume_i = next((i for i, h in enumerate(header) if h.startswith("volume")), None)
    out = []
    for line in lines[1:]:
        cells = line.split("|")
        if len(cells) != len(header):
            raise CnbError(f"CZEONIA yearly file has an unreadable row: {line!r}")
        if not cells[rate_i].strip():
            continue  # no fixing that day
        out.append(
            RatePoint(
                date=_date(cells[date_i]),
                rate=_decimal(cells[rate_i]),
                volume=_decimal(cells[volume_i])
                if volume_i is not None and cells[volume_i].strip()
                else None,
            )
        )
    return sorted(out, key=lambda p: p.date)


def parse_czeonia_daily(text: str) -> list[RatePoint]:
    """The latest CZEONIA fixing (one point), or [] for an empty file."""
    lines = _lines(text)
    if not lines:
        return []
    if lines[0].startswith("<"):
        raise CnbError("ČNB answered with an HTML page instead of the CZEONIA file")
    if len(lines) < 3:
        raise CnbError("CZEONIA daily file is too short")
    day = _date(lines[0].split("#")[0])
    header = [h.strip().lower() for h in lines[1].split("|")]
    if "rate" not in header:
        raise CnbError(f"CZEONIA daily file has an unexpected header: {lines[1]!r}")
    cells = lines[2].split("|")
    if len(cells) != len(header):
        raise CnbError(f"CZEONIA daily file has an unreadable row: {lines[2]!r}")
    volume_i = next((i for i, h in enumerate(header) if h.startswith("volume")), None)
    return [
        RatePoint(
            date=day,
            rate=_decimal(cells[header.index("rate")]),
            volume=_decimal(cells[volume_i]) if volume_i is not None else None,
        )
    ]


def parse_repo_rate(text: str) -> list[RatePoint]:
    """Every change of the 2W repo rate, oldest first: each point is the date the new rate
    became valid."""
    lines = _lines(text)
    if not lines or lines[0].startswith("<"):
        raise CnbError("ČNB repo rate file is empty or not a text file")
    header = [h.strip().upper() for h in lines[0].split("|")]
    if len(header) < 2 or header[0] != "VALID_FROM":
        raise CnbError(f"ČNB repo rate file has an unexpected header: {lines[0]!r}")
    out = []
    for line in lines[1:]:
        cells = line.split("|")
        try:
            day = dt.datetime.strptime(cells[0].strip(), "%Y%m%d").date()
        except (ValueError, IndexError) as exc:
            raise CnbError(f"ČNB repo rate file has an unreadable row: {line!r}") from exc
        if len(cells) < 2:
            raise CnbError(f"ČNB repo rate file has an unreadable row: {line!r}")
        out.append(RatePoint(date=day, rate=_decimal(cells[1])))
    return sorted(out, key=lambda p: p.date)


async def _get_text(http: httpx.AsyncClient, url: str, params: dict[str, str] | None) -> str:
    """GET with a short retry on transport errors and 5xx; 4xx fails at once."""
    last: str = ""
    for _attempt in range(RETRIES + 1):
        try:
            response = await http.get(url, params=params, timeout=30.0)
        except httpx.TransportError as exc:
            last = f"{type(exc).__name__}: {exc}"[:200]
            continue
        if response.status_code >= 500:
            last = f"HTTP {response.status_code}"
            continue
        if response.status_code != 200:
            raise CnbError(f"ČNB returned HTTP {response.status_code} for {url}")
        return response.text
    raise CnbError(f"ČNB could not be reached ({last}) for {url}")


async def fetch_czeonia_year(http: httpx.AsyncClient, year: int) -> list[RatePoint]:
    return parse_czeonia_yearly(
        await _get_text(http, CZEONIA_YEARLY_URL, {"calendarYear": str(year)})
    )


async def fetch_czeonia_daily(http: httpx.AsyncClient) -> list[RatePoint]:
    return parse_czeonia_daily(await _get_text(http, CZEONIA_DAILY_URL, None))


async def fetch_czeonia(
    http: httpx.AsyncClient, today: dt.date, since: dt.date | None
) -> list[RatePoint]:
    """With `since`, every fixing from that date (one yearly file per year); otherwise the
    current year's file (a few KB), so a missed day is filled on the next run. The daily
    file is merged in, in case it is ahead of the yearly one."""
    first_year = since.year if since else today.year
    points: dict[dt.date, RatePoint] = {}
    for year in range(first_year, today.year + 1):
        for p in await fetch_czeonia_year(http, year):
            points[p.date] = p
    try:
        for p in await fetch_czeonia_daily(http):
            points.setdefault(p.date, p)
    except CnbError:
        if not points:
            raise
    out = sorted(points.values(), key=lambda p: p.date)
    return [p for p in out if since is None or p.date >= since]


async def fetch_repo_rate(http: httpx.AsyncClient) -> list[RatePoint]:
    return parse_repo_rate(await _get_text(http, REPO_RATE_URL, None))
