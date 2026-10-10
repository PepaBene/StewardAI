"""Authenticated routes for the decision journal, the yearly scorecard and the data-quality
cockpit (plan Phase 7). The journal is append-only: there is no edit or delete route."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from api.auth import Owner
from api.db import Pool
from steward.journal import entries, process
from steward.journal import export as owner_export
from steward.journal import service as journal_service
from steward.journal.entries import JournalEntry, JournalEntryIn
from steward.journal.export import OwnerReport
from steward.journal.scorecard import Scorecard
from steward.quality import snooze
from steward.quality.cockpit import Cockpit, build_cockpit

router = APIRouter(tags=["journal"])


@router.get("/journal")
async def list_journal(
    pool: Pool,
    owner: Owner,
    ticker: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[JournalEntry]:
    """Journal entries, newest decision first, optionally for one security. `offset` skips that
    many newer entries so older ones can be read in pages."""
    return await entries.list_entries(pool, owner.user_id, ticker, limit, offset)


@router.post("/journal", status_code=201)
async def add_journal_entry(body: JournalEntryIn, pool: Pool, owner: Owner) -> JournalEntry:
    """Record a decision with the reasons written now. The thesis, holding, price and the config
    and method versions in force are stored with it, so a later outcome can be judged against what
    was known. An entry is never edited; to revisit one, add a review that names it."""
    try:
        return await entries.record(pool, owner.user_id, body)
    except entries.JournalError as err:
        raise HTTPException(422, str(err)) from err


@router.get("/journal/scorecard")
async def get_scorecard(
    pool: Pool,
    owner: Owner,
    since: Annotated[dt.date | None, Query(description="Defaults to one year ago.")] = None,
    ticker: Annotated[str | None, Query(max_length=40, description="One security.")] = None,
    reason: Annotated[str | None, Query(max_length=40, description="One main reason.")] = None,
) -> Scorecard:
    """Sell targets reached, returns by conviction and reason, bought against passed, and what
    stocks did after they were sold, each with its sample size. Below five decisions a section
    says so instead of reporting a figure. `ticker` and `reason` limit it to one security or one
    main reason, which usually leaves too few decisions to summarise; the sections say so."""
    return await journal_service.scorecard(pool, owner.user_id, since, ticker, reason)


@router.get("/journal/process")
async def get_process(pool: Pool, owner: Owner) -> process.ProcessReport:
    """Closed theses (a buy or add followed by a sell): the price return over the holding
    period against the benchmark, the hit rate by tier (withheld below five theses) and how
    many of the losing ones had a portfolio finding open on the ticker before the sale."""
    return await process.process_report(pool, owner.user_id)


@router.get("/journal/export/report")
async def export_report(
    pool: Pool,
    owner: Owner,
    since: Annotated[dt.date | None, Query(description="Scorecard start; one year ago.")] = None,
) -> OwnerReport:
    """The journal, scorecard and data-quality checks in one file with a manifest of sources,
    coverage and calculation versions. Private: returned to you and stored nowhere."""
    return await owner_export.build_report(pool, owner.user_id, since)


@router.get("/journal/export/report.html", response_class=HTMLResponse)
async def export_report_html(
    pool: Pool,
    owner: Owner,
    since: Annotated[dt.date | None, Query(description="Scorecard start; one year ago.")] = None,
) -> HTMLResponse:
    """The same report as a print-ready page; use Print > Save as PDF."""
    report = await owner_export.build_report(pool, owner.user_id, since)
    return HTMLResponse(owner_export.report_html(report))


@router.get("/journal/export/report.pdf", response_class=Response)
async def export_report_pdf(
    pool: Pool,
    owner: Owner,
    since: Annotated[dt.date | None, Query(description="Scorecard start; one year ago.")] = None,
) -> Response:
    """The same report as a PDF file."""
    report = await owner_export.build_report(pool, owner.user_id, since)
    return Response(
        owner_export.report_pdf(report),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="owner-report.pdf"'},
    )


@router.get("/journal/export/entries.csv", response_class=PlainTextResponse)
async def export_entries_csv(pool: Pool, owner: Owner) -> PlainTextResponse:
    """Every journal entry as CSV, oldest decision last."""
    rows = await entries.list_entries(pool, owner.user_id, limit=None)
    return PlainTextResponse(
        owner_export.journal_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="decision-journal.csv"'},
    )


@router.get("/journal/{entry_id}")
async def get_journal_entry(entry_id: int, pool: Pool, owner: Owner) -> JournalEntry:
    found = await entries.get_entry(pool, owner.user_id, entry_id)
    if found is None:
        raise HTTPException(404, "That journal entry does not exist.")
    return found


@router.get("/quality/cockpit")
async def get_cockpit(pool: Pool, owner: Owner) -> Cockpit:
    """What is wrong with the data behind the portfolio, largest affected weight first, with the
    checks that ran, their denominators, and the checks that are not covered yet."""
    cockpit = await build_cockpit(pool)
    return snooze.with_snoozes(
        cockpit, await snooze.list_rows(pool, owner.user_id), dt.date.today()
    )


@router.post("/quality/snooze", status_code=201)
async def snooze_issue(body: snooze.SnoozeIn, pool: Pool, owner: Owner) -> snooze.SnoozeRow:
    """Set one data-quality issue aside until a date, or bring it back early. The check keeps
    running; the issue returns on its own when the date passes."""
    return await snooze.record(pool, owner.user_id, body)
