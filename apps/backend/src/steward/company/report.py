"""The versioned company report (plan M2.9).

The report holds three kinds of content and keeps them apart:

* **code-computed tables and scores** (key figures, the assessment rubric, segment mix), each a
  stored figure with its method version, never written by a model;
* **model-written prose** from the existing research pipeline: the latest research report for the
  company, with its sentence-level citations and the claim check that pipeline already applied.
  Nothing new is generated here, so reading a report costs no model call;
* **your assessment**: the cited case items and an editable conclusion.

Conflict flags list where these disagree or are out of date (research older than the latest
filing, failed statement checks, disputed relationships, case items without a citation, a rubric
that abstained). Each save stores a new version, so an earlier report can be reread as it was.
"""

# ruff: noqa: E501
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.contract import input_hash
from steward.analytics.versions import version
from steward.company.evidence import CaseView, Overview
from steward.company.ratios import RatiosView
from steward.company.rubric import Rubric
from steward.company.segments import SegmentAnalysis
from steward.company.service import VersionConflict
from steward.company.statements import StatementsView
from steward.research.run import ResearchBody

KEY_RATIOS = (
    "roic",
    "gross_margin",
    "fcf_margin",
    "net_debt_to_ebitda",
    "interest_coverage",
    "dilution_3y",
    "pe",
    "ev_to_ebit",
    "earnings_yield",
    "fcf_yield",
    "reverse_dcf_growth",
)


class KeyFigure(BaseModel):
    id: str
    label: str
    unit: str
    period: str | None
    value: Decimal | None
    own_median_5y: Decimal | None
    reason: str | None = None
    formula: str


class SegmentMix(BaseModel):
    breakdown: str
    period: str | None
    lines: list[tuple[str, Decimal | None]] = Field(
        description="(label, share of operating revenue)"
    )
    note: str | None = None


class ResearchExcerpt(BaseModel):
    report_id: int
    created_at: dt.datetime
    body: ResearchBody
    older_than_latest_filing: bool


class Conflict(BaseModel):
    severity: Literal["review", "note"]
    area: str
    text: str


class CompanyReport(BaseModel):
    ticker: str
    name: str | None
    as_of: dt.date
    key_figures: list[KeyFigure]
    key_figure_warnings: list[str]
    rubric: Rubric
    segments: list[SegmentMix]
    case_items: list[dict[str, Any]]
    research: ResearchExcerpt | None
    conflicts: list[Conflict]
    conclusion: str = ""
    method_version: str
    input_hash: str


class ReportVersionSummary(BaseModel):
    version: int
    created_at: dt.datetime
    conclusion_excerpt: str
    input_hash: str
    method_version: str


class ReportVersion(ReportVersionSummary):
    report: CompanyReport


def key_figures(view: RatiosView) -> list[KeyFigure]:
    rows = {r.id: r for g in view.groups for r in g.ratios}
    out: list[KeyFigure] = []
    for rid in KEY_RATIOS:
        r = rows.get(rid)
        if r is None:
            continue
        latest = r.latest
        out.append(
            KeyFigure(
                id=rid,
                label=r.label,
                unit=r.unit,
                period=latest.label if latest else None,
                value=latest.value if latest else None,
                own_median_5y=r.own_median_5y,
                reason=(latest.reason if latest and latest.value is None else None),
                formula=r.formula,
            )
        )
    return out


def segment_mix(seg: SegmentAnalysis) -> list[SegmentMix]:
    out: list[SegmentMix] = []
    for b in seg.breakdowns:
        lines = [(m.label, m.latest_share) for m in b.members if m.role == "operating"][:8]
        out.append(
            SegmentMix(
                breakdown=b.label,
                period=b.latest_period,
                lines=lines,
                note=b.difference_note,
            )
        )
    return out


def conflicts(
    *,
    rubric: Rubric,
    annual: StatementsView,
    case: CaseView,
    research_created: dt.datetime | None,
    disputed_edges: int,
    today: dt.date,
) -> list[Conflict]:
    out: list[Conflict] = []
    if research_created is None:
        out.append(
            Conflict(
                severity="note",
                area="Research",
                text="No model-written research report is stored for this company; the prose section is empty until one is run from the Research page.",
            )
        )
    elif annual.known_at is not None and research_created < annual.known_at:
        out.append(
            Conflict(
                severity="review",
                area="Research",
                text=f"The research report ({research_created:%d %b %Y}) is older than the latest stored filing ({annual.known_at:%d %b %Y}); its figures may not reflect the newest statements.",
            )
        )
    elif (today - research_created.date()).days > 120:
        out.append(
            Conflict(
                severity="note",
                area="Research",
                text=f"The research report is {(today - research_created.date()).days} days old.",
            )
        )
    failed = [c for c in annual.checks if c.passed is False]
    if failed:
        out.append(
            Conflict(
                severity="review",
                area="Statements",
                text="Statement checks that did not tie: "
                + "; ".join(f"{c.name} ({c.column})" for c in failed[:5]),
            )
        )
    if disputed_edges:
        out.append(
            Conflict(
                severity="review",
                area="Relationships",
                text=f"{disputed_edges} relationship(s) are marked disputed in the evidence graph; the report does not rely on them.",
            )
        )
    uncited = [i for i in case.items if not i.evidence]
    if uncited:
        out.append(
            Conflict(
                severity="review",
                area="Your assessment",
                text=f"{len(uncited)} of your case item(s) have no citation and stay drafts: "
                + "; ".join(i.statement[:60] for i in uncited[:3]),
            )
        )
    if rubric.abstained:
        out.append(
            Conflict(
                severity="note",
                area="Assessment rubric",
                text=rubric.reason or "The rubric abstained.",
            )
        )
    for c in rubric.components:
        if c.abstained:
            out.append(Conflict(severity="note", area=c.label, text=c.reason or "No score."))
    return out


def assemble(
    *,
    ticker: str,
    overview: Overview | None,
    ratios: RatiosView,
    rubric: Rubric,
    segments: SegmentAnalysis,
    annual: StatementsView,
    case: CaseView,
    research: tuple[int, dt.datetime, dict[str, Any]] | None,
    disputed_edges: int,
    today: dt.date,
) -> CompanyReport:
    excerpt = None
    if research is not None:
        rid, created, body = research
        excerpt = ResearchExcerpt(
            report_id=rid,
            created_at=created,
            body=ResearchBody.model_validate(body),
            older_than_latest_filing=bool(annual.known_at and created < annual.known_at),
        )
    figures = key_figures(ratios)
    mix = segment_mix(segments)
    items = [i.model_dump(mode="json") for i in case.items]
    flags = conflicts(
        rubric=rubric,
        annual=annual,
        case=case,
        research_created=research[1] if research else None,
        disputed_edges=disputed_edges,
        today=today,
    )
    return CompanyReport(
        ticker=ticker,
        name=overview.name if overview else None,
        as_of=today,
        key_figures=figures,
        key_figure_warnings=list(ratios.warnings),
        rubric=rubric,
        segments=mix,
        case_items=items,
        research=excerpt,
        conflicts=flags,
        method_version=version("company_report"),
        input_hash=input_hash(
            {
                "ratios": ratios.input_hash,
                "rubric": rubric.input_hash,
                "statements": annual.input_hash,
                "case": [(i.id, i.updated_at) for i in case.items],
                "research": research[0] if research else None,
                "day": today,
            }
        ),
    )


async def latest_research(
    pool: asyncpg.Pool, ticker: str
) -> tuple[int, dt.datetime, dict[str, Any]] | None:
    row = await pool.fetchrow(
        "select id, created_at, body from public.report where type='research' and ticker=$1"
        " order by created_at desc, id desc limit 1",
        ticker,
    )
    if row is None:
        return None
    body = row["body"]
    return row["id"], row["created_at"], json.loads(body) if isinstance(body, str) else dict(body)


async def disputed_edge_count(pool: asyncpg.Pool, ticker: str) -> int:
    n = await pool.fetchval(
        "select count(*) from public.company_graph_edge where subject_key=$1 and status='disputed'",
        ticker,
    )
    return int(n or 0)


async def list_versions(
    pool: asyncpg.Pool, owner_id: Any, ticker: str
) -> list[ReportVersionSummary]:
    rows = await pool.fetch(
        "select version, created_at, conclusion, input_hash, method_version"
        " from public.company_report_version where owner_id=$1 and ticker=$2 order by version desc",
        owner_id,
        ticker,
    )
    return [
        ReportVersionSummary(
            version=r["version"],
            created_at=r["created_at"],
            conclusion_excerpt=(r["conclusion"] or "")[:160],
            input_hash=r["input_hash"],
            method_version=r["method_version"],
        )
        for r in rows
    ]


async def load_version(
    pool: asyncpg.Pool, owner_id: Any, ticker: str, number: int
) -> ReportVersion | None:
    r = await pool.fetchrow(
        "select version, created_at, conclusion, input_hash, method_version, snapshot"
        " from public.company_report_version where owner_id=$1 and ticker=$2 and version=$3",
        owner_id,
        ticker,
        number,
    )
    if r is None:
        return None
    snap = r["snapshot"]
    report = CompanyReport.model_validate(json.loads(snap) if isinstance(snap, str) else snap)
    report.conclusion = r["conclusion"]
    return ReportVersion(
        version=r["version"],
        created_at=r["created_at"],
        conclusion_excerpt=(r["conclusion"] or "")[:160],
        input_hash=r["input_hash"],
        method_version=r["method_version"],
        report=report,
    )


async def save_version(
    pool: asyncpg.Pool, owner_id: Any, report: CompanyReport, conclusion: str
) -> ReportVersion:
    snapshot = report.model_copy(update={"conclusion": ""}).model_dump(mode="json")
    research_id = report.research.report_id if report.research else None
    try:
        async with pool.acquire() as con, con.transaction():
            # One save per owner and ticker at a time, so max+1 cannot hand out a number twice.
            await con.execute(
                "select pg_advisory_xact_lock(hashtext($1))",
                f"company_report_version:{owner_id}:{report.ticker}",
            )
            row = await con.fetchrow(
                """insert into public.company_report_version
                     (owner_id, ticker, version, snapshot, research_report_id, conclusion, method_version, input_hash)
                   select $1, $2, coalesce(max(version), 0) + 1, $3::jsonb, $4, $5, $6, $7
                   from public.company_report_version where owner_id=$1 and ticker=$2
                   returning version, created_at""",
                owner_id,
                report.ticker,
                json.dumps(snapshot),
                research_id,
                conclusion,
                report.method_version,
                report.input_hash,
            )
    except asyncpg.UniqueViolationError as err:
        raise VersionConflict("Another save took this version number; try again.") from err
    assert row is not None
    report = report.model_copy(update={"conclusion": conclusion})
    return ReportVersion(
        version=row["version"],
        created_at=row["created_at"],
        conclusion_excerpt=conclusion[:160],
        input_hash=report.input_hash,
        method_version=report.method_version,
        report=report,
    )
