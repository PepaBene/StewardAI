"""The API's Postgres pool, created on first use so /health works without a database."""

import asyncio
from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, status

from steward.db import create_pool
from steward.settings import get_settings

_pool: asyncpg.Pool | None = None
_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _lock:
        if _pool is None:
            settings = get_settings()
            if settings.database_url is None:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "Database is not configured"
                )
            try:
                _pool = await create_pool(settings)
            except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    f"Database is unreachable ({type(exc).__name__})",
                ) from exc
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


Pool = Annotated[asyncpg.Pool, Depends(get_pool)]


async def get_pool_or_error() -> asyncpg.Pool | str:
    """The pool, or why there is none (for the readiness check, which must answer anyway)."""
    try:
        return await get_pool()
    except HTTPException as exc:
        return str(exc.detail)
