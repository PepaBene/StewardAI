"""Which dated holdings snapshots are kept (plan D6.8).

Historical look-through needs the holdings as they were, so pruning to the newest N snapshots
(the old behaviour) threw away exactly what a past report or backtest needs. A snapshot is
kept when any of these holds:

1. it is among the newest `keep_latest` of its fund;
2. it is the last snapshot of one of the most recent `keep_month_ends` calendar months (a
   dense recent window plus a sparse long history);
3. it is pinned: a saved report or decision refers to it (`fund_snapshot_pin`). The pin is a
   foreign key with ON DELETE RESTRICT, so the database refuses to delete it as well.

Everything else may be pruned. The policy is data (`RetentionPolicy`); the Jobs config
supplies `keep_snapshots` today and a `keep_month_ends` field when it gains one.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Any, Literal

import asyncpg

from steward.config.jobs import LookThroughSettings

DEFAULT_KEEP_MONTH_ENDS = 60


@dataclass(frozen=True)
class RetentionPolicy:
    keep_latest: int = 12
    keep_month_ends: int = DEFAULT_KEEP_MONTH_ENDS

    @classmethod
    def from_settings(cls, settings: LookThroughSettings) -> "RetentionPolicy":
        return cls(
            keep_latest=settings.keep_snapshots,
            keep_month_ends=int(getattr(settings, "keep_month_ends", DEFAULT_KEEP_MONTH_ENDS)),
        )


@dataclass(frozen=True)
class SnapshotRef:
    id: int
    as_of: dt.date
    fetched_at: dt.datetime


def snapshots_to_keep(
    snapshots: list[SnapshotRef], pinned: set[int], policy: RetentionPolicy
) -> dict[int, str]:
    """Snapshot id -> why it is kept (snapshots absent from the result may be pruned)."""
    ordered = sorted(snapshots, key=lambda s: (s.as_of, s.fetched_at), reverse=True)
    keep: dict[int, str] = {}
    for snap in ordered[: policy.keep_latest]:
        keep[snap.id] = "latest"
    if policy.keep_month_ends > 0:
        months: dict[tuple[int, int], SnapshotRef] = {}
        for snap in ordered:  # newest first: the first of a month is its last snapshot
            months.setdefault((snap.as_of.year, snap.as_of.month), snap)
        for _month, snap in sorted(months.items(), reverse=True)[: policy.keep_month_ends]:
            keep.setdefault(snap.id, "month_end")
    for snap in ordered:
        if snap.id in pinned:
            keep[snap.id] = "pinned"
    return keep


async def pinned_ids(pool: asyncpg.Pool, isin: str) -> set[int]:
    rows = await pool.fetch(
        "select p.snapshot_id from public.fund_snapshot_pin p"
        " join public.etf_snapshot s on s.id = p.snapshot_id where s.fund_isin = $1",
        isin,
    )
    return {r["snapshot_id"] for r in rows}


async def prune_snapshots(pool: asyncpg.Pool, isin: str, policy: RetentionPolicy) -> dict[str, Any]:
    """Delete the snapshots of a fund the policy does not keep; returns what was kept/dropped."""
    rows = await pool.fetch(
        "select id, as_of, fetched_at from public.etf_snapshot where fund_isin = $1", isin
    )
    refs = [SnapshotRef(r["id"], r["as_of"], r["fetched_at"]) for r in rows]
    keep = snapshots_to_keep(refs, await pinned_ids(pool, isin), policy)
    drop = [s.id for s in refs if s.id not in keep]
    if drop:
        await pool.execute("delete from public.etf_snapshot where id = any($1::bigint[])", drop)
    return {
        "kept": len(keep),
        "deleted": len(drop),
        "pinned": sum(v == "pinned" for v in keep.values()),
    }


async def pin_snapshot(
    pool: asyncpg.Pool,
    snapshot_id: int,
    ref_kind: Literal["report", "decision", "manual"],
    ref_id: str,
    reason: str | None = None,
) -> bool:
    """Protect a snapshot a saved report/decision rests on; False when it does not exist."""
    exists = await pool.fetchval("select 1 from public.etf_snapshot where id = $1", snapshot_id)
    if not exists:
        return False
    await pool.execute(
        "insert into public.fund_snapshot_pin (snapshot_id, ref_kind, ref_id, reason)"
        " values ($1, $2, $3, $4) on conflict (snapshot_id, ref_kind, ref_id) do nothing",
        snapshot_id,
        ref_kind,
        ref_id,
        reason,
    )
    return True


async def pin_snapshots_as_of(
    pool: asyncpg.Pool,
    isins: list[str],
    as_of: dt.date,
    ref_kind: Literal["report", "decision", "manual"],
    ref_id: str,
    reason: str | None = None,
) -> list[int]:
    """Pin, for each fund, the snapshot a point-in-time look-through at `as_of` would use."""
    from steward.lookthrough.pit import snapshot_ids_as_of  # local: pit imports read

    pinned: list[int] = []
    for snapshot_id in (await snapshot_ids_as_of(pool, isins, as_of)).values():
        if await pin_snapshot(pool, snapshot_id, ref_kind, ref_id, reason):
            pinned.append(snapshot_id)
    return pinned
