"""The API's Postgres pool, created on first use so /health works without a database."""

import asyncio
from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, Path, Query, status
from pydantic import Field

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

# Row ids are Postgres bigints: bound them so an out-of-range id is a 422, not a 500 from
# asyncpg failing to encode it.
DB_ID_MAX = 2**63 - 1
DbId = Annotated[int, Field(ge=1, le=DB_ID_MAX)]
IdPath = Annotated[int, Path(ge=1, le=DB_ID_MAX)]
IdQuery = Annotated[int | None, Query(ge=1, le=DB_ID_MAX)]


async def get_pool_or_error() -> asyncpg.Pool | str:
    """The pool, or why there is none (for the readiness check, which must answer anyway)."""
    try:
        return await get_pool()
    except HTTPException as exc:
        return str(exc.detail)
