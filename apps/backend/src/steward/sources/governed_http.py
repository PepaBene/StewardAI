"""One governed GET for the free-registry adapters (Companies House, EDINET, Twelve Data).

Every call: the provider-access gate (an endpoint is usable while `available` or still
`access_unverified`, so the first call is the probe; a hard block, a paid-only latch or a
Retry-After window refuses it), one credit reserved through the Postgres governor at background
priority, the request, then the typed outcome recorded through `record_provider_attempt`.
A denial that means "paid plan needed" is recorded as `paid_only` (which hard-blocks the endpoint
and is never retried); a quota answer is `quota_exhausted` with its Retry-After.
Errors never carry the request URL (EDINET sends its key as a query parameter).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import asyncpg
import httpx

from steward.graph import provider_gate
from steward.sources.base import AccessResult, AccessState, Priority
from steward.sources.company_research import _record_http_failure
from steward.sources.free_market import FreeSourceError
from steward.sources.governor import Budget, HardBlocked, PostgresRequestGovernor, QuotaExceeded
from steward.sources.store import record_access

BodyDenial = Callable[[httpx.Response], AccessResult | None]


async def gate_or_probe(pool: asyncpg.Pool, provider: str, endpoint: str) -> None:
    """Refuse a hard-blocked, paid-only or cooling-down endpoint; allow an unverified one."""
    row = await pool.fetchrow(
        "select state, public.provider_gate_open(provider, endpoint) as open"
        " from public.provider_access where provider=$1 and endpoint=$2",
        provider,
        endpoint,
    )
    if row is None:
        raise RuntimeError(f"{provider} {endpoint} has no provider_access row (migration missing)")
    if row["state"] == AccessState.PAID_ONLY.value:
        raise HardBlocked(f"{provider} {endpoint} needs a paid plan and is latched off")
    if not row["open"]:
        raise RuntimeError(f"{provider} {endpoint} endpoint is blocked or cooling down")


async def governed_get(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    provider: str,
    endpoint: str,
    url: str,
    budget: Budget,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    auth: httpx.BasicAuth | None = None,
    body_denial: BodyDenial | None = None,
) -> httpx.Response | None:
    """The 200 response, or None when the provider has nothing for the request (404).

    Raises QuotaExceeded / HardBlocked for quota and denial outcomes, FreeSourceError for any
    other failure."""
    await gate_or_probe(pool, provider, endpoint)
    governor = PostgresRequestGovernor(pool)
    try:
        await governor.reserve(provider, endpoint, Decimal(1), budget, priority=Priority.BACKGROUND)
    except QuotaExceeded:
        await record_access(
            pool, provider, endpoint, AccessResult(AccessState.QUOTA_EXHAUSTED, "free budget used")
        )
        raise
    try:
        response = await http.get(url, params=params, headers=headers, auth=auth)
    except httpx.HTTPError as exc:
        await record_access(
            pool,
            provider,
            endpoint,
            AccessResult(AccessState.TEMPORARILY_FAILED, f"{type(exc).__name__}"),
        )
        raise FreeSourceError(
            f"{provider} request failed ({type(exc).__name__})", provider_charged=False
        ) from exc
    if response.status_code == 404:
        await record_access(
            pool,
            provider,
            endpoint,
            AccessResult(AccessState.AVAILABLE, "nothing for this request (404)"),
            status_code=404,
            credits=1,
        )
        return None
    if response.status_code != 200:
        result = await _record_http_failure(
            pool, provider, endpoint, response, f"{provider} request failed"
        )
        if result.state is AccessState.PAID_ONLY:
            raise HardBlocked(f"{provider} {endpoint} answered a paid-plan denial")
        if result.state is AccessState.QUOTA_EXHAUSTED:
            raise QuotaExceeded(f"{provider} {endpoint} quota answer HTTP {response.status_code}")
        raise FreeSourceError(
            f"{provider} returned HTTP {response.status_code}", response.status_code
        )
    denial = body_denial(response) if body_denial else None
    if denial is not None:
        await record_access(pool, provider, endpoint, denial, status_code=200, credits=1)
        if denial.state is AccessState.PAID_ONLY:
            raise HardBlocked(f"{provider} {endpoint}: {denial.detail}")
        if denial.state is AccessState.QUOTA_EXHAUSTED:
            raise QuotaExceeded(f"{provider} {endpoint}: {denial.detail}")
        raise FreeSourceError(f"{provider}: {denial.detail}", 200)
    await provider_gate.note_success(pool, provider, endpoint)
    await record_access(
        pool,
        provider,
        endpoint,
        AccessResult(AccessState.AVAILABLE, "free endpoint returned a response"),
        status_code=200,
        credits=1,
    )
    return response
