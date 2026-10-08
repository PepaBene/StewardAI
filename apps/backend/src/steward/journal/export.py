"""Personal owner report (M7.7): the journal, the scorecard and the data-quality checks in one
file, with a manifest of what they were built from. Private by default: it is returned to the
owner who asks and stored nowhere. Values the app does not hold stay null; nothing is filled in."""

from __future__ import annotations

import csv
import datetime as dt
import io

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.versions import METHOD_VERSIONS
from steward.journal import entries
from steward.journal import service as journal_service
from steward.journal.entries import JournalEntry
from steward.journal.scorecard import Scorecard
from steward.quality.cockpit import Cockpit, build_cockpit

CSV_COLUMNS = (
    "id", "decided_on", "scope", "ticker", "decision", "parent_id", "conviction", "main_reason",
    "price_at_decision", "price_currency", "review_date", "hypothesis", "valuation_assumptions",
    "catalysts", "disconfirming",
)  # fmt: skip


class Manifest(BaseModel):
    generated_at: dt.datetime
    visibility: str = "private: returned to the owner who asked; not stored or shared"
    sources: list[str]
    coverage: dict[str, str] = Field(
        description="Each part of the report and the denominator it covers."
    )
    method_versions: dict[str, str]
    notes: list[str]


class OwnerReport(BaseModel):
    manifest: Manifest
    journal: list[JournalEntry]
    scorecard: Scorecard
    cockpit: Cockpit


async def build_report(
    pool: asyncpg.Pool, owner_id: str, since: dt.date | None = None
) -> OwnerReport:
    rows = await entries.list_entries(pool, owner_id, limit=2000)
    card = await journal_service.scorecard(pool, owner_id, since)
    cockpit = await build_cockpit(pool)
    snapshot_day = await pool.fetchval("select max(snapshot_date) from public.position_snapshot")
    manifest = Manifest(
        generated_at=dt.datetime.now(dt.UTC),
        sources=[
            "Decision journal: entries you wrote, with the evidence stored at the time.",
            "Stored price series from your holding profiles (outcomes since each decision).",
            f"Trading212 position snapshot of {snapshot_day or 'no date: no snapshot exists'}.",
            "Provider access and quota records, identity decisions, fund holdings snapshots and"
            " researched claims awaiting review (data-quality checks).",
        ],
        coverage={
            "journal": f"{len(rows)} entries, all you have recorded",
            "scorecard": f"{card.entries} decisions from {card.since} to {card.until}",
            "cockpit": f"{len(cockpit.checks)} checks ran; {len(cockpit.not_covered)} not covered",
        },
        method_versions=dict(METHOD_VERSIONS),
        notes=[
            "Returns are price changes without dividends, in the instrument's currency.",
            "A section with fewer than five decisions is listed but not summarised.",
            "No paid-only source is used; a missing value is null, never estimated.",
        ],
    )
    return OwnerReport(manifest=manifest, journal=rows, scorecard=card, cockpit=cockpit)


def journal_csv(rows: list[JournalEntry]) -> str:
    """One line per entry; lists are joined with ' | '. Spreadsheet formulas are neutralised."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for e in rows:
        values = e.model_dump(mode="json")
        writer.writerow(
            [
                _cell(" | ".join(v) if isinstance(v, list) else v)
                for v in (values[c] for c in CSV_COLUMNS)
            ]
        )
    return out.getvalue()


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
