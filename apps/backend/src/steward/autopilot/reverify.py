"""Re-verification sweep (autonomous engine plan, 1.4): applied decisions are looked at again.

A decision that was right when applied can stop being right (the item is waiting again, a newer
record replaced it). Once a week the sweep takes the recent applied decisions nobody has reviewed,
plus a sample of the ones that were reviewed as right, and asks the kind's `recheck` and the
generic "is it waiting again" question. A problem opens a notice for the owner, who can undo the
decision there. A notice the owner dismissed is not opened again for that decision.

The sweep changes nothing itself and does not mark a decision wrong (calibration only counts
reviews). Each run is stored in `engine_sweep`, so the disagreement rate can be read over time.
"""

import datetime as dt
import json
import logging

import asyncpg
from pydantic import BaseModel

from steward.autopilot import ledger
from steward.autopilot.judge import JudgeKind
from steward.ops import tasks

logger = logging.getLogger("steward.autopilot")

KIND = "reverify"
NAME = "reverify"
INTERVAL = dt.timedelta(days=7)
WINDOW = dt.timedelta(days=30)
BATCH = 200
REVIEWED_SAMPLE = 20

_NOT_DISMISSED = (
    " and not exists (select 1 from public.ops_task t where t.kind = 'reverify'"
    " and t.subject = 'auto_decision:' || public.auto_decision.id and t.last_action = 'dismissed')"
)


class SweepRun(BaseModel):
    ran_at: dt.datetime
    checked: int
    flagged: int

    @property
    def disagreement_rate(self) -> float | None:
        return self.flagged / self.checked if self.checked else None


async def due(pool: asyncpg.Pool, now: dt.datetime) -> bool:
    last = await pool.fetchval("select max(ran_at) from public.engine_sweep where name = $1", NAME)
    return last is None or now - last >= INTERVAL


async def history(pool: asyncpg.Pool, limit: int = 12) -> list[SweepRun]:
    rows = await pool.fetch(
        "select ran_at, checked, flagged from public.engine_sweep where name = $1"
        " order by ran_at desc limit $2",
        NAME,
        limit,
    )
    return [SweepRun(**dict(r)) for r in rows]


async def problems(pool: asyncpg.Pool, kind: JudgeKind, row: ledger.AutoDecision) -> list[str]:
    found: list[str] = []
    try:
        if row.subject_ref in await kind.is_open(pool, [row.subject_ref]):
            found.append("the item is waiting for a decision again")
        if kind.recheck is not None:
            found.extend(await kind.recheck(pool, row))
    except Exception:
        logger.warning("recheck of decision %s failed", row.id, exc_info=True)
    return found


async def _candidates(pool: asyncpg.Pool, now: dt.datetime) -> list[asyncpg.Record]:
    since = now - WINDOW
    unreviewed = await pool.fetch(
        ledger.SELECT + " where status = 'applied' and decided_at >= $1 and review is null"
        " and decided_by <> 'owner-accepted'" + _NOT_DISMISSED + " order by decided_at desc"
        " limit $2",
        since,
        BATCH,
    )
    sampled = await pool.fetch(
        ledger.SELECT
        + " where status = 'applied' and decided_at >= $1 and review = 'ok'"
        + _NOT_DISMISSED
        + " order by random() limit $2",
        since,
        REVIEWED_SAMPLE,
    )
    return [*unreviewed, *sampled]


async def sweep(
    pool: asyncpg.Pool, kinds: dict[str, JudgeKind], now: dt.datetime, *, force: bool = False
) -> dict[str, int]:
    """Run the sweep when a week has passed since the last one (or when forced)."""
    if not force and not await due(pool, now):
        return {"skipped": 1}
    flagged = 0
    by_kind: dict[str, list[int]] = {}
    rows = await _candidates(pool, now)
    for rec in rows:
        row = ledger.from_row(rec)
        kind = kinds.get(row.kind)
        if kind is None:
            continue
        tally = by_kind.setdefault(row.kind, [0, 0])
        tally[0] += 1
        found = await problems(pool, kind, row)
        if not found:
            continue
        tally[1] += 1
        flagged += 1
        await tasks.see(
            pool,
            KIND,
            f"auto_decision:{row.id}",
            title=f"An automatic {kind.label.lower()} decision may no longer hold",
            detail=f"{row.summary} Now: {'; '.join(found)}. You can undo it from the autopilot.",
            href="/autopilot",
            weight=0.5,
            status="needs_owner",
        )
    checked = sum(t[0] for t in by_kind.values())
    await pool.execute(
        "insert into public.engine_sweep (name, ran_at, checked, flagged, detail)"
        " values ($1, $2, $3, $4, $5::jsonb)",
        NAME,
        now,
        checked,
        flagged,
        json.dumps({"by_kind": by_kind}),
    )
    return {"checked": checked, "flagged": flagged}
