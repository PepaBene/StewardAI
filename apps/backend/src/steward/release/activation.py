"""Switching features on (M8.5): the owner's decision, recorded and held to the release gate.

A feature can be switched on only while the gate says it may (status ready, source audit passed,
zero-cost audit passed). Switching off is always allowed. Append-only: the newest row stands."""

from __future__ import annotations

import datetime as dt

import asyncpg
from pydantic import BaseModel, Field

from steward.release.readiness import FEATURES, Readiness


class ActivationIn(BaseModel):
    feature: str = Field(min_length=1, max_length=60)
    active: bool
    note: str | None = Field(default=None, max_length=500)


class Activation(BaseModel):
    feature: str
    active: bool
    note: str | None
    since: dt.datetime


class ActivationRefused(Exception):
    """The gate does not allow this feature to be switched on."""


def check(body: ActivationIn, readiness: Readiness) -> None:
    """Raise KeyError for an unknown feature, ActivationRefused when it may not be switched on."""
    if body.feature not in {f.id for f in FEATURES}:
        raise KeyError(body.feature)
    if not body.active:
        return
    state = next(f for f in readiness.features if f.id == body.feature)
    if not state.can_activate:
        raise ActivationRefused(
            f"{state.title} stays off: {state.reason}; source audit {state.audit}."
        )


async def record(pool: asyncpg.Pool, owner_id: str, body: ActivationIn) -> None:
    await pool.execute(
        "insert into public.feature_activation (owner_id, feature, active, note)"
        " values ($1,$2,$3,$4)",
        owner_id,
        body.feature,
        body.active,
        body.note,
    )


async def list_current(pool: asyncpg.Pool, owner_id: str) -> list[Activation]:
    rows = await pool.fetch(
        "select distinct on (feature) feature, active, note, created_at as since"
        " from public.feature_activation where owner_id = $1 order by feature, id desc",
        owner_id,
    )
    return [Activation(**dict(r)) for r in rows]
