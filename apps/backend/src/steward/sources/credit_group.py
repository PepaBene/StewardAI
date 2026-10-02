"""Atomic multi-credit reservations with refund (audit B24).

A call that spends credits on two governed endpoints (Alpha Vantage's account-wide
`account_free` budget and its own endpoint budget) used to reserve them one after the other: if
the second reservation was refused, the first credit stayed spent for a request that never
happened. `reserve_group` makes the reservation one database transaction: either every part is
granted or none is. `refund` gives credits back when the request provably never reached the
provider (a network failure before any response).
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import asyncpg

from steward.sources.governor import Budget, QuotaExceeded


@dataclass(frozen=True, slots=True)
class CreditPart:
    provider: str
    endpoint: str
    cost: Decimal
    budget: Budget
    # Spend the budget's reserve too (an owner-triggered request).
    urgent: bool = False


@dataclass(frozen=True, slots=True)
class GroupReservation:
    parts: tuple[CreditPart, ...]
    at: dt.datetime


async def reserve_group(
    pool: asyncpg.Pool, parts: list[CreditPart], *, now: dt.datetime | None = None
) -> GroupReservation:
    """Reserve every part in one transaction; raise QuotaExceeded (nothing spent) otherwise."""
    if not parts:
        raise ValueError("a credit group needs at least one part")
    at = now or dt.datetime.now(dt.UTC)
    async with pool.acquire() as conn, conn.transaction():
        # A stable order keeps two concurrent groups from locking in opposite orders.
        for part in sorted(parts, key=lambda p: (p.provider, p.endpoint)):
            if part.cost <= 0:
                raise ValueError("request cost must be positive")
            allowed = await conn.fetchval(
                "select public.reserve_provider_credits($1,$2,$3,$4,$5,$6,$7,$8)",
                part.provider,
                part.endpoint,
                part.cost,
                part.budget.per_minute,
                part.budget.daily,
                part.budget.monthly,
                Decimal(0) if part.urgent else part.budget.reserve,
                at,
            )
            if not allowed:
                # Leaving the transaction block raises: the earlier parts roll back with it.
                raise QuotaExceeded(
                    f"{part.provider}/{part.endpoint}: free quota exhausted or endpoint is"
                    " hard-blocked"
                )
    return GroupReservation(tuple(parts), at)


async def refund(pool: asyncpg.Pool, reservation: GroupReservation) -> None:
    """Give a reservation back (the request never reached the provider)."""
    async with pool.acquire() as conn, conn.transaction():
        for part in sorted(reservation.parts, key=lambda p: (p.provider, p.endpoint)):
            await conn.execute(
                "select public.refund_provider_credits($1,$2,$3,$4)",
                part.provider,
                part.endpoint,
                part.cost,
                reservation.at,
            )
