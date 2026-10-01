"""Enforcement adapters: the Phase 8 gates made callable from production code (audit B46).

`require_domain_gate`, `publish_gate`, `disable_policy`, `budget_from_access` and
`verify_hard_stop` used to be called by tests and read-only routes only.  This module is the
small surface other code calls so a gate can actually stop something.

Alerts and valuation conclusions (reconciliation gate, plan D8.3)::

    decision = await conclusion_gate(pool, "price_alert")       # never raises; fails closed
    if not decision.allowed:
        skip, and trail decision.reasons          # e.g. "0 distinct subjects sampled, 30 required"
    # or, where an exception is more convenient:
    await require_conclusion(pool, "valuation_conclusion")       # raises GateBlocked

Derived signal publishers (audit gate, plan D8.6) call `pre_publish_check` immediately before
they write or serve a derived value; it raises `PublishBlocked` unless the signal's latest
overall audit passes and no newer scoped audit fails::

    await pre_publish_check(pool, "insider_net_discretionary")

Collectors and the production governor (zero-cost gate, plan D8.5).  Every provider call goes
through three steps, all provided here::

    budget = await budget_for_endpoint(pool, provider, endpoint)      # the ledger's free quota
    await reserve_for_endpoint(pool, provider, endpoint)              # raises QuotaExceeded
    ... call the provider ...
    directive = await record_outcome(pool, provider, endpoint, access, status_code=status)
    if directive.stop:                                  # disable_policy said stop: do not loop
        break

* `budget_for_endpoint` is `budget_from_access` applied to the endpoint's `provider_access`
  row: the budget a collector hands to `PostgresRequestGovernor.reserve` is derived from the
  declared free quota instead of a number typed into the collector.  An endpoint with a
  declared quota but a window the governor cannot count (week/year) needs an explicit budget
  from the caller; a credentialed endpoint with no declared quota is refused unless its
  provider is a documented no-overage provider.
* `reserve_for_endpoint` is that budget plus `PostgresRequestGovernor.reserve`, which already
  refuses hard-blocked endpoints and pending Retry-After windows (migration
  20261005100000: `reserve_provider_credits` / `provider_gate_open`).
* `record_outcome` writes the attempt through `record_access` and applies `disable_policy`:
  paid-only and rejected-key outcomes are hard-blocked by `record_provider_attempt`; an
  exhausted quota that arrives without a Retry-After is given a retry window that ends when
  the allowance window resets, so the endpoint really stops instead of being retried on every
  tick; a per-request `not_covered` stops only that request.

`EndpointDisabled`, `HardBlocked` and `RetryPending` are `QuotaExceeded` subclasses, so every
collector that already stops on `QuotaExceeded` stops on these too.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import asyncpg

from steward.sources.base import AccessResult, AccessState, Priority
from steward.sources.governor import (
    Budget,
    HardBlocked,
    PostgresRequestGovernor,
    QuotaExceeded,
    RetryPending,
)
from steward.sources.store import provider_gate, record_access
from steward.verify_sources.budgets import NO_OVERAGE_PROVIDERS
from steward.verify_sources.signal_audit import (
    PublishBlocked,
    PublishDecision,
    publish_gate,
)
from steward.verify_sources.thresholds import (
    CONCLUSION_DOMAINS,
    GateBlocked,
    GateDecision,
    require_domain_gate,
)
from steward.verify_sources.zero_cost import DisableDecision, budget_from_access, disable_policy


# ----------------------------------------------------------------------------- gates
@dataclass(frozen=True, slots=True)
class Enforcement:
    """The answer of a gate adapter: may the caller go ahead, and why not."""

    allowed: bool
    kind: Literal["conclusion", "signal"]
    subject: str
    reasons: tuple[str, ...] = ()


async def conclusion_gate(
    pool: asyncpg.Pool, conclusion: str, *, now: dt.datetime | None = None
) -> Enforcement:
    """May a new alert or valuation conclusion of this kind be switched on?  Never raises:
    an unknown conclusion kind or a database error is a refusal (fail closed)."""
    try:
        await require_domain_gate(pool, conclusion, now=now)
    except GateBlocked as exc:
        reasons = tuple(
            f"{d.domain}: {reason}" for d in exc.decisions if not d.allowed for reason in d.reasons
        )
        return Enforcement(False, "conclusion", conclusion, reasons or (str(exc),))
    except (asyncpg.PostgresError, OSError) as exc:
        return Enforcement(False, "conclusion", conclusion, (f"gate unavailable: {exc}",))
    return Enforcement(True, "conclusion", conclusion)


async def require_conclusion(
    pool: asyncpg.Pool, conclusion: str, *, now: dt.datetime | None = None
) -> list[GateDecision]:
    """`require_domain_gate`, re-exported as the production entry point (raises GateBlocked)."""
    return await require_domain_gate(pool, conclusion, now=now)


async def signal_gate(
    pool: asyncpg.Pool, signal_name: str, *, now: dt.datetime | None = None
) -> Enforcement:
    """May this derived signal publish?  Never raises; fails closed."""
    try:
        decision = await publish_gate(pool, signal_name, now=now)
    except (asyncpg.PostgresError, OSError) as exc:
        return Enforcement(False, "signal", signal_name, (f"gate unavailable: {exc}",))
    return Enforcement(decision.allowed, "signal", signal_name, decision.reasons)


async def pre_publish_check(
    pool: asyncpg.Pool, signal_name: str, *, now: dt.datetime | None = None
) -> PublishDecision:
    """Raise `PublishBlocked` unless `signal_name` may publish.  Call it right before a derived
    signal is written or served, in the publisher itself (not in a route in front of it)."""
    decision = await publish_gate(pool, signal_name, now=now)
    if not decision.allowed:
        raise PublishBlocked(decision)
    return decision


def conclusions_requiring(domain: str) -> list[str]:
    """Which conclusion kinds depend on a domain (for trails and messages)."""
    return sorted(c for c, domains in CONCLUSION_DOMAINS.items() if domain in domains)


# ----------------------------------------------------------------------------- endpoints
class EndpointDisabled(QuotaExceeded):
    """The endpoint must not be called: its state or quota declaration forbids it."""


class NoDeclaredQuota(EndpointDisabled):
    """A credentialed endpoint has no free quota the governor could stop at."""


@dataclass(frozen=True, slots=True)
class EndpointDirective:
    provider: str
    endpoint: str
    stop: bool
    permanent: bool
    reason: str | None
    scope: Literal["endpoint", "request", "none"]
    retry_at: dt.datetime | None = None


async def _ledger_row(pool: asyncpg.Pool, provider: str, endpoint: str) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "select state, credential_env, quota, quota_period, hard_blocked, retry_at"
        " from public.provider_access where provider=$1 and endpoint=$2",
        provider,
        endpoint,
    )


async def budget_for_endpoint(
    pool: asyncpg.Pool,
    provider: str,
    endpoint: str,
    *,
    reserve: Decimal = Decimal(0),
    fallback: Budget | None = None,
) -> Budget:
    """The governor budget the access ledger implies for this endpoint.

    * declared quota in a minute/day/month window -> `budget_from_access` (reserve applies to
      day and month windows only);
    * declared quota in a window the governor cannot count -> `fallback` (required);
    * no declared quota and a documented no-overage provider, or a keyless endpoint -> an empty
      budget (no local cap: the provider itself refuses beyond its free limit);
    * no declared quota on a credentialed endpoint of any other provider -> `NoDeclaredQuota`.
    """
    row = await _ledger_row(pool, provider, endpoint)
    if row is None:
        raise EndpointDisabled(f"{provider}/{endpoint} has no provider_access row")
    if row["quota"] is None:
        if row["credential_env"] and provider not in NO_OVERAGE_PROVIDERS:
            raise NoDeclaredQuota(
                f"{provider}/{endpoint} uses a key but declares no free quota: "
                "no hard stop is possible"
            )
        return fallback or Budget()
    budget = budget_from_access(row["quota"], row["quota_period"], reserve=reserve)
    if budget is None:
        if fallback is None:
            raise NoDeclaredQuota(
                f"{provider}/{endpoint} quota is per {row['quota_period']!r}, which the governor "
                "cannot count; pass an explicit fallback budget"
            )
        return fallback
    return budget


async def reserve_for_endpoint(
    pool: asyncpg.Pool,
    provider: str,
    endpoint: str,
    cost: Decimal = Decimal(1),
    *,
    reserve: Decimal = Decimal(0),
    fallback: Budget | None = None,
    priority: Priority | None = None,
    urgent: bool = False,
    job_id: int | None = None,
    now: dt.datetime | None = None,
) -> Budget:
    """Reserve `cost` credits against the ledger-derived budget on the production governor.

    Raises `QuotaExceeded` (or `HardBlocked` / `RetryPending` / `EndpointDisabled`, its
    subclasses) when the call must not be made.  Returns the budget it used."""
    budget = await budget_for_endpoint(pool, provider, endpoint, reserve=reserve, fallback=fallback)
    await PostgresRequestGovernor(pool).reserve(
        provider, endpoint, cost, budget, urgent=urgent, priority=priority, job_id=job_id, now=now
    )
    return budget


async def require_endpoint_open(
    pool: asyncpg.Pool, provider: str, endpoint: str, *, now: dt.datetime | None = None
) -> None:
    """Raise unless a collector may call the endpoint now (the single `provider_gate` rule)."""
    gate = await provider_gate(pool, provider, endpoint, now=now)
    if gate.open:
        return
    if gate.retry_at is not None:
        raise RetryPending(f"{provider}/{endpoint}: {gate.reason}", gate.retry_at)
    if gate.reason and gate.reason.startswith("hard-blocked"):
        raise HardBlocked(f"{provider}/{endpoint}: {gate.reason}")
    raise EndpointDisabled(f"{provider}/{endpoint}: {gate.reason}")


def next_window_start(period: str | None, now: dt.datetime) -> dt.datetime:
    """When the allowance window containing `now` resets (UTC); an hour when unknown."""
    if period == "minute":
        return now.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    if period == "day":
        return dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(), dt.UTC)
    if period == "month":
        first = now.date().replace(day=1)
        year, month = (first.year + 1, 1) if first.month == 12 else (first.year, first.month + 1)
        return dt.datetime(year, month, 1, tzinfo=dt.UTC)
    return now + dt.timedelta(hours=1)


def directive_for(
    provider: str,
    endpoint: str,
    decision: DisableDecision,
    retry_at: dt.datetime | None,
) -> EndpointDirective:
    return EndpointDirective(
        provider,
        endpoint,
        decision.stop_calling,
        decision.permanent,
        decision.reason.value if decision.reason else None,
        decision.scope,
        retry_at,
    )


async def record_outcome(
    pool: asyncpg.Pool,
    provider: str,
    endpoint: str,
    access: AccessResult,
    *,
    status_code: int | None = None,
    request_id: str | None = None,
    elapsed_ms: int | None = None,
    response_bytes: int | None = None,
    credits: float = 0,
    job_id: int | None = None,
    now: dt.datetime | None = None,
) -> EndpointDirective:
    """Record one attempt and return what `disable_policy` says the collector must do next.

    The attempt is written with `record_access` (hard-blocking paid-only and rejected-key
    outcomes in SQL).  A quota-exhausted outcome without a server Retry-After gets a retry
    window ending at the next reset of the endpoint's allowance, so the endpoint stops for
    every caller instead of being retried until the local counters catch up."""
    now = now or dt.datetime.now(dt.UTC)
    decision = disable_policy(access.state, status_code)
    retry_at = access.retry_at
    if access.state == AccessState.QUOTA_EXHAUSTED and retry_at is None:
        row = await _ledger_row(pool, provider, endpoint)
        retry_at = next_window_start(row["quota_period"] if row else None, now)
        access = AccessResult(access.state, access.detail, retry_at, access.request_id)
    await record_access(
        pool,
        provider,
        endpoint,
        access,
        status_code=status_code,
        request_id=request_id,
        elapsed_ms=elapsed_ms,
        response_bytes=response_bytes,
        credits=credits,
        job_id=job_id,
    )
    return directive_for(provider, endpoint, decision, retry_at)
