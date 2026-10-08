"""Snoozing a data-quality issue (M7.5). A snooze hides one issue until a date; it never removes the
check, and the issue returns on its own when the date passes. Rows are append-only: ending a snooze
early is a newer 'clear' row, so the history of what was set aside stays on record."""

from __future__ import annotations

import datetime as dt
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field, model_validator

from steward.quality.cockpit import Cockpit, IssueKind, QualityIssue, SnoozedIssue

MAX_SNOOZE_DAYS = 180


class SnoozeIn(BaseModel):
    kind: IssueKind
    subject: str = Field(min_length=1, max_length=120)
    action: Literal["snooze", "clear"] = "snooze"
    until: dt.date | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _until(self) -> SnoozeIn:
        if self.action == "snooze":
            if self.until is None:
                raise ValueError("Choose the date the issue should come back.")
            if self.until <= dt.date.today():
                raise ValueError("The date must be in the future.")
            if (self.until - dt.date.today()).days > MAX_SNOOZE_DAYS:
                raise ValueError(f"A snooze lasts at most {MAX_SNOOZE_DAYS} days.")
        return self


class SnoozeRow(BaseModel):
    id: int
    kind: IssueKind
    subject: str
    action: Literal["snooze", "clear"]
    until: dt.date | None
    note: str | None


def with_snoozes(cockpit: Cockpit, rows: list[SnoozeRow], today: dt.date) -> Cockpit:
    """The cockpit with set-aside issues moved to `snoozed`. The check counts and the affected
    weight describe what is still open."""
    active, snoozed = split_snoozed(cockpit.issues, rows, today)
    counts: dict[str, int] = {}
    for i in active:
        counts[i.kind] = counts.get(i.kind, 0) + 1
    weights = {i.subject: i.weight for i in active if i.weight is not None}
    return cockpit.model_copy(
        update={
            "issues": active,
            "snoozed": snoozed,
            "checks": [
                c.model_copy(update={"issues": counts.get(c.kind, 0)}) for c in cockpit.checks
            ],
            "affected_weight": sum(weights.values()),
        }
    )


def split_snoozed(
    issues: list[QualityIssue], rows: list[SnoozeRow], today: dt.date
) -> tuple[list[QualityIssue], list[SnoozedIssue]]:
    """Issues still active, and those set aside. The newest row for an issue decides, and a
    snooze whose date has passed no longer hides it."""
    newest: dict[tuple[str, str], SnoozeRow] = {}
    for r in sorted(rows, key=lambda r: r.id):
        newest[(r.kind, r.subject)] = r
    active: list[QualityIssue] = []
    snoozed: list[SnoozedIssue] = []
    for issue in issues:
        row = newest.get((issue.kind, issue.subject))
        if row and row.action == "snooze" and row.until and row.until >= today:
            snoozed.append(SnoozedIssue(issue=issue, until=row.until, note=row.note))
        else:
            active.append(issue)
    return active, snoozed


async def record(pool: asyncpg.Pool, owner_id: str, body: SnoozeIn) -> SnoozeRow:
    row = await pool.fetchrow(
        "insert into public.quality_snooze (owner_id, kind, subject, action, until, note)"
        " values ($1,$2,$3,$4,$5,$6) returning id, kind, subject, action, until, note",
        owner_id, body.kind, body.subject.strip(), body.action, body.until, body.note,
    )  # fmt: skip
    return SnoozeRow(**dict(row))


async def list_rows(pool: asyncpg.Pool, owner_id: str) -> list[SnoozeRow]:
    rows = await pool.fetch(
        "select id, kind, subject, action, until, note from public.quality_snooze"
        " where owner_id = $1 order by id desc limit 2000",
        owner_id,
    )
    return [SnoozeRow(**dict(r)) for r in rows]
