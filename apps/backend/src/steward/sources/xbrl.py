"""XBRL facts outside SEC companyfacts: a filing's XBRL instance document (`*_htm.xml` on
EDGAR) and ESEF filings in xBRL-JSON from filings.xbrl.org.

- The instance is the filing's own machine-readable data. SEC companyfacts is built from it
  but can lag or silently miss a filing (TSMC's FY2025 20-F reached companyfacts only with
  its cover-page facts), so the valuation job falls back to the instance when companyfacts
  has no fact from the latest periodic report. Tier 1.
- filings.xbrl.org (`/api/entities/{lei}/filings`, no key) republishes ESEF annual and
  interim reports of EU issuers as xBRL-JSON. Coverage is uneven: Danish and Dutch issuers
  (Novo Nordisk's quarterly reports, ASML's annual) are current, German ones absent and the
  Czech collection stops at FY2020. Tier 1 data, republished by XBRL International.

Only non-dimensional facts are read (the face-statement figures); a fact with any axis
member is a breakdown and is skipped. Units are written the way companyfacts writes them:
"USD", "USD/shares", "shares".
"""

import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

FILINGS_XBRL_URL = "https://filings.xbrl.org"
ENTITY_FILINGS_URL = FILINGS_XBRL_URL + "/api/entities/{lei}/filings"

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
# Namespace URI fragments -> the prefixes companyfacts uses.
TAXONOMIES: tuple[tuple[str, str], ...] = (
    ("fasb.org/us-gaap", "us-gaap"),
    ("xbrl.ifrs.org", "ifrs-full"),
    ("xbrl.sec.gov/dei", "dei"),
)


class XbrlError(Exception):
    pass


@dataclass(frozen=True)
class XbrlFact:
    """One non-dimensional fact. `start` is None for an instant (balance-sheet) fact."""

    concept: str  # "ifrs-full:DilutedEarningsLossPerShare"
    start: dt.date | None
    end: dt.date
    value: Decimal
    unit: str  # "USD/shares"
    decimals: str | None
    # The fact as the source wrote it (attributes and value, or the xBRL-JSON object).
    raw: dict[str, Any] = field(repr=False, compare=False)


def _taxonomy(namespace: str) -> str | None:
    for fragment, prefix in TAXONOMIES:
        if fragment in namespace:
            return prefix
    return None


def _measure(text: str) -> str:
    """'iso4217:USD' -> 'USD', 'xbrli:shares' / 'shares' -> 'shares'."""
    return text.strip().rsplit(":", 1)[-1]


def parse_instance(content: bytes, concepts: set[str] | None = None) -> list[XbrlFact]:
    """Non-dimensional numeric facts of an XBRL instance, optionally only `concepts`.

    The document comes from sec.gov over HTTPS. Python's expat (2.4+) refuses the entity
    expansion attacks ElementTree was once open to, and instances declare no DTD."""
    try:
        root = ET.fromstring(content)  # noqa: S314 - see the docstring
    except ET.ParseError as exc:
        raise XbrlError(f"not an XBRL instance: {exc}") from exc

    periods: dict[str, tuple[dt.date | None, dt.date]] = {}
    for context in root.iter(f"{XBRLI}context"):
        if context.find(f".//{XBRLDI}explicitMember") is not None:
            continue
        if context.find(f".//{XBRLDI}typedMember") is not None:
            continue
        period = context.find(f"{XBRLI}period")
        if period is None:
            continue
        try:
            instant = period.findtext(f"{XBRLI}instant")
            if instant:
                periods[context.get("id", "")] = (None, dt.date.fromisoformat(instant.strip()))
                continue
            start_text = period.findtext(f"{XBRLI}startDate")
            end_text = period.findtext(f"{XBRLI}endDate")
            if start_text and end_text:
                periods[context.get("id", "")] = (
                    dt.date.fromisoformat(start_text.strip()),
                    dt.date.fromisoformat(end_text.strip()),
                )
        except ValueError:
            continue

    units: dict[str, str] = {}
    for unit in root.iter(f"{XBRLI}unit"):
        numerator = unit.find(f"{XBRLI}divide/{XBRLI}unitNumerator/{XBRLI}measure")
        denominator = unit.find(f"{XBRLI}divide/{XBRLI}unitDenominator/{XBRLI}measure")
        measure = unit.find(f"{XBRLI}measure")
        if numerator is not None and denominator is not None:
            units[unit.get("id", "")] = (
                f"{_measure(numerator.text or '')}/{_measure(denominator.text or '')}"
            )
        elif measure is not None:
            units[unit.get("id", "")] = _measure(measure.text or "")

    facts: list[XbrlFact] = []
    for element in root:
        tag = element.tag
        if not isinstance(tag, str) or not tag.startswith("{"):
            continue
        namespace, _, local = tag[1:].partition("}")
        prefix = _taxonomy(namespace)
        if prefix is None:
            continue
        concept = f"{prefix}:{local}"
        if concepts is not None and concept not in concepts:
            continue
        context_ref = element.get("contextRef")
        unit_ref = element.get("unitRef")
        if context_ref not in periods or unit_ref is None or unit_ref not in units:
            continue
        text = (element.text or "").strip()
        try:
            value = Decimal(text)
        except InvalidOperation:
            continue
        start, end = periods[context_ref]
        facts.append(
            XbrlFact(
                concept=concept,
                start=start,
                end=end,
                value=value,
                unit=units[unit_ref],
                decimals=element.get("decimals"),
                raw={
                    "concept": concept,
                    "contextRef": context_ref,
                    "unitRef": unit_ref,
                    "decimals": element.get("decimals"),
                    "value": text,
                    "period": {
                        "start": start.isoformat() if start else None,
                        "end": end.isoformat(),
                    },
                },
            )
        )
    return facts


# ---------------------------------------------------------------------------
# filings.xbrl.org (ESEF)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EsefFiling:
    id: str
    period_end: dt.date
    added: dt.datetime | None
    country: str | None
    json_url: str
    report_url: str | None
    raw: dict[str, Any] = field(repr=False, compare=False)


def parse_filings(data: dict[str, Any]) -> list[EsefFiling]:
    """An `/api/entities/{lei}/filings` page, newest period first."""
    out = []
    for row in data.get("data") or []:
        attrs = row.get("attributes") or {}
        json_url = attrs.get("json_url")
        period = attrs.get("period_end")
        if not json_url or not period:
            continue
        try:
            period_end = dt.date.fromisoformat(str(period)[:10])
        except ValueError:
            continue
        added = None
        if attrs.get("date_added"):
            try:
                added = dt.datetime.fromisoformat(str(attrs["date_added"])).replace(tzinfo=dt.UTC)
            except ValueError:
                added = None
        report = attrs.get("report_url")
        out.append(
            EsefFiling(
                id=str(row.get("id")),
                period_end=period_end,
                added=added,
                country=attrs.get("country"),
                json_url=FILINGS_XBRL_URL + str(json_url),
                report_url=FILINGS_XBRL_URL + str(report) if report else None,
                raw=row,
            )
        )
    out.sort(key=lambda f: (f.period_end, f.added or dt.datetime.min.replace(tzinfo=dt.UTC)))
    out.reverse()
    return out


async def esef_filings(http: httpx.AsyncClient, lei: str) -> list[EsefFiling]:
    """The issuer's ESEF filings on filings.xbrl.org, newest period first (empty when it
    has none)."""
    response = await http.get(
        ENTITY_FILINGS_URL.format(lei=lei),
        params={"sort": "-period_end", "page[size]": "20"},
        headers={"Accept": "application/vnd.api+json"},
    )
    if response.status_code == 404:
        return []
    if response.status_code != 200:
        raise XbrlError(f"filings.xbrl.org returned HTTP {response.status_code}")
    try:
        return parse_filings(response.json())
    except ValueError as exc:
        raise XbrlError(f"filings.xbrl.org sent no JSON: {exc}") from exc


async def esef_document(http: httpx.AsyncClient, filing: EsefFiling) -> dict[str, Any]:
    response = await http.get(filing.json_url)
    if response.status_code != 200:
        raise XbrlError(f"{filing.json_url} returned HTTP {response.status_code}")
    try:
        data: dict[str, Any] = response.json()
    except ValueError as exc:
        raise XbrlError(f"{filing.json_url} is not xBRL-JSON: {exc}") from exc
    return data


def _json_moment(text: str) -> dt.date:
    """xBRL-JSON writes period boundaries as datetimes; a midnight end is exclusive (the
    period "…/2026-07-01T00:00:00" ends on 30 June)."""
    moment = dt.datetime.fromisoformat(text)
    if moment.time() == dt.time(0, 0):
        return moment.date() - dt.timedelta(days=1)
    return moment.date()


def _json_unit(text: str) -> str:
    numerator, _, denominator = text.partition("/")
    return f"{_measure(numerator)}/{_measure(denominator)}" if denominator else _measure(numerator)


def parse_xbrl_json(doc: dict[str, Any], concepts: set[str] | None = None) -> list[XbrlFact]:
    """Non-dimensional numeric facts of an xBRL-JSON report. Concept prefixes are the
    document's own (ČEZ writes `ifrs:`); they are mapped to the canonical ones through
    `documentInfo.namespaces`."""
    namespaces = (doc.get("documentInfo") or {}).get("namespaces") or {}
    prefixes = {p: _taxonomy(str(uri)) or p for p, uri in namespaces.items()}
    facts: list[XbrlFact] = []
    for fact_id, fact in (doc.get("facts") or {}).items():
        dims = fact.get("dimensions") or {}
        written = str(dims.get("concept") or "")
        prefix, _, local = written.partition(":")
        concept = f"{prefixes.get(prefix, prefix)}:{local}" if local else written
        if not concept or (concepts is not None and concept not in concepts):
            continue
        extra = set(dims) - {"concept", "entity", "period", "unit", "language"}
        if extra or "unit" not in dims or "period" not in dims:
            continue
        try:
            value = Decimal(str(fact.get("value")))
            first, _, second = str(dims["period"]).partition("/")
            if second:
                # A duration's start is inclusive: "2026-01-01T00:00:00" is 1 January.
                start: dt.date | None = dt.datetime.fromisoformat(first).date()
                end = _json_moment(second)
            else:
                start, end = None, _json_moment(first)
        except (InvalidOperation, ValueError):
            continue
        decimals = fact.get("decimals")
        facts.append(
            XbrlFact(
                concept=concept,
                start=start,
                end=end,
                value=value,
                unit=_json_unit(str(dims["unit"])),
                decimals=None if decimals is None else str(decimals),
                raw={"id": fact_id, **fact},
            )
        )
    return facts
