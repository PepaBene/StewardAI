"""Budget config: spend caps, the guard applied to every AI request of a job, and the cost
summary."""

import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any, get_args

import asyncpg
import httpx
import httpx2
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from api.auth import AuthenticatedUser, require_owner
from api.budget_routes import router as budget_router
from api.db import get_pool, get_pool_or_error
from steward import budget_guard
from steward.ai import BudgetedAnthropic
from steward.budget_guard import BudgetExceeded, month_bounds, month_of, month_spend
from steward.config.budget import BudgetConfig, BudgetJobType
from steward.config.store import save_config
from steward.jobs.registry import JOB_HANDLERS, JobType
from steward.jobs.runner import JobContext, StepUsage, create_job, current_job, run_job

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
NOW = dt.datetime(2026, 9, 15, 12, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_the_budget_job_types_are_the_registry_job_types() -> None:
    assert set(get_args(BudgetJobType)) == set(get_args(JobType)) == set(JOB_HANDLERS)


def test_an_old_saved_budget_stays_valid_with_no_caps() -> None:
    cfg = BudgetConfig.model_validate({"schema_version": 1, "web_search_per_1000": 10})
    assert cfg.monthly_cap_usd is None
    assert cfg.at_cap == "pause_noncritical"
    assert cfg.critical_job_types == ["earnings_check", "event_escalation", "chat"]
    assert cfg.job_cap("research") is None


@pytest.mark.parametrize(
    "body",
    [
        {"critical_job_types": ["no_such_job"]},
        {"per_job_cap_usd": {"no_such_job": 1}},
        {"per_job_cap_usd": {"research": 0}},
        {"monthly_cap_usd": 0},
        {"warn_at_fraction": 1.5},
        {"critical_job_types": ["chat", "chat"]},
        {"at_cap": "stop_everything"},
        # The downgrade model must be priced, or its steps would cost nothing.
        {"at_cap": "downgrade_noncritical", "downgrade_model": "claude-unknown-1"},
    ],
)
def test_invalid_budget_limits_are_rejected(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BudgetConfig.model_validate(body)


def test_per_job_cap_wins_over_the_default() -> None:
    cfg = BudgetConfig(per_job_cap_usd={"research": 2.5}, default_job_cap_usd=1)
    assert cfg.job_cap("research") == 2.5
    assert cfg.job_cap("daily_digest") == 1


def test_budget_months_are_prague_calendar_months() -> None:
    # 22:30 UTC on 30 September is already 1 October in Prague (CEST, UTC+2).
    assert month_of(dt.datetime(2026, 9, 30, 22, 30, tzinfo=dt.UTC)) == "2026-10"
    start, end = month_bounds("2026-12")
    assert start.isoformat() == "2026-12-01T00:00:00+01:00"
    assert end.isoformat() == "2027-01-01T00:00:00+01:00"


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


async def _step(
    pool: asyncpg.Pool, job_id: int, cost: str | None, at: dt.datetime, model: str = "m"
) -> None:
    index = await pool.fetchval(
        "select coalesce(max(step_index) + 1, 0) from public.job_step where job_id = $1", job_id
    )
    await pool.execute(
        "insert into public.job_step (job_id, step_index, name, status, started_at, model,"
        " cost_usd) values ($1, $2, 'ai', 'succeeded', $3, $4, $5)",
        job_id,
        index,
        at,
        model,
        Decimal(cost) if cost is not None else None,
    )


async def _budget(pool: asyncpg.Pool, **fields: Any) -> None:
    async with pool.acquire() as conn:
        await save_config(conn, "budget", BudgetConfig(**fields), created_by=None)


async def _ctx(pool: asyncpg.Pool, job_type: str) -> JobContext:
    job_id = await create_job(pool, job_type, "manual")
    return JobContext(pool=pool, job_id=job_id, job_type=job_type, trigger="manual")


@pytest.mark.db
async def test_month_spend_sums_only_that_prague_month(backend_pool: asyncpg.Pool) -> None:
    other = await create_job(backend_pool, "research", "manual")
    await _step(backend_pool, other, "1.250000", dt.datetime(2026, 9, 1, 0, 30, tzinfo=dt.UTC))
    await _step(backend_pool, other, "0.333333", dt.datetime(2026, 9, 20, tzinfo=dt.UTC))
    # 31 August 22:30 UTC is 1 September in Prague; 30 September 22:30 UTC is October.
    await _step(backend_pool, other, "2.000000", dt.datetime(2026, 8, 31, 22, 30, tzinfo=dt.UTC))
    await _step(backend_pool, other, "5.000000", dt.datetime(2026, 9, 30, 22, 30, tzinfo=dt.UTC))
    await _step(backend_pool, other, None, dt.datetime(2026, 9, 10, tzinfo=dt.UTC))
    assert await month_spend(backend_pool, "2026-09") == Decimal("3.583333")
    assert await month_spend(backend_pool, "2026-10") == Decimal("5.000000")


@pytest.mark.db
async def test_under_the_cap_the_request_goes_as_asked(backend_pool: asyncpg.Pool) -> None:
    await _budget(backend_pool, monthly_cap_usd=10)
    other = await create_job(backend_pool, "research", "manual")
    await _step(backend_pool, other, "5", NOW)
    ctx = await _ctx(backend_pool, "daily_digest")
    assert await budget_guard.before_request(ctx, "claude-sonnet-5", now=NOW) == "claude-sonnet-5"


@pytest.mark.db
async def test_at_the_cap_non_critical_jobs_are_paused(backend_pool: asyncpg.Pool) -> None:
    await _budget(backend_pool, monthly_cap_usd=10)
    other = await create_job(backend_pool, "research", "manual")
    await _step(backend_pool, other, "10.5", NOW)
    # Last month's spend does not count.
    await _step(backend_pool, other, "100", NOW - dt.timedelta(days=40))
    ctx = await _ctx(backend_pool, "daily_digest")
    with pytest.raises(BudgetExceeded) as exc:
        await budget_guard.before_request(ctx, "claude-sonnet-5", now=NOW)
    assert str(exc.value) == (
        "monthly AI budget reached ($10.50 of $10.00); non-critical jobs paused"
    )
    critical = await _ctx(backend_pool, "earnings_check")
    assert (
        await budget_guard.before_request(critical, "claude-sonnet-5", now=NOW) == "claude-sonnet-5"
    )


@pytest.mark.db
async def test_alert_only_keeps_every_job_running(backend_pool: asyncpg.Pool) -> None:
    await _budget(backend_pool, monthly_cap_usd=10, at_cap="alert_only")
    other = await create_job(backend_pool, "research", "manual")
    await _step(backend_pool, other, "11", NOW)
    ctx = await _ctx(backend_pool, "daily_digest")
    assert await budget_guard.before_request(ctx, "claude-sonnet-5", now=NOW) == "claude-sonnet-5"


@pytest.mark.db
async def test_at_the_cap_non_critical_jobs_are_downgraded(backend_pool: asyncpg.Pool) -> None:
    await _budget(backend_pool, monthly_cap_usd=10, at_cap="downgrade_noncritical")
    other = await create_job(backend_pool, "research", "manual")
    await _step(backend_pool, other, "10", NOW)
    ctx = await _ctx(backend_pool, "research")
    model = await budget_guard.before_request(ctx, "claude-opus-5-5", now=NOW)
    assert model == "claude-haiku-4-5"
    # The step is recorded and priced as the model actually used.
    async with ctx.step("write") as step:
        step.usage = StepUsage(model="claude-opus-5-5", input_tokens=1_000_000)
    row = await backend_pool.fetchrow(
        "select model, cost_usd, detail from public.job_step where job_id = $1", ctx.job_id
    )
    assert row["model"] == "claude-haiku-4-5"
    assert row["cost_usd"] == Decimal("1.000000")
    assert json.loads(row["detail"])["budget_downgrade"]["from"] == "claude-opus-5-5"


@pytest.mark.db
async def test_the_warning_and_the_cap_alert_once_a_month(backend_pool: asyncpg.Pool) -> None:
    await _budget(backend_pool, monthly_cap_usd=10, warn_at_fraction=0.5)
    other = await create_job(backend_pool, "research", "manual")
    await _step(backend_pool, other, "6", NOW)
    for _ in range(2):
        ctx = await _ctx(backend_pool, "earnings_check")
        await budget_guard.before_request(ctx, "claude-sonnet-5", now=NOW)
    await _step(backend_pool, other, "5", NOW)
    ctx = await _ctx(backend_pool, "earnings_check")
    await budget_guard.before_request(ctx, "claude-sonnet-5", now=NOW)
    keys = [
        r["dedupe_key"]
        for r in await backend_pool.fetch(
            "select distinct dedupe_key from public.alert where kind = 'system' order by 1"
        )
    ]
    assert keys == ["budget:cap:2026-09", "budget:warn:2026-09"]


@pytest.mark.db
async def test_a_run_over_its_own_cap_is_stopped(backend_pool: asyncpg.Pool) -> None:
    await _budget(backend_pool, per_job_cap_usd={"research": 1.0})
    ctx = await _ctx(backend_pool, "research")
    assert await budget_guard.before_request(ctx, "claude-sonnet-5", now=NOW) == "claude-sonnet-5"
    # A response's cost counts before its step is recorded: 100k output tokens of
    # claude-sonnet-5 at $10/Mtok = $1.
    usage = type("U", (), {"input_tokens": 0, "output_tokens": 100_000})()
    budget_guard.after_response(ctx, "claude-sonnet-5", usage)
    with pytest.raises(BudgetExceeded, match=r"one research run reached \(\$1.00 of \$1.00\)"):
        await budget_guard.before_request(ctx, "claude-sonnet-5", now=NOW)
    # Other job types have no cap.
    other = await _ctx(backend_pool, "daily_digest")
    await budget_guard.before_request(other, "claude-sonnet-5", now=NOW)


@pytest.mark.db
async def test_the_client_applies_the_guard_inside_a_job(backend_pool: asyncpg.Pool) -> None:
    """The Anthropic client every call site gets from steward.ai: a paused job fails before
    anything is sent; a downgraded request is sent to the cheaper model."""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        sent.append(body)
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    client = BudgetedAnthropic(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    params: dict[str, Any] = {
        "model": "claude-opus-5-5",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
        "output_config": {"effort": "high"},
    }
    # Outside a job: no guard.
    await client.messages.create(**params)
    assert sent[-1]["model"] == "claude-opus-5-5"

    await _budget(backend_pool, monthly_cap_usd=1, at_cap="downgrade_noncritical")
    other = await create_job(backend_pool, "research", "manual")
    await _step(backend_pool, other, "2", dt.datetime.now(dt.UTC))

    async def job(ctx: JobContext) -> None:
        async with ctx.step("ask") as step:
            response = await client.messages.create(**params)
            step.usage = StepUsage(model="claude-opus-5-5", input_tokens=10, output_tokens=5)
            assert response.model == "claude-haiku-4-5"

    assert await run_job(backend_pool, "daily_digest", job, trigger="manual") == "succeeded"
    assert sent[-1]["model"] == "claude-haiku-4-5"
    assert "effort" not in sent[-1].get("output_config", {})
    assert current_job.get() is None

    await _budget(backend_pool, monthly_cap_usd=1)
    count = len(sent)
    assert await run_job(backend_pool, "daily_digest", job, trigger="manual") == "failed"
    assert len(sent) == count
    error = await backend_pool.fetchval(
        "select error from public.job where job_type = 'daily_digest' order by id desc limit 1"
    )
    assert error.startswith("monthly AI budget reached ($2.00 of $1.00)")


# ---------------------------------------------------------------------------
# Cost summary
# ---------------------------------------------------------------------------


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(budget_router)

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.db
async def test_the_summary_total_is_the_sum_of_step_costs(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await _budget(backend_pool, monthly_cap_usd=4, per_job_cap_usd={"research": 2})
    costs = ["0.004999", "0.004999", "0.004999", "1.333333", "0.666667", "0.000001"]
    research = [await create_job(backend_pool, "research", "manual") for _ in range(2)]
    digest = await create_job(backend_pool, "daily_digest", "schedule")
    await _step(backend_pool, research[0], costs[0], NOW, "claude-opus-5-5")
    await _step(backend_pool, research[0], costs[1], NOW, "claude-sonnet-5")
    await _step(backend_pool, research[1], costs[2], NOW, "claude-opus-5-5")
    await _step(backend_pool, digest, costs[3], NOW, "claude-sonnet-5")
    await _step(backend_pool, digest, costs[4], NOW, "claude-sonnet-5")
    await _step(backend_pool, digest, costs[5], NOW, "claude-sonnet-5")
    await _step(backend_pool, digest, None, NOW)
    await _step(backend_pool, digest, "9", NOW - dt.timedelta(days=40))

    res = await api.get("/costs/summary", params={"month": "2026-09"})
    assert res.status_code == 200, res.text
    body = res.json()
    expected = sum(Decimal(c) for c in costs)
    assert Decimal(str(body["total_usd"])) == expected
    assert body["cap_usd"] == 4
    assert body["fraction"] == pytest.approx(float(expected / 4))
    rows = {r["job_type"]: r for r in body["by_job_type"]}
    assert rows["research"]["runs"] == 2
    assert rows["research"]["job_cap_usd"] == 2
    assert rows["daily_digest"]["runs"] == 1
    assert not rows["daily_digest"]["critical"]
    by_type = sum(Decimal(str(r["cost_usd"])) for r in body["by_job_type"])
    by_model = sum(Decimal(str(r["cost_usd"])) for r in body["by_model"])
    assert round(by_type, 2) == round(by_model, 2) == round(expected, 2)
    assert by_type == expected

    assert (await api.get("/costs/summary", params={"month": "2026-13"})).status_code == 422
    empty = (await api.get("/costs/summary", params={"month": "2020-01"})).json()
    assert empty["total_usd"] == 0 and empty["by_job_type"] == []


def test_downgrade_to_haiku_keeps_the_request_valid() -> None:
    from steward.ai import _downgrade

    params = {
        "model": "claude-sonnet-5",
        "output_config": {"effort": "high", "format": {"type": "json_schema"}},
        "thinking": {"type": "adaptive"},
        "tools": [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
            {"type": "web_fetch_20260209", "name": "web_fetch"},
            {"name": "custom", "input_schema": {}},
        ],
    }
    out = _downgrade(params, "claude-haiku-4-5")
    assert out["model"] == "claude-haiku-4-5"
    assert out["output_config"] == {"format": {"type": "json_schema"}}
    assert "thinking" not in out
    assert [t.get("type") for t in out["tools"]] == [
        "web_search_20250305",
        "web_fetch_20250910",
        None,
    ]
    assert out["tools"][0]["max_uses"] == 3
    # Another target keeps the tools and thinking as they were.
    other = _downgrade(params, "claude-sonnet-5")
    assert other["tools"] == params["tools"] and other["thinking"] == {"type": "adaptive"}
