"""Owner overrides (autonomous engine plan, L3): the owner overruling the engine, and why.

`record` stores one row with an optional reason chip. `patterns` groups the rows by what the
engine used (an autopilot kind and the rule or model that decided) so a pattern, not a single
override, is what changes anything: `PATTERN_MIN` overrides within `WINDOW_DAYS` and a share of
that source's decisions overruled at or above `PATTERN_SHARE`.
"""

import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

import asyncpg

logger = logging.getLogger("steward.engine")

Action = Literal["reverted", "edited", "rejected", "marked_wrong"]
Reason = Literal[
    "wrong_data",
    "wrong_source",
    "right_but_unimportant",
    "too_early",
    "too_late",
    "prefer_my_judgement",
    "other",
]
Via = Literal["screen", "chat"]

REASONS: tuple[str, ...] = (
    "wrong_data",
    "wrong_source",
    "right_but_unimportant",
    "too_early",
    "too_late",
    "prefer_my_judgement",
    "other",
)
WINDOW_DAYS = 90
PATTERN_MIN = 4
PATTERN_SHARE = 0.4


async def record(
    pool: asyncpg.Pool,
    *,
    area: str,
    subject_kind: str,
    subject_ref: str,
    action: Action,
    via: Via,
    reason_code: Reason | None = None,
    note: str | None = None,
    decided_by: str | None = None,
    decision_id: int | None = None,
    before: Any = None,
    after: Any = None,
) -> None:
    """Never raises: an override must not fail because its note could not be stored."""
    try:
        await pool.execute(
            "insert into public.owner_override (area, subject_kind, subject_ref, action,"
            " reason_code, note, decided_by, decision_id, via, before, after)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11::jsonb)",
            area,
            subject_kind,
            subject_ref,
            action,
            reason_code,
            note[:1000] if note else None,
            decided_by,
            decision_id,
            via,
            json.dumps(before, default=str) if before is not None else None,
            json.dumps(after, default=str) if after is not None else None,
        )
    except Exception:
        logger.warning("could not record an owner override", exc_info=True)


@dataclass(frozen=True)
class Pattern:
    kind: str
    decided_by: str
    overridden: int
    decided: int
    top_reason: str | None

    @property
    def share(self) -> float:
        return self.overridden / self.decided if self.decided else 0.0


def is_pattern(overridden: int, decided: int) -> bool:
    return overridden >= PATTERN_MIN and decided > 0 and overridden / decided >= PATTERN_SHARE


async def patterns(pool: asyncpg.Pool, now: dt.datetime) -> list[Pattern]:
    """Sources (kind + rule or model) whose decisions are overruled often enough to act on."""
    since = now - dt.timedelta(days=WINDOW_DAYS)
    rows = await pool.fetch(
        "select o.subject_kind as kind, o.decided_by, count(*) as overridden,"
        " mode() within group (order by o.reason_code) as top_reason,"
        " (select count(*) from public.auto_decision d where d.kind = o.subject_kind"
        "  and d.decided_by = o.decided_by and d.decided_at >= $1"
        "  and d.status in ('applied', 'shadow', 'reverted')) as decided"
        " from public.owner_override o where o.created_at >= $1 and o.decided_by is not null"
        " group by o.subject_kind, o.decided_by",
        since,
    )
    return [
        Pattern(r["kind"], r["decided_by"], r["overridden"], r["decided"], r["top_reason"])
        for r in rows
        if is_pattern(r["overridden"], r["decided"])
    ]
