"""Governed source adapter contract shared by free-data providers."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


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


def access_from_http(
    status: int,
    *,
    has_rows: bool = True,
    retry_at: datetime | None = None,
    paid_tier_denial: bool = False,
) -> AccessResult:
    """Map transport outcomes without disguising denials as empty successes."""
    if status in (401, 403, 402):
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


def retry_after_at(value: str | None, *, now: datetime | None = None) -> datetime | None:
    """Honor HTTP Retry-After and add small positive jitter to avoid synchronized workers."""
    if not value:
        return None
    now = now or datetime.now().astimezone()
    try:
        seconds = max(0.0, float(value.strip()))
        retry_at = now + timedelta(seconds=seconds)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=now.tzinfo)
            retry_at = max(now, retry_at)
        except (TypeError, ValueError, OverflowError):
            return None
    return retry_at + timedelta(seconds=random.SystemRandom().uniform(0, 3))
