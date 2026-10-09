"""Owner-saved analyses: X-Ray what-if scenarios and performance report snapshots.

Stored in `saved_analysis`; a saved item is archived, never edited or deleted."""

import datetime as dt
import json
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

SavedKind = Literal[
    "xray_scenario",
    "performance_report",
    "shock_scenario",
    "whatif_case",
    "rebalance_plan",
    "theme_exposure",
]


class SavedAnalysisItem(BaseModel):
    id: int
    kind: SavedKind
    name: str
    note: str | None
    method_version: str | None
    as_of: dt.date | None
    created_at: dt.datetime


class SavedAnalysisRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)


async def save(
    pool: asyncpg.Pool,
    owner_id: str,
    kind: SavedKind,
    body: SavedAnalysisRequest,
    payload: dict[str, Any],
    *,
    method_version: str | None = None,
    as_of: dt.date | None = None,
) -> SavedAnalysisItem:
    row = await pool.fetchrow(
        "insert into public.saved_analysis"
        " (owner_id, kind, name, note, payload, method_version, as_of)"
        " values ($1,$2,$3,$4,$5::jsonb,$6,$7)"
        " returning id, kind, name, note, method_version, as_of, created_at",
        owner_id,
        kind,
        body.name.strip(),
        body.note,
        json.dumps(payload, default=str),
        method_version,
        as_of,
    )
    assert row is not None
    return SavedAnalysisItem(**dict(row))


async def list_saved(pool: asyncpg.Pool, owner_id: str, kind: SavedKind) -> list[SavedAnalysisItem]:
    rows = await pool.fetch(
        "select id, kind, name, note, method_version, as_of, created_at"
        " from public.saved_analysis"
        " where owner_id=$1 and kind=$2 and not archived order by created_at desc, id desc",
        owner_id,
        kind,
    )
    return [SavedAnalysisItem(**dict(r)) for r in rows]


async def load_saved(
    pool: asyncpg.Pool, owner_id: str, kind: SavedKind, item_id: int
) -> tuple[SavedAnalysisItem, dict[str, Any]] | None:
    row = await pool.fetchrow(
        "select id, kind, name, note, method_version, as_of, created_at, payload"
        " from public.saved_analysis"
        " where id=$1 and owner_id=$2 and kind=$3 and not archived",
        item_id,
        owner_id,
        kind,
    )
    if row is None:
        return None
    data = dict(row)
    payload = data.pop("payload")
    return SavedAnalysisItem(**data), json.loads(payload) if isinstance(payload, str) else payload


async def archive(pool: asyncpg.Pool, owner_id: str, kind: SavedKind, item_id: int) -> bool:
    done = await pool.execute(
        "update public.saved_analysis set archived=true"
        " where id=$1 and owner_id=$2 and kind=$3 and not archived",
        item_id,
        owner_id,
        kind,
    )
    return str(done).endswith("1")
