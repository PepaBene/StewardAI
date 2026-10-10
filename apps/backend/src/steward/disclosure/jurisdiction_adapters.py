"""P2.6: pure discovery adapters for EDINET (Japan) and SEDAR+ (Canada).

`ADAPTER_CONTRACTS` in `financials.py` states what each adapter must provide; these functions
turn one service response into `DiscoveredDocument`s so discovery can be tested offline. They
only discover documents: no fact extraction, no download, no fetch.

Rules:
  * `known_at` is never earlier than the stated filing time. It is the filing time, or the time
    the caller observed the row when that is later (`observed_at`), so a row cannot be known
    before it was filed even if the service stamps a future time.
  * EDINET stamps `submitDateTime` in Japan Standard Time (UTC+9, no daylight saving); it is
    converted to UTC.
  * A row that cannot be dated, has no document id, or is withdrawn is returned in `skipped`
    with a reason, never dropped and never given a guessed time.

UNVERIFIED against the live services. EDINET: the field names (`docID`, `formCode`,
`docTypeCode`, `edinetCode`, `issuerEdinetCode`, `submitDateTime`, `withdrawalStatus`, the
`metadata.status` envelope) follow the EDINET API v2 specification as in `sources.edinet`; the
`docTypeCode` labels below are from memory. SEDAR+: access has not been shown (the registry
records no stable document id, no incremental discovery and no export), so the row shape here
is an ASSUMED contract (`document_id`, `profile_number`, `issuer_name`, `document_type`,
`filing_date`, `filing_time`, `period_end`, `url`) to be replaced by what a recorded access test
shows. A SEDAR+ row without a document id is skipped, not given a made-up one. A SEDAR+ filing
time without an offset has no stated zone, so it is read in UTC-8, the most westerly Canadian
standard offset: that can only make `known_at` later than the true moment, never earlier. A
date without a time is known at the end of that day in the same offset.
"""

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from steward.sources.edinet import JST, document_url
from steward.sources.free_market import FreeSourceError

# The most westerly Canadian standard offset; see the module docstring.
CANADA_LATEST = dt.timezone(dt.timedelta(hours=-8))
# EDINET document-type codes whose label is relied on here (from memory, unverified).
EDINET_TYPE_LABELS = {
    "120": "securities report",
    "130": "amended securities report",
    "140": "quarterly report",
    "160": "semi-annual report",
    "350": "large shareholding report",
    "360": "amended large shareholding report",
}


@dataclass(frozen=True)
class DiscoveredDocument:
    jurisdiction: Literal["JP", "CA"]
    source: Literal["edinet", "sedarplus"]
    doc_id: str
    form_code: str | None
    form_label: str | None
    issuer_code: str | None  # EDINET code, or SEDAR+ profile number
    issuer_name: str | None
    filer_code: str | None  # EDINET filer; differs from the issuer on a large-holding report
    filed_at: dt.datetime | None  # UTC; None when only the filing date is stated
    filed_precision: Literal["minute", "day"]
    known_at: dt.datetime  # never earlier than the filing time
    period_end: dt.date | None
    url: str | None


@dataclass(frozen=True)
class SkippedDocument:
    doc_id: str | None
    reason: str  # no_doc_id, withdrawn, no_filing_time, not_a_row
    detail: str


@dataclass(frozen=True)
class AdapterDiscovery:
    discovered: list[DiscoveredDocument] = field(default_factory=list)
    skipped: list[SkippedDocument] = field(default_factory=list)


def _text(value: object) -> str | None:
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else None


def _date(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _known(filed: dt.datetime, observed_at: dt.datetime | None) -> dt.datetime:
    return filed if observed_at is None else max(filed, observed_at)


def _jst_moment(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=JST).astimezone(dt.UTC)
        except ValueError:
            continue
    return None


def edinet_documents(
    payload: object,
    wanted_codes: set[str] | None = None,
    observed_at: dt.datetime | None = None,
) -> AdapterDiscovery:
    """Documents of one EDINET `documents.json?type=2` response (one submission day).

    `wanted_codes` keeps rows whose filer or issuer EDINET code is in the set (all rows when
    None). A day with no list (envelope status 404, no `results`) is empty; a body that is not
    the envelope, or whose status is not 200/404, raises."""
    if not isinstance(payload, dict) or not isinstance(payload.get("metadata"), dict):
        raise FreeSourceError("EDINET document-list schema changed")
    status = str(payload["metadata"].get("status"))
    if status not in {"200", "404"}:
        raise FreeSourceError(f"EDINET envelope status {status}")
    results = payload.get("results")
    if results is None:
        return AdapterDiscovery()
    if not isinstance(results, list):
        raise FreeSourceError("EDINET document-list schema changed")
    out = AdapterDiscovery()
    for row in results:
        if not isinstance(row, dict):
            out.skipped.append(SkippedDocument(None, "not_a_row", "result entry is not an object"))
            continue
        filer = _text(row.get("edinetCode"))
        issuer = _text(row.get("issuerEdinetCode")) or filer
        if wanted_codes is not None and not {filer, issuer} & wanted_codes:
            continue
        doc_id = _text(row.get("docID"))
        if doc_id is None:
            out.skipped.append(SkippedDocument(None, "no_doc_id", "row has no docID"))
            continue
        if str(row.get("withdrawalStatus")) == "1":
            out.skipped.append(SkippedDocument(doc_id, "withdrawn", "the filing was withdrawn"))
            continue
        filed = _jst_moment(row.get("submitDateTime"))
        if filed is None:
            out.skipped.append(
                SkippedDocument(
                    doc_id, "no_filing_time", f"submitDateTime {row.get('submitDateTime')!r}"
                )
            )
            continue
        type_code = _text(row.get("docTypeCode"))
        out.discovered.append(
            DiscoveredDocument(
                "JP",
                "edinet",
                doc_id,
                _text(row.get("formCode")),
                EDINET_TYPE_LABELS.get(type_code or ""),
                issuer,
                _text(row.get("filerName")),
                filer,
                filed,
                "minute",
                _known(filed, observed_at),
                _date(row.get("periodEnd")),
                document_url(doc_id),
            )
        )
    return out


def _sedar_moment(day: object, clock: object) -> tuple[dt.datetime | None, dt.datetime | None]:
    """(filed_at, end of the filing day) in UTC for an assumed SEDAR+ date and optional time."""
    date = _date(day) if day not in (None, "") else None
    if date is None:
        return None, None
    end_of_day = dt.datetime.combine(date, dt.time(23, 59, 59), tzinfo=CANADA_LATEST)
    end_utc = end_of_day.astimezone(dt.UTC)
    if not isinstance(clock, str) or not clock.strip():
        return None, end_utc
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            when = dt.datetime.strptime(clock.strip(), fmt).time()
        except ValueError:
            continue
        return dt.datetime.combine(date, when, tzinfo=CANADA_LATEST).astimezone(dt.UTC), end_utc
    return None, end_utc  # an unreadable time falls back to the day, the later reading


def _aware_moment(value: object) -> dt.datetime | None:
    """An ISO time that states its own offset, in UTC; anything else is None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.astimezone(dt.UTC) if parsed.tzinfo else None


# Zone names the SEDAR+ results table prints, as offsets in hours.
_CANADA_ZONES = {
    "Newfoundland Daylight Time": -2.5,
    "Newfoundland Standard Time": -3.5,
    "Atlantic Daylight Time": -3,
    "Atlantic Standard Time": -4,
    "Eastern Daylight Time": -4,
    "Eastern Standard Time": -5,
    "Central Daylight Time": -5,
    "Central Standard Time": -6,
    "Mountain Daylight Time": -6,
    "Mountain Standard Time": -7,
    "Pacific Daylight Time": -7,
    "Pacific Standard Time": -8,
}
_SEDAR_STAMP = re.compile(r"(\w+ \d{1,2} \d{4}) at (\d{1,2}:\d{2}:\d{2}) ([A-Za-z ]+?)\s*$")
_SEDAR_PROFILE = re.compile(r"\((\d{6,})\)\s*$")


def sedarplus_page_rows(captured: object) -> list[dict[str, Any]]:
    """Contract rows from the rows of the SEDAR+ document-search results table, as read from a
    signed-in browser page (checked against the live page on 2026-10-08). A row carries the
    profile as "Name (000006073)", the document name, a submitted stamp such as
    "October 08 2026 at 03:21:37 Eastern Daylight Time", the principal jurisdiction and a size.
    The page gives no document id (its links are session tokens), so the id is derived from the
    stated profile, time, name and size, with a counter for rows identical in all four, and is
    marked as derived. A row whose time or profile cannot be read keeps no id and is skipped
    downstream rather than guessed."""
    if not isinstance(captured, list):
        raise ValueError("captured SEDAR+ rows are not a list")
    out: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for item in captured:
        if not isinstance(item, dict):
            out.append({})
            continue
        profile_text = _text(item.get("profile")) or ""
        match = _SEDAR_PROFILE.search(profile_text)
        stamp = _SEDAR_STAMP.search(_text(item.get("submitted")) or "")
        filed: dt.datetime | None = None
        if stamp and stamp.group(3) in _CANADA_ZONES:
            try:
                local = dt.datetime.strptime(
                    f"{stamp.group(1)} {stamp.group(2)}", "%B %d %Y %H:%M:%S"
                )
            except ValueError:
                local = None
            if local:
                zone = dt.timezone(dt.timedelta(hours=_CANADA_ZONES[stamp.group(3)]))
                filed = local.replace(tzinfo=zone).astimezone(dt.UTC)
        name = _text(item.get("document"))
        row: dict[str, Any] = {
            "profile_number": match.group(1) if match else None,
            "issuer_name": _text(profile_text[: match.start()].split(" / ")[0]) if match else None,
            "document_type": name,
            "jurisdiction": _text(item.get("jurisdiction")),
            "size": _text(item.get("size")),
            "id_basis": "derived",
        }
        if filed is not None and match and name:
            row["filed_at"] = filed.isoformat()
            base = "|".join((match.group(1), filed.isoformat(), name, row["size"] or ""))
            digest = hashlib.sha256(base.encode()).hexdigest()[:12]
            n = seen[digest] = seen.get(digest, -1) + 1
            row["document_id"] = f"{match.group(1)}:{filed:%Y%m%dT%H%M%SZ}:{digest}:{n}"
        out.append(row)
    return out


def sedarplus_documents(rows: object, observed_at: dt.datetime | None = None) -> AdapterDiscovery:
    """Documents of an assumed SEDAR+ filing-list export (see the module docstring: the row
    shape is not verified). A row needs a document id and a filing date; without a time the
    document is known at the end of its filing day."""
    if not isinstance(rows, list):
        raise ValueError("SEDAR+ filing list is not a list of rows")
    out = AdapterDiscovery()
    for row in rows:
        if not isinstance(row, dict):
            out.skipped.append(SkippedDocument(None, "not_a_row", "list entry is not an object"))
            continue
        doc_id = _text(row.get("document_id"))
        if doc_id is None:
            out.skipped.append(
                SkippedDocument(None, "no_doc_id", "row has no stable document id; none invented")
            )
            continue
        filed, end_of_day = _sedar_moment(row.get("filing_date"), row.get("filing_time"))
        stated = _aware_moment(row.get("filed_at"))
        if stated is not None:  # a time that carries its own zone beats the assumed offset
            filed, end_of_day = stated, stated
        if end_of_day is None:
            out.skipped.append(
                SkippedDocument(doc_id, "no_filing_time", f"filing_date {row.get('filing_date')!r}")
            )
            continue
        out.discovered.append(
            DiscoveredDocument(
                "CA",
                "sedarplus",
                doc_id,
                _text(row.get("document_type")),
                _text(row.get("document_type")),
                _text(row.get("profile_number")),
                _text(row.get("issuer_name")),
                None,
                filed,
                "minute" if filed else "day",
                _known(filed or end_of_day, observed_at),
                _date(row.get("period_end")) if row.get("period_end") else None,
                _text(row.get("url")),
            )
        )
    return out


def contract_summary(discovery: AdapterDiscovery) -> dict[str, Any]:
    """Counts by skip reason, so a coverage report shows what was not read and why."""
    reasons: dict[str, int] = {}
    for skipped in discovery.skipped:
        reasons[skipped.reason] = reasons.get(skipped.reason, 0) + 1
    return {"discovered": len(discovery.discovered), "skipped": reasons}
