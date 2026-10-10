"""Saved copies of the risk reports (quant plan engineering rule 5).

The ex-ante risk report, the past-episode replay and the factor exposures are recomputed from
stored prices on every request. A saved copy keeps what the owner saw, with its method version,
so a later change to a model cannot rewrite it. A report is saved once per kind, scope, data
date, method version and content: the same report asked for again changes nothing, a different
one for the same data date (after a trade or a deposit) is kept beside it.
"""

import datetime as dt
import hashlib
import json
import logging
import math
from typing import Any, Literal

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


def _clean(value: Any) -> Any:
    """The value with every NaN or infinity replaced by null: Postgres jsonb rejects them (R26)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


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
        payload = json.dumps(
            _clean(report.model_dump(mode="json")), sort_keys=True, allow_nan=False
        )
        row = await pool.fetchrow(
            "insert into public.risk_report_snapshot"
            " (kind, scope, as_of, method_version, payload, payload_hash)"
            " values ($1, $2, $3, $4, $5::jsonb, $6)"
            " on conflict (kind, scope, as_of, method_version, payload_hash) do nothing"
            " returning id",
            kind,
            scope,
            as_of,
            method_version,
            payload,
            hashlib.sha256(payload.encode()).hexdigest(),
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
