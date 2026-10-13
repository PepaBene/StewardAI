"""Deciding a stored LEI/FIGI suggestion: final, noted when flagged, fills only empty identifiers.

The owner's endpoint and the autopilot both settle a suggestion through `decide_suggestion`, so the
guards are the same for both: a decision is final, a name-only or ambiguous match needs a note of
at least 10 characters to be confirmed, and a confirmed identifier fills the issuer's or
security's empty LEI or FIGI but never overwrites one that is already set.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Literal

import asyncpg

MIN_FLAGGED_NOTE = 10

type Refusal = Literal["missing", "final", "note", "conflict"]


class IdentifierDecisionError(Exception):
    """The decision was refused. `kind` says why so a caller can pick its own response."""

    def __init__(self, kind: Refusal, message: str) -> None:
        super().__init__(message)
        self.kind: Refusal = kind


async def decide_suggestion(
    pool: asyncpg.Pool,
    suggestion_id: int,
    decision: Literal["confirmed", "rejected"],
    note: str | None,
    by: str,
) -> Any:
    """Confirm or reject one suggestion and return the updated row (with `legal_name`).

    `by` is recorded in the row's evidence (`owner:<id>` for the owner, `autopilot` for the
    autopilot), which is how the two are told apart afterwards."""
    text = (note or "").strip()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """select p.*, i.legal_name from public.provider_identifier p
                 left join public.issuer i on i.id = p.issuer_id
                where p.id = $1 for update of p""",
            suggestion_id,
        )
        if row is None:
            raise IdentifierDecisionError("missing", "No such suggestion")
        if row["match_status"] != "suggested":
            raise IdentifierDecisionError(
                "final", f"Already {row['match_status']}; decisions are final"
            )
        evidence = row["evidence"]
        evidence = json.loads(evidence) if isinstance(evidence, str) else dict(evidence or {})
        flagged = bool(evidence.get("name_only") or evidence.get("ambiguous"))
        if decision == "confirmed" and flagged and len(text) < MIN_FLAGGED_NOTE:
            raise IdentifierDecisionError(
                "note", "A name-only or ambiguous match needs a note of at least 10 characters"
            )
        evidence["decision"] = {
            "by": by,
            "at": dt.datetime.now(dt.UTC).isoformat(),
            "note": text or None,
        }
        try:
            if decision == "confirmed":
                target = None
                if row["identifier_type"] == "lei" and row["issuer_id"] is not None:
                    target = ("issuer", "lei", row["issuer_id"])
                elif row["identifier_type"] == "figi" and row["security_id"] is not None:
                    target = ("security", "figi", row["security_id"])
                if target is not None:
                    table, column, record_id = target
                    changed = await conn.execute(
                        f"update public.{table} set {column}=$2 where id=$1 and {column} is null",  # noqa: S608 - fixed names
                        record_id,
                        row["identifier"],
                    )
                    held = await conn.fetchval(
                        f"select {column} from public.{table} where id=$1",  # noqa: S608
                        record_id,
                    )
                    if changed == "UPDATE 0" and held != row["identifier"]:
                        raise IdentifierDecisionError(
                            "conflict",
                            f"The record already holds a different {column.upper()} ({held}); "
                            "reject this suggestion or clear the stored one first",
                        )
            await conn.execute(
                "update public.provider_identifier set match_status=$2, evidence=$3::jsonb"
                " where id=$1",
                suggestion_id,
                decision,
                json.dumps(evidence),
            )
        except asyncpg.UniqueViolationError as exc:
            raise IdentifierDecisionError(
                "conflict", "That identifier already belongs to another record"
            ) from exc
        return await conn.fetchrow(
            """select p.*, i.legal_name from public.provider_identifier p
                 left join public.issuer i on i.id = p.issuer_id where p.id = $1""",
            suggestion_id,
        )
