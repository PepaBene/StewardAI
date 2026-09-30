"""Atomic request-budget decisions; database persistence is provided by provider_quota_usage."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import asyncpg


class QuotaExceeded(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Budget:
    per_minute: Decimal | None = None
    daily: Decimal | None = None
    monthly: Decimal | None = None
    reserve: Decimal = Decimal(0)


class RequestGovernor:
    """Process-local governor. reserve() is atomic and never allows paid usage."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._usage: dict[tuple[str, str, str, dt.datetime | dt.date], Decimal] = {}
        self._blocked: set[tuple[str, str]] = set()

    async def reserve(
        self,
        provider: str,
        endpoint: str,
        cost: Decimal,
        budget: Budget,
        *,
        urgent: bool = False,
        now: dt.datetime | None = None,
    ) -> None:
        if cost <= 0:
            raise ValueError("request cost must be positive")
        now = now or dt.datetime.now(dt.UTC)
        async with self._lock:
            if (provider, endpoint) in self._blocked:
                raise QuotaExceeded(
                    "endpoint is hard-blocked after an authentication or paid-tier denial"
                )
            minute_key = (provider, endpoint, "minute", now.replace(second=0, microsecond=0))
            day_key = (provider, endpoint, "day", now.date())
            month_key = (provider, endpoint, "month", now.date().replace(day=1))
            minute = self._usage.get(minute_key, Decimal(0))
            day = self._usage.get(day_key, Decimal(0))
            month = self._usage.get(month_key, Decimal(0))
            reserve = Decimal(0) if urgent else budget.reserve
            if budget.per_minute is not None and minute + cost > budget.per_minute:
                raise QuotaExceeded("per-minute free quota exhausted")
            if budget.daily is not None and day + cost > budget.daily - reserve:
                raise QuotaExceeded("daily free quota exhausted")
            if budget.monthly is not None and month + cost > budget.monthly - reserve:
                raise QuotaExceeded("monthly free quota exhausted")
            self._usage[minute_key] = minute + cost
            self._usage[day_key] = day + cost
            self._usage[month_key] = month + cost

    async def hard_block(self, provider: str, endpoint: str) -> None:
        async with self._lock:
            self._blocked.add((provider, endpoint))


class PostgresRequestGovernor:
    """Cross-process quota governor backed by the database.

    Workers must use this implementation for real provider calls.  The in-memory governor
    remains useful for isolated adapters and tests, but cannot coordinate multiple workers.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def reserve(
        self,
        provider: str,
        endpoint: str,
        cost: Decimal,
        budget: Budget,
        *,
        urgent: bool = False,
        now: dt.datetime | None = None,
    ) -> None:
        if cost <= 0:
            raise ValueError("request cost must be positive")
        allowed = await self._pool.fetchval(
            "select public.reserve_provider_credits($1,$2,$3,$4,$5,$6,$7,$8)",
            provider,
            endpoint,
            cost,
            budget.per_minute,
            budget.daily,
            budget.monthly,
            Decimal(0) if urgent else budget.reserve,
            now or dt.datetime.now(dt.UTC),
        )
        if not allowed:
            raise QuotaExceeded("free quota exhausted or endpoint is hard-blocked")

    async def hard_block(self, provider: str, endpoint: str) -> None:
        await self._pool.execute(
            "update public.provider_access set hard_blocked = true, updated_at = now() "
            "where provider = $1 and endpoint = $2",
            provider,
            endpoint,
        )
