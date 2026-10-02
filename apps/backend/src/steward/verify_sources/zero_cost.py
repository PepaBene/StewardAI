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
   `record_provider_attempt` really does; and
4. the budgets the COLLECTORS pass to the governor (see `budgets.py`: literals in the code and
   the job configuration's provider settings) never exceed the declared free quota and exist
   for every reachable credentialed endpoint.  An endpoint that a collector can reach with a
   key but that has neither a bounded collector budget nor a documented no-overage provider is
   a violation, not a warning.

`audit_zero_cost` reads only; it never calls a provider.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

import asyncpg

from steward.sources.base import AccessState
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.verify_sources.budgets import (
    NO_OVERAGE_PROVIDERS,
    CollectorBudget,
    collector_budgets,
    has_any_limit,
    limit_in_period,
)


class DisableReason(StrEnum):
    PAID_ONLY = "paid_only"
    KEY_REJECTED_OR_EXPIRED = "key_rejected_or_expired"
    ROUTE_UNAVAILABLE = "route_unavailable"
    QUOTA_EXHAUSTED = "quota_exhausted"


@dataclass(frozen=True, slots=True)
class DisableDecision:
    """`stop_calling`: do not repeat the request.  `scope` says how far the stop reaches:
    "endpoint" - every caller of the endpoint (a hard block, or a retry window), enforced by
    `record_provider_attempt` / `provider_gate_open`; "request" - only this request or entity
    (a 2xx without rows is a per-entity answer; the database never latches it on the endpoint);
    "none" - keep calling after the normal back-off."""

    stop_calling: bool
    permanent: bool
    reason: DisableReason | None
    sql_hard_block: bool
    scope: Literal["endpoint", "request", "none"] = "none"


def disable_policy(state: AccessState | str, status_code: int | None = None) -> DisableDecision:
    """When a collector may stop calling an endpoint (and nothing else).

    Transient failures (5xx, timeouts, a disputed value) never disable: they back off and
    retry.  A generic 403 with no plan evidence stays ``access_unverified`` and is blocked
    until the key or configuration changes, because retrying a rejected key can burn the
    free allowance of some providers."""
    state = AccessState(state)
    if state == AccessState.PAID_ONLY:
        return DisableDecision(True, True, DisableReason.PAID_ONLY, True, "endpoint")
    if state == AccessState.ACCESS_UNVERIFIED:
        rejected = status_code in (401, 403)
        return DisableDecision(
            rejected,
            rejected,
            DisableReason.KEY_REJECTED_OR_EXPIRED if rejected else None,
            rejected,
            "endpoint" if rejected else "none",
        )
    if state == AccessState.QUOTA_EXHAUSTED:
        # Stops the endpoint until the allowance window resets: a retry window, not a latch.
        return DisableDecision(True, False, DisableReason.QUOTA_EXHAUSTED, False, "endpoint")
    if state == AccessState.NOT_COVERED:
        return DisableDecision(True, False, DisableReason.ROUTE_UNAVAILABLE, False, "request")
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
            "violation_count": len(self.violations),
            "warning_count": sum(1 for f in self.findings if f.severity == "warning"),
            "warnings_by_code": dict(
                sorted(Counter(f.code for f in self.findings if f.severity == "warning").items())
            ),
            "findings": [asdict(f) for f in self.findings],
        }


REACHABLE_STATES = frozenset({AccessState.AVAILABLE.value, AccessState.ACCESS_UNVERIFIED.value})
QUOTA_PERIODS = ("minute", "day", "month")


def evaluate_endpoint(
    row: dict[str, Any], budgets: Sequence[CollectorBudget] | None = None
) -> list[Finding]:
    """Findings for one manifest/access row (pure, so it can be tested without a database).

    With `budgets` (the collectors' registry) the row is also checked against what the code
    would ask the governor for."""
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
        if row.get("denial_on_record"):
            # Safe: nothing can be spent.  The endpoint was blocked for a permitted reason and
            # a later attempt (a timeout, a 5xx) moved `state` on; `hard_blocked` stays latched
            # until the credential or plan changes.  A warning, never a failing job.
            add(
                "warning",
                "blocked_state_drift",
                f"endpoint is hard-blocked after a recorded denial but its state is now {state!r}",
            )
        else:
            add(
                "violation",
                "disabled_without_reason",
                f"endpoint is disabled but its state is {state!r}, which is not a permitted "
                "disable reason (paid_only, rejected/expired key, route unavailable, quota) "
                "and no denial is on record",
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
    if budgets is not None:
        out.extend(evaluate_budgets(row, budgets))
    return out


def evaluate_budgets(row: dict[str, Any], budgets: Sequence[CollectorBudget]) -> list[Finding]:
    """Compare the budgets collectors pass to the governor with the endpoint's free quota."""
    provider, endpoint = row["provider"], row["endpoint"]
    covering = [b for b in budgets if b.covers(provider, endpoint)]
    quota, period = row.get("quota"), row.get("quota_period")
    reachable = not row["hard_blocked"] and str(row["state"]) in REACHABLE_STATES
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()

    def add(severity: str, code: str, message: str) -> None:
        if (code, message) not in seen:
            seen.add((code, message))
            out.append(Finding(provider, endpoint, severity, code, message))

    if (
        reachable
        and row.get("credential_env")
        and provider not in NO_OVERAGE_PROVIDERS
        and not any(has_any_limit(b.budget) for b in covering)
    ):
        add(
            "violation",
            "reachable_without_hard_stop",
            "a key-holding collector can reach this endpoint but no registered collector budget "
            "bounds it and the provider is not a documented no-overage provider",
        )
    if quota is None:
        return out
    for b in covering:
        if period not in QUOTA_PERIODS:
            add(
                "warning",
                "quota_period_unverifiable",
                f"quota is stated per {period!r}; collector budgets cannot be compared to it",
            )
            break
        limit = limit_in_period(b.budget, period)
        if limit is None:
            add(
                "violation",
                "collector_budget_unbounded",
                f"{b.where} ({b.origin}) sets no {period} cap but the free quota is "
                f"{quota} per {period}",
            )
        elif limit > Decimal(str(quota)):
            add(
                "violation",
                "collector_budget_exceeds_quota",
                f"{b.where} ({b.origin}) allows {limit} per {period}, above the free quota "
                f"of {quota}",
            )
    return out


async def audit_zero_cost(
    pool: asyncpg.Pool,
    *,
    providers: Mapping[str, Any] | None = None,
    budgets: Sequence[CollectorBudget] | None = None,
) -> ZeroCostReport:
    """Check every registered endpoint manifest row against the access ledger and the budgets
    the collectors actually use (`providers`: the active jobs configuration's provider
    settings; default: the shipped defaults).  `budgets` overrides the registry (tests)."""
    registry = list(budgets) if budgets is not None else collector_budgets(providers)
    rows = await pool.fetch(
        """select m.provider, m.endpoint, m.request_weight, m.production_consumer,
                  a.account_tier, a.state, a.hard_blocked, a.credential_env, a.quota,
                  a.quota_period,
                  exists (select 1 from public.provider_fetch_audit f
                           where f.provider = m.provider and f.endpoint = m.endpoint
                             and (f.state = 'paid_only'
                                  or (f.state = 'access_unverified'
                                      and f.status_code in (401, 403)))) as denial_on_record
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
        findings = evaluate_endpoint(row, registry)
        report.findings.extend(findings)
        if row["hard_blocked"] and row["state"] in DISABLED_OK_STATES:
            report.disabled_ok += 1
        elif row["state"] == AccessState.AVAILABLE.value and not row["hard_blocked"]:
            report.free_available += 1
        elif row["state"] == AccessState.ACCESS_UNVERIFIED.value:
            report.gated_unverified += 1
    ledger = {
        (r["provider"], r["endpoint"])
        for r in await pool.fetch("select provider, endpoint from public.provider_access")
    }
    for entry in registry:
        if entry.endpoint != "*" and (entry.provider, entry.endpoint) not in ledger:
            report.findings.append(
                Finding(
                    entry.provider,
                    entry.endpoint,
                    "violation",
                    "budget_without_access_row",
                    f"{entry.where} reserves credits for an endpoint with no access ledger row",
                )
            )
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
    """The governor budget a declared free quota implies (None = no declared quota, or a
    window - week, year - the governor has no counter for).

    Reserve semantics: `reserve` credits are held back from NON-urgent work in the *day* and
    *month* windows only (Budget.reserve is subtracted from those caps by both governors).  A
    per-minute window refills within 60 seconds, so holding credits back from it protects
    nothing and the governor does not apply it: for ``period="minute"`` the reserve is dropped
    (``Budget.reserve == 0``) rather than silently left on a budget where it means nothing.  A
    reserve that would leave no usable credit is an error, not an empty budget."""
    if quota is None or not period:
        return None
    amount = Decimal(str(quota))
    if not amount.is_finite() or amount <= 0:
        raise ValueError("a declared quota must be a positive number")
    if reserve < 0:
        raise ValueError("reserve cannot be negative")
    kind = {"minute": "per_minute", "day": "daily", "month": "monthly"}.get(period)
    if kind is None:
        return None  # week/year windows are enforced by provider-specific budgets
    if kind == "per_minute":
        return Budget(per_minute=amount)
    if reserve >= amount:
        raise ValueError(f"reserve {reserve} leaves no usable credit of the {amount} quota")
    return Budget(**{kind: amount}, reserve=reserve)


def usable_credits(budget: Budget) -> Decimal:
    """Credits one window can spend for non-urgent work before the governor refuses.

    The minute cap is not reduced by the reserve; the day and month caps are."""
    caps = [
        c
        for c in (
            budget.per_minute,
            None if budget.daily is None else budget.daily - budget.reserve,
            None if budget.monthly is None else budget.monthly - budget.reserve,
        )
        if c is not None
    ]
    if not caps:
        raise AssertionError("budget has no limit to stop at")
    return max(Decimal(0), min(caps))


HARD_STOP_PROBE = ("__hard_stop_probe__", "probe")


async def verify_hard_stop(
    pool: asyncpg.Pool, budget: Budget, *, attempts: int | None = None
) -> Decimal:
    """Spend against the PRODUCTION governor (`PostgresRequestGovernor` and the
    `reserve_provider_credits` function) until it refuses; return the credits it allowed.

    A throwaway ``provider_access`` row (never a real endpoint, so no real quota is touched) is
    created for the probe and removed, with its usage windows, afterwards.  Raises
    AssertionError if the governor lets usage reach beyond what the budget allows for
    non-urgent work, i.e. a paid threshold could be crossed, or never refuses."""
    expected = usable_credits(budget)
    provider, endpoint = HARD_STOP_PROBE
    governor = PostgresRequestGovernor(pool)
    now = dt.datetime(2026, 10, 15, 12, 0, 30, tzinfo=dt.UTC)
    await pool.execute(
        "insert into public.provider_access(provider,endpoint,state)"
        " values($1,$2,'available') on conflict (provider,endpoint) do update"
        " set state='available', hard_blocked=false, retry_at=null",
        provider,
        endpoint,
    )
    try:
        spent = Decimal(0)
        for _ in range(attempts or int(expected) + 5):
            try:
                await governor.reserve(provider, endpoint, Decimal(1), budget, now=now)
            except QuotaExceeded:
                break
            spent += 1
        else:
            raise AssertionError("governor never refused: no hard stop below the quota")
    finally:
        await pool.execute(
            "delete from public.provider_quota_usage where provider=$1 and endpoint=$2",
            provider,
            endpoint,
        )
        await pool.execute(
            "delete from public.provider_access where provider=$1 and endpoint=$2",
            provider,
            endpoint,
        )
    assert spent <= expected, f"spent {spent} beyond the {expected} the budget allows"
    assert spent == expected, f"governor refused after {spent}, before the allowed {expected}"
    return spent
