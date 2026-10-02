"""B46: the Phase 8 gates are callable from production code and really stop things."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import asyncpg
import pytest

from steward.sources.base import AccessResult, AccessState
from steward.sources.governor import (
    Budget,
    HardBlocked,
    PostgresRequestGovernor,
    QuotaExceeded,
    RetryPending,
)
from steward.verify_sources.enforce import (
    EndpointDisabled,
    NoDeclaredQuota,
    budget_for_endpoint,
    conclusion_gate,
    conclusions_requiring,
    next_window_start,
    pre_publish_check,
    record_outcome,
    require_conclusion,
    require_endpoint_open,
    reserve_for_endpoint,
    signal_gate,
)
from steward.verify_sources.signal_audit import (
    AuditObservation,
    PublishBlocked,
    record_audit_result,
)
from steward.verify_sources.thresholds import (
    DEFAULT_THRESHOLDS,
    GateBlocked,
    PrimarySample,
    reconcile_samples,
    record_reconciliation,
)
from tests.conftest import MigratedDatabase

DAY = dt.date(2026, 9, 30)


# ---------------------------------------------------------------------------------------
# Conclusion gate (reconciliation) and the pre-publish check (audit)
# ---------------------------------------------------------------------------------------
@pytest.mark.db
async def test_conclusion_gate_fails_closed_and_opens_on_real_samples(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    blocked = await conclusion_gate(pool, "price_alert")
    assert not blocked.allowed and blocked.kind == "conclusion"
    assert any("no reconciliation samples" in r for r in blocked.reasons)
    unknown = await conclusion_gate(pool, "teleport_alert")
    assert not unknown.allowed and "unknown conclusion" in unknown.reasons[0]
    with pytest.raises(GateBlocked):
        await require_conclusion(pool, "price_alert")

    t = DEFAULT_THRESHOLDS["prices"]
    samples = []
    for i in range(t.min_samples):
        await pool.execute(
            "insert into public.price_bar(symbol,date,close,source,bar_type)"
            " values($1,$2,100,'stooq','raw')",
            f"S{i}",
            DAY,
        )
        samples.append(
            PrimarySample(
                "prices",
                f"S{i}:stooq:raw",
                "close",
                Decimal(100),
                "exchange",
                "https://primary.example/x",
                DAY,
                reviewer="owner",
            )
        )
    await record_reconciliation(pool, reconcile_samples(samples, t))
    assert (await conclusion_gate(pool, "price_alert")).allowed
    assert not (await conclusion_gate(pool, "return_alert")).allowed  # needs corporate actions


@pytest.mark.db
async def test_closed_pool_means_closed_gate(migrated_db: MigratedDatabase) -> None:
    pool = await asyncpg.create_pool(migrated_db.backend_url, min_size=1, max_size=1)
    await pool.close()
    decision = await conclusion_gate(pool, "price_alert")
    assert not decision.allowed and "gate unavailable" in decision.reasons[0]
    assert not (await signal_gate(pool, "deal_state")).allowed


def test_conclusions_requiring_a_domain() -> None:
    assert "price_alert" in conclusions_requiring("prices")
    assert conclusions_requiring("nothing") == []


@pytest.mark.db
async def test_pre_publish_check_raises_until_the_signal_is_audited(
    backend_pool: asyncpg.Pool,
) -> None:
    with pytest.raises(PublishBlocked, match="never audited"):
        await pre_publish_check(backend_pool, "deal_state")
    assert not (await signal_gate(backend_pool, "deal_state")).allowed
    obs = [AuditObservation(f"s{i}", True, pit_ok=True) for i in range(40)]
    await record_audit_result(
        backend_pool,
        "deal_state",
        obs,
        reviewer="owner",
        audited_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
    )
    decision = await pre_publish_check(backend_pool, "deal_state")
    assert decision.allowed and decision.reviewer == "owner"
    assert (await signal_gate(backend_pool, "deal_state")).allowed
    with pytest.raises(PublishBlocked, match="unknown signal"):
        await pre_publish_check(backend_pool, "made_up")


# ---------------------------------------------------------------------------------------
# Budgets come from the access ledger
# ---------------------------------------------------------------------------------------
@pytest.mark.db
async def test_budget_for_endpoint_is_derived_from_the_declared_quota(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    assert await budget_for_endpoint(pool, "fmp", "profile") == Budget(daily=Decimal(250))
    with_reserve = await budget_for_endpoint(pool, "fmp", "profile", reserve=Decimal(5))
    assert with_reserve == Budget(daily=Decimal(250), reserve=Decimal(5))
    assert (await budget_for_endpoint(pool, "diffbot", "organization")).monthly == Decimal(10_000)
    # a documented no-overage provider and a keyless endpoint need no local cap
    assert await budget_for_endpoint(pool, "finnhub", "quote") == Budget()
    assert await budget_for_endpoint(pool, "world_bank", "indicators") == Budget()
    # an unknown endpoint is refused, not guessed
    with pytest.raises(EndpointDisabled, match="no provider_access row"):
        await budget_for_endpoint(pool, "nobody", "nothing")


@pytest.mark.db
async def test_credentialed_endpoint_without_a_quota_has_no_hard_stop_and_is_refused(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await pool.execute(
        "insert into public.provider_access(provider,endpoint,state,credential_env)"
        " values('newvendor','x','available','NEWVENDOR_KEY')"
    )
    with pytest.raises(NoDeclaredQuota, match="no hard stop"):
        await budget_for_endpoint(pool, "newvendor", "x")
    with pytest.raises(NoDeclaredQuota):
        await reserve_for_endpoint(pool, "newvendor", "x")
    assert await pool.fetchval("select count(*) from public.provider_quota_usage") == 0
    # a window the governor cannot count needs an explicit cap from the caller
    await pool.execute(
        "insert into public.provider_access(provider,endpoint,state,credential_env,quota,"
        "quota_period) values('weekly','x','available','K',5,'week')"
    )
    with pytest.raises(NoDeclaredQuota, match="cannot count"):
        await budget_for_endpoint(pool, "weekly", "x")
    fallback = Budget(monthly=Decimal(20))
    assert await budget_for_endpoint(pool, "weekly", "x", fallback=fallback) == fallback


@pytest.mark.db
async def test_reserve_for_endpoint_spends_on_the_production_governor_and_stops_at_the_quota(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await pool.execute(
        "insert into public.provider_access(provider,endpoint,state,quota,quota_period)"
        " values('tiny','e','available',3,'day')"
    )
    now = dt.datetime(2026, 10, 15, 9, 0, tzinfo=dt.UTC)
    for _ in range(3):
        await reserve_for_endpoint(pool, "tiny", "e", now=now)
    with pytest.raises(QuotaExceeded):
        await reserve_for_endpoint(pool, "tiny", "e", now=now)
    used = await pool.fetchval(
        "select sum(credits) from public.provider_quota_usage where provider='tiny'"
        " and window_kind='day'"
    )
    assert used == Decimal(3)
    # the reserve is honoured for normal work and not for urgent work
    await pool.execute("update public.provider_access set quota=10 where provider='tiny'")
    later = now + dt.timedelta(days=1)
    spent = 0
    with pytest.raises(QuotaExceeded):
        for _ in range(20):
            await reserve_for_endpoint(pool, "tiny", "e", reserve=Decimal(4), now=later)
            spent += 1
    assert spent == 6
    await reserve_for_endpoint(pool, "tiny", "e", reserve=Decimal(4), urgent=True, now=later)


# ---------------------------------------------------------------------------------------
# Collectors stop on disable_policy outcomes
# ---------------------------------------------------------------------------------------
async def collector_run(
    pool: asyncpg.Pool, provider: str, endpoint: str, outcomes: list[tuple[AccessState, int]]
) -> list[str]:
    """A collector loop as the plan wants it: reserve, call, record, stop when told to."""
    trace: list[str] = []
    for state, status in outcomes:
        try:
            await reserve_for_endpoint(pool, provider, endpoint)
        except QuotaExceeded as exc:
            trace.append(f"refused:{type(exc).__name__}")
            break
        directive = await record_outcome(
            pool, provider, endpoint, AccessResult(state, f"HTTP {status}"), status_code=status
        )
        trace.append(f"{state.value}:{'stop' if directive.stop else 'go'}:{directive.scope}")
        if directive.stop and directive.scope == "endpoint":
            continue  # the next reserve must now be refused
    return trace


@pytest.mark.db
@pytest.mark.parametrize(
    ("state", "status", "refused"),
    [
        (AccessState.PAID_ONLY, 402, "HardBlocked"),
        (AccessState.ACCESS_UNVERIFIED, 401, "HardBlocked"),
        (AccessState.ACCESS_UNVERIFIED, 403, "HardBlocked"),
        (AccessState.QUOTA_EXHAUSTED, 429, "RetryPending"),
    ],
)
async def test_collector_stops_after_a_disabling_outcome(
    backend_pool: asyncpg.Pool, state: AccessState, status: int, refused: str
) -> None:
    trace = await collector_run(
        backend_pool, "fmp", "profile", [(state, status), (AccessState.AVAILABLE, 200)]
    )
    assert trace == [f"{state.value}:stop:endpoint", f"refused:{refused}"]
    with pytest.raises(QuotaExceeded):
        await require_endpoint_open(backend_pool, "fmp", "profile")


@pytest.mark.db
@pytest.mark.parametrize(
    ("state", "status", "scope"),
    [
        (AccessState.TEMPORARILY_FAILED, 503, "none"),
        (AccessState.ACCESS_UNVERIFIED, None, "none"),
        (AccessState.NOT_COVERED, 200, "request"),
        (AccessState.AVAILABLE, 200, "none"),
    ],
)
async def test_collector_keeps_going_after_non_disabling_outcomes(
    backend_pool: asyncpg.Pool, state: AccessState, status: int | None, scope: str
) -> None:
    outcomes = [(state, status or 0)] * 3
    pool = backend_pool
    trace = []
    for st, code in outcomes:
        await reserve_for_endpoint(pool, "fmp", "profile")
        directive = await record_outcome(
            pool, "fmp", "profile", AccessResult(st), status_code=code or None
        )
        trace.append((directive.stop and directive.scope == "endpoint", directive.scope))
    assert [t[0] for t in trace] == [False, False, False]
    assert {t[1] for t in trace} == {scope}
    await require_endpoint_open(pool, "fmp", "profile")


@pytest.mark.db
async def test_quota_exhaustion_without_retry_after_gets_a_window_ending_at_the_reset(
    backend_pool: asyncpg.Pool,
) -> None:
    now = dt.datetime(2026, 10, 15, 22, 30, tzinfo=dt.UTC)
    directive = await record_outcome(
        backend_pool,
        "fmp",
        "profile",
        AccessResult(AccessState.QUOTA_EXHAUSTED, "daily limit"),
        status_code=429,
        now=now,
    )
    assert directive.stop and not directive.permanent
    assert directive.retry_at == dt.datetime(2026, 10, 16, tzinfo=dt.UTC)  # the day window resets
    with pytest.raises(RetryPending) as pending:
        await reserve_for_endpoint(backend_pool, "fmp", "profile", now=now)
    assert pending.value.retry_at == directive.retry_at
    # after the reset the endpoint is usable again
    await reserve_for_endpoint(
        backend_pool, "fmp", "profile", now=directive.retry_at + dt.timedelta(minutes=1)
    )
    # a server-supplied Retry-After is kept as it is
    server = now + dt.timedelta(minutes=7)
    again = await record_outcome(
        backend_pool,
        "fmp",
        "profile",
        AccessResult(AccessState.QUOTA_EXHAUSTED, "slow down", retry_at=server),
        status_code=429,
        now=now,
    )
    assert again.retry_at == server


@pytest.mark.db
@pytest.mark.parametrize(
    ("state", "status"),
    [
        (AccessState.AVAILABLE, 200),
        (AccessState.PAID_ONLY, 403),
        (AccessState.ACCESS_UNVERIFIED, 401),
        (AccessState.ACCESS_UNVERIFIED, None),
        (AccessState.QUOTA_EXHAUSTED, 429),
        (AccessState.TEMPORARILY_FAILED, 503),
        (AccessState.NOT_COVERED, 200),
    ],
)
async def test_disable_policy_agrees_with_the_database_gate(
    backend_pool: asyncpg.Pool, state: AccessState, status: int | None
) -> None:
    """The policy's endpoint-wide stops and `provider_gate_open` never disagree."""
    now = dt.datetime.now(dt.UTC)
    directive = await record_outcome(
        backend_pool, "fmp", "profile", AccessResult(state), status_code=status, now=now
    )
    gate_open = await backend_pool.fetchval(
        "select public.provider_gate_open('fmp','profile',$1)", now
    )
    assert gate_open is not (directive.stop and directive.scope == "endpoint")


def test_endpoint_errors_are_quota_errors_so_existing_collectors_already_stop() -> None:
    for exc in (EndpointDisabled, NoDeclaredQuota, HardBlocked, RetryPending):
        assert issubclass(exc, QuotaExceeded)
    assert PostgresRequestGovernor is not None


def test_next_window_start() -> None:
    now = dt.datetime(2026, 12, 31, 23, 59, 30, tzinfo=dt.UTC)
    assert next_window_start("minute", now) == dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
    assert next_window_start("day", now) == dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
    assert next_window_start("month", now) == dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
    mid = dt.datetime(2026, 3, 10, 8, tzinfo=dt.UTC)
    assert next_window_start("month", mid) == dt.datetime(2026, 4, 1, tzinfo=dt.UTC)
    assert next_window_start("week", mid) == mid + dt.timedelta(hours=1)
    assert next_window_start(None, mid) == mid + dt.timedelta(hours=1)
