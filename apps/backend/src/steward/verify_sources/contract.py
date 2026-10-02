"""Typed-failure classification for adapter contracts (plan D8.1).

Adapters either return an `AccessResult` or raise their own typed error whose message names the
HTTP status or the provider's envelope text ("premium", "call frequency").  A denial must never
look like an empty success, so every such failure maps to an `AccessState` here.
"""

from __future__ import annotations

import re

from steward.sources.base import AccessResult, AccessState, access_from_http

_STATUS = re.compile(r"\bHTTP[ /]*(\d{3})\b", re.IGNORECASE)
PAID_WORDS = ("premium", "upgrade", "subscription required", "paid plan", "paid tier")
QUOTA_WORDS = (
    "call frequency",
    "rate limit",
    "quota",
    "too many requests",
    "requests per",
    "daily hits",
)
CREDENTIAL_WORDS = ("invalid api key", "apikey is invalid", "unauthorized", "get your apikey")


def status_of(exc: BaseException) -> int | None:
    """The HTTP status an adapter error carries (attribute or `HTTP nnn` in the message)."""
    attr = getattr(exc, "status_code", None)
    if isinstance(attr, int):
        return attr
    match = _STATUS.search(str(exc))
    return int(match.group(1)) if match else None


def classify_failure(exc: BaseException) -> AccessResult:
    """Map a typed adapter failure to the access state the collector must record."""
    text = str(exc).casefold()
    status = status_of(exc)
    if status is not None:
        paid = status == 402 or any(word in text for word in PAID_WORDS)
        return access_from_http(status, paid_tier_denial=paid)
    if any(word in text for word in PAID_WORDS):
        return AccessResult(AccessState.PAID_ONLY, "provider reports a paid-tier endpoint")
    if any(word in text for word in QUOTA_WORDS):
        return AccessResult(AccessState.QUOTA_EXHAUSTED, "provider rate or quota limit")
    if any(word in text for word in CREDENTIAL_WORDS):
        return AccessResult(AccessState.ACCESS_UNVERIFIED, "credential rejected")
    return AccessResult(AccessState.TEMPORARILY_FAILED, type(exc).__name__)


DENIAL_STATES = frozenset(
    {
        AccessState.PAID_ONLY,
        AccessState.ACCESS_UNVERIFIED,
        AccessState.QUOTA_EXHAUSTED,
        AccessState.TEMPORARILY_FAILED,
    }
)
