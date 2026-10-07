"""Owner-triggered disclosure jobs: ingest an issuer's SEC filings, suggest missing identifiers.

Both refuse to run unless the sources they need are enabled with a recorded access test; neither
confirms anything (identifier rows are suggestions a person decides).
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from api.auth import Owner
from api.db import IdPath, Pool
from api.routes import Storage
from steward.disclosure.fetch import DisclosureFetcher, FetchFailure
from steward.disclosure.identifier_job import (
    gleif_lei_lookup,
    openfigi_figi_lookup,
    resolve_identifiers,
)
from steward.disclosure.ingest_job import ingest_filings, mirror_stored_facts
from steward.disclosure.parse import ParseError, parse_html
from steward.disclosure.registry import SourceNotEnabled
from steward.disclosure.sec_discovery import DiscoveredFiling, discover_filings
from steward.settings import get_settings
from steward.sources.gleif import GleifClient
from steward.storage import StorageError

router = APIRouter(prefix="/disclosures", tags=["disclosures"])

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
DEFAULT_FORMS = {"10-K", "10-Q", "20-F", "40-F"}


class FilingResultOut(BaseModel):
    accession: str
    status: str
    reason: str | None
    quarantined: bool
    facts_mirrored: int
    note_rows: int
    gaps: list[str]


class IngestOut(BaseModel):
    discovered: int
    ingested: int
    unchanged: int
    failed: int
    results: list[FilingResultOut]


@router.post("/issuers/{issuer_id}/ingest")
async def ingest_issuer_filings(
    issuer_id: IdPath,
    pool: Pool,
    storage: Storage,
    _owner: Owner,
    since: Annotated[dt.date | None, Query(description="Only filings filed on or after.")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> IngestOut:
    """Fetch, store, parse and mirror the issuer's newest annual and quarterly filings. Identical
    bytes already stored are skipped; a filing that fails is reported and does not stop the rest."""
    settings = get_settings()
    if not settings.sec_user_agent:
        raise HTTPException(status.HTTP_409_CONFLICT, "SEC_USER_AGENT is not configured")
    issuer = await pool.fetchrow("select id, cik from public.issuer where id=$1", issuer_id)
    if issuer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such issuer")
    if not issuer["cik"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The issuer has no CIK")
    ticker = await pool.fetchval(
        """select l.ticker from public.security_listing l
             join public.security s on s.id = l.security_id
            where s.issuer_id=$1 and l.status='active' order by l.id limit 1""",
        issuer_id,
    )
    cik = str(int(issuer["cik"])).zfill(10)
    async with httpx.AsyncClient(timeout=60) as http:
        fetcher = DisclosureFetcher(pool, http, user_agent=settings.sec_user_agent, store=storage)
        try:
            listing = await fetcher.fetch("sec.submissions", SUBMISSIONS_URL.format(cik=cik))
            submissions: dict[str, Any] = json.loads(listing.content or b"{}")
            filings: list[DiscoveredFiling] = discover_filings(
                cik, submissions, forms=DEFAULT_FORMS, since=since
            )[-limit:]

            async def fetch(url: str) -> Any:
                return await fetcher.fetch("sec.archive", url)

            outcome = await ingest_filings(
                pool,
                issuer_id=issuer_id,
                ticker=ticker,
                filings=filings,
                fetch=fetch,
                store=storage,
            )
        except SourceNotEnabled as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except FetchFailure as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{exc.state}: {exc}") from exc
    return IngestOut(
        discovered=len(filings),
        ingested=outcome.ingested,
        unchanged=outcome.unchanged,
        failed=outcome.failed,
        results=[
            FilingResultOut(
                accession=r.accession,
                status=r.status.value,
                reason=r.reason,
                quarantined=r.quarantined,
                facts_mirrored=r.facts_mirrored,
                note_rows=r.note_rows,
                gaps=list(r.gaps),
            )
            for r in outcome.results
        ],
    )


class MirrorOut(BaseModel):
    version_id: int
    facts_mirrored: int


@router.post("/versions/{version_id}/mirror-facts")
async def mirror_version_facts(
    version_id: IdPath, pool: Pool, storage: Storage, _owner: Owner
) -> MirrorOut:
    """Link the stored statement facts of an archived filing to the exact place in the document
    that prints them. A fact with no single exact match stays unlinked; re-running adds nothing
    twice."""
    row = await pool.fetchrow(
        """select v.archive_path, d.issuer_id, d.external_id
             from public.source_document_version v
             join public.source_document d on d.id = v.document_id
            where v.id = $1""",
        version_id,
    )
    if row is None or not row["archive_path"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No archived copy of that version")
    if row["issuer_id"] is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "The document is not linked to an issuer yet"
        )
    try:
        data = await storage.get(row["archive_path"])
        doc = parse_html(data)
    except StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except ParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    mirrored = await mirror_stored_facts(
        pool, row["issuer_id"], row["external_id"], version_id, doc
    )
    return MirrorOut(version_id=version_id, facts_mirrored=mirrored)


class IdentifierSuggestionOut(BaseModel):
    provider: str
    identifier_type: str
    identifier: str
    issuer_id: int
    security_id: int | None
    score: float
    name_only: bool
    ambiguous: bool
    stored: bool


class IdentifierJobOut(BaseModel):
    issuers_checked: int
    suggestions: list[IdentifierSuggestionOut]
    skipped: list[str]
    failed: list[str]


@router.post("/identifiers/resolve")
async def resolve_missing_identifiers(
    pool: Pool,
    _owner: Owner,
    issuer_id: Annotated[int | None, Query(ge=1)] = None,
) -> IdentifierJobOut:
    """Look up missing LEIs (GLEIF) and share-class FIGIs (OpenFIGI) and store them as
    suggestions. Nothing is confirmed; a person decides each suggestion."""
    settings = get_settings()
    key = settings.openfigi_api_key.get_secret_value() if settings.openfigi_api_key else None
    async with httpx.AsyncClient(timeout=30) as http:
        result = await resolve_identifiers(
            pool,
            lei_lookup=gleif_lei_lookup(GleifClient(http)),
            figi_lookup=openfigi_figi_lookup(http, key),
            issuer_ids=[issuer_id] if issuer_id is not None else None,
        )
    return IdentifierJobOut(
        issuers_checked=result.issuers_checked,
        suggestions=[
            IdentifierSuggestionOut(
                provider=s.provider,
                identifier_type=s.identifier_type,
                identifier=s.identifier,
                issuer_id=s.issuer_id,
                security_id=s.security_id,
                score=s.score,
                name_only=s.name_only,
                ambiguous=s.ambiguous,
                stored=s.stored,
            )
            for s in result.suggestions
        ],
        skipped=list(result.skipped),
        failed=list(result.failed),
    )
