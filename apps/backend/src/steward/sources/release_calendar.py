"""Adapters for the macro providers' own published release calendars.

Every source here is free (a free FRED key, or keyless) and was verified live on 2026-10-03;
docs/data/macro-release-calendar.md records the evidence. Providers without a free
machine-readable calendar (OECD, World Bank, UN Comtrade, ČNB) have no adapter on purpose.

* FRED: `/fred/series/release` (series -> release id), then `/fred/release/dates` with
  `include_release_dates_with_no_data=true` (future dates). JSON, free API key.
* Eurostat: the iCalendar export of the release calendar (euro indicators), keyless.
* ECB: the statistical-calendar pages (`/press/calendars/statscal/mfm/html/<page>.en.html`),
  structured HTML (`<dt>dd/mm/yyyy hh:mm</dt><dd>... (Dataset: BSI)`), keyless.
* BIS: `data.bis.org/api/v0/releasecalendar`, JSON, keyless.

A parser raises `CalendarDrift` when the shape is not the verified one (a missing field, a
changed format, no entries at all); the caller quarantines the payload and stores nothing. A
request is only made after `before_call` reserved its budget: a refusal comes back as a typed
`quota_exhausted` result, a 402 as `paid_only`; nothing paid is ever called.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from steward.redact import redact_text
from steward.sources.base import AccessResult, AccessState, access_from_http, retry_after_at
from steward.sources.governor import QuotaExceeded


class _RedactHttpxLog(logging.Filter):
    """httpx logs every request URL at INFO and a FRED URL carries the API key as a query
    parameter: mask credentials in those records before any handler sees them."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return True


def _install_httpx_log_redaction() -> None:
    logger = logging.getLogger("httpx")
    if not any(isinstance(f, _RedactHttpxLog) for f in logger.filters):
        logger.addFilter(_RedactHttpxLog())


_install_httpx_log_redaction()

FRED_API = "https://api.stlouisfed.org/fred"
FRED_RELEASE_PAGE = "https://fred.stlouisfed.org/releases/calendar"
EUROSTAT_ICS = "https://ec.europa.eu/eurostat/o/calendars/eventsIcal"
EUROSTAT_PAGE = "https://ec.europa.eu/eurostat/web/main/news/release-calendar"
ECB_CALENDAR = "https://www.ecb.europa.eu/press/calendars/statscal/mfm/html/{page}.en.html"
BIS_API = "https://data.bis.org/api/v0/releasecalendar"
BIS_PAGE = "https://data.bis.org/release-calendar"

# Our series (config keys) -> the provider's calendar entries. Only series whose release the
# provider's calendar names unambiguously are listed; everything else has no calendar entry
# (and the macro context falls back to the observed-cadence estimate).
ECB_PAGES: dict[str, tuple[str, tuple[str, ...]]] = {
    # page -> (dataset the page must declare, our series keys)
    "stprmp": ("BSI", ("euro_area_m3_growth", "euro_area_private_credit")),
    "stprmf": (
        "MIR",
        (
            "euro_area_household_loan_rate",
            "euro_area_corporate_loan_rate",
            "ea_household_overnight_deposit_rate",
            "ea_corporate_term_deposit_rate",
        ),
    ),
    "stpbls": ("BLS", ("ea_bls_enterprise_credit_standards",)),
}
# Eurostat euro-indicator event titles (the calendar carries no dataset code).
EUROSTAT_EVENTS: dict[str, tuple[str, ...]] = {
    "hicp": ("Inflation (HICP)",),
    "unemployment": ("Unemployment",),
    "industrial_production": ("Industrial production",),
    "gdp": (
        "Flash estimate GDP and employment - EU and euro area",
        "GDP main aggregates and employment",
    ),
}
# BIS release category ids ("Release" rows only, not "Revision" rows).
BIS_CATEGORIES: dict[str, str] = {
    "total_credit": "TOTAL_CREDIT",
    "debt_service": "DSR",
    "banking_claims": "LBS",
    "debt_securities": "DSS",
    "global_liquidity": "GLI",
}


class CalendarDrift(ValueError):
    """The calendar is not in the verified shape; nothing from it may be stored."""


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    provider: str
    series_id: str
    calendar_key: str
    release_date: dt.date
    source_url: str
    release_time: dt.time | None = None
    title: str | None = None
    reference_period: str | None = None

    @property
    def identity(self) -> tuple[str, str, str, dt.date]:
        return self.provider, self.series_id, self.calendar_key, self.release_date


@dataclass(slots=True)
class CalendarFetch:
    """The outcome of reading one provider's calendar.

    `covered`: (series_id, calendar_key) pairs whose calendar was read completely, so a live
    stored date that is absent from `entries` for such a pair has been rescheduled or dropped.
    `drift` is set when a payload was rejected; `raw` then holds it for the quarantine archive."""

    provider: str
    access: AccessResult
    entries: list[CalendarEntry] = field(default_factory=list)
    covered: set[tuple[str, str]] = field(default_factory=set)
    drift: str | None = None
    raw: Any = None
    calls: int = 0
    status_code: int | None = None
    response_bytes: int = 0


BeforeCall = Callable[[], Awaitable[None]]


# ----------------------------------------------------------------------------------- parsers
def _iso_date(value: object, what: str) -> dt.date:
    if not isinstance(value, str):
        raise CalendarDrift(f"{what}: not a string date")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CalendarDrift(f"{what}: bad date {value!r}") from exc


def parse_fred_series_release(body: object) -> tuple[int, str]:
    """(release id, name) of the single release a FRED series belongs to."""
    releases = body.get("releases") if isinstance(body, dict) else None
    if not isinstance(releases, list) or len(releases) != 1 or not isinstance(releases[0], dict):
        raise CalendarDrift("FRED series/release: expected exactly one release")
    release = releases[0]
    release_id, name = release.get("id"), release.get("name")
    if not isinstance(release_id, int) or isinstance(release_id, bool):
        raise CalendarDrift("FRED series/release: release id is not an integer")
    return release_id, name if isinstance(name, str) else ""


def parse_fred_release_dates(body: object, release_id: int) -> list[dt.date]:
    rows = body.get("release_dates") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise CalendarDrift("FRED release/dates: no release_dates list")
    out: set[dt.date] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("release_id") != release_id:
            raise CalendarDrift("FRED release/dates: row for a different release or not an object")
        out.add(_iso_date(row.get("date"), "FRED release/dates"))
    return sorted(out)


_ICS_EVENT = re.compile(r"BEGIN:VEVENT\r?\n(.*?)END:VEVENT", re.S)


def _unfold(text: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", text)


def _ics_unescape(value: str) -> str:
    return value.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").replace("\\\\", "\\")


def parse_eurostat_ics(text: str) -> list[tuple[str, dt.date]]:
    """(title, date) of every event of Eurostat's iCalendar export (all-day events)."""
    if not isinstance(text, str) or not text.lstrip().startswith("BEGIN:VCALENDAR"):
        raise CalendarDrift("Eurostat calendar: not an iCalendar document")
    if "Eurostat" not in text[:400]:
        raise CalendarDrift("Eurostat calendar: unexpected PRODID")
    events: list[tuple[str, dt.date]] = []
    for block in _ICS_EVENT.findall(_unfold(text)):
        summary = re.search(r"^SUMMARY[^:\n]*:(.*)$", block, re.M)
        start = re.search(r"^DTSTART[^:\n]*:(\d{8})(?:T\d{6}Z?)?\s*$", block, re.M)
        if summary is None or start is None:
            raise CalendarDrift("Eurostat calendar: event without SUMMARY or DTSTART")
        try:
            day = dt.datetime.strptime(start.group(1), "%Y%m%d").date()
        except ValueError as exc:
            raise CalendarDrift("Eurostat calendar: bad DTSTART") from exc
        events.append((_ics_unescape(summary.group(1).strip()), day))
    if not events:
        raise CalendarDrift("Eurostat calendar: no events")
    return events


_ECB_ENTRY = re.compile(
    r"<dt>\s*(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})\s*</dt>\s*<dd>(.*?)</dd>", re.S
)


def parse_ecb_calendar_html(
    page: str, dataset: str
) -> list[tuple[dt.date, dt.time, str | None, str]]:
    """(date, time, reference period, title) of an ECB statistical-calendar page, which must
    declare `dataset` on every entry."""
    if "definition-list" not in page or "<dl" not in page:
        raise CalendarDrift("ECB calendar: no definition list")
    out: list[tuple[dt.date, dt.time, str | None, str]] = []
    for day, month, year, hour, minute, body in _ECB_ENTRY.findall(page):
        text = html.unescape(re.sub(r"<br\s*/?>", "\n", body))
        text = re.sub(r"<[^>]+>", "", text)
        declared = re.search(r"\(Dataset:\s*([A-Z0-9_]+)\)", text)
        if declared is None or declared.group(1) != dataset:
            raise CalendarDrift(f"ECB calendar: entry does not declare dataset {dataset}")
        reference = re.search(r"Reference period:\s*(.+)", text)
        title = text.strip().splitlines()[0].strip()
        try:
            when = (
                dt.date(int(year), int(month), int(day)),
                dt.time(int(hour), int(minute)),
            )
        except ValueError as exc:
            raise CalendarDrift("ECB calendar: bad date or time") from exc
        out.append((*when, reference.group(1).strip() if reference else None, title))
    if not out:
        raise CalendarDrift("ECB calendar: no entries")
    return out


_BIS_FIELDS = ("category_id", "release_type", "release_date", "time_period")


def parse_bis_calendar(body: object) -> list[tuple[str, str, dt.date, str]]:
    """(category id, release type, date, reference period) of every BIS calendar row."""
    if not isinstance(body, list) or not body:
        raise CalendarDrift("BIS calendar: not a non-empty list")
    out: list[tuple[str, str, dt.date, str]] = []
    for row in body:
        if not isinstance(row, dict) or any(not isinstance(row.get(f), str) for f in _BIS_FIELDS):
            raise CalendarDrift("BIS calendar: row without the expected fields")
        try:
            day = dt.datetime.strptime(row["release_date"], "%Y/%m/%d").date()
        except ValueError as exc:
            raise CalendarDrift(f"BIS calendar: bad date {row['release_date']!r}") from exc
        out.append((row["category_id"], row["release_type"], day, row["time_period"]))
    return out


# ------------------------------------------------------------------------------------ fetching
def _outcome(response: httpx.Response, *, has_rows: bool = True) -> AccessResult:
    return access_from_http(
        response.status_code,
        has_rows=has_rows,
        retry_at=retry_after_at(response.headers.get("Retry-After")),
    )


async def _get(
    http: httpx.AsyncClient,
    fetch: CalendarFetch,
    before_call: BeforeCall,
    url: str,
    **kwargs: Any,
) -> httpx.Response | None:
    """One governed request. None (with `fetch.access` set) when it was refused or failed; the
    error text never carries the URL (a FRED URL holds the API key)."""
    try:
        await before_call()
    except QuotaExceeded as exc:
        fetch.access = AccessResult(AccessState.QUOTA_EXHAUSTED, str(exc))
        return None
    fetch.calls += 1
    try:
        response = await http.get(url, **kwargs)
    except httpx.HTTPError as exc:
        fetch.access = AccessResult(
            AccessState.TEMPORARILY_FAILED, f"request failed: {type(exc).__name__}"
        )
        return None
    fetch.status_code = response.status_code
    fetch.response_bytes += len(response.content)
    if response.status_code != 200:
        fetch.access = _outcome(response)
        return None
    return response


def _json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _drift(fetch: CalendarFetch, error: CalendarDrift, raw: Any) -> CalendarFetch:
    fetch.access = AccessResult(AccessState.TEMPORARILY_FAILED, f"schema drift: {error}")
    fetch.drift = str(error)
    fetch.raw = raw
    return fetch


async def fetch_fred(
    http: httpx.AsyncClient,
    api_key: str,
    series: Mapping[str, str],
    today: dt.date,
    before_call: BeforeCall,
) -> CalendarFetch:
    """`series`: our series key -> FRED series id. One `series/release` call per series, one
    `release/dates` call per distinct release (future dates only)."""
    fetch = CalendarFetch("fred", AccessResult(AccessState.AVAILABLE))
    if not api_key:
        fetch.access = AccessResult(AccessState.ACCESS_UNVERIFIED, "FRED_API_KEY is not configured")
        return fetch
    common = {"api_key": api_key, "file_type": "json"}
    release_of: dict[str, tuple[int, str]] = {}
    for key, fred_id in series.items():
        response = await _get(
            http,
            fetch,
            before_call,
            f"{FRED_API}/series/release",
            params={**common, "series_id": fred_id},
        )
        if response is None:
            return fetch
        body = _json(response)
        try:
            release_of[key] = parse_fred_series_release(body)
        except CalendarDrift as exc:
            return _drift(
                fetch, exc, {"endpoint": "series/release", "series": fred_id, "body": body}
            )
    dates_of: dict[int, list[dt.date]] = {}
    for release_id, _name in release_of.values():
        if release_id in dates_of:
            continue
        response = await _get(
            http,
            fetch,
            before_call,
            f"{FRED_API}/release/dates",
            params={
                **common,
                "release_id": release_id,
                "include_release_dates_with_no_data": "true",
                "realtime_start": today.isoformat(),
                "realtime_end": "9999-12-31",
                "sort_order": "asc",
                "limit": 200,
            },
        )
        if response is None:
            return fetch
        body = _json(response)
        try:
            dates_of[release_id] = parse_fred_release_dates(body, release_id)
        except CalendarDrift as exc:
            return _drift(
                fetch, exc, {"endpoint": "release/dates", "release": release_id, "body": body}
            )
    for key, (release_id, name) in release_of.items():
        calendar_key = f"release:{release_id}"
        fetch.covered.add((key, calendar_key))
        url = f"{FRED_RELEASE_PAGE}?rid={release_id}"
        for day in dates_of[release_id]:
            if day >= today:
                fetch.entries.append(
                    CalendarEntry("fred", key, calendar_key, day, url, title=name or None)
                )
    return fetch


async def fetch_eurostat(
    http: httpx.AsyncClient, today: dt.date, before_call: BeforeCall
) -> CalendarFetch:
    """The euro-indicator calendar (category 2), mapped to our Eurostat dataset keys by title."""
    fetch = CalendarFetch("eurostat", AccessResult(AccessState.AVAILABLE))
    response = await _get(
        http, fetch, before_call, EUROSTAT_ICS, params={"theme": "", "category": "2"}
    )
    if response is None:
        return fetch
    text = response.text
    try:
        events = parse_eurostat_ics(text)
    except CalendarDrift as exc:
        return _drift(fetch, exc, {"raw_text": text[:200_000]})
    by_title: dict[str, list[dt.date]] = {}
    for title, day in events:
        by_title.setdefault(title, []).append(day)
    for series_id, titles in EUROSTAT_EVENTS.items():
        for title in titles:
            if title not in by_title:
                # The calendar no longer names a release we rely on: that is drift, not "none".
                return _drift(
                    fetch,
                    CalendarDrift(f"Eurostat calendar: event {title!r} is missing"),
                    {"raw_text": text[:200_000]},
                )
            fetch.covered.add((series_id, title))
            for day in sorted(set(by_title[title])):
                if day >= today:
                    fetch.entries.append(
                        CalendarEntry("eurostat", series_id, title, day, EUROSTAT_PAGE, title=title)
                    )
    return fetch


async def fetch_ecb(
    http: httpx.AsyncClient, today: dt.date, before_call: BeforeCall
) -> CalendarFetch:
    """One request per statistical-calendar page. A page that fails or drifts leaves the others
    usable: its series simply stay uncovered (no supersede, no new rows)."""
    fetch = CalendarFetch("ecb", AccessResult(AccessState.AVAILABLE))
    failure: AccessResult | None = None
    for page, (dataset, series_ids) in ECB_PAGES.items():
        url = ECB_CALENDAR.format(page=page)
        sub = CalendarFetch("ecb", AccessResult(AccessState.AVAILABLE))
        response = await _get(http, sub, before_call, url)
        fetch.calls += sub.calls
        fetch.response_bytes += sub.response_bytes
        fetch.status_code = sub.status_code or fetch.status_code
        if response is None:
            failure = failure or sub.access
            if sub.access.state in (AccessState.PAID_ONLY, AccessState.QUOTA_EXHAUSTED):
                break
            continue
        try:
            rows = parse_ecb_calendar_html(response.text, dataset)
        except CalendarDrift as exc:
            drifted = _drift(
                CalendarFetch("ecb", fetch.access), exc, {"raw_text": response.text[:200_000]}
            )
            failure = failure or drifted.access
            fetch.drift = fetch.drift or drifted.drift
            fetch.raw = fetch.raw or drifted.raw
            continue
        for series_id in series_ids:
            fetch.covered.add((series_id, f"{page}:{dataset}"))
            for day, time, reference, title in rows:
                if day >= today:
                    fetch.entries.append(
                        CalendarEntry(
                            "ecb",
                            series_id,
                            f"{page}:{dataset}",
                            day,
                            url,
                            release_time=time,
                            title=title,
                            reference_period=reference,
                        )
                    )
    if failure is not None:
        fetch.access = failure
    return fetch


async def fetch_bis(
    http: httpx.AsyncClient, today: dt.date, before_call: BeforeCall
) -> CalendarFetch:
    fetch = CalendarFetch("bis", AccessResult(AccessState.AVAILABLE))
    response = await _get(http, fetch, before_call, BIS_API)
    if response is None:
        return fetch
    body = _json(response)
    try:
        rows = parse_bis_calendar(body)
    except CalendarDrift as exc:
        return _drift(fetch, exc, {"body": body})
    categories = {row[0] for row in rows}
    for series_id, category in BIS_CATEGORIES.items():
        if category not in categories:
            return _drift(
                fetch,
                CalendarDrift(f"BIS calendar: category {category} is missing"),
                {"body": body},
            )
        fetch.covered.add((series_id, category))
        for category_id, release_type, day, period in rows:
            if category_id == category and release_type == "Release" and day >= today:
                fetch.entries.append(
                    CalendarEntry(
                        "bis",
                        series_id,
                        category,
                        day,
                        BIS_PAGE,
                        title=f"BIS {category} release",
                        reference_period=period,
                    )
                )
    return fetch


def series_with_calendar(provider: str, keys: Sequence[str]) -> list[str]:
    """Which of our series keys a provider's mapping covers (the rest has no calendar)."""
    known: set[str]
    if provider == "ecb":
        known = {s for _, (_, ids) in ECB_PAGES.items() for s in ids}
    elif provider == "eurostat":
        known = set(EUROSTAT_EVENTS)
    elif provider == "bis":
        known = set(BIS_CATEGORIES)
    else:
        known = set(keys)  # FRED: any configured series has a release
    return [k for k in keys if k in known]
