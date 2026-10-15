"""Learning triggers (autonomous engine plan, 4.3): the engine reacts to how often it is overruled.

- A kind that acts on its own (auto) and is overruled in a pattern (`overrides.patterns`) is put
  back to shadow: it keeps deciding and logging, and changes nothing until the owner or the
  calibration job lets it act again. The owner gets a notice. This only ever makes the engine do
  less.
- A kind that is only watching, has been right often enough to act, was not overruled in the last
  month, but cannot be promoted by the calibration job (its decisions cannot be undone, or it is
  not marked promotable) gets one notice asking the owner to decide. The engine never hands such
  a kind the keys by a statistic.
"""

import datetime as dt

import asyncpg

from steward.autopilot import ledger
from steward.engine import overrides, triggers
from steward.engine.override_watch import describe
from steward.ops import tasks

NOTICE = "learning_trigger"
LOOKBACK_DAYS = 30


async def run(
    pool: asyncpg.Pool,
    modes: dict[str, str],
    *,
    promote_min_reviewed: int,
    promote_precision: float,
    owner_decides: set[str],
    now: dt.datetime,
) -> dict[str, list[str]]:
    """`modes` is each kind's effective mode; `owner_decides` the kinds the calibration job will
    not promote by itself."""
    out: dict[str, list[str]] = {"to_shadow": [], "ready_for_owner": []}
    overruled = {p.kind: p for p in await overrides.patterns(pool, now) if p.kind in modes}

    for name, pattern in overruled.items():
        if modes[name] != "auto":
            continue

        async def put_back(name: str = name, why: str = describe(pattern)) -> int | None:
            await ledger.set_mode(pool, name, "shadow", f"Put back to watching: {why}")
            return None

        result = await triggers.fire(
            pool,
            "learning",
            f"shadow:{name}",
            describe(pattern),
            f"put the {name} autopilot back to watching",
            put_back,
            now=now,
        )
        if result.fired:
            out["to_shadow"].append(name)
            await tasks.see(
                pool,
                NOTICE,
                f"shadow:{name}",
                title=f"The {name} autopilot was put back to watching",
                detail=f"{describe(pattern)} It still decides and logs, but changes nothing "
                "until you or the calibration let it act again.",
                href="/autopilot",
                weight=0.4,
                status="needs_owner",
            )

    since = now - dt.timedelta(days=LOOKBACK_DAYS)
    for name in sorted(owner_decides):
        if modes.get(name) != "shadow" or name in overruled:
            continue
        reviewed, right = await ledger.review_tally(pool, name, since)
        if reviewed < promote_min_reviewed or right / reviewed < promote_precision:
            continue
        reason = f"{right} of {reviewed} reviewed decisions in {LOOKBACK_DAYS} days were right"
        if await triggers.log(
            pool,
            "learning",
            f"promote:{name}",
            reason,
            f"ask whether the {name} autopilot should act on its own",
            now=now,
            cooldown_days=LOOKBACK_DAYS,
        ):
            out["ready_for_owner"].append(name)
            await tasks.see(
                pool,
                NOTICE,
                f"promote:{name}",
                title=f"The {name} autopilot is accurate enough to act on its own",
                detail=f"{reason.capitalize()}. Its decisions cannot all be undone, so only you "
                "can let it act: set it to auto in the Jobs config if you agree.",
                href="/config/jobs",
                weight=0.3,
                status="needs_owner",
            )
    return out
