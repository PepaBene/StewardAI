"""Authenticated API routes for the prompts config: the owner's overrides of the AI steps'
system prompts, and the built-in defaults they replace."""

import datetime as dt
from typing import get_args

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.routes import BaseVersionParam, NoteParam, _as_dict, _not_found, save_versioned
from steward.config import PromptsConfig
from steward.config.prompts import PROMPTS, PromptKey, default_text
from steward.config.store import load_active

router = APIRouter()


class PromptsConfigVersion(BaseModel):
    id: int
    created_at: dt.datetime
    note: str | None
    body: PromptsConfig


class PromptDefault(BaseModel):
    key: PromptKey
    title: str
    description: str
    default_text: str = Field(description="The built-in prompt, used while no override is set.")


@router.get("/config/prompts", tags=["config"])
async def get_prompts_config(pool: Pool, _owner: Owner) -> PromptsConfigVersion:
    """The saved prompt overrides. 404 means every AI step uses its built-in prompt."""
    stored = await load_active(pool, "prompts", PromptsConfig)
    if stored is None:
        raise _not_found("prompts")
    return PromptsConfigVersion(**_as_dict(stored))


@router.put("/config/prompts", tags=["config"])
async def save_prompts_config(
    body: PromptsConfig,
    pool: Pool,
    owner: Owner,
    note: NoteParam = None,
    base_version_id: BaseVersionParam = None,
) -> PromptsConfigVersion:
    """Save prompt overrides. The next run of each AI step uses them; an overridden prompt is
    not covered by the CI evals."""
    stored = await save_versioned(
        pool,
        "prompts",
        body,
        created_by=owner.user_id,
        note=note,
        base_version_id=base_version_id,
    )
    return PromptsConfigVersion(**_as_dict(stored))


@router.post("/config/prompts/validate", tags=["config"])
async def validate_prompts(body: PromptsConfig, _owner: Owner) -> PromptsConfig:
    """Validate prompt overrides without saving. 422 lists every error."""
    return body


@router.get("/config/prompts/defaults", tags=["config"])
async def get_prompt_defaults(_owner: Owner) -> list[PromptDefault]:
    """Every overridable prompt with its built-in text, in pipeline order."""
    return [
        PromptDefault(
            key=key,
            title=PROMPTS[key].title,
            description=PROMPTS[key].description,
            default_text=default_text(key),
        )
        for key in get_args(PromptKey)
    ]
