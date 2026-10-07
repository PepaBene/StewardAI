"""D8.5: the zero-cost access gate."""

from __future__ import annotations

import ast
import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.gate_routes import router as gate_router
from steward.sources.base import AccessState, access_from_http
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.verify_sources.budgets import (
    ANY_ENDPOINT,
    BUDGET_SITES,
    SETTINGS_SITES,
    CollectorBudget,
    collector_budgets,
    configured_budgets,
    literal_budgets,
)
from steward.verify_sources.contract import classify_failure
from steward.verify_sources.zero_cost import (
    DISABLED_OK_STATES,
    DisableReason,
    audit_zero_cost,
    budget_from_access,
    disable_policy,
    evaluate_endpoint,
    usable_credits,
    verify_hard_stop,
)

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"


def row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "provider": "p",
        "endpoint": "e",
        "state": "available",
        "hard_blocked": False,
        "account_tier": "free",
        "credential_env": "P_KEY",
        "quota": 100,
        "quota_period": "day",
        "request_weight": 1,
        "production_consumer": "price fallback",
    }
    return base | over


def codes(**over: Any) -> set[str]:
    return {f.code for f in evaluate_endpoint(row(**over)) if f.severity == "violation"}


# ---------------------------------------------------------------------------------------
# Disable policy: only the four permitted reasons
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("state", "status", "stop", "reason", "sql_block"),
    [
        (AccessState.PAID_ONLY, 403, True, DisableReason.PAID_ONLY, True),
        (AccessState.PAID_ONLY, 402, True, DisableReason.PAID_ONLY, True),
        (AccessState.ACCESS_UNVERIFIED, 401, True, DisableReason.KEY_REJECTED_OR_EXPIRED, True),
        (AccessState.ACCESS_UNVERIFIED, 403, True, DisableReason.KEY_REJECTED_OR_EXPIRED, True),
        (AccessState.ACCESS_UNVERIFIED, None, False, None, False),
        (AccessState.QUOTA_EXHAUSTED, 429, True, DisableReason.QUOTA_EXHAUSTED, False),
        (AccessState.NOT_COVERED, 200, True, DisableReason.ROUTE_UNAVAILABLE, False),
        (AccessState.TEMPORARILY_FAILED, 503, False, None, False),
        (AccessState.TEMPORARILY_FAILED, None, False, None, False),
        (AccessState.DISPUTED, 200, False, None, False),
        (AccessState.AVAILABLE, 200, False, None, False),
    ],
)
def test_collector_auto_disable_only_on_the_four_permitted_reasons(
    state: AccessState,
    status: int | None,
    stop: bool,
    reason: DisableReason | None,
    sql_block: bool,
) -> None:
    decision = disable_policy(state, status)
    assert (decision.stop_calling, decision.reason, decision.sql_hard_block) == (
        stop,
        reason,
        sql_block,
    )


def test_quota_exhaustion_is_temporary_and_paid_only_is_permanent() -> None:
    assert not disable_policy(AccessState.QUOTA_EXHAUSTED, 429).permanent
    assert disable_policy(AccessState.PAID_ONLY, 402).permanent


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (401, AccessState.ACCESS_UNVERIFIED),
        (402, AccessState.PAID_ONLY),
        (429, AccessState.QUOTA_EXHAUSTED),
    ],
)
def test_transport_classification_feeds_the_policy(status: int, state: AccessState) -> None:
    assert access_from_http(status).state == state
    assert disable_policy(state, status).stop_calling


def test_a_provider_error_message_alone_maps_to_a_disable_state() -> None:
    class Denied(Exception):
        pass

    paid = classify_failure(Denied("this endpoint requires a premium plan"))
    assert disable_policy(paid.state).reason == DisableReason.PAID_ONLY
    assert not disable_policy(classify_failure(Denied("socket timeout")).state).stop_calling


# ---------------------------------------------------------------------------------------
# Row evaluation
# ---------------------------------------------------------------------------------------
def test_free_available_row_has_no_violation() -> None:
    assert codes() == set()


def test_paid_endpoint_that_is_not_blocked_is_a_violation() -> None:
    assert codes(state="paid_only") == {"paid_reachable"}
    assert codes(state="paid_only", hard_blocked=True) == set()


def test_disabling_for_a_transient_failure_is_a_violation() -> None:
    assert codes(state="temporarily_failed", hard_blocked=True) == {"disabled_without_reason"}
    assert codes(state="disputed", hard_blocked=True) == {"disabled_without_reason"}
    for state in DISABLED_OK_STATES:
        assert codes(state=state, hard_blocked=True) == set(), state


def test_non_free_tier_and_unbounded_quota_are_flagged() -> None:
    assert "not_free_tier" in codes(account_tier="premium")
    assert "quota_without_period" in codes(quota_period=None)
    warnings = {f.code for f in evaluate_endpoint(row(quota=None)) if f.severity == "warning"}
    assert "no_declared_quota" in warnings


# ---------------------------------------------------------------------------------------
# Hard stop below the paid threshold
# ---------------------------------------------------------------------------------------
@pytest.mark.db
@pytest.mark.parametrize("period", ["minute", "day", "month"])
async def test_governor_stops_at_the_declared_free_quota(
    backend_pool: asyncpg.Pool, period: str
) -> None:
    """Runs against PostgresRequestGovernor / reserve_provider_credits, the production path."""
    budget = budget_from_access(Decimal(7), period)
    assert budget is not None
    assert await verify_hard_stop(backend_pool, budget) == Decimal(7)
    # the throwaway probe row and its usage windows are gone
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.provider_access where provider='__hard_stop_probe__'"
        )
        == 0
    )
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.provider_quota_usage where provider='__hard_stop_probe__'"
        )
        == 0
    )


@pytest.mark.db
async def test_reserve_keeps_credits_back_from_day_and_month_windows(
    backend_pool: asyncpg.Pool,
) -> None:
    for period in ("day", "month"):
        budget = budget_from_access(Decimal(10), period, reserve=Decimal(3))
        assert budget is not None and budget.reserve == Decimal(3)
        assert await verify_hard_stop(backend_pool, budget) == Decimal(7), period


@pytest.mark.db
async def test_per_minute_window_has_no_reserve(backend_pool: asyncpg.Pool) -> None:
    """The governor never subtracts the reserve from a per-minute cap, so the budget helper
    drops it instead of leaving a number that means nothing."""
    budget = budget_from_access(Decimal(10), "minute", reserve=Decimal(3))
    assert budget is not None and budget.reserve == Decimal(0)
    assert await verify_hard_stop(backend_pool, budget) == Decimal(10)
    # a hand-built budget with both: the minute cap is not reduced, the day cap is.
    mixed = Budget(per_minute=Decimal(10), daily=Decimal(6), reserve=Decimal(2))
    assert usable_credits(mixed) == Decimal(4)
    assert await verify_hard_stop(backend_pool, mixed) == Decimal(4)


def test_reserve_that_leaves_nothing_is_an_error() -> None:
    with pytest.raises(ValueError, match="no usable credit"):
        budget_from_access(Decimal(5), "day", reserve=Decimal(5))
    with pytest.raises(ValueError):
        budget_from_access(Decimal(0), "day")
    with pytest.raises(ValueError):
        budget_from_access(Decimal("NaN"), "day")


def test_week_or_unknown_periods_have_no_builtin_budget() -> None:
    assert budget_from_access(Decimal(5), "week") is None
    assert budget_from_access(None, "day") is None


@pytest.mark.db
async def test_every_seeded_quota_has_a_hard_stop(backend_pool: asyncpg.Pool) -> None:
    rows = await backend_pool.fetch(
        "select provider,endpoint,quota,quota_period from public.provider_access"
        " where quota is not null"
    )
    assert rows, "the seed declares free quotas"
    checked = 0
    for r in rows:
        budget = budget_from_access(r["quota"], r["quota_period"])
        if budget is None:
            continue
        # keep the loop cheap for large quotas: the governor logic is size independent
        small = Budget(
            per_minute=min(budget.per_minute, Decimal(5)) if budget.per_minute else None,
            daily=min(budget.daily, Decimal(5)) if budget.daily else None,
            monthly=min(budget.monthly, Decimal(5)) if budget.monthly else None,
        )
        assert await verify_hard_stop(backend_pool, small) <= Decimal(5), (
            r["provider"],
            r["endpoint"],
        )
        checked += 1
    assert checked > 0


@pytest.mark.db
async def test_database_governor_stops_before_the_quota_and_resets_by_window(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute(
        "update public.provider_access set state='available', hard_blocked=false"
        " where provider='finnhub' and endpoint='earnings_calendar'"
    )
    governor = PostgresRequestGovernor(backend_pool)
    budget = Budget(daily=Decimal(3))
    day1 = dt.datetime(2026, 10, 10, 9, 0, tzinfo=dt.UTC)
    for i in range(3):
        await governor.reserve(
            "finnhub", "earnings_calendar", Decimal(1), budget, now=day1 + dt.timedelta(minutes=i)
        )
    with pytest.raises(QuotaExceeded):
        await governor.reserve("finnhub", "earnings_calendar", Decimal(1), budget, now=day1)
    day2 = day1 + dt.timedelta(days=1)
    await governor.reserve("finnhub", "earnings_calendar", Decimal(1), budget, now=day2)
    used = await backend_pool.fetchval(
        "select sum(credits) from public.provider_quota_usage where provider='finnhub'"
        " and endpoint='earnings_calendar' and window_kind='day'"
    )
    assert used == Decimal(4)  # 3 on day 1 (never a 4th), 1 on day 2


@pytest.mark.parametrize(
    ("state", "status"),
    [
        ("available", 200),
        ("paid_only", 403),
        ("access_unverified", 401),
        ("access_unverified", 403),
        ("access_unverified", None),
        ("quota_exhausted", 429),
        ("temporarily_failed", 503),
        ("not_covered", 200),
        ("disputed", 200),
    ],
)
@pytest.mark.db
async def test_database_function_matches_the_disable_policy(
    backend_pool: asyncpg.Pool, state: str, status: int | None
) -> None:
    await backend_pool.execute(
        "insert into public.provider_access(provider,endpoint,state)"
        " values('zz','route','available')"
    )
    await backend_pool.execute(
        "select public.record_provider_attempt('zz','route',$1,null,null,$2,null,null,null,0)",
        state,
        status,
    )
    blocked = await backend_pool.fetchval(
        "select hard_blocked from public.provider_access where provider='zz'"
    )
    assert blocked is disable_policy(AccessState(state), status).sql_hard_block


# ---------------------------------------------------------------------------------------
# The audit over the real manifest
# ---------------------------------------------------------------------------------------
@pytest.mark.db
async def test_seeded_manifest_has_no_zero_cost_violation(backend_pool: asyncpg.Pool) -> None:
    report = await audit_zero_cost(backend_pool)
    assert report.checked > 20
    assert report.passed, [f"{v.provider}/{v.endpoint}: {v.code}" for v in report.violations]
    body = report.to_json()
    assert body["passed"] is True and body["checked"] == report.checked


@pytest.mark.db
async def test_audit_flags_a_reachable_paid_endpoint_and_a_transient_disable(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute(
        "update public.provider_access set state='paid_only', hard_blocked=false"
        " where provider='fmp' and endpoint='profile'"
    )
    await backend_pool.execute(
        "update public.provider_access set state='temporarily_failed', hard_blocked=true"
        " where provider='diffbot' and endpoint='extract'"
    )
    await backend_pool.execute(
        "update public.provider_access set account_tier='premium'"
        " where provider='eodhd' and endpoint='eod'"
    )
    report = await audit_zero_cost(backend_pool)
    assert not report.passed
    found = {(v.provider, v.endpoint, v.code) for v in report.violations}
    assert found == {
        ("fmp", "profile", "paid_reachable"),
        ("diffbot", "extract", "disabled_without_reason"),
        ("eodhd", "eod", "not_free_tier"),
    }


@pytest.mark.db
async def test_available_endpoint_missing_from_the_manifest_is_a_warning(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute(
        "insert into public.provider_access(provider,endpoint,state)"
        " values('new','thing','available')"
    )
    report = await audit_zero_cost(backend_pool)
    assert any(f.code == "not_in_manifest" and f.provider == "new" for f in report.findings)
    assert report.passed  # a warning, not a violation


# ---------------------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------------------
@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> Any:
    app = FastAPI()
    app.include_router(gate_router)

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.db
async def test_gate_routes_are_read_only_and_report_blocked_by_default(
    api: httpx.AsyncClient,
) -> None:
    zero = (await api.get("/verification/zero-cost-gate")).json()
    assert zero["passed"] is True
    domains = (await api.get("/verification/domain-gates")).json()
    assert domains["domains"]["prices"]["allowed"] is False
    assert "no reconciliation samples" in domains["domains"]["prices"]["reasons"][0]
    assert domains["conclusions"]["price_alert"]["allowed"] is False
    signals = (await api.get("/verification/signals")).json()
    assert signals and all(s["publishable"] is False for s in signals.values())
    assert (await api.get("/verification/signals/deal_state")).status_code == 200
    assert (await api.get("/verification/signals/nope")).status_code == 404
    assert (await api.get("/verification/shadow-report")).status_code == 404
    assert (await api.get("/verification/coverage")).json() == []
    for path in ("/verification/zero-cost-gate", "/verification/signals"):
        assert (await api.post(path)).status_code == 405


# ---------------------------------------------------------------------------------------
# Collector budgets (B51): the audit inspects what the code asks the governor for
# ---------------------------------------------------------------------------------------
SRC = Path(__file__).resolve().parents[1] / "src"


def budget_sites() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted((SRC / "steward").rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("steward/verify_sources/"):
            continue
        count = sum(
            1
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Budget"
        )
        if count:
            found[rel] = count
    return found


def test_every_budget_construction_in_the_code_is_registered() -> None:
    """Adding a `Budget(...)` to a collector without registering it in verify_sources/budgets.py
    (BUDGET_SITES plus a CollectorBudget entry) fails here, so the zero-cost audit cannot be
    blind to a new spender."""
    assert budget_sites() == BUDGET_SITES, (
        "a Budget(...) was added or removed; update BUDGET_SITES and the registry in "
        "src/steward/verify_sources/budgets.py"
    )
    wheres = " ".join(b.where for b in collector_budgets())
    assert [f for f in BUDGET_SITES if f not in wheres] == []


def test_registered_literal_budgets_equal_the_constants_in_the_collectors() -> None:
    from steward.valuation.collect import ALPHA_VANTAGE_DAILY_LIMIT

    valuation = [b for b in literal_budgets() if b.where == "steward/valuation/collect.py"]
    assert [b.budget.daily for b in valuation] == [Decimal(ALPHA_VANTAGE_DAILY_LIMIT)]


class _Settings:
    def __init__(
        self, daily: int | None = None, monthly: int | None = None, reserve: int = 1
    ) -> None:
        self.daily_quota, self.monthly_quota, self.reserve_credits = daily, monthly, reserve


def test_shipped_provider_defaults_stay_inside_the_seeded_free_quotas() -> None:
    by = {b.provider: b for b in configured_budgets() if b.where == SETTINGS_SITES}
    assert by["alpha_vantage"].budget.daily == Decimal(25)
    assert by["fmp"].budget.daily == Decimal(250)
    assert by["un_comtrade"].budget.daily == Decimal(500)
    assert by["diffbot"].budget.monthly == Decimal(10_000)
    assert by["people_data_labs"].budget.monthly == Decimal(100)
    capped = [b for b in configured_budgets({"eodhd": _Settings(daily=999)}) if "prices" in b.where]
    assert capped[0].budget.daily == Decimal(20)  # prices.py caps the configured value


def codes_with(budgets: list[CollectorBudget], **over: Any) -> set[str]:
    return {f.code for f in evaluate_endpoint(row(**over), budgets) if f.severity == "violation"}


def entry(budget: Budget, endpoint: str = "e", provider: str = "p") -> CollectorBudget:
    return CollectorBudget(provider, endpoint, budget, "steward/x.py", "literal")


def test_collector_budget_above_the_free_quota_is_a_violation() -> None:
    assert codes_with([entry(Budget(daily=Decimal(100)))]) == set()
    assert codes_with([entry(Budget(daily=Decimal(101)))]) == {"collector_budget_exceeds_quota"}
    assert codes_with([entry(Budget(daily=Decimal(100)), ANY_ENDPOINT)]) == set()
    # a monthly cap does not bound a per-day quota
    assert codes_with([entry(Budget(monthly=Decimal(50)))]) == {"collector_budget_unbounded"}


def test_reachable_key_endpoint_needs_a_bounded_collector_budget_or_a_no_overage_provider() -> None:
    assert codes_with([]) == {"reachable_without_hard_stop"}
    assert "reachable_without_hard_stop" in codes_with([entry(Budget())])
    assert codes_with([entry(Budget(daily=Decimal(5)))]) == set()
    assert codes_with([], provider="finnhub", quota=None, quota_period=None) == set()
    # keyless endpoints and hard-blocked endpoints are not reachable with a key
    assert codes_with([], credential_env=None) == set()
    assert codes_with([], state="paid_only", hard_blocked=True) == set()


def test_unverifiable_quota_window_is_a_warning_not_a_pass() -> None:
    findings = evaluate_endpoint(
        row(quota=5, quota_period="week"), [entry(Budget(daily=Decimal(1)))]
    )
    assert {f.code for f in findings if f.severity == "warning"} >= {"quota_period_unverifiable"}


@pytest.mark.db
async def test_audit_flags_configuration_that_exceeds_a_free_quota(
    backend_pool: asyncpg.Pool,
) -> None:
    from steward.config.jobs import JobsConfig

    providers = JobsConfig().providers
    providers["alpha_vantage"] = providers["alpha_vantage"].model_copy(update={"daily_quota": 500})
    report = await audit_zero_cost(backend_pool, providers=providers)
    assert not report.passed
    assert {v.code for v in report.violations} == {"collector_budget_exceeds_quota"}
    assert {v.provider for v in report.violations} == {"alpha_vantage"}


@pytest.mark.db
async def test_audit_flags_a_collector_budget_for_an_endpoint_nobody_registered(
    backend_pool: asyncpg.Pool,
) -> None:
    report = await audit_zero_cost(
        backend_pool,
        budgets=[*collector_budgets(), entry(Budget(daily=Decimal(1)), "ghost", "ghost_provider")],
    )
    assert {(v.provider, v.endpoint, v.code) for v in report.violations} == {
        ("ghost_provider", "ghost", "budget_without_access_row")
    }


@pytest.mark.db
async def test_hard_blocked_endpoint_that_later_records_a_transient_failure_does_not_fail_the_gate(
    backend_pool: asyncpg.Pool,
) -> None:
    """B51: paid-only -> hard-blocked -> a later 503 moved `state`; nothing can be spent, so the
    daily job must not fail.  Without any denial on record it still fails (policy)."""
    await backend_pool.execute(
        "select public.record_provider_attempt('fmp','profile','paid_only','premium',null,402,"
        "null,null,null,0)"
    )
    await backend_pool.execute(
        "update public.provider_access set state='temporarily_failed' where provider='fmp'"
        " and endpoint='profile'"
    )
    report = await audit_zero_cost(backend_pool)
    assert report.passed, [f"{v.provider}/{v.endpoint}: {v.code}" for v in report.violations]
    assert any(f.code == "blocked_state_drift" for f in report.findings)
    await backend_pool.execute("delete from public.provider_fetch_audit where provider='fmp'")
    again = await audit_zero_cost(backend_pool)
    assert {(v.provider, v.endpoint, v.code) for v in again.violations} == {
        ("fmp", "profile", "disabled_without_reason")
    }


@pytest.mark.db
async def test_report_counts_warnings_by_code(backend_pool: asyncpg.Pool) -> None:
    body = (await audit_zero_cost(backend_pool)).to_json()
    assert body["violation_count"] == 0
    assert body["warning_count"] == sum(body["warnings_by_code"].values()) > 0
    assert "unverified_not_blocked" in body["warnings_by_code"]


@pytest.mark.parametrize(
    ("state", "status", "scope"),
    [
        ("paid_only", 402, "endpoint"),
        ("access_unverified", 401, "endpoint"),
        ("access_unverified", None, "none"),
        ("quota_exhausted", 429, "endpoint"),
        ("not_covered", 200, "request"),
        ("temporarily_failed", 503, "none"),
    ],
)
def test_disable_scope_says_how_far_the_stop_reaches(
    state: str, status: int | None, scope: str
) -> None:
    assert disable_policy(AccessState(state), status).scope == scope
