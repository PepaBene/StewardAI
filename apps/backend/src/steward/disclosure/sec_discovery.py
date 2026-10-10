"""SEC filing discovery (plan P1.3): submissions JSON and archive `index.json` -> filing records.

Pure functions over already-fetched JSON, so they run offline against fixtures. The network side
is `DisclosureFetcher` with source `sec.submissions` / `sec.archive`.

Dates are kept apart: `accepted_at` (EDGAR acceptance, the moment of public dissemination),
`filed_at` (the filing date), `published_at` (EDGAR has no separate publication time: left null
and explained in `date_notes`), `first_seen_at`/`retrieved_at` (ours, set at ingest).
`known_at` is the acceptance time; when only a filing date exists it is the *end* of that day, so
a replay never sees a filing earlier than it could have been seen.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

_NEW_YORK = ZoneInfo("America/New_York")
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{name}"
_XBRL_SIDE = re.compile(
    r"(_cal|_def|_lab|_pre)\.xml$|\.xsd$|^R\d+\.htm$|^FilingSummary\.xml$|\.paper$|\.json$"
    r"|-index(-headers)?\.html?$|\.txt$|^Financial_Report\.xlsx$|\.zip$",
    re.IGNORECASE,
)
_INSTANCE = re.compile(r"(_htm\.xml|\.xml)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DiscoveredFiling:
    cik: str
    accession: str
    form: str
    filed_at: dt.date
    accepted_at: dt.datetime | None
    report_date: dt.date | None
    primary_document: str
    items: str
    known_at: dt.datetime
    date_notes: dict[str, str]
    amends_accession: str | None = None

    @property
    def folder(self) -> str:
        return self.accession.replace("-", "")

    def url(self, name: str) -> str:
        return ARCHIVE.format(cik=int(self.cik), folder=self.folder, name=name)

    @property
    def primary_url(self) -> str:
        return self.url(self.primary_document)

    @property
    def base_form(self) -> str:
        return self.form.removesuffix("/A")

    @property
    def is_amendment(self) -> bool:
        return self.form.endswith("/A")


def known_at_for(
    accepted_at: dt.datetime | None, filed_at: dt.date
) -> tuple[dt.datetime, dict[str, str]]:
    notes = {"published_at": "EDGAR publishes no separate publication time; acceptance time used"}
    if accepted_at is not None:
        return accepted_at, notes
    notes["accepted_at"] = "no acceptance time in the submissions feed; end of filing day used"
    return dt.datetime.combine(filed_at, dt.time(23, 59, 59), dt.UTC), notes


def _accepted(value: str) -> dt.datetime | None:
    """The feed's `acceptanceDateTime` as a true UTC instant.

    The feed's `Z` is not UTC. Checked against the `ACCEPTANCE-DATETIME` header (Eastern wall
    time) of Apple's filings 0000320193-23-000106 (header 2023-11-02 18:08:27, feed
    2023-11-03T02:08:27Z), -24-000006 (2024-02-01 18:03:38, feed 2024-02-02T04:03:38Z) and
    -24-000081 (2024-08-01 18:03:34, feed 2024-08-02T02:03:34Z): the feed is the Eastern wall time
    shifted by the New York offset twice. Reading the feed instant in New York time and taking
    that wall clock as UTC gives the true instant in all three."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(_NEW_YORK).replace(tzinfo=dt.UTC)


def _date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def discover_filings(
    cik: str,
    submissions: dict[str, Any],
    *,
    forms: set[str] | None = None,
    since: dt.date | None = None,
) -> list[DiscoveredFiling]:
    """Filings in a submissions document (or an older `filings.files` page, passed as
    {"filings": {"recent": page}}), oldest first, with amendment links resolved. A row that
    cannot be read is skipped, never guessed."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms_col = recent.get("form") or []

    def col(name: str, i: int) -> str:
        values = recent.get(name) or []
        return str(values[i]) if i < len(values) and values[i] is not None else ""

    out: list[DiscoveredFiling] = []
    for i, form in enumerate(forms_col):
        accession = col("accessionNumber", i)
        filed = _date(col("filingDate", i))
        primary = col("primaryDocument", i)
        if not accession or filed is None or not primary:
            continue
        if forms is not None and form not in forms and form.removesuffix("/A") not in forms:
            continue
        if since is not None and filed < since:
            continue
        accepted = _accepted(col("acceptanceDateTime", i))
        known, notes = known_at_for(accepted, filed)
        out.append(
            DiscoveredFiling(
                cik=str(int(cik)).zfill(10),
                accession=accession,
                form=str(form),
                filed_at=filed,
                accepted_at=accepted,
                report_date=_date(col("reportDate", i)),
                primary_document=primary,
                items=col("items", i),
                known_at=known,
                date_notes=notes,
            )
        )
    out.sort(key=lambda f: (f.known_at, f.accession))
    return link_amendments(out)


def link_amendments(filings: list[DiscoveredFiling]) -> list[DiscoveredFiling]:
    """Point each `/A` filing at the latest earlier filing of the same base form and period."""
    linked: list[DiscoveredFiling] = []
    for filing in filings:
        if not filing.is_amendment:
            linked.append(filing)
            continue
        candidates = [
            f
            for f in filings
            if f.base_form == filing.base_form
            and f.report_date == filing.report_date
            and f.known_at < filing.known_at
            and f.accession != filing.accession
        ]
        mine = set(filter(None, filing.items.split(",")))
        if mine:  # same-day 8-Ks: an 8-K/A belongs to the filing that reported the same items
            same_items = [c for c in candidates if mine & set(filter(None, c.items.split(",")))]
            candidates = same_items if same_items or filing.base_form != "8-K" else []
        target = max(candidates, key=lambda f: (f.known_at, f.accession), default=None)
        linked.append(
            DiscoveredFiling(
                **{
                    **{n: getattr(filing, n) for n in DiscoveredFiling.__slots__},
                    "amends_accession": target.accession if target else None,
                }
            )
        )
    return linked


def incremental(
    filings: list[DiscoveredFiling], cursor: dict[str, str] | None
) -> list[DiscoveredFiling]:
    """Filings newer than the stored cursor (`{"known_at": iso, "accession": acc}`)."""
    if not cursor or not cursor.get("known_at"):
        return filings
    last = dt.datetime.fromisoformat(cursor["known_at"])
    seen = cursor.get("accession", "")
    return [f for f in filings if (f.known_at, f.accession) > (last, seen)]


def next_cursor(filings: list[DiscoveredFiling], cursor: dict[str, str] | None) -> dict[str, str]:
    if not filings:
        return dict(cursor or {})
    newest = max(filings, key=lambda f: (f.known_at, f.accession))
    return {"known_at": newest.known_at.isoformat(), "accession": newest.accession}


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    name: str
    url: str
    role: str  # 'primary', 'exhibit', 'xbrl_instance', 'other'


def classify_archive(filing: DiscoveredFiling, index: dict[str, Any]) -> list[ArchiveFile]:
    """The files of a filing's archive folder (`index.json`) with the roles discovery needs: the
    primary document, candidate exhibits, and the XBRL instance. Unknown files are `other`."""
    items = (index.get("directory") or {}).get("item") or []
    out: list[ArchiveFile] = []
    for item in items:
        name = str(item.get("name") or "") if isinstance(item, dict) else ""
        if not name:
            continue
        if name == filing.primary_document:
            role = "primary"
        elif _XBRL_SIDE.search(name) and not _INSTANCE.search(name):
            role = "other"
        elif _INSTANCE.search(name) and name.lower().endswith("_htm.xml"):
            role = "xbrl_instance"
        elif re.search(r"\.(htm|html|pdf)$", name, re.IGNORECASE):
            role = "exhibit"
        else:
            role = "other"
        out.append(ArchiveFile(name, filing.url(name), role))
    return out
