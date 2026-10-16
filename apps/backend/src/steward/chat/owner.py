"""Who the single owner is, for background runs that have no signed-in request."""

from __future__ import annotations

import asyncpg

from steward.settings import get_settings


async def owner_of(pool: asyncpg.Pool) -> str | None:
    """The single owner's user id, for the owner-scoped read tools; None if it cannot be read.
    The configured owner (the one the API signs in) comes first: the backend's least-privilege
    role cannot read `private.app_owner`, so on its own that read always came back empty."""
    configured = get_settings().owner_user_id
    if configured:
        return configured
    try:
        value = await pool.fetchval("select user_id::text from private.app_owner limit 1")
    except asyncpg.PostgresError:
        return None
    return str(value) if value else None
