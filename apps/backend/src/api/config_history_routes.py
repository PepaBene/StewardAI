"""Config history for every domain: list versions, read one, diff any two, roll back, and
export or import every domain's active version as one JSON file.

Rolling back never rewrites history: the chosen version's body is saved as a new active
version. An import is validated domain by domain first and saved all together, or not at
all.
"""

import datetime as dt
from typing import Annotated, Any, Literal, get_args

import asyncpg
from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import DB_ID_MAX, IdPath, Pool
from api.routes import BaseVersionParam, config_conflict
from steward import __version__
from steward.config import CONFIG_MODELS
from steward.config.store import (
    ConfigConflictError,
    ConfigImportError,
    RollbackError,
    VersionMeta,
    export_active,
    get_version,
    import_all,
    json_diff,
    list_versions,
    rollback,
)
from steward.engine import overrides

router = APIRouter(tags=["config"])

# URL slugs of the config domains (the API's config routes use hyphens).
ConfigDomainSlug = Literal[
    "strategy",
    "theses",
    "jobs",
    "holding-profiles",
    "verification",
    "news-sources",
    "event-rules",
    "notifications",
    "budget",
    "research-profiles",
    "prompts",
    "performance",
]
DOMAIN_BY_SLUG: dict[str, str] = {s: s.replace("-", "_") for s in get_args(ConfigDomainSlug)}
assert set(DOMAIN_BY_SLUG.values()) == set(CONFIG_MODELS), "ConfigDomainSlug is out of date"

SlugPath = Annotated[ConfigDomainSlug, Path(description="Config domain, as in /config/<domain>.")]


class ConfigVersionMeta(BaseModel):
    id: int
    domain: str
    created_at: dt.datetime
    note: str | None
    created_by: str | None
    active: bool
    parent_id: int | None
    schema_version: int
    changes: int | None = Field(
        description="Number of changed paths against the previous version; null for the first."
    )


class ConfigVersionBody(ConfigVersionMeta):
    body: dict[str, Any]


class DiffChange(BaseModel):
    path: str = Field(description="JSON pointer-style path, e.g. /target_positions/0/band.")
    op: Literal["add", "remove", "replace"]
    old: Any = None
    new: Any = None


class ConfigDiff(BaseModel):
    domain: str
    from_id: int
    to_id: int
    changes: list[DiffChange]


def _meta(meta: VersionMeta) -> ConfigVersionMeta:
    return ConfigVersionMeta(**meta.__dict__)


@router.get("/config/{domain}/versions")
async def get_config_versions(
    domain: SlugPath,
    pool: Pool,
    _owner: Owner,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[ConfigVersionMeta]:
    """Every saved version of a domain, newest first; the active one is flagged."""
    return [_meta(m) for m in await list_versions(pool, DOMAIN_BY_SLUG[domain], limit)]


@router.get("/config/{domain}/versions/{version_id}")
async def get_config_version(
    domain: SlugPath, version_id: IdPath, pool: Pool, _owner: Owner
) -> ConfigVersionBody:
    found = await get_version(pool, DOMAIN_BY_SLUG[domain], version_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No {domain} version {version_id}")
    meta, body = found
    return ConfigVersionBody(**meta.__dict__, body=body)


@router.get("/config/{domain}/diff")
async def get_config_diff(
    domain: SlugPath,
    pool: Pool,
    _owner: Owner,
    from_id: Annotated[int, Query(alias="from", ge=1, le=DB_ID_MAX)],
    to_id: Annotated[int, Query(alias="to", ge=1, le=DB_ID_MAX)],
) -> ConfigDiff:
    """What changed from one version to another (any two versions of the domain)."""
    name = DOMAIN_BY_SLUG[domain]
    old = await get_version(pool, name, from_id)
    new = await get_version(pool, name, to_id)
    if old is None or new is None:
        missing = from_id if old is None else to_id
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No {domain} version {missing}")
    return ConfigDiff(
        domain=name,
        from_id=from_id,
        to_id=to_id,
        changes=[DiffChange(**c) for c in json_diff(old[1], new[1])],
    )


@router.post("/config/{domain}/versions/{version_id}/rollback")
async def rollback_config(
    domain: SlugPath,
    version_id: IdPath,
    pool: Pool,
    owner: Owner,
    base_version_id: BaseVersionParam = None,
    reason_code: overrides.Reason | None = None,
) -> ConfigVersionBody:
    """Make an old version current again by saving its body as a new active version. The
    next run of every job uses it. 409 if the old body no longer passes validation, or if
    `base_version_id` is given and is no longer the active version. When the version being
    replaced was written by the engine, `reason_code` says why it was undone."""
    name = DOMAIN_BY_SLUG[domain]
    previous = await overrides.active_version(pool, name)
    try:
        async with pool.acquire() as conn:
            stored = await rollback(
                conn,
                name,
                version_id,
                created_by=owner.user_id,
                expected_active_id=base_version_id,
            )
    except RollbackError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.errors) from exc
    except (ConfigConflictError, asyncpg.UniqueViolationError) as exc:
        raise config_conflict(exc) from exc
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No {domain} version {version_id}")
    found = await get_version(pool, name, stored.id)
    assert found is not None
    await overrides.record_config_edit(
        pool, name, previous, found[1], action="reverted", reason_code=reason_code
    )
    return ConfigVersionBody(**found[0].__dict__, body=found[1])


# ---------------------------------------------------------------------------
# Export and import
# ---------------------------------------------------------------------------


class ExportedDomain(BaseModel):
    version_id: int | None = None
    created_at: dt.datetime | None = None
    note: str | None = None
    body: dict[str, Any]


class ConfigExport(BaseModel):
    format: Literal["stewardai-config"] = "stewardai-config"
    format_version: Literal[1] = 1
    exported_at: dt.datetime | None = None
    app_version: str | None = None
    domains: dict[str, ExportedDomain] = Field(
        description="Active version per config domain (strategy, theses, jobs, ...)."
    )


class ImportResult(BaseModel):
    saved: dict[str, int] = Field(description="Domain -> the version now active.")
    unchanged: list[str] = Field(description="Domains whose body equalled the active version.")


@router.get("/config/export")
async def export_config(pool: Pool, _owner: Owner) -> ConfigExport:
    """Every domain's active version in one file, to keep config in git or move it to
    another project."""
    active = await export_active(pool)
    return ConfigExport(
        exported_at=dt.datetime.now(dt.UTC),
        app_version=__version__,
        domains={d: ExportedDomain(**v) for d, v in active.items()},
    )


@router.post("/config/import")
async def import_config(
    body: ConfigExport,
    pool: Pool,
    owner: Owner,
    note: Annotated[str | None, Query(max_length=500)] = None,
) -> ImportResult:
    """Validate every domain of an exported file, then save them all as new active
    versions. Any invalid domain rejects the whole file with every error listed (422) and
    nothing saved."""
    bodies = {d: v.body for d, v in body.domains.items()}
    before = {d: v["version_id"] for d, v in (await export_active(pool)).items()}
    try:
        async with pool.acquire() as conn:
            saved = await import_all(conn, bodies, created_by=owner.user_id, note=note)
    except ConfigImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors) from exc
    except (ConfigConflictError, asyncpg.UniqueViolationError) as exc:
        raise config_conflict(exc) from exc
    return ImportResult(
        saved={d: s.id for d, s in saved.items()},
        unchanged=sorted(d for d, s in saved.items() if before.get(d) == s.id),
    )
