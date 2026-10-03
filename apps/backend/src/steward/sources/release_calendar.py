"""Adapters for the macro providers' own published release calendars.

Every source here is free (a free FRED key, or keyless) and was verified live on 2026-10-03;
docs/data/macro-release-calendar.md records the evidence. Providers without a free
machine-readable calendar (OECD, World Bank, UN Comtrade) have no adapter on purpose.

Basis of a stored date (`CalendarEntry.basis`, the `source` column): `provider_calendar` (a date the
provider lists) or `published_rule` (derived from a release rule AND a holiday list that the
provider itself publishes: ECB yield curves "every TARGET business day" with the ECB's TARGET
closing days; ČNB CZEONIA "the next working day" with the ČNB bank holiday list). A rule is only
used when both pages still say what was verified (otherwise drift: nothing stored).

* FRED: `/fred/series/release` (series -> release id), then `/fred/release/dates` with
  `include_release_dates_with_no_data=true` (future dates). JSON, free API key.
* Eurostat: the iCalendar export of the release calendar (euro indicators), keyless.
* ECB: the statistical-calendar pages (`/press/calendars/statscal/mfm/html/<page>.en.html`),
  structured HTML (`<dt>dd/mm/yyyy hh:mm</dt><dd>... (Dataset: BSI)`), keyless.
* ECB Governing Council meeting calendar (`/press/calendars/mgcgc/`): the monetary policy
  decision days behind `ecb_deposit_rate`.
* BIS: `data.bis.org/api/v0/releasecalendar`, JSON, keyless; our series map to a calendar
  category through the dataflow each series is actually collected from.
* DG ECFIN (European Commission): the business and consumer survey "Publication dates <year>"
  PDF (date and time), the source of the Economic Sentiment Indicator (`ei_bssi_m_r2`).
* ČNB: the Bank Board monetary-policy meeting dates on the "Bank Board decisions" page.
* BLS: the iCalendar release schedule (CPI, Employment Situation: 08:30 `TZID=US-Eastern`),
  needs a User-Agent with a contact address (BLS answers 403 without one).

A parser raises `CalendarDrift` when the shape is not the verified one (a missing field, a
changed format, no entries at all); the caller quarantines the payload and stores nothing. A
request is only made after `before_call` reserved its budget: a refusal comes back as a typed
`quota_exhausted` result, a 402 as `paid_only`; nothing paid is ever called.
"""

from __future__ import annotations

import datetime as dt
import html
import io
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

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
ECB_MEETINGS = "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"
ECFIN_PDF = (
    "https://ec.europa.eu/economy_finance/db_indicators/surveys/documents/calendar/"
    "Publication%20dates%20{year}.pdf"
)
CNB_BANK_BOARD = "https://www.cnb.cz/en/monetary-policy/bank-board-decisions/"
BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
ECB_YIELD_PAGE = (
    "https://www.ecb.europa.eu/stats/financial_markets_and_interest_rates/euro_area_yield_curves/"
    "html/index.en.html"
)
ECB_TARGET_PAGE = "https://www.ecb.europa.eu/paym/target/t2/html/index.en.html"
CNB_HOLIDAYS = (
    "https://www.cnb.cz/en/public/media-service/schedules-and-other-info/"
    "bank-holidays-in-the-czech-republic/"
)
CNB_CZEONIA_METHOD = (
    "https://www.cnb.cz/en/financial-markets/money-market/czeonia-reference-interest-rate/"
    "calculation-methodology-for-the-czeonia-reference-rate/"
)

BASIS_CALENDAR = "provider_calendar"
BASIS_RULE = "published_rule"
TZ_STATED = "stated_by_provider"
TZ_AGENCY = "agency_location"

# The zone of a published release time. ONE table, keyed by the publishing agency (the calendar the
# time is read from). A zone the provider itself states (BLS iCal TZID, "CET" on the ECB yield
# curve page) always wins and is stored as `stated_by_provider`; otherwise the zone is that of the
# agency's seat and is stored as `agency_location`, an assumption that a client must show as
# assumed. A zone is only ever attached to a release that has a time.
AGENCY_TIMEZONES: dict[str, str] = {
    "ecb": "Europe/Berlin",  # Frankfurt (same offsets as CET/CEST)
    "cnb": "Europe/Prague",
    "ecfin": "Europe/Brussels",  # DG ECFIN, European Commission
    "eurostat": "Europe/Luxembourg",
    "bis": "Europe/Zurich",  # Basel
    "bls": "America/New_York",
    "fred": "America/New_York",
    "bea": "America/New_York",
    "oecd": "Europe/Paris",
    "world_bank": "America/New_York",
    "imf": "America/New_York",
    "un_comtrade": "America/New_York",
}


def agency_timezone(agency: str) -> str:
    """The IANA zone of an agency's seat (`AGENCY_TIMEZONES`); KeyError for an unknown agency."""
    zone = AGENCY_TIMEZONES[agency]
    ZoneInfo(zone)  # must be a real IANA name
    return zone


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
# The ECB's monetary policy decision (Governing Council meeting, Day 2, press conference) behind
# the deposit facility rate.
ECB_DECISION_SERIES = ("ecb_deposit_rate",)
ECB_DECISION_KEY = "mgcgc:monetary-policy-decision"
# Eurostat euro-indicator event titles (the calendar carries no dataset code; the link of each
# title to the dataset was read from the euro-indicator release pages, which name their source
# datasets: docs/data/macro-release-calendar.md).
EUROSTAT_EVENTS: dict[str, tuple[str, ...]] = {
    "hicp": ("Inflation (HICP)",),
    "unemployment": ("Unemployment",),
    "industrial_production": ("Industrial production",),
    # namq_10_gdp is updated by all three: the preliminary flash (t+30, euro area, EU and member
    # states), the flash (t+45) and the full release (t+65).
    "gdp": (
        "Preliminary flash estimate GDP - EU and euro area",
        "Flash estimate GDP and employment - EU and euro area",
        "GDP main aggregates and employment",
    ),
}
# prc_hicp_minr is also updated by the flash estimate, but only for the euro area and its member
# states (checked on 2026-10-03: exactly these 25 areas already carried 2026-09 after the flash of
# 2026-10-02, Czechia did not). Those areas get the flash dates in addition to the full release.
EURO_AREA_GEOS = (
    "EA", "EA19", "EA20", "EA21", "AT", "BE", "BG", "CY", "DE", "EE", "EL", "ES", "FI", "FR",
    "HR", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PT", "SI", "SK",
)  # fmt: skip
EUROSTAT_GEO_EVENTS: dict[str, tuple[str, ...]] = {
    f"hicp:{geo}": ("Inflation (HICP)", "Flash estimate inflation euro area")
    for geo in EURO_AREA_GEOS
}
# BIS release category per SDMX dataflow ("Release" rows only, not "Revision" rows). A series is
# mapped through the dataflow it is really collected from, never through its label: the dataflow
# names are those of stats.bis.org/api/v2/structure/dataflow/BIS and the categories those of the
# release calendar (names in docs/data/macro-release-calendar.md).
BIS_FLOW_CATEGORY: dict[str, str] = {
    "WS_TC": "TOTAL_CREDIT",  # Total credit
    "WS_DSR": "DSR",  # Debt service ratios
    "WS_LBS_D_PUB": "LBS",  # Locational banking
    "WS_DEBT_SEC2_PUB": "IDS",  # International debt securities (BIS-compiled)
    "WS_GLI": "GLI",  # Global liquidity indicators
    "WS_LONG_CPI": "CPI",  # Consumer prices statistics
    "WS_SPP": "RPP",  # Selected residential property prices
}
# BIS release_type values of the rows that update a dataflow (default: "Release"). The RPP category
# carries the detailed dataset (WS_DPP: "Detailed residential release") and the selected one
# (WS_SPP: the three rows below), see data.bis.org/topics/RPP.
BIS_RELEASE_TYPES: dict[str, frozenset[str]] = {
    "WS_SPP": frozenset(
        {
            "Selected residential standard release",
            "Selected residential, partial update 1",
            "Selected residential, partial update 2",
        }
    ),
}
BIS_DEFAULT_RELEASE_TYPES = frozenset({"Release"})
# The shipped configuration (PublicDataSettings.sdmx): series key -> dataflow id.
DEFAULT_BIS_FLOWS: dict[str, str] = {
    "total_credit": "WS_TC",
    "debt_service": "WS_DSR",
    "banking_claims": "WS_LBS_D_PUB",
    "debt_securities": "WS_DEBT_SEC2_PUB",
    "global_liquidity": "WS_GLI",
    "property_prices": "WS_SPP",
}
ECFIN_SERIES = "sentiment"
ECFIN_KEY = "bcs-publication-dates"
CNB_SERIES = "cnb_repo_2w"
CNB_KEY = "bank-board-monetary-policy"


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
    basis: str = BASIS_CALENDAR  # provider_calendar | published_rule
    # The calendar (publishing agency) the time is read from, a key of AGENCY_TIMEZONES; defaults
    # to `provider` (DG ECFIN rows are stored under `eurostat`, BLS rows under `fred`).
    agency: str | None = None
    # A zone the provider itself states (an IANA name); wins over the agency's location.
    stated_timezone: str | None = None
    release_timezone: str | None = field(default=None, init=False)
    release_timezone_source: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.basis not in (BASIS_CALENDAR, BASIS_RULE):
            raise ValueError(f"unknown basis {self.basis!r}")
        if self.release_time is None:
            return  # a date-only release gets no zone
        if self.stated_timezone:
            ZoneInfo(self.stated_timezone)
            zone, source = self.stated_timezone, TZ_STATED
        else:
            zone, source = agency_timezone(self.agency or self.provider), TZ_AGENCY
        object.__setattr__(self, "release_timezone", zone)
        object.__setattr__(self, "release_timezone_source", source)

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
    # The provider value of the stored rows (the observation source of the series). It differs
    # from `provider` (the endpoint that was called) when one source serves another's series:
    # the DG ECFIN calendar dates the `eurostat` sentiment series.
    rows_provider: str = ""
    entries: list[CalendarEntry] = field(default_factory=list)
    covered: set[tuple[str, str]] = field(default_factory=set)
    drift: str | None = None
    raw: Any = None
    calls: int = 0
    status_code: int | None = None
    response_bytes: int = 0

    def __post_init__(self) -> None:
        self.rows_provider = self.rows_provider or self.provider


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


_ECB_MEETING = re.compile(r"<dt>\s*(\d{2})/(\d{2})/(\d{4})\s*</dt>\s*<dd>(.*?)</dd>", re.S)


def parse_ecb_decisions(page: str) -> list[dt.date]:
    """Dates of the monetary policy decisions on the Governing Council meeting calendar: Day 2 of
    a monetary policy meeting, "followed by press conference". The page gives dates only. Every
    Day 2 must follow a monetary policy meeting entry of the previous day, otherwise the layout
    changed (drift). (The live page has a Day 1 without its "(Day 1)" label, 2028-10-11.)"""
    if "definition-list" not in page or "<dl" not in page:
        raise CalendarDrift("ECB meetings: no definition list")
    entries: list[tuple[dt.date, str]] = []
    for day, month, year, body in _ECB_MEETING.findall(page):
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()
        try:
            entries.append((dt.date(int(year), int(month), int(day)), text))
        except ValueError as exc:
            raise CalendarDrift("ECB meetings: bad date") from exc
    if not entries:
        raise CalendarDrift("ECB meetings: no entries")
    day_one = {d for d, t in entries if "monetary policy meeting" in t and "(Day 2)" not in t}
    decisions: list[dt.date] = []
    for day, text in entries:
        if "monetary policy meeting" in text and "(Day 2)" in text:
            if "press conference" not in text or day - dt.timedelta(days=1) not in day_one:
                raise CalendarDrift(
                    "ECB meetings: Day 2 entry without its Day 1 or press conference"
                )
            decisions.append(day)
    if not decisions:
        raise CalendarDrift("ECB meetings: no monetary policy decision")
    return sorted(decisions)


_MONTHS = {
    name: number
    for number, name in enumerate(
        "January February March April May June July August September October November"
        " December".split(),
        1,
    )
}
_ECFIN_ROW = re.compile(
    r"^(?P<m1>[A-Z][a-z]+) (?P<d1>\d{1,2}) (?P<n1>[A-Z][a-z]+) (?P<y1>\d{4})"
    r" (?P<h1>\d{2})h(?P<i1>\d{2}) (?P<m2>[A-Z][a-z]+)(?: \d\))*"
    r" (?P<d2>\d{1,2}) (?P<n2>[A-Z][a-z]+) (?P<y2>\d{4})"
    r" (?P<h2>\d{2})h(?P<i2>\d{2})$"
)


def parse_ecfin_calendar(text: str, year: int) -> list[tuple[dt.date, dt.time]]:
    """(date, time) of the monthly business and consumer survey results (ESI, the right-hand
    column) from the text of the DG ECFIN "Publication dates <year>" PDF. Exactly twelve months in
    calendar order, a matching month in both columns, and the heading must be present."""
    if not isinstance(text, str) or f"Publication dates {year}" not in text:
        raise CalendarDrift("ECFIN calendar: heading not found")
    if "Business and consumer survey results" not in text:
        raise CalendarDrift("ECFIN calendar: results column not found")
    out: list[tuple[dt.date, dt.time]] = []
    for line in text.splitlines():
        row = _ECFIN_ROW.match(line.strip())
        if row is None:
            continue
        g = row.groupdict()
        if g["m1"] != g["m2"] or g["n1"] not in _MONTHS or g["n2"] not in _MONTHS:
            raise CalendarDrift("ECFIN calendar: month columns do not agree")
        try:
            out.append(
                (
                    dt.date(int(g["y2"]), _MONTHS[g["n2"]], int(g["d2"])),
                    dt.time(int(g["h2"]), int(g["i2"])),
                )
            )
        except ValueError as exc:
            raise CalendarDrift("ECFIN calendar: bad date or time") from exc
    if len(out) != 12:
        raise CalendarDrift(f"ECFIN calendar: expected 12 monthly releases, got {len(out)}")
    # January to November are released in their own month; December's results may come in January.
    if [(d.year, d.month) for d, _ in out[:11]] != [(year, m) for m in range(1, 12)]:
        raise CalendarDrift("ECFIN calendar: releases are not in calendar order")
    if (out[11][0].year, out[11][0].month) not in ((year, 12), (year + 1, 1)):
        raise CalendarDrift("ECFIN calendar: the December release is out of place")
    return out


def ecfin_pdf_text(content: bytes) -> str:
    """Text of the first page of the ECFIN calendar PDF (pypdf, already a dependency)."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages[:2])
    except (PyPdfError, ValueError, KeyError, OSError) as exc:
        raise CalendarDrift(f"ECFIN calendar: unreadable PDF ({type(exc).__name__})") from exc


_CNB_BLOCK = re.compile(
    r"<h2><strong>Bank Board meetings on monetary policy in (\d{4})</strong></h2>"
    r"(.*?)(?=</div>\s*</div>)",
    re.S,
)
_CNB_DAY = re.compile(r">\s*(\d{1,2}) ([A-Z][a-z]+)\s*(?:</div>|$)")
_CNB_TIME = re.compile(
    r"monetary policy decision is published after the end of monetary policy meeting,"
    r" at (\d{1,2}):(\d{2}) ([ap])\.m\."
)


def parse_cnb_bank_board(page: str) -> tuple[list[dt.date], dt.time | None]:
    """(meeting dates, decision time) from the ČNB "Bank Board decisions" page: the lists headed
    "Bank Board meetings on monetary policy in <year>" (one per year), strictly increasing within a
    year. The decision time (Czech local time) is read from the page's own sentence and is None
    when that sentence is absent."""
    blocks = _CNB_BLOCK.findall(page) if isinstance(page, str) else []
    if not blocks:
        raise CalendarDrift("CNB bank board: no meeting list")
    days: list[dt.date] = []
    for year, body in blocks:
        listed = _CNB_DAY.findall(body)
        if not listed:
            raise CalendarDrift("CNB bank board: empty meeting list")
        year_days: list[dt.date] = []
        for number, month in listed:
            if month not in _MONTHS:
                raise CalendarDrift(f"CNB bank board: unknown month {month!r}")
            try:
                year_days.append(dt.date(int(year), _MONTHS[month], int(number)))
            except ValueError as exc:
                raise CalendarDrift("CNB bank board: bad date") from exc
        if year_days != sorted(set(year_days)):
            raise CalendarDrift("CNB bank board: meetings not strictly increasing")
        days.extend(year_days)
    when = _CNB_TIME.search(re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))))
    decided: dt.time | None = None
    if when is not None:
        hour = int(when.group(1)) % 12 + (12 if when.group(3) == "p" else 0)
        try:
            decided = dt.time(hour, int(when.group(2)))
        except ValueError as exc:
            raise CalendarDrift("CNB bank board: bad decision time") from exc
    return sorted(days), decided


# ------------------------------------------------------------------- published rules (dates)
RULE_HORIZON_DAYS = 14  # a rule is turned into dates for this many days ahead, re-derived daily
ECB_YIELD_SERIES = "euro_area_10y_yield"
ECB_YIELD_KEY = "rule:target-business-day"
ECB_YIELD_TIME = dt.time(12, 0)
CNB_CZEONIA_SERIES = "czeonia"
CNB_CZEONIA_KEY = "rule:czech-working-day"


def easter_sunday(year: int) -> dt.date:
    """Gregorian Easter Sunday (anonymous / Meeus-Jones-Butcher algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def target_closing_days(year: int) -> set[dt.date]:
    """The days besides Saturdays and Sundays on which T2/TARGET is closed for euro settlement, as
    the ECB lists them: 1 January, Good Friday, Easter Monday, 1 May, 25 and 26 December."""
    easter = easter_sunday(year)
    return {
        dt.date(year, 1, 1),
        easter - dt.timedelta(days=2),
        easter + dt.timedelta(days=1),
        dt.date(year, 5, 1),
        dt.date(year, 12, 25),
        dt.date(year, 12, 26),
    }


def is_target_business_day(day: dt.date) -> bool:
    return day.weekday() < 5 and day not in target_closing_days(day.year)


def _plain(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).strip()


_TARGET_CLOSED = (
    "T2 is open from Monday to Friday and closed on Saturday and Sunday",
    "1 January (New Year's Day)",
    "Good Friday",
    "Easter Monday",
    "1 May (Labour Day)",
    "25 December (Christmas Day)",
    "26 December (Boxing Day)",
)


def verify_ecb_target_rule(yield_page: str, target_page: str) -> None:
    """The two ECB pages must still say what the rule was verified against (2026-10-03): the yield
    curve page "updated every TARGET business day at noon (12:00 CET)" and the T2 page's list of
    closing days. Anything else is drift (the rule is not applied to a changed statement)."""
    text = _plain(yield_page) if isinstance(yield_page, str) else ""
    if "updated every TARGET business day at noon (12:00 CET)" not in text:
        raise CalendarDrift("ECB yield curves: the TARGET business day rule is no longer stated")
    closed = _plain(target_page) if isinstance(target_page, str) else ""
    at = closed.find(_TARGET_CLOSED[0])
    if at < 0:
        raise CalendarDrift("ECB T2: opening hours statement not found")
    for phrase in _TARGET_CLOSED[1:]:
        found = closed.find(phrase, at)
        if found < 0:
            raise CalendarDrift(f"ECB T2: closing day {phrase!r} is no longer listed")
        at = found


def ecb_yield_rule_entries(today: dt.date) -> list[CalendarEntry]:
    """One entry per TARGET business day from `today` for RULE_HORIZON_DAYS days: 12:00 CET."""
    out: list[CalendarEntry] = []
    for offset in range(RULE_HORIZON_DAYS + 1):
        day = today + dt.timedelta(days=offset)
        if is_target_business_day(day):
            out.append(
                CalendarEntry(
                    "ecb",
                    ECB_YIELD_SERIES,
                    ECB_YIELD_KEY,
                    day,
                    ECB_YIELD_PAGE,
                    release_time=ECB_YIELD_TIME,
                    title="Euro area yield curves: updated every TARGET business day at noon "
                    f"(12:00 CET); TARGET closing days as listed by the ECB at {ECB_TARGET_PAGE}; "
                    "an update may be delayed or postponed to the next TARGET business day",
                    basis=BASIS_RULE,
                    agency="ecb",
                    stated_timezone="Europe/Berlin",  # the page states "CET"
                )
            )
    return out


_CNB_YEAR = re.compile(r"<div>\s*(\d{4})\s*</div>")
_CNB_HOLIDAY = re.compile(
    r"<p>\s*(\d{1,2}) ([A-Z][a-z]+) \u2013 [^<(]+\((Monday|Tuesday|Wednesday|Thursday|Friday|"
    r"Saturday|Sunday)\)\s*</p>"
)
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def parse_cnb_holidays(page: str) -> dict[int, set[dt.date]]:
    """Per year, the ČNB's list of Czech bank holidays: exactly 13 per year, each weekday as the
    page states it, the sliding-holiday sentence present, and Good Friday and Easter Monday equal to
    the dates computed from Easter (a changed layout or list is drift)."""
    if not isinstance(page, str) or "sliding holiday" not in page:
        raise CalendarDrift("CNB holidays: the no-sliding-holiday statement is not on the page")
    if "There are 13 bank holidays" not in page:
        raise CalendarDrift("CNB holidays: the count statement is not on the page")
    years = list(_CNB_YEAR.finditer(page))
    if not years:
        raise CalendarDrift("CNB holidays: no year sections")
    out: dict[int, set[dt.date]] = {}
    for position, match in enumerate(years):
        year = int(match.group(1))
        end = years[position + 1].start() if position + 1 < len(years) else len(page)
        days: set[dt.date] = set()
        for number, month, weekday in _CNB_HOLIDAY.findall(page[match.end() : end]):
            if month not in _MONTHS:
                raise CalendarDrift(f"CNB holidays: unknown month {month!r}")
            try:
                day = dt.date(year, _MONTHS[month], int(number))
            except ValueError as exc:
                raise CalendarDrift("CNB holidays: bad date") from exc
            if _WEEKDAYS[day.weekday()] != weekday:
                raise CalendarDrift(f"CNB holidays: {day} is not a {weekday}")
            days.add(day)
        if len(days) != 13:
            raise CalendarDrift(f"CNB holidays: {year} lists {len(days)} holidays, expected 13")
        easter = easter_sunday(year)
        if not {easter - dt.timedelta(days=2), easter + dt.timedelta(days=1)} <= days:
            raise CalendarDrift(f"CNB holidays: {year} Good Friday / Easter Monday disagree")
        out[year] = days
    return out


def verify_cnb_czeonia_rule(method_page: str) -> None:
    text = _plain(method_page) if isinstance(method_page, str) else ""
    if (
        "on day D are published on the immediately following working day, D+1" not in text
        or "usually by 9.30 a.m. local time" not in text
    ):
        raise CalendarDrift("CNB CZEONIA: the D+1 working day publication rule is not stated")


def cnb_czeonia_rule_entries(
    holidays: Mapping[int, set[dt.date]], today: dt.date
) -> list[CalendarEntry]:
    """One entry per Czech working day (not Saturday/Sunday, not a listed bank holiday) from `today`
    for RULE_HORIZON_DAYS days, only for years whose holiday list the ČNB has published. No time:
    the ČNB says "usually by 9.30 a.m." (an upper bound, not a release time)."""
    out: list[CalendarEntry] = []
    for offset in range(RULE_HORIZON_DAYS + 1):
        day = today + dt.timedelta(days=offset)
        if day.year not in holidays:
            break  # beyond the published holiday lists
        if day.weekday() < 5 and day not in holidays[day.year]:
            out.append(
                CalendarEntry(
                    "cnb",
                    CNB_CZEONIA_SERIES,
                    CNB_CZEONIA_KEY,
                    day,
                    CNB_CZEONIA_METHOD,
                    title="CZEONIA of the previous working day: published each Czech working day "
                    "(D+1), usually by 9.30 a.m. local time; working days = not Saturday/Sunday "
                    f"and not a bank holiday listed at {CNB_HOLIDAYS}",
                    basis=BASIS_RULE,
                )
            )
    return out


BLS_RELEASES: dict[str, tuple[str, str]] = {
    # FRED series id -> (our calendar key, the BLS event title)
    "CPIAUCSL": ("bls:cpi", "Consumer Price Index"),
    "UNRATE": ("bls:empsit", "Employment Situation"),
}
BLS_ZONE = "America/New_York"  # the iCal's TZID "US-Eastern" (EST/EDT rules in its VTIMEZONE)
_BLS_START = re.compile(r"^DTSTART;TZID=([^:\n]+):(\d{8})T(\d{6})\s*$", re.M)


def parse_bls_ics(text: str) -> list[tuple[str, dt.date, dt.time]]:
    """(title, date, local time) of every BLS release-schedule event. Every event must carry a
    `DTSTART;TZID=US-Eastern` (the only zone the calendar declares), else drift."""
    if not isinstance(text, str) or not text.lstrip().startswith("BEGIN:VCALENDAR"):
        raise CalendarDrift("BLS calendar: not an iCalendar document")
    if "Bureau of Labor Statistics" not in text[:400] or "TZID:US-Eastern" not in text:
        raise CalendarDrift("BLS calendar: unexpected PRODID or time zone")
    events: list[tuple[str, dt.date, dt.time]] = []
    for block in _ICS_EVENT.findall(_unfold(text)):
        summary = re.search(r"^SUMMARY[^:\n]*:(.*)$", block, re.M)
        start = _BLS_START.search(block)
        if summary is None or start is None or start.group(1) != "US-Eastern":
            raise CalendarDrift("BLS calendar: event without SUMMARY or a US-Eastern DTSTART")
        try:
            when = dt.datetime.strptime(start.group(2) + start.group(3), "%Y%m%d%H%M%S")
        except ValueError as exc:
            raise CalendarDrift("BLS calendar: bad DTSTART") from exc
        events.append((_ics_unescape(summary.group(1).strip()), when.date(), when.time()))
    if not events:
        raise CalendarDrift("BLS calendar: no events")
    return events


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
    for series_id, titles in {**EUROSTAT_EVENTS, **EUROSTAT_GEO_EVENTS}.items():
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
    """One request per statistical-calendar page, plus the Governing Council meeting calendar. A
    page that fails or drifts leaves the others usable: its series simply stay uncovered (no
    supersede, no new rows)."""
    fetch = CalendarFetch("ecb", AccessResult(AccessState.AVAILABLE))
    failure: AccessResult | None = None
    pages: list[tuple[str, str]] = [(page, ECB_CALENDAR.format(page=page)) for page in ECB_PAGES]
    pages.append(("mgcgc", ECB_MEETINGS))
    for page, url in pages:
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
            if page == "mgcgc":
                _ecb_decision_entries(fetch, parse_ecb_decisions(response.text), today, url)
                continue
            dataset, series_ids = ECB_PAGES[page]
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
    if failure is None or failure.state not in (AccessState.PAID_ONLY, AccessState.QUOTA_EXHAUSTED):
        rule_failure = await _ecb_yield_rule(http, fetch, before_call, today)
        failure = failure or rule_failure
    if failure is not None:
        fetch.access = failure
    return fetch


async def _ecb_yield_rule(
    http: httpx.AsyncClient, fetch: CalendarFetch, before_call: BeforeCall, today: dt.date
) -> AccessResult | None:
    """The yield-curve release rule ("every TARGET business day at noon") with the ECB's TARGET
    closing days: two page reads, both checked against the verified wording. None on success (the
    dates are in `fetch`), else the failure to report."""
    pages: list[str] = []
    for url in (ECB_YIELD_PAGE, ECB_TARGET_PAGE):
        sub = CalendarFetch("ecb", AccessResult(AccessState.AVAILABLE))
        response = await _get(http, sub, before_call, url)
        fetch.calls += sub.calls
        fetch.response_bytes += sub.response_bytes
        fetch.status_code = sub.status_code or fetch.status_code
        if response is None:
            return sub.access
        pages.append(response.text)
    try:
        verify_ecb_target_rule(pages[0], pages[1])
    except CalendarDrift as exc:
        drifted = _drift(
            CalendarFetch("ecb", fetch.access),
            exc,
            {"yield_curve": pages[0][:100_000], "t2": pages[1][:100_000]},
        )
        fetch.drift = fetch.drift or drifted.drift
        fetch.raw = fetch.raw or drifted.raw
        return drifted.access
    fetch.covered.add((ECB_YIELD_SERIES, ECB_YIELD_KEY))
    fetch.entries.extend(ecb_yield_rule_entries(today))
    return None


def _ecb_decision_entries(
    fetch: CalendarFetch, decisions: Sequence[dt.date], today: dt.date, url: str
) -> None:
    for series_id in ECB_DECISION_SERIES:
        fetch.covered.add((series_id, ECB_DECISION_KEY))
        fetch.entries.extend(
            CalendarEntry(
                "ecb",
                series_id,
                ECB_DECISION_KEY,
                day,
                url,
                title="Governing Council monetary policy meeting (Day 2), press conference",
            )
            for day in decisions
            if day >= today
        )


async def fetch_bis(
    http: httpx.AsyncClient,
    today: dt.date,
    before_call: BeforeCall,
    flows: Mapping[str, str] | None = None,
) -> CalendarFetch:
    """`flows`: our BIS series key -> the SDMX dataflow id it is collected from (the shipped
    defaults when omitted). A series whose dataflow has no known calendar category gets no entry."""
    fetch = CalendarFetch("bis", AccessResult(AccessState.AVAILABLE))
    chosen = DEFAULT_BIS_FLOWS if flows is None else flows
    series = {
        key: BIS_FLOW_CATEGORY[flow] for key, flow in chosen.items() if flow in BIS_FLOW_CATEGORY
    }
    types = {
        key: BIS_RELEASE_TYPES.get(flow, BIS_DEFAULT_RELEASE_TYPES) for key, flow in chosen.items()
    }
    response = await _get(http, fetch, before_call, BIS_API)
    if response is None:
        return fetch
    body = _json(response)
    try:
        rows = parse_bis_calendar(body)
    except CalendarDrift as exc:
        return _drift(fetch, exc, {"body": body})
    categories = {row[0] for row in rows}
    for series_id, category in series.items():
        if category not in categories:
            return _drift(
                fetch,
                CalendarDrift(f"BIS calendar: category {category} is missing"),
                {"body": body},
            )
        fetch.covered.add((series_id, category))
        for category_id, release_type, day, period in rows:
            if category_id == category and release_type in types[series_id] and day >= today:
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


async def fetch_ecfin(
    http: httpx.AsyncClient, today: dt.date, before_call: BeforeCall
) -> CalendarFetch:
    """The DG ECFIN publication-date PDF of this year and (when published) of the next. The rows
    belong to the `eurostat` sentiment series (`ei_bssi_m_r2`). A missing next-year PDF is normal
    (not published yet); a missing or unreadable current-year PDF is not."""
    fetch = CalendarFetch("ecfin", AccessResult(AccessState.AVAILABLE), rows_provider="eurostat")
    published: dict[int, list[tuple[dt.date, dt.time]]] = {}
    for year in (today.year, today.year + 1):
        url = ECFIN_PDF.format(year=year)
        sub = CalendarFetch("ecfin", AccessResult(AccessState.AVAILABLE))
        response = await _get(http, sub, before_call, url)
        fetch.calls += sub.calls
        fetch.response_bytes += sub.response_bytes
        fetch.status_code = sub.status_code or fetch.status_code
        if response is None:
            if year > today.year and sub.status_code in (301, 302, 404):
                # Next year's calendar is not out yet: the server answers 404, or redirects to the
                # surveys home page (observed 2026-10-03 for the 2027 file).
                continue
            fetch.access = sub.access
            return fetch
        try:
            published[year] = parse_ecfin_calendar(ecfin_pdf_text(response.content), year)
        except CalendarDrift as exc:
            return _drift(fetch, exc, {"source": url, "text": ecfin_pdf_text_safe(response)})
    if today.year not in published:
        fetch.access = AccessResult(AccessState.NOT_COVERED, "no calendar PDF for this year")
        return fetch
    fetch.covered.add((ECFIN_SERIES, ECFIN_KEY))
    for year, rows in published.items():
        for day, time in rows:
            # December's results may be dated January of the next year: that date comes from the
            # next year's PDF once it exists.
            if day.year != year and year + 1 in published:
                continue
            if day >= today:
                fetch.entries.append(
                    CalendarEntry(
                        "eurostat",
                        ECFIN_SERIES,
                        ECFIN_KEY,
                        day,
                        ECFIN_PDF.format(year=year),
                        release_time=time,
                        title="Business and consumer survey results (ESI)",
                        agency="ecfin",
                    )
                )
    return fetch


def ecfin_pdf_text_safe(response: httpx.Response) -> str:
    try:
        return ecfin_pdf_text(response.content)[:50_000]
    except CalendarDrift:
        return ""


async def fetch_cnb(
    http: httpx.AsyncClient, today: dt.date, before_call: BeforeCall
) -> CalendarFetch:
    """The ČNB Bank Board monetary-policy meeting dates (the repo-rate decision days) and the
    CZEONIA publication rule (every Czech working day, from the ČNB's bank holiday list). A page
    that fails or drifts leaves the other usable."""
    fetch = CalendarFetch("cnb", AccessResult(AccessState.AVAILABLE))
    failure: AccessResult | None = None

    async def read(url: str) -> httpx.Response | None:
        nonlocal failure
        sub = CalendarFetch("cnb", AccessResult(AccessState.AVAILABLE))
        response = await _get(http, sub, before_call, url)
        fetch.calls += sub.calls
        fetch.response_bytes += sub.response_bytes
        fetch.status_code = sub.status_code or fetch.status_code
        if response is None:
            failure = failure or sub.access
        return response

    def drifted(exc: CalendarDrift, raw: Any) -> None:
        nonlocal failure
        marked = _drift(CalendarFetch("cnb", fetch.access), exc, raw)
        failure = failure or marked.access
        fetch.drift = fetch.drift or marked.drift
        fetch.raw = fetch.raw or marked.raw

    response = await read(CNB_BANK_BOARD)
    if response is not None:
        try:
            days, decided = parse_cnb_bank_board(response.text)
        except CalendarDrift as exc:
            drifted(exc, {"raw_text": response.text[:200_000]})
        else:
            fetch.covered.add((CNB_SERIES, CNB_KEY))
            fetch.entries.extend(
                CalendarEntry(
                    "cnb",
                    CNB_SERIES,
                    CNB_KEY,
                    day,
                    CNB_BANK_BOARD,
                    release_time=decided,
                    title="Bank Board monetary policy meeting (rate decision)",
                )
                for day in days
                if day >= today
            )
    if failure is None or failure.state not in (AccessState.PAID_ONLY, AccessState.QUOTA_EXHAUSTED):
        holidays_page = await read(CNB_HOLIDAYS)
        method_page = await read(CNB_CZEONIA_METHOD) if holidays_page is not None else None
        if holidays_page is not None and method_page is not None:
            try:
                verify_cnb_czeonia_rule(method_page.text)
                holidays = parse_cnb_holidays(holidays_page.text)
            except CalendarDrift as exc:
                drifted(
                    exc,
                    {
                        "holidays": holidays_page.text[:100_000],
                        "methodology": method_page.text[:100_000],
                    },
                )
            else:
                fetch.covered.add((CNB_CZEONIA_SERIES, CNB_CZEONIA_KEY))
                fetch.entries.extend(cnb_czeonia_rule_entries(holidays, today))
    if failure is not None:
        fetch.access = failure
    return fetch


async def fetch_bls(
    http: httpx.AsyncClient,
    today: dt.date,
    before_call: BeforeCall,
    fred_series: Mapping[str, str],
    user_agent: str | None,
) -> CalendarFetch:
    """The BLS release schedule (iCalendar) for the FRED series that BLS publishes (CPI, Employment
    Situation): date and 08:30 local time, `TZID=US-Eastern`. The rows are stored under provider
    `fred` (the observation source of the series) with calendar key `bls:...`, and the read path
    prefers them over FRED's date-only rows. BLS answers 403 without a User-Agent that carries a
    contact address, so no request is made without one."""
    fetch = CalendarFetch("bls", AccessResult(AccessState.AVAILABLE), rows_provider="fred")
    if not user_agent or "@" not in user_agent:
        fetch.access = AccessResult(
            AccessState.ACCESS_UNVERIFIED,
            "BLS_USER_AGENT (or SEC_USER_AGENT) with a contact address is not configured: "
            "BLS refuses requests without one",
        )
        return fetch
    mapped = {key: BLS_RELEASES[fid] for key, fid in fred_series.items() if fid in BLS_RELEASES}
    response = await _get(http, fetch, before_call, BLS_ICS, headers={"User-Agent": user_agent})
    if response is None:
        return fetch
    try:
        events = parse_bls_ics(response.text)
    except CalendarDrift as exc:
        return _drift(fetch, exc, {"raw_text": response.text[:200_000]})
    by_title: dict[str, list[tuple[dt.date, dt.time]]] = {}
    for title, day, when in events:
        by_title.setdefault(title, []).append((day, when))
    for series_id, (calendar_key, title) in mapped.items():
        if title not in by_title:
            return _drift(
                fetch,
                CalendarDrift(f"BLS calendar: event {title!r} is missing"),
                {"raw_text": response.text[:200_000]},
            )
        fetch.covered.add((series_id, calendar_key))
        for day, when in sorted(set(by_title[title])):
            if day >= today:
                fetch.entries.append(
                    CalendarEntry(
                        "fred",
                        series_id,
                        calendar_key,
                        day,
                        BLS_ICS,
                        release_time=when,
                        title=f"BLS release: {title}",
                        agency="bls",
                        stated_timezone=BLS_ZONE,
                    )
                )
    return fetch


def series_with_calendar(provider: str, keys: Sequence[str]) -> list[str]:
    """Which of our series keys a provider's mapping covers (the rest has no calendar)."""
    known: set[str]
    if provider == "ecb":
        known = (
            {s for _, (_, ids) in ECB_PAGES.items() for s in ids}
            | set(ECB_DECISION_SERIES)
            | {ECB_YIELD_SERIES}
        )
    elif provider == "eurostat":
        known = set(EUROSTAT_EVENTS) | set(EUROSTAT_GEO_EVENTS) | {ECFIN_SERIES}
    elif provider == "bis":
        known = {k for k, flow in DEFAULT_BIS_FLOWS.items() if flow in BIS_FLOW_CATEGORY}
    elif provider == "cnb":
        known = {CNB_SERIES, CNB_CZEONIA_SERIES}
    else:
        known = set(keys)  # FRED: any configured series has a release
    return [k for k in keys if k in known]
