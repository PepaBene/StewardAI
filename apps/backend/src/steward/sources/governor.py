"""Atomic request-budget decisions; database persistence is provided by provider_quota_usage."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import asyncpg

from steward.sources.base import Priority


class QuotaExceeded(RuntimeError):
    """The request must not be made now: allowance used up, endpoint blocked or backing off."""


class HardBlocked(QuotaExceeded):
    """The endpoint is hard-blocked after an authentication or paid-tier denial. Not a quota
    state: only a changed credential or an owner re-probe lifts it (B1)."""


class RetryPending(QuotaExceeded):
    """The provider's Retry-After / backoff window has not elapsed yet (A6)."""

    def __init__(self, message: str, retry_at: dt.datetime | None = None) -> None:
        super().__init__(message)
        self.retry_at = retry_at


@dataclass(frozen=True, slots=True)
class Budget:
    per_minute: Decimal | None = None
    daily: Decimal | None = None
    monthly: Decimal | None = None
    reserve: Decimal = Decimal(0)


def effective_reserve(budget: Budget, urgent: bool, priority: Priority | None) -> Decimal:
    """The part of the allowance a reservation of this priority must leave untouched."""
    if priority is None:
        priority = Priority.URGENT if urgent else Priority.NORMAL
    return budget.reserve * int(priority)


class RequestGovernor:
    """Process-local governor. reserve() is atomic and never allows paid usage."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._usage: dict[tuple[str, str, str, dt.datetime | dt.date], Decimal] = {}
        self._blocked: set[tuple[str, str]] = set()
        self._retry_at: dict[tuple[str, str], dt.datetime] = {}

    async def defer(self, provider: str, endpoint: str, until: dt.datetime) -> None:
        """Refuse reservations for the endpoint until `until` (a server Retry-After)."""
        async with self._lock:
            current = self._retry_at.get((provider, endpoint))
            if current is None or until > current:
                self._retry_at[(provider, endpoint)] = until

    async def reserve(
        self,
        provider: str,
        endpoint: str,
        cost: Decimal,
        budget: Budget,
        *,
        urgent: bool = False,
        priority: Priority | None = None,
        job_id: int | None = None,
        now: dt.datetime | None = None,
    ) -> None:
        if cost <= 0:
            raise ValueError("request cost must be positive")
        now = now or dt.datetime.now(dt.UTC)
        async with self._lock:
            if (provider, endpoint) in self._blocked:
                raise HardBlocked(
                    "endpoint is hard-blocked after an authentication or paid-tier denial"
                )
            until = self._retry_at.get((provider, endpoint))
            if until is not None and until > now:
                raise RetryPending(f"provider asked to wait until {until.isoformat()}", until)
            minute_key = (provider, endpoint, "minute", now.replace(second=0, microsecond=0))
            day_key = (provider, endpoint, "day", now.date())
            month_key = (provider, endpoint, "month", now.date().replace(day=1))
            minute = self._usage.get(minute_key, Decimal(0))
            day = self._usage.get(day_key, Decimal(0))
            month = self._usage.get(month_key, Decimal(0))
            reserve = effective_reserve(budget, urgent, priority)
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
        priority: Priority | None = None,
        job_id: int | None = None,
        now: dt.datetime | None = None,
    ) -> None:
        if cost <= 0:
            raise ValueError("request cost must be positive")
        now = now or dt.datetime.now(dt.UTC)
        allowed = await self._pool.fetchval(
            "select public.reserve_provider_credits($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            provider,
            endpoint,
            cost,
            budget.per_minute,
            budget.daily,
            budget.monthly,
            effective_reserve(budget, urgent, priority),
            now,
            job_id,
        )
        if allowed:
            return
        # The function answers only yes/no; say why, so callers (and the audit) never report
        # a hard block or a Retry-After window as an exhausted quota.
        gate = await self._pool.fetchrow(
            "select hard_blocked, retry_at from public.provider_access"
            " where provider=$1 and endpoint=$2",
            provider,
            endpoint,
        )
        if gate is None:
            raise QuotaExceeded(f"{provider}/{endpoint} has no provider_access row")
        if gate["hard_blocked"]:
            raise HardBlocked(
                "endpoint is hard-blocked after an authentication or paid-tier denial"
            )
        if gate["retry_at"] is not None and gate["retry_at"] > now:
            raise RetryPending(
                f"provider asked to wait until {gate['retry_at'].isoformat()}", gate["retry_at"]
            )
        raise QuotaExceeded("free quota exhausted")

    async def defer(self, provider: str, endpoint: str, until: dt.datetime) -> None:
        """Refuse reservations for the endpoint until `until` (a server Retry-After)."""
        await self._pool.execute(
            "update public.provider_access set retry_at = greatest(coalesce(retry_at, $3), $3),"
            " updated_at = now() where provider = $1 and endpoint = $2",
            provider,
            endpoint,
            until,
        )

    async def hard_block(self, provider: str, endpoint: str) -> None:
        await self._pool.execute(
            "update public.provider_access set hard_blocked = true, updated_at = now() "
            "where provider = $1 and endpoint = $2",
            provider,
            endpoint,
        )
