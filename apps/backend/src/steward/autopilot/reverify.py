"""Re-verification sweep (autonomous engine plan, 1.4): applied decisions are looked at again.

A decision that was right when applied can stop being right (the item is waiting again, a newer
record replaced it). For each recent applied decision nobody has reviewed, the sweep asks the
kind's `recheck` and the generic "is it waiting again" question; a problem opens a notice for
the owner, who can undo the decision there. The sweep changes nothing itself and does not mark
the decision wrong (calibration only counts reviews).
"""

import datetime as dt
import logging

import asyncpg

from steward.autopilot import ledger
from steward.autopilot.judge import JudgeKind
from steward.ops import tasks

logger = logging.getLogger("steward.autopilot")

KIND = "reverify"
WINDOW = dt.timedelta(days=30)
BATCH = 200


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


async def sweep(
    pool: asyncpg.Pool, kinds: dict[str, JudgeKind], now: dt.datetime
) -> dict[str, int]:
    rows = await pool.fetch(
        ledger.SELECT + " where status = 'applied' and decided_at >= $1 and review is null"
        " and decided_by <> 'owner-accepted' order by decided_at desc limit $2",
        now - WINDOW,
        BATCH,
    )
    flagged = 0
    for rec in rows:
        row = ledger.from_row(rec)
        kind = kinds.get(row.kind)
        if kind is None:
            continue
        found = await problems(pool, kind, row)
        if not found:
            continue
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
    return {"checked": len(rows), "flagged": flagged}
