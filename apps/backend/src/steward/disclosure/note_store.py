"""P2.4: persist extracted note buckets and turn them into validator results.

No new table: a bucket is one `financial_statement_fact` row with statement='note', concept
`steward-note:<schedule>` and the bucket, offsets, status and reasons in `dimensions->'note'`.
`dimensions->'members'` stays empty, so the row is never read as a dimensional statement fact, and
the statement reader's own query (income, cash flow, balance sheet, shares) never sees it.

The reader side stays point in time: only rows with `known_at <= as_of` count, and for each
period the latest filing that carried a given schedule wins as a whole, never row by row.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure.financials import (
    DEFAULT_TOLERANCE,
    Check,
    Repurchase,
    check_debt_maturities,
    check_lease_schedule,
    check_repurchase,
)
from steward.disclosure.note_tables import RepurchaseTable, Schedule

type Pool = asyncpg.Pool | asyncpg.Connection

NOTE_SOURCE = "note_extract"
EXTRACTOR_VERSION = "note_tables-1"
CONCEPT_PREFIX = "steward-note:"
SCHEDULES = ("debt_maturity", "lease_maturity", "repurchase")


def _s(value: Decimal | dt.date | None) -> str | None:
    return None if value is None else str(value)


def build_note_fact_rows(
    *,
    debt: Schedule | None = None,
    lease: Schedule | None = None,
    repurchases: RepurchaseTable | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """The rows to store, as plain dicts: schedule, value, value_text and the `note` dimension.
    `offset` moves the extractor's block-relative offsets to offsets in the whole document."""
    out: list[dict[str, Any]] = []
    for sched in (debt, lease):
        if sched is None:
            continue
        mult = sched.unit.multiplier
        for r in sched.rows:
            out.append(
                {
                    "schedule": sched.kind,
                    "value": r.value,
                    "value_text": r.raw or "null",
                    "note": {
                        "schedule": sched.kind,
                        "bucket": r.key,
                        "label": r.label,
                        "status": r.status,
                        "reasons": list(r.reasons),
                        "start": r.start + offset,
                        "end": r.end + offset,
                        "raw": r.raw,
                        "columns": list(r.columns),
                        "dash": r.dash,
                        "unit_multiplier": _s(mult),
                        "extractor": EXTRACTOR_VERSION,
                    },
                }
            )
    if repurchases is not None:
        for i, p in enumerate(repurchases.rows):
            out.append(
                {
                    "schedule": "repurchase",
                    "value": p.amount,
                    "value_text": ", ".join(p.columns) or "null",
                    "note": {
                        "schedule": "repurchase",
                        "bucket": f"row_{i + 1}",
                        "label": p.period_label,
                        "status": p.status,
                        "reasons": list(p.reasons),
                        "start": p.start + offset,
                        "end": p.end + offset,
                        "raw": ", ".join(p.columns),
                        "columns": list(p.columns),
                        "shares": _s(p.shares),
                        "average_price": _s(p.average_price),
                        "period_start": _s(p.period_start),
                        "period_end": _s(p.period_end),
                        "share_class": p.share_class,
                        "is_total": p.is_total,
                        "unit_multiplier": _s(repurchases.unit.multiplier),
                        "extractor": EXTRACTOR_VERSION,
                    },
                }
            )
    return out


async def store_note_tables(
    pool: Pool,
    *,
    issuer_id: int,
    ticker: str | None,
    accession: str,
    form: str | None,
    period_end: dt.date,
    known_at: dt.datetime,
    source_url: str,
    currency: str,
    debt: Schedule | None = None,
    lease: Schedule | None = None,
    repurchases: RepurchaseTable | None = None,
    evidence_id: int | None = None,
    offset: int = 0,
) -> int:
    """Insert the extracted buckets once; returns the number of new rows. `known_at` is the
    filing's public time. The currency is required because note text does not carry one."""
    inserted = 0
    for row in build_note_fact_rows(debt=debt, lease=lease, repurchases=repurchases, offset=offset):
        note = row["note"]
        res = await pool.fetchval(
            """insert into public.financial_statement_fact
                 (issuer_id, ticker, accession, form, concept, line_item, statement, period_end,
                  basis, value, value_text, unit, currency, dimensions, source, source_url,
                  known_at, evidence_id)
               select $1,$2,$3,$4,$5,$6,'note',$7,'as_filed',$8,$9,$10,$10,$11::jsonb,$12,$13,
                      $14,$15
                where not exists (
                  select 1 from public.financial_statement_fact
                   where issuer_id=$1 and accession=$3 and concept=$5 and source=$12
                     and dimensions #>> '{note,start}' = $16
                     and dimensions #>> '{note,extractor}' = $17
                     and dimensions #>> '{note,bucket}' is not distinct from $18)
               returning id""",
            issuer_id,
            ticker,
            accession,
            form,
            CONCEPT_PREFIX + row["schedule"],
            row["schedule"],
            period_end,
            row["value"],
            row["value_text"],
            currency,
            json.dumps({"members": [], "note": note}),
            NOTE_SOURCE,
            source_url,
            known_at,
            evidence_id,
            str(note["start"]),
            EXTRACTOR_VERSION,
            note["bucket"],
        )
        inserted += res is not None
    return inserted


# ----------------------------------------------------------------------------------- reading
@dataclass(frozen=True)
class StoredNote:
    schedule: str
    bucket: str | None
    value: Decimal | None
    status: str
    reasons: tuple[str, ...]
    dash: bool
    note: dict[str, Any]
    accession: str
    fact_id: int


async def load_note_rows(
    pool: Pool, issuer_id: int, as_of: dt.datetime
) -> dict[dt.date, list[StoredNote]]:
    """Stored note buckets known by `as_of`, per period. For each (period, schedule) only the
    latest filing's rows are kept, so an older filing's buckets never mix with a newer one's."""
    rows = await pool.fetch(
        """select id, accession, period_end, value, known_at, dimensions::text dims
             from public.financial_statement_fact
            where issuer_id=$1 and statement='note' and source=$2 and known_at <= $3""",
        issuer_id,
        NOTE_SOURCE,
        as_of,
    )
    grouped: dict[tuple[dt.date, str], list[Any]] = defaultdict(list)
    for r in rows:
        note = json.loads(r["dims"]).get("note") or {}
        grouped[(r["period_end"], str(note.get("schedule")))].append((r, note))
    out: dict[dt.date, list[StoredNote]] = defaultdict(list)
    for (end, _), items in grouped.items():
        latest = max(items, key=lambda it: (it[0]["known_at"], it[0]["accession"]))[0]
        for r, note in items:
            if r["accession"] != latest["accession"]:
                continue
            out[end].append(
                StoredNote(
                    str(note.get("schedule")),
                    note.get("bucket"),
                    None if r["value"] is None else Decimal(str(r["value"])),
                    str(note.get("status", "review")),
                    tuple(note.get("reasons") or ()),
                    bool(note.get("dash")),
                    note,
                    r["accession"],
                    int(r["id"]),
                )
            )
    return dict(out)


# ---------------------------------------------------------------------------------- checks
def _review(name: str, why: str) -> Check:
    return Check(name, False, None, None, DEFAULT_TOLERANCE, why, "needs_review")


def _blocked(rows: list[StoredNote]) -> str | None:
    bad = [r for r in rows if r.status != "extracted"]
    if not bad:
        return None
    why = sorted({reason for r in bad for reason in r.reasons if not reason.startswith("dash")})
    return f"{len(bad)} extracted row(s) need review: {'; '.join(why) or 'unspecified'}"


def _dash_note(rows: list[StoredNote]) -> str:
    dashes = sorted(str(r.bucket) for r in rows if r.dash)
    return f" (dash buckets kept null, not zero: {', '.join(dashes)})" if dashes else ""


def _amounts(rows: list[StoredNote]) -> dict[str, Decimal]:
    return {
        str(r.bucket): r.value
        for r in rows
        if r.bucket
        and (r.bucket.startswith("year_") or r.bucket == "thereafter")
        and r.value is not None
    }


def _one(rows: list[StoredNote], bucket: str) -> Decimal | None:
    return next((r.value for r in rows if r.bucket == bucket), None)


def note_checks(rows: list[StoredNote]) -> list[Check]:
    """Run the existing validators on one period's stored buckets. An empty list means the
    period has no note data; the caller says so explicitly instead of passing."""
    by: dict[str, list[StoredNote]] = defaultdict(list)
    for r in rows:
        by[r.schedule].append(r)
    checks: list[Check] = []
    if debt := by.get("debt_maturity"):
        if why := _blocked(debt):
            checks.append(_review("debt_maturities", why))
        else:
            disc = _one(debt, "discount")
            c = check_debt_maturities(
                _amounts(debt), _one(debt, "total"), unamortized_discount=abs(disc or Decimal(0))
            )
            checks.append(dataclasses.replace(c, detail=c.detail + _dash_note(debt)))
    if lease := by.get("lease_maturity"):
        if why := _blocked(lease):
            checks.append(_review("lease_schedule", why))
        else:
            interest = _one(lease, "imputed_interest")
            c = check_lease_schedule(
                _amounts(lease),
                None if interest is None else abs(interest),
                _one(lease, "present_value"),
            )
            checks.append(dataclasses.replace(c, detail=c.detail + _dash_note(lease)))
    for r in by.get("repurchase", []):
        if r.note.get("is_total"):
            continue
        label = str(r.note.get("label"))
        if r.status != "extracted":
            reasons = "; ".join(x for x in r.reasons if "dash kept" not in x) or "unspecified"
            checks.append(_review("repurchase", f"{label}: {reasons}"))
            continue
        shares = r.note.get("shares")
        end = r.note.get("period_end")
        c = check_repurchase(
            Repurchase(
                r.value,
                None if shares is None else Decimal(shares),
                dt.date.fromisoformat(r.note["period_start"])
                if r.note.get("period_start")
                else None,
                dt.date.fromisoformat(end) if end else None,
                r.note.get("share_class"),
            )
        )
        checks.append(dataclasses.replace(c, detail=f"{label}: {c.detail}"))
    return checks
