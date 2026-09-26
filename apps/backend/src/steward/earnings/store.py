"""Database writes for the earnings pipeline: archived evidence and validated facts."""

import json
from dataclasses import dataclass, field
from decimal import Decimal

import asyncpg

from steward.documents.fetch import Document
from steward.earnings.periods import period_label
from steward.earnings.validate import FactKey, KnownFact, RejectedFact, ValidatedFigure
from steward.storage import EvidenceStore, evidence_path


@dataclass(frozen=True)
class ArchivedDocument:
    sha256: str
    path: str
    url: str


async def archive(
    pool: asyncpg.Pool, storage: EvidenceStore, document: Document, job_id: int | None
) -> ArchivedDocument:
    """Upload the document's bytes (once per hash) and record them in `evidence`."""
    sha = document.sha256
    existing = await pool.fetchval(
        "select storage_path from public.evidence where sha256 = $1", sha
    )
    if existing:
        return ArchivedDocument(sha, existing, document.url)
    path = evidence_path(sha, document.extension)
    await storage.put(path, document.data, document.content_type)
    await pool.execute(
        "insert into public.evidence (sha256, storage_path, source_url, content_type,"
        " byte_size, job_id) values ($1, $2, $3, $4, $5, $6) on conflict (sha256) do nothing",
        sha,
        path,
        document.url,
        document.content_type,
        len(document.data),
        job_id,
    )
    return ArchivedDocument(sha, path, document.url)


async def load_known(
    pool: asyncpg.Pool | asyncpg.Connection, ticker: str
) -> dict[FactKey, KnownFact]:
    """Latest verified or held fact per key (rejected and quarantined ones don't count)."""
    rows = await pool.fetch(
        "select distinct on (metric, basis, fiscal_year, fiscal_period)"
        " id, metric, basis, fiscal_year, fiscal_period, value_units, unit, currency, status"
        " from public.fact where ticker = $1 and status in ('verified', 'unverified')"
        " order by metric, basis, fiscal_year, fiscal_period, id desc",
        ticker,
    )
    return {
        FactKey(r["metric"], r["basis"], r["fiscal_year"], r["fiscal_period"]): KnownFact(
            r["id"], r["value_units"], r["unit"], r["currency"], r["status"]
        )
        for r in rows
    }


async def load_owner_rejected(
    pool: asyncpg.Pool | asyncpg.Connection, ticker: str
) -> dict[FactKey, list[RejectedFact]]:
    """Facts the owner rejected (or replaced with a correction), per key. A re-run of the
    same release must not queue those values for review again."""
    rows = await pool.fetch(
        "select id, metric, basis, fiscal_year, fiscal_period, value_units, unit"
        " from public.fact where ticker = $1 and status = 'rejected'"
        " and reviewed_at is not null order by id",
        ticker,
    )
    rejected: dict[FactKey, list[RejectedFact]] = {}
    for r in rows:
        key = FactKey(r["metric"], r["basis"], r["fiscal_year"], r["fiscal_period"])
        rejected.setdefault(key, []).append(RejectedFact(r["id"], r["value_units"], r["unit"]))
    return rejected


@dataclass
class StoreSummary:
    inserted: dict[str, int] = field(
        default_factory=lambda: {"verified": 0, "unverified": 0, "rejected": 0}
    )
    duplicates: int = 0
    owner_rejected: int = 0
    reasons: list[str] = field(default_factory=list)

    def as_detail(self) -> dict[str, object]:
        return {
            "inserted": self.inserted,
            "already_stored": self.duplicates,
            "rejected_by_owner_before": self.owner_rejected,
            "not_verified": self.reasons[:20],
        }


async def store_facts(
    pool: asyncpg.Pool,
    ticker: str,
    figures: list[ValidatedFigure],
    evidence: ArchivedDocument,
    job_id: int | None,
    config_version_ids: list[int],
) -> StoreSummary:
    summary = StoreSummary()
    async with pool.acquire() as conn, conn.transaction():
        for v in figures:
            if v.duplicate_of is not None:
                summary.duplicates += 1
                continue
            if v.owner_rejected is not None:
                summary.owner_rejected += 1
                continue
            f = v.figure
            status = v.status
            value: Decimal = Decimal(0) if v.value is None else v.value
            # Rejected before the number could be read: value 0, the claim kept as text.
            value_note = None if v.value is not None else f"unreadable {f.value_as_written!r}"
            await conn.execute(
                "insert into public.fact (ticker, metric, basis, fiscal_year, fiscal_period,"
                " period, period_end, value, value_as_written, scale, unit, currency,"
                " source_url, snippet, unit_snippet, evidence_sha256, evidence_path, status,"
                " status_reason, checks, restates_fact_id, job_id, config_version_ids)"
                " values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,"
                " $16, $17, $18, $19, $20::jsonb, $21, $22, $23)",
                ticker,
                f.metric,
                f.basis,
                f.fiscal_year,
                f.fiscal_period,
                period_label(f.fiscal_year, f.fiscal_period),
                v.period_end,
                value,
                f.value_as_written,
                f.scale,
                f.unit,
                f.currency
                if f.currency and len(f.currency) == 3 and f.currency.isupper()
                else None,
                evidence.url,
                f.source_snippet,
                f.unit_snippet,
                evidence.sha256,
                evidence.path,
                status,
                v.reason or value_note,
                json.dumps([c.as_json() for c in v.checks]),
                v.restates,
                job_id,
                config_version_ids,
            )
            summary.inserted[status] += 1
            if v.reason:
                summary.reasons.append(
                    f"{f.metric} {period_label(f.fiscal_year, f.fiscal_period)}: {v.reason}"
                )
    return summary
