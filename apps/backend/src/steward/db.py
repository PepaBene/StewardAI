"""Postgres access over a direct connection as the least-privilege `app_backend` role."""

import asyncpg

from steward.settings import Settings


async def create_pool(settings: Settings, *, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is not set")
    return await asyncpg.create_pool(
        settings.database_url.get_secret_value(),
        min_size=min_size,
        max_size=max_size,
        # Fail fast rather than hang when the database is unreachable.
        timeout=10,
        command_timeout=60,
        server_settings={"application_name": "steward-backend"},
    )
