"""ESEF annual and interim reports from filings.xbrl.org (`/api/entities/{lei}/filings`, no
key), read as xBRL-JSON.

filings.xbrl.org republishes the ESEF reports of EU issuers. Coverage is uneven: Danish and
Dutch issuers (Novo Nordisk's quarterly reports, ASML's annual) are current, German ones
absent and the Czech collection stops at FY2020. Tier 1 data, republished by XBRL
International.

Only non-dimensional numeric facts are read (face-statement figures); a fact with any axis
member is a breakdown and is skipped. xBRL-JSON is JSON, so there is no DTD or entity to
refuse. Concept prefixes are mapped to the canonical ones with the SEC instance reader's
`canonical_prefix` (ČEZ writes `ifrs:`), units written as companyfacts writes them.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from steward.sources.xbrl import XbrlError, canonical_prefix, plain_unit

FILINGS_XBRL_URL = "https://filings.xbrl.org"
ENTITY_FILINGS_URL = FILINGS_XBRL_URL + "/api/entities/{lei}/filings"


@dataclass(frozen=True)
class EsefFact:
    """One numeric fact or cited note block. `start` is None for an instant fact."""

    concept: str  # "ifrs-full:DilutedEarningsLossPerShare"
    start: dt.date | None
    end: dt.date
    value: Decimal | None
    unit: str  # "DKK/shares"
    decimals: str | None
    # The xBRL-JSON fact object as published, with its id.
    raw: dict[str, Any] = field(repr=False, compare=False)
    dimensions: dict[str, str] = field(default_factory=dict)
    text: str | None = None


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


def parse_xbrl_json(
    doc: dict[str, Any],
    concepts: set[str] | None = None,
    *,
    include_dimensions: bool = False,
    include_text_blocks: bool = False,
) -> list[EsefFact]:
    """Parse numeric facts, optionally retaining axes and cited note blocks.

    Concept prefixes are mapped from the document's own namespaces; ČEZ writes `ifrs:`.
    """
    namespaces = (doc.get("documentInfo") or {}).get("namespaces") or {}
    prefixes = {p: canonical_prefix(str(uri), p) for p, uri in namespaces.items()}
    facts: list[EsefFact] = []
    for fact_id, fact in (doc.get("facts") or {}).items():
        dims = fact.get("dimensions") or {}
        written = str(dims.get("concept") or "")
        prefix, _, local = written.partition(":")
        concept = f"{prefixes.get(prefix, prefix)}:{local}" if local else written
        if not concept or (concepts is not None and concept not in concepts):
            continue
        extra = set(dims) - {"concept", "entity", "period", "unit", "language"}
        is_text_block = include_text_blocks and "textblock" in local.casefold()
        if (
            (extra and not include_dimensions)
            or ("unit" not in dims and not is_text_block)
            or "period" not in dims
        ):
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
            text = fact.get("value")
            if (
                not include_text_blocks
                or "textblock" not in local.casefold()
                or not isinstance(text, str)
            ):
                continue
            value = None
        else:
            text = None
        decimals = fact.get("decimals")
        facts.append(
            EsefFact(
                concept=concept,
                start=start,
                end=end,
                value=value,
                unit=plain_unit(str(dims.get("unit") or "pure")),
                decimals=None if decimals is None else str(decimals),
                raw={"id": fact_id, **fact},
                dimensions={key: str(member) for key, member in dims.items() if key in extra},
                text=text,
            )
        )
    return facts
