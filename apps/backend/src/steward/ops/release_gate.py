"""Release gate upkeep (D6): a feature the owner switched on that no longer passes the gate is
switched off, once the failure has lasted `retry_hours`.

The gate is checked when a feature is switched on; afterwards an input can go stale or an audit
can fail, and the activation screen only flagged it ("nothing does so for you"). Here the first
sighting is only recorded (a probe that fails once is not a reason), and a failure that is
still there after the retry window switches the feature off with a note saying why, and puts a
notice in "Needs you". Switching off is always allowed and one click undoes it once the gate
passes again; the owner's choice to have a feature on is never overridden while it passes.
"""

import datetime as dt
import logging
from typing import Any

import asyncpg

from steward.ops import tasks
from steward.release import activation, defects
from steward.release.readiness import Readiness, build_readiness

logger = logging.getLogger("steward.ops")

KIND = "release_gate"


def due(task: tasks.OpsTask, now: dt.datetime, retry_hours: float) -> bool:
    """The failure has lasted long enough to act on."""
    return now - task.first_seen >= dt.timedelta(hours=retry_hours)


def defect_title(feature: str) -> str:
    return f"Release gate: {feature} was switched off"


async def close_fixed_defects(pool: asyncpg.Pool, owner_id: str, readiness: Readiness) -> int:
    """Close the defects this module opened once the feature passes the gate again. They are
    found in the defect log itself, so dismissing or snoozing the notice does not lose them."""
    passing = {f.id for f in readiness.features if f.can_activate}
    closed = 0
    for d in await defects.list_current(pool, owner_id):
        if d.state != "open" or d.feature not in passing or d.title != defect_title(d.feature):
            continue
        await defects.update(
            pool,
            owner_id,
            d.id,
            defects.DefectUpdate(state="fixed", detail="The release gate passes again."),
        )
        closed += 1
    return closed


async def check_release_gate(
    pool: asyncpg.Pool, owner_id: str, now: dt.datetime, retry_hours: float
) -> dict[str, Any]:
    readiness = await build_readiness(pool, owner_id)
    current = activation.with_gate(await activation.list_current(pool, owner_id), readiness)
    failing = [a for a in current if a.active and a.needs_switch_off]
    switched_off: list[str] = []
    for item in failing:
        note = item.gate_note or "no longer passes the release gate"

        async def seen(feature: str = item.feature, detail: str = note) -> tasks.OpsTask:
            return await tasks.see(
                pool,
                KIND,
                feature,
                title=f"{feature} fails the release gate",
                detail=detail,
                href="/release",
                weight=0.4,
            )

        task = await seen()
        if task.status == "needs_owner":
            # It was switched off and the owner switched it back on while it still fails: the
            # old notice is done, and a new failure window starts from now.
            await tasks.resolve(pool, task.id)
            task = await seen()
        if task.status == "fixing" and due(task, now, retry_hours):
            await activation.record(
                pool,
                owner_id,
                activation.ActivationIn(
                    feature=item.feature,
                    active=False,
                    note=f"Switched off automatically: {note}"[:500],
                ),
            )
            await tasks.hand_over(pool, task.id)
            defect_id = await defects.report(
                pool,
                owner_id,
                defects.DefectIn(
                    title=defect_title(item.feature),
                    detail=f"Opened automatically. {note}"[:2000],
                    ticker=None,
                    feature=item.feature,
                ),
            )
            await tasks.acted(pool, task.id, f"defect:{defect_id}")
            switched_off.append(item.feature)
    closed = await close_fixed_defects(pool, owner_id, readiness)
    # A feature that passes again closes its task. The notice for a feature that was switched
    # off stays until the owner dismisses it.
    keep = {a.feature for a in failing} | {
        t.subject for t in await tasks.needs_owner(pool) if t.kind == KIND
    }
    return {
        "failing": sorted(keep),
        "switched_off": switched_off,
        "defects_closed": closed,
        "resolved": await tasks.settle(pool, KIND, keep),
    }
