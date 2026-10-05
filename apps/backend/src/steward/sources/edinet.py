"""EDINET (Japan FSA) API v2: the document list by submission date and the document download,
for holdings whose profile carries an `edinet_code` (D3.5).

The free API needs a Subscription-Key (sent as the `Subscription-Key` query parameter, so it is
never put in an error or log line). One `documents.json?type=2` request returns every filing of
one day; the collector keeps only rows whose `edinetCode` or `issuerEdinetCode` belongs to a
holding. Each kept row is stored as a `source_document` version (regulator `edinet`, external id
the `docID`) linked to the holding through `source_record_evidence` (domain table
`holding_filing_event`), with the filer's own `docDescription` as evidence.

XBRL financial parsing is deliberately NOT done: the repository's generic XBRL code is built for
SEC and ESEF taxonomies, not the EDINET one, so only the list and the document links are stored.
`download_document` is available for a later feature and is not called by the scheduled job.

Field names (`results[].docID`, `edinetCode`, `issuerEdinetCode`, `secCode`, `filerName`,
`docTypeCode`, `docDescription`, `submitDateTime`, `periodEnd`, `xbrlFlag`, `pdfFlag`, the
envelope's `metadata.status`) follow the EDINET API v2 specification and have NOT been checked
against a live response (no key was available when this was written).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx

from steward.sources.base import AccessResult, AccessState
from steward.sources.documents import link_public_document
from steward.sources.free_market import FreeSourceError
from steward.sources.governed_http import governed_get

PROVIDER = "edinet"
API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
LIST_URL = f"{API_BASE}/documents.json"
DOMAIN_TABLE = "holding_filing_event"
JST = dt.timezone(dt.timedelta(hours=9))
# documents.json?type=2 answers HTTP 200 with an envelope status; only "200" carries rows.
STATUS_OK = "200"


@dataclass(frozen=True, slots=True)
class EdinetDocument:
    doc_id: str
    edinet_code: str
    filer_name: str
    doc_type_code: str
    description: str
    submitted_at: dt.datetime | None
    period_end: dt.date | None
    has_xbrl: bool
    has_pdf: bool
    raw: dict[str, Any]


def classify_envelope(response: httpx.Response) -> AccessResult | None:
    """A 200 answer whose envelope says it failed: 401 is a key problem, anything else other
    than 404 (no list for the date) a transient failure. None when the body carries rows or is
    an empty day."""
    try:
        body = response.json()
    except ValueError:
        return AccessResult(AccessState.TEMPORARILY_FAILED, "EDINET sent no JSON")
    meta = body.get("metadata") if isinstance(body, dict) else None
    if not isinstance(meta, dict):
        # An API-gateway error body (`statusCode`/`message`) rather than the envelope.
        status = str(body.get("statusCode")) if isinstance(body, dict) else ""
        if status in {"401", "403"}:
            return AccessResult(AccessState.ACCESS_UNVERIFIED, "EDINET key rejected")
        return AccessResult(AccessState.TEMPORARILY_FAILED, "EDINET schema changed")
    status = str(meta.get("status"))
    if status in {STATUS_OK, "404"}:
        return None
    if status in {"401", "403"}:
        return AccessResult(AccessState.ACCESS_UNVERIFIED, "EDINET key rejected")
    return AccessResult(AccessState.TEMPORARILY_FAILED, f"EDINET envelope status {status}")


def _parse_dt(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    except ValueError:
        return None


def _parse_date(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def parse_document_list(payload: object, wanted_codes: set[str]) -> list[EdinetDocument]:
    """Documents of the wanted EDINET codes (as filer or issuer). A day with no list (envelope
    status 404, no `results`) is an empty list; a body that is not the envelope raises."""
    if not isinstance(payload, dict) or not isinstance(payload.get("metadata"), dict):
        raise FreeSourceError("EDINET document-list schema changed")
    results = payload.get("results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise FreeSourceError("EDINET document-list schema changed")
    out: list[EdinetDocument] = []
    for row in results:
        if not isinstance(row, dict) or not isinstance(doc_id := row.get("docID"), str):
            continue
        code = next(
            (
                c
                for c in (row.get("edinetCode"), row.get("issuerEdinetCode"))
                if isinstance(c, str) and c in wanted_codes
            ),
            None,
        )
        if code is None or row.get("withdrawalStatus") == "1":
            continue
        out.append(
            EdinetDocument(
                doc_id=doc_id,
                edinet_code=code,
                filer_name=str(row.get("filerName") or ""),
                doc_type_code=str(row.get("docTypeCode") or ""),
                description=" ".join(str(row.get("docDescription") or "").split()),
                submitted_at=_parse_dt(row.get("submitDateTime")),
                period_end=_parse_date(row.get("periodEnd")),
                has_xbrl=str(row.get("xbrlFlag")) == "1",
                has_pdf=str(row.get("pdfFlag")) == "1",
                raw={
                    k: row.get(k)
                    for k in (
                        "docID",
                        "edinetCode",
                        "issuerEdinetCode",
                        "secCode",
                        "filerName",
                        "docTypeCode",
                        "formCode",
                        "docDescription",
                        "submitDateTime",
                        "periodStart",
                        "periodEnd",
                        "xbrlFlag",
                        "pdfFlag",
                    )
                    if k in row
                },
            )
        )
    return out


def document_url(doc_id: str, kind: int = 2) -> str:
    """API link of a document (1 submission zip, 2 PDF, ...). It needs the caller's own key, so
    the stored link never contains one."""
    return f"{API_BASE}/documents/{doc_id}?type={kind}"


async def collect_edinet_day(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    day: dt.date,
    holdings: dict[str, str],  # EDINET code -> ticker
    api_key: str,
    provider_settings: Any,
) -> int:
    """Store the day's filings of the mapped holdings; one credit. Returns the rows stored."""
    from steward.verify_sources.budgets import settings_budget

    response = await governed_get(
        pool,
        http,
        provider=PROVIDER,
        endpoint="documents_list",
        url=LIST_URL,
        params={"date": day.isoformat(), "type": 2, "Subscription-Key": api_key},
        budget=settings_budget(provider_settings),
        body_denial=classify_envelope,
    )
    if response is None:
        return 0
    try:
        payload = response.json()
    except ValueError as exc:
        raise FreeSourceError("EDINET sent no JSON") from exc
    documents = parse_document_list(payload, set(holdings))
    now = dt.datetime.now(dt.UTC)
    for doc in documents:
        ticker = holdings[doc.edinet_code]
        await link_public_document(
            pool,
            regulator=PROVIDER,
            external_id=doc.doc_id,
            stable_url=document_url(doc.doc_id),
            document_type=doc.doc_type_code or "filing",
            content=json.dumps(doc.raw, sort_keys=True, default=str).encode(),
            known_at=now,
            filed_at=doc.submitted_at,
            quote=doc.description or None,
            domain_table=DOMAIN_TABLE,
            record_key=f"{ticker}:{doc.doc_id}",
        )
    return len(documents)


async def download_document(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    doc_id: str,
    api_key: str,
    provider_settings: Any,
    kind: int = 2,
) -> bytes | None:
    """The document bytes (default PDF), one credit on the `document_download` endpoint, or
    None when EDINET has no such document. Not used by the scheduled job."""
    from steward.verify_sources.budgets import settings_budget

    response = await governed_get(
        pool,
        http,
        provider=PROVIDER,
        endpoint="document_download",
        url=f"{API_BASE}/documents/{doc_id}",
        params={"type": kind, "Subscription-Key": api_key},
        budget=settings_budget(provider_settings),
    )
    return response.content if response is not None else None
