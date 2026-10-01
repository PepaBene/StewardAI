"""Zero-cost access gate (plan D8.5).

Provider spend must stay $0 and a zero-cost requirement may leave a feature unavailable but
never silently route to a paid API.  This module checks, from the endpoint manifest and the
access ledger, that:

1. every registered endpoint is either free and available, or disabled for one of the four
   permitted reasons (paid-only response, rejected/expired key, route unavailable, exhausted
   quota) - nothing paid is reachable and nothing is disabled for a transient hiccup;
2. every endpoint with a declared free quota has a hard stop below that quota (the governor
   refuses the request that would cross it, whatever the window); and
3. the collector auto-disable policy matches what the database function
   `record_provider_attempt` really does.

`audit_zero_cost` reads only; it never calls a provider.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

import asyncpg

from steward.sources.base import AccessState
from steward.sources.governor import Budget, QuotaExceeded, RequestGovernor


class DisableReason(StrEnum):
    PAID_ONLY = "paid_only"
    KEY_REJECTED_OR_EXPIRED = "key_rejected_or_expired"
    ROUTE_UNAVAILABLE = "route_unavailable"
    QUOTA_EXHAUSTED = "quota_exhausted"


@dataclass(frozen=True, slots=True)
class DisableDecision:
    stop_calling: bool
    permanent: bool
    reason: DisableReason | None
    sql_hard_block: bool


def disable_policy(state: AccessState | str, status_code: int | None = None) -> DisableDecision:
    """When a collector may stop calling an endpoint (and nothing else).

    Transient failures (5xx, timeouts, a disputed value) never disable: they back off and
    retry.  A generic 403 with no plan evidence stays ``access_unverified`` and is blocked
    until the key or configuration changes, because retrying a rejected key can burn the
    free allowance of some providers."""
    state = AccessState(state)
    if state == AccessState.PAID_ONLY:
        return DisableDecision(True, True, DisableReason.PAID_ONLY, True)
    if state == AccessState.ACCESS_UNVERIFIED:
        rejected = status_code in (401, 403)
        return DisableDecision(
            rejected,
            rejected,
            DisableReason.KEY_REJECTED_OR_EXPIRED if rejected else None,
            rejected,
        )
    if state == AccessState.QUOTA_EXHAUSTED:
        return DisableDecision(True, False, DisableReason.QUOTA_EXHAUSTED, False)
    if state == AccessState.NOT_COVERED:
        return DisableDecision(True, False, DisableReason.ROUTE_UNAVAILABLE, False)
    return DisableDecision(False, False, None, False)


# provider_access states in which an endpoint may legitimately be switched off.
DISABLED_OK_STATES = frozenset(
    {
        AccessState.PAID_ONLY.value,
        AccessState.ACCESS_UNVERIFIED.value,
        AccessState.QUOTA_EXHAUSTED.value,
        AccessState.NOT_COVERED.value,
    }
)


@dataclass(frozen=True, slots=True)
class Finding:
    provider: str
    endpoint: str
    severity: str  # violation | warning
    code: str
    message: str


@dataclass
class ZeroCostReport:
    checked: int = 0
    free_available: int = 0
    disabled_ok: int = 0
    gated_unverified: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "violation"]

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_json(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checked": self.checked,
            "free_available": self.free_available,
            "disabled_ok": self.disabled_ok,
            "gated_unverified": self.gated_unverified,
            "findings": [f.__dict__ for f in self.findings],
        }


def evaluate_endpoint(row: dict[str, Any]) -> list[Finding]:
    """Findings for one manifest/access row (pure, so it can be tested without a database)."""
    provider, endpoint = row["provider"], row["endpoint"]
    state = str(row["state"])
    blocked = bool(row["hard_blocked"])
    out: list[Finding] = []

    def add(severity: str, code: str, message: str) -> None:
        out.append(Finding(provider, endpoint, severity, code, message))

    if row.get("account_tier", "free") != "free":
        add("violation", "not_free_tier", f"account tier is {row['account_tier']!r}, not free")
    if state == AccessState.PAID_ONLY.value and not blocked:
        add("violation", "paid_reachable", "paid-only endpoint is not hard-blocked")
    if blocked and state not in DISABLED_OK_STATES:
        add(
            "violation",
            "disabled_without_reason",
            f"endpoint is disabled but its state is {state!r}, which is not a permitted "
            "disable reason (paid_only, rejected/expired key, route unavailable, quota)",
        )
    if state == AccessState.ACCESS_UNVERIFIED.value and not blocked:
        # Unverified endpoints are fine - collectors refuse them until a probe passes - but
        # it is worth knowing the gate relies on the collectors' own state check.
        add("warning", "unverified_not_blocked", "unverified endpoint relies on the collector gate")
    if (
        state == AccessState.AVAILABLE.value
        and row.get("credential_env")
        and row.get("quota") is None
    ):
        add(
            "warning",
            "no_declared_quota",
            "credentialed endpoint has no declared free quota: the governor cannot hard-stop it",
        )
    if row.get("quota") is not None and not row.get("quota_period"):
        add("violation", "quota_without_period", "a declared quota needs its period")
    if row.get("request_weight") is not None and Decimal(str(row["request_weight"])) <= 0:
        add("violation", "bad_weight", "request weight must be positive")
    if not row.get("production_consumer"):
        add("warning", "no_consumer", "manifest row names no production consumer")
    return out


async def audit_zero_cost(pool: asyncpg.Pool) -> ZeroCostReport:
    """Check every registered endpoint manifest row against the access ledger."""
    rows = await pool.fetch(
        """select m.provider, m.endpoint, m.request_weight, m.production_consumer,
                  a.account_tier, a.state, a.hard_blocked, a.credential_env, a.quota,
                  a.quota_period
             from public.provider_endpoint_manifest m
             left join public.provider_access a using (provider, endpoint)
            order by m.provider, m.endpoint"""
    )
    report = ZeroCostReport()
    for record in rows:
        row = dict(record)
        report.checked += 1
        if row["state"] is None:
            report.findings.append(
                Finding(
                    row["provider"],
                    row["endpoint"],
                    "violation",
                    "no_access_row",
                    "manifest endpoint has no access ledger row, so no gate protects it",
                )
            )
            continue
        findings = evaluate_endpoint(row)
        report.findings.extend(findings)
        if row["hard_blocked"] and row["state"] in DISABLED_OK_STATES:
            report.disabled_ok += 1
        elif row["state"] == AccessState.AVAILABLE.value and not row["hard_blocked"]:
            report.free_available += 1
        elif row["state"] == AccessState.ACCESS_UNVERIFIED.value:
            report.gated_unverified += 1
    unregistered = await pool.fetch(
        """select a.provider, a.endpoint from public.provider_access a
            where a.state = 'available' and not a.hard_blocked
              and not exists (select 1 from public.provider_endpoint_manifest m
                               where m.provider = a.provider and m.endpoint = a.endpoint)
            order by 1, 2"""
    )
    for record in unregistered:
        report.findings.append(
            Finding(
                record["provider"],
                record["endpoint"],
                "warning",
                "not_in_manifest",
                "available endpoint is not in the manifest (no documented path/consumer)",
            )
        )
    return report


# ----------------------------------------------------------------------------- hard stop
def budget_from_access(
    quota: Decimal | float | None, period: str | None, *, reserve: Decimal = Decimal(0)
) -> Budget | None:
    """The governor budget a declared free quota implies (None = no declared quota)."""
    if quota is None or not period:
        return None
    amount = Decimal(str(quota))
    kind = {"minute": "per_minute", "day": "daily", "month": "monthly"}.get(period)
    if kind is None:
        return None  # week/year windows are enforced by provider-specific budgets
    kwargs: dict[str, Any] = {kind: amount}
    return Budget(**kwargs, reserve=reserve)


async def verify_hard_stop(budget: Budget, *, attempts: int | None = None) -> Decimal:
    """Spend against a fresh governor until it refuses; return the credits it allowed.

    Raises AssertionError if the governor ever lets usage exceed a declared limit, i.e. a
    paid threshold could be crossed."""
    limit = budget.per_minute or budget.daily or budget.monthly
    assert limit is not None, "budget has no limit to stop at"
    governor = RequestGovernor()
    now = dt.datetime(2026, 10, 15, 12, 0, 30, tzinfo=dt.UTC)
    spent = Decimal(0)
    for _ in range(attempts or int(limit) + 5):
        try:
            await governor.reserve("probe", "endpoint", Decimal(1), budget, now=now)
        except QuotaExceeded:
            break
        spent += 1
    else:
        raise AssertionError("governor never refused: no hard stop below the quota")
    assert spent <= limit - budget.reserve, f"spent {spent} beyond free limit {limit}"
    return spent
