"""Reading and writing versioned config (`config_version`).

A save never edits a row: it deactivates the current version and inserts a new active one
that records its parent and a diff against it, in one transaction.
"""

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

import asyncpg
from pydantic import BaseModel

from steward.config import CONFIG_MODELS


@dataclass(frozen=True)
class StoredConfig[M: BaseModel]:
    id: int
    created_at: dt.datetime
    note: str | None
    body: M


def json_diff(old: Any, new: Any, path: str = "") -> list[dict[str, Any]]:
    """Changes from `old` to `new` as [{"path", "op", "old"?, "new"?}], objects walked by key."""
    if isinstance(old, dict) and isinstance(new, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(old.keys() | new.keys()):
            child = f"{path}/{key}"
            if key not in new:
                changes.append({"path": child, "op": "remove", "old": old[key]})
            elif key not in old:
                changes.append({"path": child, "op": "add", "new": new[key]})
            else:
                changes.extend(json_diff(old[key], new[key], child))
        return changes
    if old == new:
        return []
    return [{"path": path or "/", "op": "replace", "old": old, "new": new}]


async def load_active[M: BaseModel](
    conn: asyncpg.Connection | asyncpg.Pool, domain: str, model: type[M]
) -> StoredConfig[M] | None:
    row = await conn.fetchrow(
        "select id, created_at, note, body from public.config_version where domain = $1 and active",
        domain,
    )
    if row is None:
        return None
    return StoredConfig(
        id=row["id"],
        created_at=row["created_at"],
        note=row["note"],
        body=model.model_validate(json.loads(row["body"])),
    )


async def save_config[M: BaseModel](
    conn: asyncpg.Connection,
    domain: str,
    body: M,
    *,
    created_by: str | None,
    note: str | None = None,
) -> StoredConfig[M]:
    """Store `body` as the new active version of `domain`. A body identical to the active
    version is not stored again; the active version is returned unchanged."""
    if CONFIG_MODELS.get(domain) is not type(body):
        raise ValueError(f"{type(body).__name__} is not the model for config domain {domain!r}")
    new_body = body.model_dump(mode="json")
    schema_version = int(new_body.get("schema_version", 1))

    async with conn.transaction():
        current = await conn.fetchrow(
            "select id, created_at, note, body from public.config_version"
            " where domain = $1 and active for update",
            domain,
        )
        diff = None
        if current is not None:
            old_body = json.loads(current["body"])
            diff = json_diff(old_body, new_body)
            if not diff:
                return StoredConfig(current["id"], current["created_at"], current["note"], body)
            await conn.execute(
                "update public.config_version set active = false where id = $1", current["id"]
            )
        row = await conn.fetchrow(
            "insert into public.config_version"
            " (domain, body, schema_version, parent_id, diff, note, created_by, active)"
            " values ($1, $2::jsonb, $3, $4, $5::jsonb, $6, $7::uuid, true)"
            " returning id, created_at",
            domain,
            json.dumps(new_body),
            schema_version,
            current["id"] if current else None,
            json.dumps(diff) if diff is not None else None,
            note,
            created_by,
        )
    return StoredConfig(row["id"], row["created_at"], note, body)
