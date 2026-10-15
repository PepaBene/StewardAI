"""The autopilot ledger: every automatic decision and escalation, in `auto_decision`.

A decision is recorded once it is made, with the evidence it rested on and, when it was
applied, what is needed to undo it. An item the app could not settle is an `escalated` row
(one per subject); it stays until the owner or the expiry rule settles it. Calibration reads
the `review` column, which the owner (a spot check or an override) or a stronger model's
audit sets.
"""

import datetime as dt
import json
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel

from steward.training import labels

Status = Literal["shadow", "applied", "escalated", "reverted", "closed"]
Review = Literal["ok", "wrong"]

COLUMNS = (
    "id, kind, subject_ref, ticker, decision, confidence, decided_by, summary, evidence,"
    " status, revert_payload, sampled, review, reviewed_by, reviewed_at, decided_at"
)
SELECT = "select " + COLUMNS + " from public.auto_decision"  # noqa: S608 - constant columns


class AutoDecision(BaseModel):
    id: int
    kind: str
    subject_ref: str
    ticker: str | None
    decision: str
    confidence: float | None
    decided_by: str
    summary: str
    evidence: dict[str, Any]
    status: Status
    revert_payload: dict[str, Any] | None
    sampled: bool
    review: Review | None
    reviewed_by: str | None
    reviewed_at: dt.datetime | None
    decided_at: dt.datetime


def _json(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return value if isinstance(value, dict) else json.loads(value)


def from_row(row: asyncpg.Record) -> AutoDecision:
    data = dict(row)
    data["evidence"] = _json(data["evidence"]) or {}
    data["revert_payload"] = _json(data["revert_payload"])
    return AutoDecision.model_validate(data)


async def record(
    pool: asyncpg.Pool,
    *,
    kind: str,
    subject_ref: str,
    ticker: str | None,
    decision: str,
    confidence: float | None,
    decided_by: str,
    summary: str,
    evidence: dict[str, Any],
    status: Status,
    revert_payload: dict[str, Any] | None = None,
    sampled: bool = False,
) -> int:
    """Write a decision. An escalation replaces the subject's open escalation (the judge was
    asked again and still could not settle it), keeping the first one's place in the list and
    its `decided_at` (the expiry clock); `last_judged_at` is when the judge last looked."""
    if status == "escalated":
        row = await pool.fetchrow(
            "insert into public.auto_decision (kind, subject_ref, ticker, decision, confidence,"
            " decided_by, summary, evidence, status, last_judged_at) values ($1, $2, $3, $4, $5,"
            " $6, $7, $8::jsonb, 'escalated', now())"
            " on conflict (kind, subject_ref) where status = 'escalated'"
            " do update set decision = excluded.decision, confidence = excluded.confidence,"
            " decided_by = excluded.decided_by, summary = excluded.summary,"
            " evidence = excluded.evidence, ticker = excluded.ticker, last_judged_at = now()"
            " returning id",
            kind,
            subject_ref,
            ticker,
            decision,
            confidence,
            decided_by,
            summary,
            json.dumps(evidence, default=str),
        )
        return int(row["id"])
    return int(
        await pool.fetchval(
            "insert into public.auto_decision (kind, subject_ref, ticker, decision, confidence,"
            " decided_by, summary, evidence, status, revert_payload, sampled)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10::jsonb, $11) returning id",
            kind,
            subject_ref,
            ticker,
            decision,
            confidence,
            decided_by,
            summary,
            json.dumps(evidence, default=str),
            status,
            None if revert_payload is None else json.dumps(revert_payload, default=str),
            sampled,
        )
    )


async def get(pool: asyncpg.Pool, decision_id: int) -> AutoDecision | None:
    row = await pool.fetchrow(SELECT + " where id = $1", decision_id)
    return None if row is None else from_row(row)


async def list_decisions(
    pool: asyncpg.Pool,
    *,
    kind: str | None = None,
    status: str | None = None,
    ticker: str | None = None,
    limit: int = 100,
) -> list[AutoDecision]:
    rows = await pool.fetch(
        SELECT + " where ($1::text is null or kind = $1) and ($2::text is null or status = $2)"
        " and ($3::text is null or ticker = $3) order by decided_at desc, id desc limit $4",
        kind,
        status,
        ticker,
        limit,
    )
    return [from_row(r) for r in rows]


async def set_status(
    pool: asyncpg.Pool, decision_id: int, status: Status, *, only_from: tuple[str, ...] = ()
) -> bool:
    """Move a decision to `status`; with `only_from`, only when it is currently in one of
    those states (so a double click cannot revert twice). True when a row changed."""
    result = await pool.execute(
        "update public.auto_decision set status = $2 where id = $1"
        " and (cardinality($3::text[]) = 0 or status = any($3))",
        decision_id,
        status,
        list(only_from),
    )
    return bool(result.endswith(" 1"))


async def set_review(
    pool: asyncpg.Pool, decision_id: int, review: Review, reviewed_by: str
) -> bool:
    row = await pool.fetchrow(
        "update public.auto_decision set review = $2, reviewed_by = $3, reviewed_at = now()"
        " where id = $1 returning kind, subject_ref, decision, status",
        decision_id,
        review,
        reviewed_by,
    )
    if row is None:
        return False
    source, by_owner = labels.source_for(reviewed_by, row["status"])
    await labels.record(
        pool,
        source=source,
        subject_kind=row["kind"],
        subject_ref=row["subject_ref"],
        label="right" if review == "ok" else "wrong",
        by_owner=by_owner,
        decision_id=decision_id,
        detail={"decision": row["decision"], "reviewed_by": reviewed_by},
    )
    return True


async def decided_refs(
    pool: asyncpg.Pool, kind: str, statuses: tuple[str, ...], since: dt.datetime
) -> set[str]:
    """Subjects of `kind` with a row in one of `statuses` decided (an escalation: last judged) at
    or after `since`."""
    rows = await pool.fetch(
        "select distinct subject_ref from public.auto_decision"
        " where kind = $1 and status = any($2) and coalesce(last_judged_at, decided_at) >= $3",
        kind,
        list(statuses),
        since,
    )
    return {r["subject_ref"] for r in rows}


async def count_since(pool: asyncpg.Pool, kind: str, since: dt.datetime) -> int:
    """Decisions (applied or shadow) of `kind` since `since`: what the daily cap counts."""
    return int(
        await pool.fetchval(
            "select count(*) from public.auto_decision where kind = $1"
            " and status in ('applied', 'shadow') and decided_at >= $2",
            kind,
            since,
        )
    )


async def close_escalation(pool: asyncpg.Pool, kind: str, subject_ref: str) -> None:
    """The subject was settled (by anyone): its open escalation leaves the owner's list."""
    await pool.execute(
        "update public.auto_decision set status = 'closed'"
        " where kind = $1 and subject_ref = $2 and status = 'escalated'",
        kind,
        subject_ref,
    )


async def escalations(pool: asyncpg.Pool, limit: int = 500) -> list[AutoDecision]:
    rows = await pool.fetch(
        SELECT + " where status = 'escalated' order by decided_at limit $1",
        limit,
    )
    return [from_row(r) for r in rows]


async def spot_checks(pool: asyncpg.Pool, limit: int = 5) -> list[AutoDecision]:
    """Applied decisions kept for a spot check and not yet reviewed, newest first."""
    rows = await pool.fetch(
        SELECT + " where status = 'applied' and sampled"
        " and review is null order by decided_at desc limit $1",
        limit,
    )
    return [from_row(r) for r in rows]


async def holding_weights(pool: asyncpg.Pool) -> dict[str, float]:
    """Portfolio weight of every holding in the latest snapshot (a fraction; absent: not held)."""
    rows = await pool.fetch(
        "select ticker, weight from public.position_snapshot where weight is not null"
        " and snapshot_date = (select max(snapshot_date) from public.position_snapshot)"
    )
    return {r["ticker"]: float(r["weight"]) for r in rows}


async def wrong_decisions(
    pool: asyncpg.Pool, kind: str, since: dt.datetime, limit: int
) -> list[AutoDecision]:
    """Decisions of `kind` judged wrong (by the owner or the audit) since `since`, newest first."""
    rows = await pool.fetch(
        SELECT + " where kind = $1 and review = 'wrong' and decided_at >= $2"
        " and status in ('shadow', 'applied', 'reverted') and decided_by <> 'owner-accepted'"
        " order by reviewed_at desc nulls last, id desc limit $3",
        kind,
        since,
        limit,
    )
    return [from_row(r) for r in rows]


async def learned_thresholds(pool: asyncpg.Pool) -> dict[str, float]:
    """Per kind, the confidence the autopilot asks for: what it learned after being wrong, or the
    floor of a promoted engine change when that is higher. Rolling a change back drops its floor."""
    rows = await pool.fetch("select kind, min_confidence from public.autopilot_learned")
    floors = {r["kind"]: round(float(r["min_confidence"]), 4) for r in rows}  # float4 to 4 places
    promoted = await pool.fetch(
        "select distinct on (target) target, (after ->> 'min_confidence')::float8 as floor"
        " from public.engine_change where change_kind = 'threshold' and status = 'promoted'"
        " and target like 'autopilot:%' and after ? 'min_confidence'"
        " order by target, decided_at desc"
    )
    for r in promoted:
        kind = r["target"].removeprefix("autopilot:")
        floors[kind] = max(floors.get(kind, 0.0), float(r["floor"]))
    return floors


async def set_learned(
    pool: asyncpg.Pool, kind: str, min_confidence: float | None, based_on: int, reason: str
) -> None:
    """Store (or, with None, clear) the learned confidence floor of a kind."""
    if min_confidence is None:
        await pool.execute("delete from public.autopilot_learned where kind = $1", kind)
        return
    await pool.execute(
        "insert into public.autopilot_learned (kind, min_confidence, based_on, reason)"
        " values ($1, $2, $3, $4) on conflict (kind) do update set"
        " min_confidence = excluded.min_confidence, based_on = excluded.based_on,"
        " reason = excluded.reason, updated_at = now()",
        kind,
        min_confidence,
        based_on,
        reason,
    )


async def mode_overrides(pool: asyncpg.Pool) -> dict[str, str]:
    rows = await pool.fetch("select kind, mode from public.autopilot_kind_state")
    return {r["kind"]: r["mode"] for r in rows}


async def mode_states(pool: asyncpg.Pool) -> dict[str, tuple[str, dt.datetime]]:
    rows = await pool.fetch("select kind, mode, updated_at from public.autopilot_kind_state")
    return {r["kind"]: (r["mode"], r["updated_at"]) for r in rows}


async def review_tally(pool: asyncpg.Pool, kind: str, since: dt.datetime) -> tuple[int, int]:
    """(reviewed, right) over decisions of `kind` made at or after `since`."""
    row = await pool.fetchrow(
        "select count(*) filter (where review is not null) as reviewed,"
        " count(*) filter (where review = 'ok') as right from public.auto_decision"
        " where kind = $1 and decided_at >= $2 and status in ('shadow', 'applied', 'reverted')"
        " and decided_by <> 'owner-accepted'",
        kind,
        since,
    )
    return int(row["reviewed"]), int(row["right"])


async def unreviewed(pool: asyncpg.Pool, since: dt.datetime, limit: int) -> list[AutoDecision]:
    """Shadow and applied decisions nobody has reviewed yet, oldest first. A decision the owner
    accepted was the owner's call, not the autopilot's, so it is not for the audit."""
    rows = await pool.fetch(
        SELECT + " where review is null and decided_by <> 'owner-accepted'"
        " and status in ('shadow', 'applied') and decided_at >= $1 order by decided_at limit $2",
        since,
        limit,
    )
    return [from_row(r) for r in rows]


async def set_mode(pool: asyncpg.Pool, kind: str, mode: str, reason: str) -> None:
    await pool.execute(
        "insert into public.autopilot_kind_state (kind, mode, reason) values ($1, $2, $3)"
        " on conflict (kind) do update set mode = excluded.mode, reason = excluded.reason,"
        " updated_at = now()",
        kind,
        mode,
        reason,
    )


class KindStats(BaseModel):
    kind: str
    decided: int
    applied: int
    shadow: int
    escalated: int  # waiting for the owner now
    reverted: int
    reviewed: int
    right: int
    raised: int = 0  # every item the engine escalated, settled since or not


async def stats(pool: asyncpg.Pool, since: dt.datetime) -> list[KindStats]:
    """Per kind, over decisions since `since`: counts by status and the review tally."""
    rows = await pool.fetch(
        "select kind,"
        " count(*) filter (where status <> 'escalated' and status <> 'closed') as decided,"
        " count(*) filter (where status = 'applied') as applied,"
        " count(*) filter (where status = 'shadow') as shadow,"
        " count(*) filter (where status = 'escalated') as escalated,"
        " count(*) filter (where status in ('escalated', 'closed')) as raised,"
        " count(*) filter (where status = 'reverted') as reverted,"
        " count(*) filter (where review is not null) as reviewed,"
        " count(*) filter (where review = 'ok') as right"
        " from public.auto_decision where decided_at >= $1 group by kind order by kind",
        since,
    )
    return [KindStats.model_validate(dict(r)) for r in rows]
