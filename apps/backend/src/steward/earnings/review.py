"""The owner's review queue and overrides.

Facts the validator held (sanity bound, restatement, a currency other than the reporting
one) and facts quarantined by cross-source reconciliation wait here. Neither reaches a
verdict (verdicts use verified facts only) until the owner decides:

- accept: the fact becomes verified as extracted;
- correct: the owner enters the right value; it is stored as a new fact (origin `owner`,
  restating the old one), and the old one is rejected. The owner's value always wins;
- reject: the fact is kept for the record and used nowhere.

Every decision needs a reason and is logged in `fact_review`; the holding's verdicts are
then re-judged (the caller queues the run).
"""

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.earnings.read import FACT_COLUMNS, FactOut, fact_columns, fact_out

ReviewAction = Literal["accept", "correct", "reject"]

ALLOWED_FROM: dict[str, set[str]] = {
    "accept": {"unverified", "quarantined"},
    "correct": {"unverified", "quarantined", "verified"},
    "reject": {"unverified", "quarantined", "verified"},
}


class ReviewError(Exception):
    def __init__(self, message: str, *, not_found: bool = False) -> None:
        super().__init__(message)
        self.not_found = not_found


@dataclass(frozen=True)
class ReviewOutcome:
    fact_id: int
    ticker: str
    status: str
    corrected_fact_id: int | None


async def review_fact(
    pool: asyncpg.Pool,
    fact_id: int,
    action: ReviewAction,
    reason: str,
    *,
    owner_id: str | None,
    corrected_value: Decimal | None = None,
) -> ReviewOutcome:
    reason = reason.strip()
    if not reason:
        raise ReviewError("a reason is required")
    if action == "correct" and corrected_value is None:
        raise ReviewError("a correction needs the corrected value")
    async with pool.acquire() as conn, conn.transaction():
        fact = await conn.fetchrow("select * from public.fact where id = $1 for update", fact_id)
        if fact is None:
            raise ReviewError(f"No fact {fact_id}", not_found=True)
        if fact["status"] not in ALLOWED_FROM[action]:
            raise ReviewError(f"Fact {fact_id} is {fact['status']}; it cannot be {action}ed")
        now = dt.datetime.now(dt.UTC)
        corrected_id: int | None = None
        if action == "accept":
            new_status = "verified"
            status_reason = f"accepted by the owner: {reason}"
        elif action == "reject":
            new_status = "rejected"
            status_reason = f"rejected by the owner: {reason}"
        else:
            assert corrected_value is not None
            corrected_id = await conn.fetchval(
                "insert into public.fact (ticker, metric, basis, fiscal_year, fiscal_period,"
                " period, period_end, value, value_as_written, scale, unit, currency,"
                " source_url, snippet, unit_snippet, evidence_sha256, evidence_path, status,"
                " status_reason, checks, restates_fact_id, config_version_ids, origin,"
                " reviewed_at, reviewed_by)"
                " select ticker, metric, basis, fiscal_year, fiscal_period, period, period_end,"
                " $2, $3, scale, unit, currency, source_url, snippet, unit_snippet,"
                " evidence_sha256, evidence_path, 'verified', $4, $5::jsonb, id,"
                " config_version_ids, 'owner', $6, $7::uuid from public.fact where id = $1"
                " returning id",
                fact_id,
                corrected_value,
                format(corrected_value, "f"),
                f"owner correction: {reason}",
                json.dumps([{"check": "owner_override", "ok": True, "detail": reason}]),
                now,
                owner_id,
            )
            new_status = "rejected"
            status_reason = f"corrected by the owner (fact #{corrected_id}): {reason}"
        await conn.execute(
            "update public.fact set status = $2, status_reason = $3, reviewed_at = $4,"
            " reviewed_by = $5::uuid where id = $1",
            fact_id,
            new_status,
            status_reason,
            now,
            owner_id,
        )
        await conn.execute(
            "insert into public.fact_review (fact_id, action, old_status, new_status, reason,"
            " corrected_fact_id, reviewed_by) values ($1, $2, $3, $4, $5, $6, $7::uuid)",
            fact_id,
            action,
            fact["status"],
            new_status,
            reason,
            corrected_id,
            owner_id,
        )
    return ReviewOutcome(fact_id, fact["ticker"], new_status, corrected_id)


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


class ReconciliationOut(BaseModel):
    concept: str | None
    accession: str | None
    form: str | None
    period_end: dt.date | None
    derivation: str | None
    xbrl_value: float | None
    difference: float | None
    outcome: Literal["match", "mismatch", "no_data"]
    detail: str | None
    checked_at: dt.datetime


class FailedCheck(BaseModel):
    check: str
    detail: str


class ReviewItem(BaseModel):
    ticker: str
    name: str | None
    fact: FactOut
    why: str = Field(description="Held by the validator, or quarantined by reconciliation.")
    failed_checks: list[FailedCheck] = Field(
        description="The checks (bounds) the figure broke, with their detail."
    )
    restates: FactOut | None = Field(description="The stored fact a restatement differs from.")
    reconciliation: list[ReconciliationOut]


async def review_queue(pool: asyncpg.Pool, ticker: str | None = None) -> list[ReviewItem]:
    rows = await pool.fetch(
        f"select f.ticker, i.name, {fact_columns('f')}"  # noqa: S608
        " from public.fact f left join public.instrument i on i.t212_ticker = f.ticker"
        " where f.status in ('unverified', 'quarantined')"
        " and ($1::text is null or f.ticker = $1) order by f.ticker, f.id",
        ticker,
    )
    items = []
    for row in rows:
        fact = fact_out(row)
        restates = None
        if fact.restates_fact_id is not None:
            prior = await pool.fetchrow(
                f"select {FACT_COLUMNS} from public.fact where id = $1",  # noqa: S608
                fact.restates_fact_id,
            )
            restates = fact_out(prior) if prior else None
        recon = await pool.fetch(
            "select concept, accession, form, period_end, derivation, xbrl_value, difference,"
            " outcome, detail, checked_at from public.fact_reconciliation where fact_id = $1"
            " order by id desc",
            fact.id,
        )
        items.append(
            ReviewItem(
                ticker=row["ticker"],
                name=row["name"],
                fact=fact,
                why=(
                    "quarantined by cross-source reconciliation"
                    if fact.status == "quarantined"
                    else "held by the validator"
                ),
                failed_checks=[
                    FailedCheck(check=c.check, detail=c.detail) for c in fact.checks if not c.ok
                ],
                restates=restates,
                reconciliation=[ReconciliationOut(**dict(r)) for r in recon],
            )
        )
    return items
