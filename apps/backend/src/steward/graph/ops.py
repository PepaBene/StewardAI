"""EPO Open Patent Services (OPS) biblio search: parser, legal status and paged search.

Parsed against the documented OPS exchange-document structure
(`ops:world-patent-data / ops:biblio-search / ops:search-result / exchange-documents /
exchange-document`), where bibliographic data are *child elements* (`publication-reference/
document-id/{country,doc-number,kind,date}`, `applicants/applicant/applicant-name/name`,
`invention-title`, `patent-classification/{section,class,subclass,main-group,subgroup}`,
`priority-claims/priority-claim/document-id/date`, `references-cited/citation/patcit`) and the
only attributes that matter on `exchange-document` are `family-id`, `country`, `doc-number`
and `kind`. The family key is the OPS family-id, so the A1 in one office and the B2 in another
merge into one family.

Recorded fixtures under tests/fixtures/graph follow the OPS reference documentation; they have
not been compared with a live payload (that needs a registered OPS client). Forward citations
are not part of the OPS bibliographic response and are therefore reported as unknown, never 0.
"""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Literal

import httpx

from steward.graph.keys import normalize_object_key
from steward.sources.base import AccessState
from steward.sources.free_market import FreeSourceError
from steward.sources.xml_guard import safe_fromstring

OPS_BASE = "https://ops.epo.org/3.2"
MAX_XML_CHARS = 5_000_000
PAGE_SIZE = 100
MAX_RESULTS = 2000  # OPS never returns more than 2,000 hits for one search
ALIASES_PER_QUERY = 5


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in node if _local(c.tag) == name]


def _child(node: ET.Element, name: str) -> ET.Element | None:
    return next(iter(_children(node, name)), None)


def _find_all(node: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in node.iter() if _local(c.tag) == name]


def _text(node: ET.Element | None) -> str:
    return " ".join((node.text or "").split()) if node is not None else ""


def _date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value.strip()[:8], "%Y%m%d").date()
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class OpsPublication:
    country: str
    doc_number: str
    kind: str
    date: dt.date | None


@dataclass(slots=True)
class OpsFamily:
    family_id: str
    publications: list[OpsPublication] = field(default_factory=list)
    priority_date: dt.date | None = None
    application_date: dt.date | None = None
    title: str | None = None
    cpc_codes: set[str] = field(default_factory=set)
    applicants: list[str] = field(default_factory=list)
    cited: set[str] = field(default_factory=set)

    @property
    def jurisdictions(self) -> list[str]:
        return sorted({p.country for p in self.publications})

    @property
    def first_publication_date(self) -> dt.date | None:
        dates = [p.date for p in self.publications if p.date]
        return min(dates) if dates else None

    @property
    def grant_date(self) -> dt.date | None:
        # Kind codes starting with B or C are granted documents (A is an application publication).
        dates = [p.date for p in self.publications if p.date and p.kind[:1] in {"B", "C"}]
        return min(dates) if dates else None


def _docdb(node: ET.Element) -> ET.Element | None:
    for doc in _find_all(node, "document-id"):
        if doc.attrib.get("document-id-type") == "docdb":
            return doc
    return None


def _publication(doc: ET.Element) -> OpsPublication | None:
    pub_ref = next(iter(_find_all(doc, "publication-reference")), None)
    if pub_ref is None:
        return None
    docdb = _docdb(pub_ref)
    if docdb is None:
        return None
    country, number, kind = (
        _text(_child(docdb, "country")),
        _text(_child(docdb, "doc-number")),
        _text(_child(docdb, "kind")),
    )
    if not (country and number):
        return None
    return OpsPublication(country, number, kind or "?", _date(_text(_child(docdb, "date"))))


def _applicant_names(doc: ET.Element) -> list[str]:
    """Applicant names, preferring the `original` data format and falling back to `docdb` /
    `epodoc`. EPODOC names are upper case with a trailing `[CC]` country tag."""
    by_format: dict[str, list[str]] = {}
    for applicant in _find_all(doc, "applicant"):
        name_node = next(iter(_find_all(applicant, "name")), None)
        name = re.sub(r"\s*\[[A-Z]{2}\]\s*$", "", _text(name_node)).strip(" ,")
        if name:
            by_format.setdefault(applicant.attrib.get("data-format", "original"), []).append(name)
    for fmt in ("original", "docdb", "epodoc"):
        if by_format.get(fmt):
            return by_format[fmt]
    return next(iter(by_format.values()), [])


def _cpc(node: ET.Element) -> str | None:
    parts = [_text(_child(node, n)) for n in ("section", "class", "subclass")]
    group, sub = _text(_child(node, "main-group")), _text(_child(node, "subgroup"))
    if not all(parts):
        return None
    code = "".join(parts)
    return f"{code}{group}/{sub}" if group and sub else code


def _title(doc: ET.Element) -> str | None:
    titles = {t.attrib.get("lang", ""): _text(t) for t in _find_all(doc, "invention-title")}
    return titles.get("en") or next((v for v in titles.values() if v), None)


def _cited_keys(doc: ET.Element) -> set[str]:
    keys: set[str] = set()
    for patcit in _find_all(doc, "patcit"):
        docdb = _docdb(patcit)
        if docdb is not None:
            keys.add(
                f"{_text(_child(docdb, 'country'))}{_text(_child(docdb, 'doc-number'))}"
                f"{_text(_child(docdb, 'kind'))}"
            )
        elif patcit.attrib.get("dnum"):
            keys.add(patcit.attrib["dnum"])
    return keys


def _priority_dates(doc: ET.Element) -> list[dt.date]:
    out: list[dt.date] = []
    for claim in _find_all(doc, "priority-claim"):
        docdb = _docdb(claim)
        parsed = _date(_text(_child(docdb, "date")) if docdb is not None else None)
        if parsed:
            out.append(parsed)
    return out


@dataclass(frozen=True, slots=True)
class OpsPage:
    families: list[OpsFamily]
    total: int
    documents: int


class UnsafeOpsXml(ValueError):
    """The guard refused the document (DTD / entity declaration) or it did not parse."""


def _parse_root(xml_text: str) -> ET.Element:
    if len(xml_text) > MAX_XML_CHARS:
        raise FreeSourceError("EPO OPS response exceeds the 5 MB parse limit")
    try:
        return safe_fromstring(xml_text, UnsafeOpsXml)
    except UnsafeOpsXml as exc:
        raise FreeSourceError(
            "EPO OPS response was not valid XML"
            if str(exc).startswith("malformed")
            else f"EPO OPS response refused: {exc}"
        ) from exc


def parse_ops_page(xml_text: str, applicant_aliases: tuple[str, ...]) -> OpsPage:
    """Families from one OPS biblio-search page whose applicants include an alias.

    Documents of a family that name no alias (a co-filed family where a different member lists
    a subsidiary) still add their publications to the family if one member matches."""
    root = _parse_root(xml_text)
    wanted = {normalize_object_key(a) for a in applicant_aliases if a.strip()}
    total_node = next(iter(_find_all(root, "biblio-search")), None)
    try:
        total = (
            int(total_node.attrib.get("total-result-count", "0")) if total_node is not None else 0
        )
    except ValueError:
        total = 0
    families: dict[str, OpsFamily] = {}
    matched: set[str] = set()
    documents = 0
    for doc in _find_all(root, "exchange-document"):
        documents += 1
        family_id = doc.attrib.get("family-id")
        if not family_id:
            raise FreeSourceError("EPO OPS exchange-document lacks a family-id")
        family = families.setdefault(family_id, OpsFamily(family_id))
        publication = _publication(doc)
        if publication is None and doc.attrib.get("doc-number"):
            publication = OpsPublication(
                doc.attrib.get("country", "??"),
                doc.attrib["doc-number"],
                doc.attrib.get("kind", "?"),
                None,
            )
        if publication is not None and publication not in family.publications:
            family.publications.append(publication)
        names = _applicant_names(doc)
        for name in names:
            if name not in family.applicants:
                family.applicants.append(name)
        if any(normalize_object_key(n) in wanted for n in names):
            matched.add(family_id)
        family.title = family.title or _title(doc)
        for node in _find_all(doc, "patent-classification"):
            code = _cpc(node)
            if code:
                family.cpc_codes.add(code)
        family.cited |= _cited_keys(doc)
        priorities = _priority_dates(doc)
        if priorities:
            earliest = min(priorities)
            family.priority_date = (
                earliest if family.priority_date is None else min(family.priority_date, earliest)
            )
        app = next(iter(_find_all(doc, "application-reference")), None)
        if app is not None:
            docdb = _docdb(app)
            filed = _date(_text(_child(docdb, "date")) if docdb is not None else None)
            if filed:
                family.application_date = (
                    filed
                    if family.application_date is None
                    else min(family.application_date, filed)
                )
    for family in families.values():
        if family.priority_date is None:
            family.priority_date = family.application_date
    return OpsPage([f for fid, f in families.items() if fid in matched], total, documents)


LegalStatus = Literal["application", "granted", "active", "expired", "unknown"]

_LAPSE_WORDS = ("LAPSED", "CEASED", "EXPIRED", "REVOKED", "WITHDRAWN", "NON-PAYMENT", "ABANDONED")
_GRANT_WORDS = ("GRANT OF PATENT", "PATENT GRANTED", "GRANTED")
_PAYMENT_WORDS = ("PAYMENT OF MAINTENANCE FEE", "RENEWAL FEE PAID", "ANNUAL FEE PAID")


@dataclass(frozen=True, slots=True)
class LegalEvent:
    code: str
    description: str
    date: dt.date | None
    influence: str | None


def parse_ops_legal(xml_text: str) -> dict[str, list[LegalEvent]]:
    """Legal events by family-id from an OPS legal-service response (`ops:legal` elements
    carrying `code`, `desc`, `date` and `infl`)."""
    root = _parse_root(xml_text)
    out: dict[str, list[LegalEvent]] = {}
    for member in _find_all(root, "family-member"):
        family_id = member.attrib.get("family-id")
        if not family_id:
            continue
        for legal in _children(member, "legal"):
            out.setdefault(family_id, []).append(
                LegalEvent(
                    legal.attrib.get("code", ""),
                    legal.attrib.get("desc", ""),
                    _date(legal.attrib.get("date")),
                    legal.attrib.get("infl"),
                )
            )
    return out


def derive_legal_status(
    family: OpsFamily,
    events: list[LegalEvent],
    *,
    today: dt.date,
) -> tuple[LegalStatus, dt.date | None]:
    """(status, expiry date). Kind codes say application vs granted; legal events say lapsed;
    a granted family is `active` until lapse or the 20-year term counted from filing
    (an *estimated* expiry, ignoring term extensions). Without legal events a granted family
    stays `granted`: OPS biblio data alone cannot prove a right is in force."""
    granted = family.grant_date is not None
    lapse = [
        e
        for e in events
        if any(w in e.description.upper() for w in _LAPSE_WORDS) and e.influence != "+"
    ]
    reinstated = any(
        any(w in e.description.upper() for w in ("REINSTATED", "RESTORED")) for e in events
    )
    paid = any(any(w in e.description.upper() for w in _PAYMENT_WORDS) for e in events)
    term_end = None
    if family.application_date:
        try:
            term_end = family.application_date.replace(year=family.application_date.year + 20)
        except ValueError:
            term_end = family.application_date + dt.timedelta(days=365 * 20 + 5)
    if lapse and not reinstated:
        lapse_dates = [e.date for e in lapse if e.date]
        return "expired", min(lapse_dates) if lapse_dates else None
    if term_end is not None and term_end < today:
        return "expired", term_end
    if granted and (paid or events):
        return "active", term_end
    if granted:
        return "granted", term_end
    return "application", None


# --------------------------------------------------------------------------------- transport


class EpoAuthError(FreeSourceError):
    """A typed OPS OAuth failure: `kind` says whether to fix credentials, wait, or retry."""

    def __init__(
        self,
        kind: Literal["invalid_credentials", "quota", "forbidden", "transient"],
        message: str,
        status_code: int | None = None,
    ) -> None:
        state = {
            "invalid_credentials": AccessState.ACCESS_UNVERIFIED,
            "quota": AccessState.QUOTA_EXHAUSTED,
            "forbidden": None,
            "transient": AccessState.TEMPORARILY_FAILED,
        }[kind]
        super().__init__(message, status_code, state=state, provider_charged=kind != "transient")
        self.kind = kind


def classify_oauth_failure(response: httpx.Response) -> EpoAuthError:
    """OPS answers bad client id/secret with 400/401 (`clients.InvalidClientIdentifier`,
    `invalid_client`), an exhausted quota or blacklisted client with 403 (message mentions the
    quota / `IndividualQuota` / `RobotDetected`) and outages with 5xx."""
    status = response.status_code
    body = response.text[:500].casefold()
    if status in (400, 401):
        return EpoAuthError(
            "invalid_credentials",
            f"EPO OPS rejected the client credentials (HTTP {status})",
            status,
        )
    if status == 403:
        if any(w in body for w in ("quota", "limit", "throttl", "blacklist")):
            return EpoAuthError("quota", "EPO OPS quota or throttling denial (HTTP 403)", status)
        return EpoAuthError("forbidden", "EPO OPS refused the token request (HTTP 403)", status)
    if status == 429 or status >= 500:
        return EpoAuthError("transient", f"EPO OPS OAuth unavailable (HTTP {status})", status)
    return EpoAuthError("transient", f"EPO OPS OAuth unexpected HTTP {status}", status)


async def fetch_token(http: httpx.AsyncClient, client_id: str, client_secret: str) -> str:
    try:
        response = await http.post(
            f"{OPS_BASE}/auth/accesstoken",
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as exc:
        raise EpoAuthError(
            "transient", f"EPO OPS OAuth network error ({type(exc).__name__})"
        ) from exc
    if response.status_code != 200:
        raise classify_oauth_failure(response)
    try:
        token = response.json().get("access_token")
    except ValueError as exc:
        raise EpoAuthError("transient", "EPO OPS OAuth response was not JSON") from exc
    if not token:
        raise EpoAuthError("invalid_credentials", "EPO OPS OAuth response omitted the access token")
    return str(token)


def build_cql(aliases: list[str]) -> str:
    quoted = [f'pa="{a.replace(chr(34), " ").strip()}"' for a in aliases if a.strip()]
    if not quoted:
        raise ValueError("at least one applicant alias is required")
    return " or ".join(quoted)


def throttle_color(header: str | None, service: str = "search") -> str | None:
    """Colour of the `search` service in `X-Throttling-Control` (green/yellow/red/black)."""
    if not header:
        return None
    match = re.search(rf"{service}=(\w+)", header)
    return match.group(1).lower() if match else None


@dataclass(slots=True)
class OpsSearchResult:
    families: dict[str, OpsFamily] = field(default_factory=dict)
    pages: int = 0
    total_reported: int = 0
    truncated: bool = False
    stopped_reason: str | None = None


async def search_applicants(
    http: httpx.AsyncClient,
    token: str,
    aliases: tuple[str, ...],
    *,
    max_pages: int = 10,
    reserve_request: object | None = None,
) -> OpsSearchResult:
    """Search every alias (applicant name, subsidiaries included in `aliases`) with paging.

    Aliases are OR-ed in groups of five; each group is paged with the `Range` parameter until
    `total-result-count` is read, OPS's 2,000-hit ceiling is reached or `max_pages` runs out.
    `reserve_request` is an awaitable callable invoked before each request (budget gate); a
    `QuotaExceeded` from it stops the search and the partial result is returned."""
    from steward.sources.governor import QuotaExceeded

    result = OpsSearchResult()
    unique = list(dict.fromkeys(a.strip() for a in aliases if a.strip()))
    for start in range(0, len(unique), ALIASES_PER_QUERY):
        group = unique[start : start + ALIASES_PER_QUERY]
        cql = build_cql(group)
        offset = 1
        while True:
            if result.pages >= max_pages:
                result.truncated = True
                result.stopped_reason = f"page cap {max_pages}"
                return result
            if reserve_request is not None:
                try:
                    await reserve_request()  # type: ignore[operator]
                except QuotaExceeded as exc:
                    result.truncated = True
                    result.stopped_reason = f"budget: {exc}"
                    return result
            rng = f"{offset}-{offset + PAGE_SIZE - 1}"
            try:
                response = await http.get(
                    f"{OPS_BASE}/rest-services/published-data/search/biblio",
                    params={"q": cql, "Range": rng},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/exchange+xml",
                        "X-OPS-Range": rng,
                    },
                )
            except httpx.HTTPError as exc:
                raise FreeSourceError(
                    f"EPO OPS network error ({type(exc).__name__})", provider_charged=False
                ) from exc
            result.pages += 1
            if response.status_code == 404:  # OPS answers an empty search with 404
                break
            if response.status_code != 200:
                raise FreeSourceError(
                    f"EPO OPS search HTTP {response.status_code}", response.status_code
                )
            page = parse_ops_page(response.text, tuple(group))
            result.total_reported = max(result.total_reported, page.total)
            for fid, family in ((f.family_id, f) for f in page.families):
                known = result.families.get(fid)
                if known is None:
                    result.families[fid] = family
                else:
                    known.publications += [
                        p for p in family.publications if p not in known.publications
                    ]
                    known.cpc_codes |= family.cpc_codes
                    known.cited |= family.cited
            offset += PAGE_SIZE
            if page.documents == 0 or offset > min(page.total, MAX_RESULTS):
                if page.total > MAX_RESULTS:
                    result.truncated = True
                    result.stopped_reason = "OPS 2,000-hit ceiling"
                break
            if throttle_color(response.headers.get("X-Throttling-Control")) in {"black"}:
                result.truncated = True
                result.stopped_reason = "OPS throttling: search service is black"
                return result
    return result
