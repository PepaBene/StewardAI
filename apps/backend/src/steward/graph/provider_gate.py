"""Auth-failure policy for research collectors (G12).

A single 401/403 is ambiguous: bad credentials, an expired token, a throttled client or a
plan boundary. Recording it as `access_unverified` latches `hard_blocked` in the database until
someone re-probes, so one transient 403 used to disable an endpoint forever. Here the first
failures are `temporarily_failed` with a growing cooldown (`retry_at`); only the third
consecutive one is reported as `access_unverified` (which hard-blocks). A success clears the
count, and `reset_block` is the owner's explicit way to lift a block.
"""

from __future__ import annotations

import datetime as dt

import asyncpg

from steward.sources.base import AccessResult, AccessState

HARD_BLOCK_AFTER = 3
COOLDOWNS = (dt.timedelta(hours=1), dt.timedelta(hours=6))


async def gate_open(pool: asyncpg.Pool, provider: str, endpoint: str) -> bool:
    """Verified available, not hard-blocked and past any retry window."""
    row = await pool.fetchrow(
        "select state, public.provider_gate_open(provider, endpoint) as open"
        " from public.provider_access where provider=$1 and endpoint=$2",
        provider,
        endpoint,
    )
    return bool(row is not None and row["open"] and row["state"] == AccessState.AVAILABLE.value)


async def gate_usable(pool: asyncpg.Pool, provider: str, endpoint: str) -> bool:
    """Open for a call: available, or never verified (the call is the probe), and neither
    hard-blocked, paid-only nor in a retry window. Without this, an endpoint seeded as
    unverified is never tried and so never becomes available."""
    row = await pool.fetchrow(
        "select state, public.provider_gate_open(provider, endpoint) as open"
        " from public.provider_access where provider=$1 and endpoint=$2",
        provider,
        endpoint,
    )
    usable = (AccessState.AVAILABLE.value, AccessState.ACCESS_UNVERIFIED.value)
    return bool(row is not None and row["open"] and row["state"] in usable)


async def note_auth_failure(
    pool: asyncpg.Pool,
    provider: str,
    endpoint: str,
    status_code: int,
    *,
    now: dt.datetime | None = None,
) -> AccessResult:
    """Count a consecutive 401/403 and say how to record it."""
    now = now or dt.datetime.now(dt.UTC)
    count = await pool.fetchval(
        """insert into public.provider_auth_failure
             (provider,endpoint,consecutive,last_status,last_failure_at)
           values($1,$2,1,$3,$4)
           on conflict(provider,endpoint) do update set
             consecutive=provider_auth_failure.consecutive+1,
             last_status=excluded.last_status,last_failure_at=excluded.last_failure_at
           returning consecutive""",
        provider,
        endpoint,
        status_code,
        now,
    )
    if count >= HARD_BLOCK_AFTER:
        await pool.execute(
            "update public.provider_auth_failure set permanent=true"
            " where provider=$1 and endpoint=$2",
            provider,
            endpoint,
        )
        return AccessResult(
            AccessState.ACCESS_UNVERIFIED,
            f"HTTP {status_code} on {count} consecutive attempts; blocked until reset",
        )
    cooldown = COOLDOWNS[min(count, len(COOLDOWNS)) - 1]
    return AccessResult(
        AccessState.TEMPORARILY_FAILED,
        f"HTTP {status_code} (attempt {count} of {HARD_BLOCK_AFTER}); retrying after cooldown",
        retry_at=now + cooldown,
    )


async def note_success(pool: asyncpg.Pool, provider: str, endpoint: str) -> None:
    await pool.execute(
        "update public.provider_auth_failure set consecutive=0, permanent=false,"
        " blocked_until=null where provider=$1 and endpoint=$2",
        provider,
        endpoint,
    )


async def reset_block(pool: asyncpg.Pool, provider: str, endpoint: str) -> dict[str, object]:
    """Owner action: lift a hard block and cooldown. An endpoint that was verified before goes
    back to `available`; one never verified returns to `access_unverified` (still gated)."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """update public.provider_access set hard_blocked=false, retry_at=null,
                 consecutive_failures=0,
                 state=case when last_success_at is not null then 'available'
                            else 'access_unverified' end,
                 updated_at=now()
               where provider=$1 and endpoint=$2
               returning provider,endpoint,state,hard_blocked""",
            provider,
            endpoint,
        )
        if row is None:
            raise LookupError(f"{provider}/{endpoint} is not a known provider endpoint")
        await conn.execute(
            "delete from public.provider_auth_failure where provider=$1 and endpoint=$2",
            provider,
            endpoint,
        )
    return dict(row)
