"""The source-to-screen audit ledger (M8.2): a displayed fact, the value in its cited source and the
result. Rows are append-only; a re-check is a new row and the newest row per fact wins.

The ledger only records and counts. It never decides that a fact is right: a person compares the
screen with the filing, issuer report or official statistic and enters what they found."""

from __future__ import annotations

import datetime as dt
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field, model_validator

SubjectKind = Literal["security", "etf", "corporate_action", "cash_flow_window"]
AuditResult = Literal["match", "mismatch", "not_checkable"]

# The sample the plan asks for before a feature can be called audited.
TARGETS: dict[str, int] = {
    "security": 20,
    "etf": 5,
    "corporate_action": 3,
    "cash_flow_window": 2,
}
MIN_REGIONS = 3
# A feature's audit reads "passed" only with this many matched facts on this many subjects.
MIN_FACTS_PER_FEATURE = 3
MIN_SUBJECTS_PER_FEATURE = 2

# Spellings that mean the same region, so "EU" and "Europe" count once.
REGION_ALIASES: dict[str, str] = {
    "eu": "Europe",
    "europe": "Europe",
    "emea": "Europe",
    "uk": "Europe",
    "us": "North America",
    "usa": "North America",
    "united states": "North America",
    "north america": "North America",
    "canada": "North America",
    "asia": "Asia-Pacific",
    "apac": "Asia-Pacific",
    "asia-pacific": "Asia-Pacific",
    "asia pacific": "Asia-Pacific",
    "japan": "Asia-Pacific",
    "latam": "Latin America",
    "latin america": "Latin America",
    "em": "Emerging markets",
    "emerging markets": "Emerging markets",
}


def normalise_region(region: str | None) -> str | None:
    if region is None:
        return None
    text = " ".join(region.split())
    if not text:
        return None
    return REGION_ALIASES.get(text.casefold(), text)


def _squash(text: str) -> str:
    return " ".join(text.split()).casefold()


def fact_key(
    subject_kind: str, subject: str, feature: str, field: str
) -> tuple[str, str, str, str]:
    """Identity of a fact: case and spacing in the typed names do not make a second fact."""
    return (subject_kind, _squash(subject), _squash(feature), _squash(field))


_COLUMNS = (
    "id, subject_kind, subject, region, feature, field, displayed, source_value, source_url,"
    " result, note, audited_on, created_at"
)


class AuditIn(BaseModel):
    subject_kind: SubjectKind
    subject: str = Field(min_length=1, max_length=80, description="Ticker, ISIN or window name.")
    region: str | None = Field(default=None, max_length=40)
    feature: str = Field(min_length=1, max_length=60, description="Release-gate feature id.")
    field: str = Field(min_length=1, max_length=120, description="The fact that was compared.")
    displayed: str = Field(max_length=400, description="What the screen showed.")
    source_value: str | None = Field(default=None, max_length=400)
    source_url: str | None = Field(default=None, max_length=1000)
    result: AuditResult
    note: str | None = Field(default=None, max_length=1000)
    audited_on: dt.date | None = Field(default=None, description="Defaults to today.")

    @model_validator(mode="after")
    def _needs_source(self) -> AuditIn:
        if self.result != "not_checkable" and not (self.source_value and self.source_url):
            raise ValueError("Enter the value found in the source and a link to the source.")
        if self.subject_kind == "security" and not self.region:
            raise ValueError("Name the security's region so regional spread can be counted.")
        return self


class AuditRow(BaseModel):
    id: int
    subject_kind: SubjectKind
    subject: str
    region: str | None
    feature: str
    field: str
    displayed: str
    source_value: str | None
    source_url: str | None
    result: AuditResult
    note: str | None
    audited_on: dt.date
    created_at: dt.datetime


class TargetProgress(BaseModel):
    subject_kind: SubjectKind
    target: int
    audited: int = Field(description="Subjects with at least one fact compared.")
    clean: int = Field(description="Subjects whose newest result is a match for every fact.")
    with_mismatch: int


class AuditSummary(BaseModel):
    facts_checked: int
    facts_matching: int
    facts_mismatched: int
    facts_not_checkable: int
    targets: list[TargetProgress]
    regions: list[str]
    regions_needed: int
    complete: bool = Field(description="Every target and the region spread are met.")
    open_mismatches: list[AuditRow]


def newest_per_fact(rows: list[AuditRow]) -> list[AuditRow]:
    """The newest row for each (subject, field), which is the standing result for that fact."""
    latest: dict[tuple[str, str, str, str], AuditRow] = {}
    for r in sorted(rows, key=lambda r: r.id):
        latest[fact_key(r.subject_kind, r.subject, r.feature, r.field)] = r
    return list(latest.values())


def summarize(rows: list[AuditRow]) -> AuditSummary:
    standing = newest_per_fact(rows)
    by_subject: dict[tuple[str, str], list[AuditRow]] = {}
    for r in standing:
        by_subject.setdefault((r.subject_kind, r.subject.casefold()), []).append(r)
    progress: list[TargetProgress] = []
    for kind, target in TARGETS.items():
        subjects = [v for (k, _), v in by_subject.items() if k == kind]
        compared = [v for v in subjects if any(r.result != "not_checkable" for r in v)]
        bad = [v for v in compared if any(r.result == "mismatch" for r in v)]
        progress.append(
            TargetProgress(
                subject_kind=kind,
                target=target,
                audited=len(compared),
                clean=len(compared) - len(bad),
                with_mismatch=len(bad),
            )
        )
    named = (
        normalise_region(r.region)
        for r in standing
        if r.subject_kind == "security" and r.result != "not_checkable"
    )
    regions = sorted({n for n in named if n is not None})
    complete = (
        all(p.clean >= p.target and p.with_mismatch == 0 for p in progress)
        and len(regions) >= MIN_REGIONS
    )
    mismatches = [r for r in standing if r.result == "mismatch"]
    return AuditSummary(
        facts_checked=len(standing),
        facts_matching=sum(r.result == "match" for r in standing),
        facts_mismatched=len(mismatches),
        facts_not_checkable=sum(r.result == "not_checkable" for r in standing),
        targets=progress,
        regions=regions,
        regions_needed=MIN_REGIONS,
        complete=complete,
        open_mismatches=sorted(mismatches, key=lambda r: -r.id),
    )


def feature_has_open_mismatch(rows: list[AuditRow], feature: str) -> bool:
    return any(r.result == "mismatch" for r in newest_per_fact(rows) if r.feature == feature)


def feature_matches(rows: list[AuditRow], feature: str) -> int:
    return sum(r.result == "match" for r in newest_per_fact(rows) if r.feature == feature)


def feature_match_subjects(rows: list[AuditRow], feature: str) -> int:
    return len(
        {
            (r.subject_kind, _squash(r.subject))
            for r in newest_per_fact(rows)
            if r.feature == feature and r.result == "match"
        }
    )


async def record(pool: asyncpg.Pool, owner_id: str, body: AuditIn) -> AuditRow:
    row = await pool.fetchrow(
        "insert into public.source_audit"  # noqa: S608 - constant column list
        " (owner_id, subject_kind, subject, region, feature, field, displayed, source_value,"
        "  source_url, result, note, audited_on)"
        " values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,coalesce($12, current_date))"
        f" returning {_COLUMNS}",
        owner_id,
        body.subject_kind,
        body.subject.strip(),
        normalise_region(body.region),
        body.feature.strip(),
        body.field.strip(),
        body.displayed,
        body.source_value,
        body.source_url,
        body.result,
        body.note,
        body.audited_on,
    )
    return AuditRow(**dict(row))


async def list_rows(pool: asyncpg.Pool, owner_id: str, limit: int = 2000) -> list[AuditRow]:
    rows = await pool.fetch(
        f"select {_COLUMNS} from public.source_audit"  # noqa: S608 - constant column list
        " where owner_id = $1 order by id desc limit $2",
        owner_id,
        limit,
    )
    return [AuditRow(**dict(r)) for r in rows]
