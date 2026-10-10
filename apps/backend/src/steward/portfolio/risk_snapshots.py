"""Saved copies of the risk reports (quant plan engineering rule 5).

The ex-ante risk report, the past-episode replay and the factor exposures are recomputed from
stored prices on every request. A saved copy keeps what the owner saw, with its method version,
so a later change to a model cannot rewrite it. A report is saved once per kind, scope, data
date and method version; a second request for the same data date changes nothing.
"""

import datetime as dt
import json
import logging
from typing import Literal

import asyncpg
from pydantic import BaseModel

log = logging.getLogger(__name__)

Kind = Literal["ex_ante", "replay", "factors", "shock", "tail"]


class SnapshotRow(BaseModel):
    id: int
    kind: Kind
    scope: str
    as_of: dt.date
    method_version: str
    created_at: dt.datetime


async def save_snapshot(
    pool: asyncpg.Pool,
    kind: Kind,
    report: BaseModel,
    as_of: dt.date | None,
    method_version: str,
    scope: str = "",
) -> bool:
    """Save a report; True when a new row was written. A report with no data date (nothing was
    computed) is not saved, and a failure to save never fails the request."""
    if as_of is None:
        return False
    try:
        row = await pool.fetchrow(
            "insert into public.risk_report_snapshot (kind, scope, as_of, method_version, payload)"
            " values ($1, $2, $3, $4, $5::jsonb)"
            " on conflict (kind, scope, as_of, method_version) do nothing returning id",
            kind,
            scope,
            as_of,
            method_version,
            json.dumps(report.model_dump(mode="json")),
        )
    except Exception as exc:
        log.warning("could not save the %s snapshot: %s", kind, exc)
        return False
    return row is not None


async def list_snapshots(
    pool: asyncpg.Pool, kind: Kind | None = None, limit: int = 50
) -> list[SnapshotRow]:
    rows = await pool.fetch(
        "select id, kind, scope, as_of, method_version, created_at"
        " from public.risk_report_snapshot where ($1::text is null or kind = $1)"
        " order by as_of desc, id desc limit $2",
        kind,
        limit,
    )
    return [SnapshotRow(**dict(r)) for r in rows]


async def get_snapshot(pool: asyncpg.Pool, snapshot_id: int) -> dict[str, object] | None:
    """The saved report exactly as it was shown."""
    row = await pool.fetchrow(
        "select payload from public.risk_report_snapshot where id = $1", snapshot_id
    )
    if row is None:
        return None
    payload = row["payload"]
    loaded = json.loads(payload) if isinstance(payload, str) else payload
    return dict(loaded)
