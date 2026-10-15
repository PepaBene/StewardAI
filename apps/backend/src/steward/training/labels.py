"""Training labels (fine-tuning data plan, phase 2): the owner's and the audit's judgements on
what the engine produced, kept as an append-only table beside `llm_call`.

Only a judgement by the owner or an audit that re-read the evidence is a label; a decision the
engine made and nobody checked is not (`origin: auto` rows are never labels). Recording a label
must never break the action that caused it, so failures are logged and ignored.
"""

import json
import logging
from typing import Any, Literal

import asyncpg

logger = logging.getLogger("steward.training")

Source = Literal["owner_review", "owner_revert", "owner_accept", "audit", "outcome"]
Label = Literal["right", "wrong", "corrected"]


def source_for(reviewed_by: str, status: str) -> tuple[Source, bool]:
    """(source, by_owner) for a review written on an autopilot decision."""
    if reviewed_by.startswith("audit:"):
        return "audit", False
    return ("owner_revert" if status == "reverted" else "owner_review"), True


async def record(
    pool: asyncpg.Pool,
    *,
    source: Source,
    subject_kind: str,
    subject_ref: str,
    label: Label,
    by_owner: bool,
    decision_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    try:
        await pool.execute(
            "insert into public.training_label (source, subject_kind, subject_ref, decision_id,"
            " label, by_owner, detail) values ($1, $2, $3, $4, $5, $6, $7::jsonb)",
            source,
            subject_kind,
            subject_ref,
            decision_id,
            label,
            by_owner,
            json.dumps(detail or {}, default=str),
        )
    except Exception:
        logger.warning("could not record a training label", exc_info=True)
