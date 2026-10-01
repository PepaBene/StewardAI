"""Governed source adapter contract shared by free-data providers."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from enum import IntEnum, StrEnum
from typing import Any, Protocol, runtime_checkable


class Priority(IntEnum):
    """Request priority: how much of the reserve a reservation may spend (D1.4).

    URGENT may use the whole allowance, NORMAL leaves the budget's reserve, BACKGROUND leaves
    twice that, so scheduled backfills never starve an owner-triggered request."""

    URGENT = 0
    NORMAL = 1
    BACKGROUND = 2


class AccessState(StrEnum):
    AVAILABLE = "available"
    NOT_COVERED = "not_covered"
    PAID_ONLY = "paid_only"
    ACCESS_UNVERIFIED = "access_unverified"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TEMPORARILY_FAILED = "temporarily_failed"
    DISPUTED = "disputed"


@dataclass(frozen=True, slots=True)
class AccessResult:
    state: AccessState
    detail: str | None = None
    retry_at: datetime | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class SourceObservation:
    domain: str
    metric: str
    source: str
    period_start: datetime
    period_end: datetime
    value: Decimal | None
    unit: str
    source_url: str
    retrieved_at: datetime
    publisher_at: datetime | None = None
    geography: str | None = None
    currency: str | None = None
    scale: Decimal = Decimal(1)
    vintage: str | None = None
    dimensions: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    # Canonical ids, when the caller knows them (A8). publish_observations also derives the
    # issuer from the security and the security from the listing when only the narrower id is
    # known; they are never guessed from names.
    issuer_id: int | None = None
    security_id: int | None = None
    listing_id: int | None = None


@dataclass(frozen=True, slots=True)
class FetchResult:
    access: AccessResult
    payload: Any = None
    credits: Decimal = Decimal(0)


@runtime_checkable
class SourceAdapter(Protocol):
    provider: str

    async def access(self) -> AccessResult: ...
    async def discover(self, query: str) -> list[dict[str, Any]]: ...
    async def fetch(self, request: dict[str, Any]) -> FetchResult: ...
    def normalize(self, payload: Any) -> list[SourceObservation]: ...
    def validate(self, records: list[SourceObservation]) -> list[str]: ...
    async def coverage(self, request: dict[str, Any]) -> AccessResult: ...


QUOTA_WORDS = (
    "rate limit",
    "rate-limit",
    "too many requests",
    "quota",
    "limit reached",
    "limit exceeded",
    "credits exhausted",
    "out of credits",
    "call frequency",
    "requests per",
)


def quota_denial_text(text: str | None) -> bool:
    """True when a response body says the free allowance (not the plan) is used up.

    402 and 403 are used by some providers for an exhausted quota; those must not be latched
    as a permanent paid-tier denial (B8)."""
    if not text:
        return False
    lowered = text.casefold()
    return any(word in lowered for word in QUOTA_WORDS)


def access_from_http(
    status: int,
    *,
    has_rows: bool = True,
    retry_at: datetime | None = None,
    paid_tier_denial: bool = False,
    quota_denial: bool = False,
) -> AccessResult:
    """Map transport outcomes without disguising denials as empty successes.

    `quota_denial` (see `quota_denial_text`) reclassifies a 402/403 whose body reports an
    exhausted allowance as a retryable `quota_exhausted`: a paid-only state hard-blocks the
    endpoint until the configuration changes, a quota state only until `retry_at`."""
    if status in (401, 403, 402):
        if quota_denial and status in (402, 403):
            return AccessResult(AccessState.QUOTA_EXHAUSTED, f"HTTP {status} quota", retry_at)
        # A generic 403 is also used for bad/expired credentials.  Only classify it as paid
        # after the provider response explicitly identifies the plan boundary.
        state = (
            AccessState.PAID_ONLY
            if status == 402 or paid_tier_denial
            else AccessState.ACCESS_UNVERIFIED
        )
        return AccessResult(state)
    if status == 429:
        return AccessResult(AccessState.QUOTA_EXHAUSTED, retry_at=retry_at)
    if status >= 500:
        return AccessResult(AccessState.TEMPORARILY_FAILED, retry_at=retry_at)
    if 200 <= status < 300:
        return AccessResult(AccessState.AVAILABLE if has_rows else AccessState.NOT_COVERED)
    return AccessResult(AccessState.TEMPORARILY_FAILED, f"HTTP {status}")


# A provider asking for a longer wait than this is treated as asking for this long; the next
# scheduled run asks again. It also keeps `now + delay` far from datetime's range (B4).
MAX_RETRY_AFTER = timedelta(days=7)


def retry_after_at(value: str | None, *, now: datetime | None = None) -> datetime | None:
    """Honor HTTP Retry-After and add small positive jitter to avoid synchronized workers.

    Never raises: an unparsable, non-finite or absurd value yields None or is capped at
    MAX_RETRY_AFTER."""
    if not value:
        return None
    now = now or datetime.now().astimezone()
    limit = MAX_RETRY_AFTER.total_seconds()
    try:
        seconds = float(value.strip())
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=now.tzinfo)
            seconds = (retry_at - now).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if math.isnan(seconds):
        return None
    delay = timedelta(seconds=min(max(0.0, seconds), limit))
    return now + delay + timedelta(seconds=random.SystemRandom().uniform(0, 3))


# A Retry-After longer than this is not waited out inside one client call; the caller gets a
# rate-limit error carrying `retry_at` and the next scheduled run asks again.
INLINE_RETRY_MAX_SECONDS = 30.0


def rate_limit_wait(
    retry_after: str | None, attempt: int, *, now: datetime | None = None
) -> tuple[float | None, datetime | None]:
    """How long a client should sleep before retrying a 429, and when the provider said to retry.

    Returns (seconds, retry_at). seconds is None when the provider asked for longer than
    INLINE_RETRY_MAX_SECONDS: do not wait inline, surface `retry_at` instead (B9)."""
    now = now or datetime.now().astimezone()
    retry_at = retry_after_at(retry_after, now=now)
    if retry_at is None:
        return min(2.0 ** (attempt + 1), INLINE_RETRY_MAX_SECONDS), None
    wait = (retry_at - now).total_seconds()
    return (wait if wait <= INLINE_RETRY_MAX_SECONDS else None), retry_at
