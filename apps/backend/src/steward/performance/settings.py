"""Owner settings for performance and holding-period tracking: a versioned config domain
(`performance`, the models in `steward.config.performance`), so every save is a config version
with history, diff and rollback. The table `public.performance_setting` only holds what was saved
before the domain existed."""

from __future__ import annotations

import asyncpg

from steward.config.performance import (
    BenchmarkComponent,
    BenchmarkSpec,
    PerformanceSettings,
)
from steward.config.store import load_active, save_config

__all__ = [
    "DOMAIN",
    "BenchmarkComponent",
    "BenchmarkSpec",
    "PerformanceSettings",
    "load_latest_settings",
    "load_settings",
    "save_settings",
]

DOMAIN = "performance"


async def load_settings(
    pool: asyncpg.Pool, owner_id: str
) -> tuple[PerformanceSettings, int | None]:
    """The active settings and their config version id (None when never saved: the defaults).
    The app has one owner, so `owner_id` only keeps the callers' signature."""
    stored = await load_active(pool, DOMAIN, PerformanceSettings)
    if stored is None:
        return PerformanceSettings(), None
    return stored.body, stored.id


async def load_latest_settings(pool: asyncpg.Pool) -> PerformanceSettings:
    """The active settings, or the defaults. For jobs that run without a signed-in user."""
    stored = await load_active(pool, DOMAIN, PerformanceSettings)
    return stored.body if stored else PerformanceSettings()


async def save_settings(pool: asyncpg.Pool, owner_id: str, settings: PerformanceSettings) -> int:
    """Save as a new config version (history, diff and rollback come with it); returns its id.
    Settings identical to the active version are not stored again."""
    async with pool.acquire() as conn:
        stored = await save_config(conn, DOMAIN, settings, created_by=owner_id)
    return stored.id
