"""Reviewer queue for low-confidence parsing, entity ambiguity, contradictions and format changes
(plan P1.7).

A queue row keeps the original candidate untouched. A decision records who decided and why, writes
an audit event, and applies its effect to the subject: an approved claim/observation becomes
`verified`; a rejected or superseded one is excluded from every authoritative read (`asof.py`)
but stays in the table. A relationship whose counterparty is unresolved cannot become a verified
named edge.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Literal

import asyncpg

Decision = Literal["approved", "rejected", "superseded", "deferred"]
Reason = Literal[
    "low_confidence", "entity_ambiguity", "contradiction", "format_change", "parse_failure"
]

LOW_CONFIDENCE = 0.7
_STATE_FOR = {"approved": "verified", "rejected": "rejected", "superseded": "superseded"}


class ReviewError(ValueError):
    """The decision is not allowed (missing reason, already decided, unresolved counterparty)."""


def needs_review(
    confidence: float | None, *, entity_resolved: bool = True, contradicted: bool = False
) -> Reason | None:
    """Why a machine result must be reviewed before it can be trusted, or None."""
    if not entity_resolved:
        return "entity_ambiguity"
    if contradicted:
        return "contradiction"
    if confidence is None or confidence < LOW_CONFIDENCE:
        return "low_confidence"
    return None


async def enqueue(
    pool: asyncpg.Pool | asyncpg.Connection,
    subject_type: str,
    subject_id: int,
    reason: Reason,
    candidate: dict[str, Any],
) -> int | None:
    """Open a review once per (subject, reason); None if one is already open or deferred, or if
    a person already decided one. A daily scan must not reopen what a person has closed."""
    opened: int | None = await pool.fetchval(
        """insert into public.quality_review(subject_type, subject_id, reason, candidate)
           select $1,$2,$3,$4::jsonb
            where not exists (select 1 from public.quality_review
                               where subject_type = $1 and subject_id = $2 and reason = $3
                                 and status in ('approved','rejected','superseded'))
           on conflict (subject_type, subject_id, reason) where status in ('open','deferred')
           do nothing returning id""",
        subject_type,
        subject_id,
        reason,
        json.dumps(candidate, default=str),
    )
    return opened


async def queue(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    status: str = "open",
    reason: str | None = None,
    subject_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """select id, subject_type, subject_id, reason, status, candidate, assignee,
                  decision_reason, decided_by, decided_at, created_at
             from public.quality_review
            where status = $1 and ($2::text is null or reason = $2)
              and ($3::text is null or subject_type = $3)
            order by created_at, id limit $4 offset $5""",
        status,
        reason,
        subject_type,
        limit,
        offset,
    )
    return [{**r, "candidate": _decode(r["candidate"])} for r in map(dict, rows)]


def _decode(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else json.loads(value or "{}")


async def history(pool: asyncpg.Pool | asyncpg.Connection, review_id: int) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "select at, actor, action, detail from public.quality_review_event"
        " where review_id=$1 order by at, id",
        review_id,
    )
    return [dict(r) for r in rows]


async def _apply(
    conn: asyncpg.Connection,
    subject_type: str,
    subject_id: int,
    decision: Decision,
    reviewer: str,
    reason: str,
    now: dt.datetime,
) -> None:
    state = _STATE_FOR.get(decision)
    if state is None:
        return
    if subject_type == "claim":
        claim = await conn.fetchrow(
            "select claim_type, object_issuer_id, object_anonymous from public.extracted_claim"
            " where id=$1 for update",
            subject_id,
        )
        if claim is None:
            raise ReviewError(f"claim {subject_id} does not exist")
        if (
            decision == "approved"
            and claim["claim_type"].startswith("relationship")
            and claim["object_issuer_id"] is None
            and not claim["object_anonymous"]
        ):
            raise ReviewError("an unresolved counterparty cannot be a verified named edge")
        await conn.execute(
            "update public.extracted_claim set review_state=$2, decided_by=$3, decided_at=$4"
            " where id=$1",
            subject_id,
            state,
            reviewer,
            now,
        )
    elif subject_type == "observation":
        await conn.execute(
            "update public.disclosure_observation set review_state=$2 where id=$1",
            subject_id,
            state,
        )
    elif subject_type == "identity_mapping":
        if decision == "approved":
            mapping = await conn.fetchrow(
                "select issuer_id, security_id, anonymous_label from"
                " public.disclosure_entity_mapping where id=$1 for update",
                subject_id,
            )
            if mapping is None or not (
                mapping["issuer_id"] or mapping["security_id"] or mapping["anonymous_label"]
            ):
                raise ReviewError("a mapping needs an entity or an anonymous label to confirm")
            await conn.execute(
                "update public.disclosure_entity_mapping set status='confirmed', decided_by=$2,"
                " decided_at=$3 where id=$1",
                subject_id,
                reviewer,
                now,
            )
        elif decision == "rejected":
            await conn.execute(
                "update public.disclosure_entity_mapping set status='rejected',"
                " rejection_reason=$2, decided_by=$3, decided_at=$4 where id=$1",
                subject_id,
                reason,
                reviewer,
                now,
            )


async def decide(
    pool: asyncpg.Pool,
    review_id: int,
    decision: Decision,
    reviewer: str,
    reason: str,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Record a decision atomically: the queue row, the effect on its subject and an audit event."""
    if not reason or not reason.strip():
        raise ReviewError("a decision needs a reason")
    if not reviewer.strip():
        raise ReviewError("a decision needs a reviewer")
    at = now or dt.datetime.now(dt.UTC)
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "select id, subject_type, subject_id, status, candidate from public.quality_review"
            " where id=$1 for update",
            review_id,
        )
        if row is None:
            raise ReviewError(f"review {review_id} does not exist")
        if row["status"] not in ("open", "deferred"):
            raise ReviewError(f"review {review_id} was already decided ({row['status']})")
        await _apply(conn, row["subject_type"], row["subject_id"], decision, reviewer, reason, at)
        if decision == "deferred":
            await conn.execute(
                "update public.quality_review set status='deferred', decision_reason=$2"
                " where id=$1",
                review_id,
                reason,
            )
        else:
            await conn.execute(
                "update public.quality_review set status=$2, decision_reason=$3, decided_by=$4,"
                " decided_at=$5 where id=$1",
                review_id,
                decision,
                reason,
                reviewer,
                at,
            )
        await conn.execute(
            "insert into public.quality_review_event(review_id, at, actor, action, detail)"
            " values($1,$2,$3,$4,$5::jsonb)",
            review_id,
            at,
            reviewer,
            decision,
            json.dumps({"reason": reason}),
        )
        return {
            "id": review_id,
            "status": decision,
            "subject_type": row["subject_type"],
            "subject_id": row["subject_id"],
            "candidate": row["candidate"],
        }
