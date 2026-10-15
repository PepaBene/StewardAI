"""Who the single owner is, for background runs that have no signed-in request."""

from __future__ import annotations

import asyncpg


async def owner_of(pool: asyncpg.Pool) -> str | None:
    """The single owner's user id, for the owner-scoped read tools; None if it cannot be read."""
    try:
        value = await pool.fetchval("select user_id::text from private.app_owner limit 1")
    except asyncpg.PostgresError:
        return None
    return str(value) if value else None
