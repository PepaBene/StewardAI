"""The curated starter screens as a versioned config document (insight plan I10.7). The Discover
page reads them with their match counts from GET /discover/templates."""

import datetime as dt

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.routes import BaseVersionParam, NoteParam, _as_dict, save_versioned
from steward.config.screen_templates import ScreenTemplatesConfig
from steward.config.store import load_active

router = APIRouter(tags=["config"])


class ScreenTemplatesVersion(BaseModel):
    id: int | None = Field(description="Saved version id; null while the defaults apply.")
    created_at: dt.datetime | None
    body: ScreenTemplatesConfig


@router.get("/config/screen-templates")
async def get_screen_templates(pool: Pool, _owner: Owner) -> ScreenTemplatesVersion:
    stored = await load_active(pool, "screen_templates", ScreenTemplatesConfig)
    if stored is None:
        return ScreenTemplatesVersion(id=None, created_at=None, body=ScreenTemplatesConfig())
    d = _as_dict(stored)
    return ScreenTemplatesVersion(id=d["id"], created_at=d["created_at"], body=d["body"])


@router.put("/config/screen-templates")
async def save_screen_templates(
    body: ScreenTemplatesConfig,
    pool: Pool,
    owner: Owner,
    note: NoteParam = None,
    base_version_id: BaseVersionParam = None,
) -> ScreenTemplatesVersion:
    """Save the templates as a new version; 422 names an unknown metric or a wrong operator."""
    stored = await save_versioned(
        pool,
        "screen_templates",
        body,
        created_by=owner.user_id,
        note=note,
        base_version_id=base_version_id,
    )
    d = _as_dict(stored)
    return ScreenTemplatesVersion(id=d["id"], created_at=d["created_at"], body=d["body"])


@router.get("/config/screen-templates/defaults")
async def screen_template_defaults(_owner: Owner) -> ScreenTemplatesConfig:
    """The built-in starter screens, for "reset to default"."""
    return ScreenTemplatesConfig()
