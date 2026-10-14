"""The gates a proposed confidence floor must pass, in a row the owner edits (autonomous engine
plan, L5).

The defaults are the old constants. The owner may change them on the engine page; the values are
bounds-checked so a typo cannot switch the gate off (at least 5 decisions, 3 reviewed, and a
floor may not be allowed to make things worse by more than a tenth). The engine only reads this:
`save` is called from the owner-authenticated route alone (a test holds that), and `gates` is on
the freeze list of `engine.change`, so the engine cannot propose a change to it.
"""

import json
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from steward.engine.change import Gates

NAME = "floor"


class FloorGateSettings(BaseModel):
    min_n: int = Field(default=30, ge=5, le=1000, description="Decisions needed to judge a floor.")
    min_reviewed: int = Field(
        default=10, ge=3, le=500, description="Of them, reviewed decisions needed."
    )
    max_wrong_rate_increase: float = Field(
        default=-0.01,
        ge=-0.5,
        le=0.1,
        description="Change in the wrong share a floor must achieve; negative means fewer wrong.",
    )

    def gates(self) -> Gates:
        return Gates(
            min_n=self.min_n,
            min_reviewed=self.min_reviewed,
            max_wrong_rate_increase=self.max_wrong_rate_increase,
            require_golden=False,
        )


async def load_settings(pool: asyncpg.Pool) -> FloorGateSettings:
    body: Any = await pool.fetchval(
        "select body from public.engine_gate_setting where name = $1", NAME
    )
    if body is None:
        return FloorGateSettings()
    return FloorGateSettings.model_validate(json.loads(body) if isinstance(body, str) else body)


async def floor_gates(pool: asyncpg.Pool) -> Gates:
    return (await load_settings(pool)).gates()


async def save(
    pool: asyncpg.Pool, settings: FloorGateSettings, owner_id: str | None
) -> FloorGateSettings:
    """Only the owner route calls this."""
    await pool.execute(
        "insert into public.engine_gate_setting (name, body, updated_by) values ($1, $2::jsonb, $3)"
        " on conflict (name) do update set body = excluded.body, updated_at = now(),"
        " updated_by = excluded.updated_by",
        NAME,
        settings.model_dump_json(),
        owner_id,
    )
    return settings
