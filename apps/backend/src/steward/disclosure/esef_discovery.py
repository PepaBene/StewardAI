"""P2.5: discover ESEF report packages from a filings.xbrl.org filing-index response.

Pure: the index JSON is passed in (see `sources.esef.esef_filings` for the fetch), so the rules
are testable offline. For each entry the result is either a `DiscoveredEsefFiling` or a
`SkippedEsefEntry` that names the reason; nothing is dropped silently.

Rules:
  * `known_at` is the time the index says the package was added or processed, the later of the
    two when both are given (a package cannot be used before it was processed). It is never the
    period end, which is the date the figures describe and can be months earlier. An entry with
    no usable added/processed time is skipped, not given a guessed time.
  * An entry with processing errors is skipped and reported with the reason, not discovered, so
    a package that failed validation is never read as if it were a clean report.
  * A package time earlier than the end of its own period is contradictory and is skipped.

UNVERIFIED against the live service: the response shape (a JSON:API list with `data[].id`,
`attributes.period_end`, `date_added`, `processed`, `package_url`, `json_url`, `report_url`,
`error_count`, `errors`, `country`, and the entity LEI in `relationships.entity.data.id`) is
written from memory of the public filings.xbrl.org API and the fixtures in the tests follow it;
no live response was compared. Timestamps without an offset are read as UTC, also unverified.
"""

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from steward.sources.esef import FILINGS_XBRL_URL


@dataclass(frozen=True)
class DiscoveredEsefFiling:
    filing_id: str
    lei: str
    period_end: dt.date
    package_url: str
    json_url: str | None
    report_url: str | None
    country: str | None
    known_at: dt.datetime


@dataclass(frozen=True)
class SkippedEsefEntry:
    filing_id: str | None
    lei: str | None
    reason: str  # a stable code, see REASONS
    detail: str


@dataclass(frozen=True)
class EsefDiscovery:
    discovered: list[DiscoveredEsefFiling] = field(default_factory=list)
    skipped: list[SkippedEsefEntry] = field(default_factory=list)


REASONS = (
    "processing_errors",
    "no_lei",
    "no_filing_id",
    "bad_period_end",
    "no_package_url",
    "no_added_time",
    "added_before_period_end",
    "duplicate",
)


def _moment(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _absolute(url: object) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    return url if url.startswith("http") else FILINGS_XBRL_URL + url


def _errors(attrs: dict[str, Any]) -> str | None:
    """The processing errors of an entry as one line, or None when it is clean."""
    listed = attrs.get("errors")
    parts: list[str] = []
    if isinstance(listed, list):
        for item in listed:
            text = item.get("message") or item.get("code") if isinstance(item, dict) else item
            if text:
                parts.append(" ".join(str(text).split()))
    count = attrs.get("error_count")
    if isinstance(count, int) and count > 0 and not parts:
        parts.append(f"{count} error(s) reported")
    return "; ".join(parts) or None


def discover_esef(index: object, since: dt.datetime | None = None) -> EsefDiscovery:
    """Filings in a filing-index response, newest first. `since` keeps only entries known
    after that time (incremental polling); older ones are neither discovered nor skipped.

    Raises ValueError when the body is not a list of entries, rather than reading it as empty."""
    if not isinstance(index, dict) or not isinstance(index.get("data"), list):
        raise ValueError("filing index has no data list")
    out = EsefDiscovery()
    seen: set[str] = set()
    for row in index["data"]:
        attrs = row.get("attributes") if isinstance(row, dict) else None
        if not isinstance(row, dict) or not isinstance(attrs, dict):
            out.skipped.append(
                SkippedEsefEntry(None, None, "no_filing_id", "entry is not an object")
            )
            continue
        rid = row.get("id")
        filing_id = str(rid) if rid not in (None, "") else None
        entity = ((row.get("relationships") or {}).get("entity") or {}).get("data") or {}
        lei = entity.get("id") if isinstance(entity.get("id"), str) else attrs.get("lei")
        lei = lei if isinstance(lei, str) and lei else None

        def skip(
            reason: str, detail: str, _id: str | None = filing_id, _lei: str | None = lei
        ) -> None:
            out.skipped.append(SkippedEsefEntry(_id, _lei, reason, detail))

        if filing_id is None:
            skip("no_filing_id", "entry has no id")
            continue
        if filing_id in seen:
            skip("duplicate", f"filing {filing_id} appears more than once in the index")
            continue
        seen.add(filing_id)
        if problem := _errors(attrs):
            skip("processing_errors", problem)
            continue
        if lei is None:
            skip("no_lei", "the entry names no entity LEI")
            continue
        try:
            period_end = dt.date.fromisoformat(str(attrs.get("period_end"))[:10])
        except ValueError:
            skip("bad_period_end", f"period_end {attrs.get('period_end')!r} is not a date")
            continue
        package = _absolute(attrs.get("package_url"))
        if package is None:
            skip("no_package_url", "the entry has no package URL")
            continue
        times = [
            t for t in (_moment(attrs.get("date_added")), _moment(attrs.get("processed"))) if t
        ]
        if not times:
            skip("no_added_time", "neither date_added nor processed is a usable time")
            continue
        known_at = max(times)
        period_close = dt.datetime.combine(period_end, dt.time.max, tzinfo=dt.UTC)
        if known_at < period_close - dt.timedelta(days=1):
            skip(
                "added_before_period_end",
                f"added {known_at.isoformat()} is before its period end {period_end}",
            )
            continue
        if since is not None and known_at <= since:
            continue
        out.discovered.append(
            DiscoveredEsefFiling(
                filing_id,
                lei,
                period_end,
                package,
                _absolute(attrs.get("json_url")),
                _absolute(attrs.get("report_url")),
                attrs.get("country") if isinstance(attrs.get("country"), str) else None,
                known_at,
            )
        )
    out.discovered.sort(key=lambda f: (f.known_at, f.filing_id), reverse=True)
    return out
