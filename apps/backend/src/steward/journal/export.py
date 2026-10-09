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
    snoozes = await snooze.list_rows(pool, owner_id)
    # The same snoozes as the cockpit page: set-aside issues are listed under "set aside".
    cockpit = snooze.with_snoozes(await build_cockpit(pool), snoozes, dt.date.today())
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
        figure_sources=figure_sources_for(rows, card, cockpit, snapshot_day),
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
        snoozed=snoozes,
    )


def figure_sources_for(
    rows: list[JournalEntry], card: Scorecard, cockpit: Cockpit, snapshot_day: object
) -> dict[str, str]:
    """A source line for each number in the report: the general lines, then one per journal
    entry, per scorecard figure (with the entries behind it) and per cockpit check."""
    out = dict(FIGURE_SOURCES)
    for e in rows:
        price = (
            f"price {e.price_at_decision} {e.price_currency or ''} as stored with the entry"
            if e.price_at_decision is not None
            else "no price stored with the entry"
        )
        out[f"journal.entry.{e.id}"] = (
            f"Written {e.created_at:%Y-%m-%d} for the decision of {e.decided_on}; {price}."
        )
    for section in card.sections:
        for row in section.rows:
            ids = ", ".join(str(i) for i in row.entry_ids) or "none"
            out[f"scorecard.{section.id}.{row.label}"] = (
                f"Journal entries {ids}; outcomes from the latest stored closes of each"
                f" holding (price changes, no dividends); position snapshot {snapshot_day}."
            )
    for check in cockpit.checks:
        out[f"cockpit.{check.kind}"] = (
            f"{check.title}: {'ran' if check.ran else 'did not run'} over {check.denominator}."
        )
    return out


def _latin(text: object) -> str:
    """The built-in PDF fonts cover Latin-1; anything else becomes '?', never an error."""
    return str(text).encode("latin-1", "replace").decode("latin-1")


def report_pdf(report: OwnerReport) -> bytes:
    """The report as a PDF file made here (no browser needed). Plain text in the built-in
    fonts: the same sections and the same source lines as the page."""
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_title("Owner report")
    pdf.add_page()

    def heading(text: str) -> None:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 7, _latin(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 9)

    def line(text: str) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 4.5, _latin(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    m = report.manifest
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, "Owner report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    line(f"Generated {m.generated_at.isoformat()}. {m.visibility}.")
    heading("Decision journal")
    for x in report.journal:
        line(
            f"{x.decided_on}  {x.ticker or x.scope}  {x.decision}"
            + (f" - {x.main_reason}" if x.main_reason else "")
        )
    if not report.journal:
        line("No entries.")
    heading("Scorecard")
    card = report.scorecard
    line(f"{card.entries} decisions from {card.since} to {card.until}.")
    for section in card.sections:
        line(f"{section.title} [{section.status}]: {section.note}")
        for row in section.rows:
            parts = [f"n={row.n}"]
            if row.share is not None:
                parts.append(f"share {row.share:.0%}")
            if row.median_change is not None:
                parts.append(f"median change {row.median_change:+.1%}")
            line(f"   {row.label}: " + ", ".join(parts))
    heading("Data-quality checks")
    for c in report.cockpit.checks:
        line(
            f"{c.title}: {'ran' if c.ran else 'did not run'}; {c.issues} issue(s); {c.denominator}"
        )
    heading("Issues set aside")
    for s in report.snoozed:
        line(f"{s.kind} {s.subject}: {s.action}" + (f" until {s.until}" if s.until else ""))
    if not report.snoozed:
        line("None.")
    heading("Sources")
    for source in m.sources:
        line(f"- {source}")
    heading("Where each figure comes from")
    for k, v in m.figure_sources.items():
        line(f"- {k}: {v}")
    heading("Coverage")
    for k, v in m.coverage.items():
        line(f"- {k}: {v}")
    heading("Notes")
    for n in m.notes:
        line(f"- {n}")
    return bytes(pdf.output())


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
    # A spreadsheet ignores leading spaces before a formula, so test the first non-blank char.
    if text[:1] in ("\t", "\r") or text.lstrip()[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text
