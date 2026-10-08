"""Personal owner report (M7.7): the journal, the scorecard and the data-quality checks in one
file, with a manifest of what they were built from. Private by default: it is returned to the
owner who asks and stored nowhere. Values the app does not hold stay null; nothing is filled in."""

from __future__ import annotations

import csv
import datetime as dt
import html
import io

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.versions import METHOD_VERSIONS
from steward.journal import entries
from steward.journal import service as journal_service
from steward.journal.entries import JournalEntry
from steward.journal.scorecard import Scorecard
from steward.quality import snooze
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
    figure_sources: dict[str, str] = Field(
        default_factory=dict,
        description="For each figure in the report, where its value comes from.",
    )
    method_versions: dict[str, str]
    notes: list[str]


FIGURE_SOURCES = {
    "journal.entries": "Decision journal table: rows you wrote; nothing is added.",
    "scorecard.hit_rate": "Journal entries compared with the stored prices since each decision.",
    "scorecard.returns": "Stored daily closes of your holdings; price change, no dividends.",
    "cockpit.checks": "Provider access, identity decisions, fund holdings and claims to review.",
}


class OwnerReport(BaseModel):
    manifest: Manifest
    journal: list[JournalEntry]
    scorecard: Scorecard
    cockpit: Cockpit
    snoozed: list[snooze.SnoozeRow] = Field(
        default_factory=list, description="Data-quality issues you set aside, with their dates."
    )


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
        figure_sources=dict(FIGURE_SOURCES),
        method_versions=dict(METHOD_VERSIONS),
        notes=[
            "Returns are price changes without dividends, in the instrument's currency.",
            "A section with fewer than five decisions is listed but not summarised.",
            "No paid-only source is used; a missing value is null, never estimated.",
        ],
    )
    return OwnerReport(
        manifest=manifest,
        journal=rows,
        scorecard=card,
        cockpit=cockpit,
        snoozed=await snooze.list_rows(pool, owner_id),
    )


def report_html(report: OwnerReport) -> str:
    """A print-ready page: open it and use the browser's Print > Save as PDF. Every value is
    escaped; there is no script and nothing is fetched."""
    e = html.escape
    m = report.manifest
    rows = "".join(
        f"<tr><td>{e(str(x.decided_on))}</td><td>{e(x.ticker or x.scope)}</td>"
        f"<td>{e(x.decision)}</td><td>{e(x.main_reason or '')}</td></tr>"
        for x in report.journal
    )
    checks = "".join(f"<li>{e(c.model_dump_json())}</li>" for c in report.cockpit.checks)
    snoozed = "".join(
        f"<li>{e(s.kind)} {e(s.subject)}: {e(s.action)}"
        f"{' until ' + e(str(s.until)) if s.until else ''}"
        f"{' (' + e(s.note) + ')' if s.note else ''}</li>"
        for s in report.snoozed
    )
    sources = "".join(f"<li>{e(s)}</li>" for s in m.sources)
    figures = "".join(f"<li><b>{e(k)}</b>: {e(v)}</li>" for k, v in m.figure_sources.items())
    coverage = "".join(f"<li><b>{e(k)}</b>: {e(v)}</li>" for k, v in m.coverage.items())
    notes = "".join(f"<li>{e(n)}</li>" for n in m.notes)
    card = e(report.scorecard.model_dump_json(indent=1))
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Owner report</title>'
        "<style>body{font:14px/1.5 system-ui;max-width:48rem;margin:2rem auto;padding:0 1rem}"
        "table{border-collapse:collapse;width:100%}td,th{border-top:1px solid #999;"
        "padding:.25rem .4rem;text-align:left;vertical-align:top}pre{white-space:pre-wrap}"
        "@media print{body{margin:0}}</style>"
        f"<h1>Owner report</h1><p>Generated {e(m.generated_at.isoformat())}. {e(m.visibility)}.</p>"
        f"<h2>Decision journal</h2><table><tr><th>Date</th><th>Subject</th><th>Decision</th>"
        f"<th>Reason</th></tr>{rows}</table>"
        f"<h2>Scorecard</h2><pre>{card}</pre>"
        f"<h2>Data-quality checks</h2><ul>{checks}</ul>"
        f"<h2>Issues set aside</h2><ul>{snoozed or '<li>None</li>'}</ul>"
        f"<h2>Sources</h2><ul>{sources}</ul><h2>Where each figure comes from</h2><ul>{figures}</ul>"
        f"<h2>Coverage</h2><ul>{coverage}</ul><h2>Notes</h2><ul>{notes}</ul></html>"
    )


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
