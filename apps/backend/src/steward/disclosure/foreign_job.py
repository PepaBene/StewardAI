"""P1.3 / P2.5 / P2.6: fetch and store filings outside the SEC, with the dates their adapters read.

* ESEF: for held issuers that have a confirmed LEI, the filing index of filings.xbrl.org is read,
  new entries become document versions (the xBRL-JSON report), known at the time the registry
  added them and never before the period end.
* EDINET: one submission day's document list is read for the issuers' EDINET codes. The list row
  is stored as the document version (the PDF costs a credit and is not downloaded here), known at
  the filing time, or at the end of the filing day when only the date is stated.
* SEDAR+: public search works only in a browser that has passed the portal's bot check, and its
  result links are session tokens. There is no unattended fetcher. A person's signed-in page is
  read into rows (`sedarplus_page_rows`) and `collect_sedarplus_page` stores the rows of held
  profiles. The portal gives no document id, so the id is derived and marked as such.

Nothing here reads a value out of a document: the statements of an ESEF report reach the evidence
layer through the existing statement pipeline. The same bytes are stored once, and a source that
is not enabled with a passing access test ends the job with its reason.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from steward.disclosure.esef_discovery import DiscoveredEsefFiling, discover_esef
from steward.disclosure.fetch import FetchFailure
from steward.disclosure.ingest import DocumentRecord, ingest_document_version
from steward.disclosure.jurisdiction_adapters import (
    DiscoveredDocument,
    edinet_documents,
    sedarplus_documents,
    sedarplus_page_rows,
)
from steward.sources.esef import ENTITY_FILINGS_URL

ESEF_SOURCE = "esef.filings_xbrl_org"
EDINET_SOURCE = "edinet.api"
SEDAR_SOURCE = "sedarplus.portal"
type Fetch = Callable[[str], Awaitable[Any]]


@dataclass
class ForeignTotals:
    seen: int = 0
    stored: int = 0
    unchanged: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_detail(self) -> dict[str, Any]:
        return {
            "seen": self.seen,
            "stored": self.stored,
            "unchanged": self.unchanged,
            "skipped": dict(self.skipped),
            "failed": self.failed,
            "errors": self.errors[:20],
        }


async def held_lei_issuers(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Issuers with a LEI whose security is in the latest position snapshot."""
    rows = await pool.fetch(
        """select distinct i.id, i.lei
             from public.issuer i
             join public.security s on s.issuer_id = i.id
             join public.instrument n on n.isin = s.isin
            where i.lei is not null and n.t212_ticker in (
                  select ticker from public.position_snapshot
                   where snapshot_date = (select max(snapshot_date) from public.account_snapshot))
            order by i.id"""
    )
    return list(rows)


async def issuer_ids_for_tickers(pool: asyncpg.Pool, tickers: Sequence[str]) -> dict[str, int]:
    """Ticker to issuer id through the instrument's ISIN; a ticker with no match is absent."""
    rows = await pool.fetch(
        """select distinct n.t212_ticker as ticker, s.issuer_id
             from public.instrument n
             join public.security s on s.isin = n.isin
            where n.t212_ticker = any($1::text[]) and s.issuer_id is not null""",
        list(tickers),
    )
    return {r["ticker"]: int(r["issuer_id"]) for r in rows}


async def store_esef(
    pool: asyncpg.Pool,
    filing: DiscoveredEsefFiling,
    content: bytes,
    *,
    issuer_id: int | None,
    archive_path: str | None = None,
    now: dt.datetime | None = None,
) -> bool:
    """Store one ESEF report as a document version. True when a new version was written."""
    out = await ingest_document_version(
        pool,
        DocumentRecord(
            regulator="ESEF",
            external_id=filing.filing_id,
            stable_url=filing.json_url or filing.package_url,
            form="ESEF annual report",
            known_at=filing.known_at,
            issuer_id=issuer_id,
            fiscal_period=str(filing.period_end),
            source_key=ESEF_SOURCE,
            published_at=filing.known_at,
        ),
        content,
        archive_path=archive_path,
        now=now,
    )
    return out.created


async def collect_esef(
    pool: asyncpg.Pool,
    issuers: Sequence[Any],
    fetch_json: Fetch,
    fetch: Fetch,
    *,
    since: dt.datetime | None = None,
) -> ForeignTotals:
    """Index, then report, for every issuer given (rows with `id` and `lei`). A fetch failure is
    counted against the issuer or entry and never stops the others."""
    totals = ForeignTotals()
    for issuer in issuers:
        try:
            got = await fetch_json(ENTITY_FILINGS_URL.format(lei=issuer["lei"]))
            found = discover_esef(json.loads(got.content or b"{}"), since)
        except FetchFailure as exc:
            if exc.state == "not_covered":  # the registry has no ESEF entity: a coverage fact
                totals.skipped["no_esef_entity"] = totals.skipped.get("no_esef_entity", 0) + 1
                continue
            totals.failed += 1
            totals.errors.append(f"issuer {issuer['id']}: {type(exc).__name__}")
            continue
        except ValueError as exc:
            totals.failed += 1
            totals.errors.append(f"issuer {issuer['id']}: {type(exc).__name__}")
            continue
        for s in found.skipped:
            totals.skipped[s.reason] = totals.skipped.get(s.reason, 0) + 1
        for filing in found.discovered:
            totals.seen += 1
            if await pool.fetchval(
                "select 1 from public.source_document"
                " where regulator = 'ESEF' and external_id = $1",
                filing.filing_id,
            ):
                totals.unchanged += 1  # a filing id is one report; it is not fetched again
                continue
            if filing.json_url is None:
                totals.skipped["no_json_report"] = totals.skipped.get("no_json_report", 0) + 1
                continue
            try:
                body = await fetch(filing.json_url)
                if not getattr(body, "modified", True) or not body.content:
                    totals.unchanged += 1
                    continue
                created = await store_esef(
                    pool,
                    filing,
                    body.content,
                    issuer_id=int(issuer["id"]),
                    archive_path=getattr(body, "archive_path", None),
                    now=getattr(body, "retrieved_at", None),
                )
            except (FetchFailure, ValueError, asyncpg.PostgresError) as exc:
                totals.failed += 1
                totals.errors.append(f"{filing.filing_id}: {type(exc).__name__}")
                continue
            if created:
                totals.stored += 1
            else:
                totals.unchanged += 1
    return totals


async def store_edinet(
    pool: asyncpg.Pool,
    doc: DiscoveredDocument,
    *,
    issuer_id: int | None,
    now: dt.datetime | None = None,
) -> bool:
    """Store one EDINET list row as a document version (its row, not the PDF)."""
    row = {
        "doc_id": doc.doc_id,
        "form_code": doc.form_code,
        "form_label": doc.form_label,
        "issuer_code": doc.issuer_code,
        "issuer_name": doc.issuer_name,
        "filer_code": doc.filer_code,
        "filed_at": doc.filed_at.isoformat() if doc.filed_at else None,
        "period_end": doc.period_end.isoformat() if doc.period_end else None,
    }
    out = await ingest_document_version(
        pool,
        DocumentRecord(
            regulator="EDINET",
            external_id=doc.doc_id,
            stable_url=doc.url or f"edinet:{doc.doc_id}",
            form=doc.form_label or doc.form_code or "filing",
            known_at=doc.known_at,
            issuer_id=issuer_id,
            fiscal_period=str(doc.period_end) if doc.period_end else None,
            source_key=EDINET_SOURCE,
            filed_at=doc.filed_at,
            date_notes={"filed_precision": doc.filed_precision},
        ),
        json.dumps(row, sort_keys=True).encode(),
        now=now,
    )
    return out.created


async def collect_sedarplus_page(
    pool: asyncpg.Pool,
    captured: object,
    holdings: dict[str, int | None],
    *,
    observed_at: dt.datetime | None = None,
) -> ForeignTotals:
    """Store the rows of one captured SEDAR+ results page whose profile number is in `holdings`
    (profile number to issuer id, None when unmatched). The page comes from a person's signed-in
    browser (see `sedarplus_page_rows`); nothing here logs in or drives a page. Other rows are
    counted as `not_held`, not stored."""
    found = sedarplus_documents(sedarplus_page_rows(captured), observed_at)
    totals = ForeignTotals()
    for s in found.skipped:
        totals.skipped[s.reason] = totals.skipped.get(s.reason, 0) + 1
    for doc in found.discovered:
        totals.seen += 1
        if doc.issuer_code not in holdings:
            totals.skipped["not_held"] = totals.skipped.get("not_held", 0) + 1
            continue
        out = await ingest_document_version(
            pool,
            DocumentRecord(
                regulator="SEDAR+",
                external_id=doc.doc_id,
                stable_url=f"sedarplus:{doc.doc_id}",
                form=doc.form_label or "filing",
                known_at=doc.known_at,
                issuer_id=holdings[doc.issuer_code or ""],
                source_key=SEDAR_SOURCE,
                filed_at=doc.filed_at,
                date_notes={"filed_precision": doc.filed_precision, "id_basis": "derived"},
            ),
            json.dumps(
                {
                    "document_id": doc.doc_id,
                    "profile_number": doc.issuer_code,
                    "issuer_name": doc.issuer_name,
                    "document": doc.form_label,
                    "filed_at": doc.filed_at.isoformat() if doc.filed_at else None,
                },
                sort_keys=True,
            ).encode(),
            now=observed_at,
        )
        if out.created:
            totals.stored += 1
        else:
            totals.unchanged += 1
    return totals


async def collect_edinet_day(
    pool: asyncpg.Pool,
    payload: object,
    holdings: dict[str, int | None],
    *,
    observed_at: dt.datetime | None = None,
) -> ForeignTotals:
    """Store one day's list rows whose filer or issuer EDINET code is in `holdings` (code to
    issuer id, None when the issuer is not matched yet)."""
    found = edinet_documents(payload, set(holdings), observed_at)
    totals = ForeignTotals()
    for s in found.skipped:
        totals.skipped[s.reason] = totals.skipped.get(s.reason, 0) + 1
    for doc in found.discovered:
        totals.seen += 1
        code = doc.issuer_code if doc.issuer_code in holdings else doc.filer_code
        created = await store_edinet(pool, doc, issuer_id=holdings.get(code or ""), now=observed_at)
        if created:
            totals.stored += 1
        else:
            totals.unchanged += 1
    return totals
