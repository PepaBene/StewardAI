"""Reading and writing versioned config (`config_version`).

A save never edits a row: it deactivates the current version and inserts a new active one
that records its parent and a diff against it, in one transaction.
"""

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

import asyncpg
from pydantic import BaseModel, ValidationError

from steward.config import CONFIG_MODELS


@dataclass(frozen=True)
class StoredConfig[M: BaseModel]:
    id: int
    created_at: dt.datetime
    note: str | None
    body: M


def json_diff(old: Any, new: Any, path: str = "") -> list[dict[str, Any]]:
    """Changes from `old` to `new` as [{"path", "op", "old"?, "new"?}]: objects walked by key,
    lists by index (items past the shorter list are added or removed)."""
    changes: list[dict[str, Any]] = []
    if isinstance(old, list) and isinstance(new, list):
        for i in range(max(len(old), len(new))):
            child = f"{path}/{i}"
            if i >= len(new):
                changes.append({"path": child, "op": "remove", "old": old[i]})
            elif i >= len(old):
                changes.append({"path": child, "op": "add", "new": new[i]})
            else:
                changes.extend(json_diff(old[i], new[i], child))
        return changes
    if isinstance(old, dict) and isinstance(new, dict):
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


# ---------------------------------------------------------------------------
# History: listing, reading, diffing, rolling back, export and import
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VersionMeta:
    id: int
    domain: str
    created_at: dt.datetime
    note: str | None
    created_by: str | None
    active: bool
    parent_id: int | None
    schema_version: int
    changes: int | None


_META_COLUMNS = (
    "id, domain, created_at, note, created_by::text as created_by, active, parent_id,"
    " schema_version, case when diff is null then null else jsonb_array_length(diff) end"
    " as changes"
)


def _meta(row: asyncpg.Record) -> VersionMeta:
    return VersionMeta(
        id=row["id"],
        domain=row["domain"],
        created_at=row["created_at"],
        note=row["note"],
        created_by=row["created_by"],
        active=row["active"],
        parent_id=row["parent_id"],
        schema_version=row["schema_version"],
        changes=row["changes"],
    )


async def list_versions(
    conn: asyncpg.Connection | asyncpg.Pool, domain: str, limit: int = 200
) -> list[VersionMeta]:
    """Every version of `domain`, newest first."""
    rows = await conn.fetch(
        f"select {_META_COLUMNS} from public.config_version where domain = $1"  # noqa: S608
        " order by id desc limit $2",
        domain,
        limit,
    )
    return [_meta(r) for r in rows]


async def get_version(
    conn: asyncpg.Connection | asyncpg.Pool, domain: str, version_id: int
) -> tuple[VersionMeta, dict[str, Any]] | None:
    row = await conn.fetchrow(
        f"select {_META_COLUMNS}, body from public.config_version"  # noqa: S608
        " where domain = $1 and id = $2",
        domain,
        version_id,
    )
    if row is None:
        return None
    return _meta(row), json.loads(row["body"])


def parse_body(domain: str, body: dict[str, Any]) -> BaseModel:
    """Validate a stored or imported body with the domain's current model. Raises
    ValidationError (every error listed) or KeyError for an unknown domain."""
    return CONFIG_MODELS[domain].model_validate(body)


class RollbackError(Exception):
    """The chosen version no longer validates under the current schema."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("the chosen version is not valid under the current schema")
        self.errors = errors


async def rollback(
    conn: asyncpg.Connection, domain: str, version_id: int, *, created_by: str | None
) -> StoredConfig[BaseModel] | None:
    """Save version `version_id`'s body as the new active version (history is never
    rewritten). None when no such version exists."""
    found = await get_version(conn, domain, version_id)
    if found is None:
        return None
    try:
        body = parse_body(domain, found[1])
    except ValidationError as exc:
        raise RollbackError(json.loads(exc.json())) from exc
    return await save_config(
        conn, domain, body, created_by=created_by, note=f"Rolled back to version #{version_id}"
    )


async def export_active(conn: asyncpg.Connection | asyncpg.Pool) -> dict[str, dict[str, Any]]:
    """The active version of every domain that has one: {domain: {version_id, created_at,
    note, body}}."""
    rows = await conn.fetch(
        "select id, domain, created_at, note, body from public.config_version where active"
        " order by domain"
    )
    return {
        r["domain"]: {
            "version_id": r["id"],
            "created_at": r["created_at"],
            "note": r["note"],
            "body": json.loads(r["body"]),
        }
        for r in rows
        if r["domain"] in CONFIG_MODELS
    }


class ConfigImportError(Exception):
    """An import file with at least one invalid domain. Nothing was saved."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__(f"{len(errors)} problem(s) in the import file")
        self.errors = errors


def validate_import(bodies: dict[str, dict[str, Any]]) -> dict[str, BaseModel]:
    """Every domain validated; every error from every domain listed, with `loc` prefixed by
    ["domains", <domain>, "body"]. Raises ConfigImportError if any fails."""
    parsed: dict[str, BaseModel] = {}
    errors: list[dict[str, Any]] = []
    for domain, body in bodies.items():
        prefix: list[str | int] = ["domains", domain]
        if domain not in CONFIG_MODELS:
            errors.append(
                {"loc": prefix, "msg": f"unknown config domain {domain!r}", "type": "value_error"}
            )
            continue
        try:
            parsed[domain] = parse_body(domain, body)
        except ValidationError as exc:
            for err in json.loads(exc.json()):
                errors.append(
                    {"loc": [*prefix, "body", *err["loc"]], "msg": err["msg"], "type": err["type"]}
                )
    if errors:
        raise ConfigImportError(errors)
    return parsed


async def import_all(
    conn: asyncpg.Connection,
    bodies: dict[str, dict[str, Any]],
    *,
    created_by: str | None,
    note: str | None = None,
) -> dict[str, StoredConfig[BaseModel]]:
    """Validate every domain first, then save them all in one transaction (all or nothing).
    A body equal to the active version is not stored again."""
    parsed = validate_import(bodies)
    saved: dict[str, StoredConfig[BaseModel]] = {}
    async with conn.transaction():
        for domain, body in parsed.items():
            saved[domain] = await save_config(
                conn, domain, body, created_by=created_by, note=note or "Imported from file"
            )
    return saved
