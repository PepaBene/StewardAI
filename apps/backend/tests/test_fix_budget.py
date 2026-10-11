"""Budget config: spend caps, the guard applied to every AI request of a job, and the cost
summary."""

import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any, get_args

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from api.auth import AuthenticatedUser, require_owner
from api.budget_routes import router as budget_router
from api.db import get_pool, get_pool_or_error
from steward import budget_guard
from steward.budget_guard import BudgetExceeded, month_bounds, month_of, month_spend
from steward.config.budget import BudgetConfig, BudgetJobType
from steward.config.store import save_config
from steward.jobs.registry import JOB_HANDLERS, JobType
from steward.jobs.runner import JobContext, StepUsage, create_job, current_job, run_job
from steward.llm.client import LLM
from steward.llm.types import Message, Request

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
        {"at_cap": "downgrade_noncritical", "downgrade_model": "gpt-unknown-1"},
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
    assert await budget_guard.before_request(ctx, "gpt-6.1-sol", now=NOW) == "gpt-6.1-sol"


@pytest.mark.db
async def test_at_the_cap_non_critical_jobs_are_paused(backend_pool: asyncpg.Pool) -> None:
    await _budget(backend_pool, monthly_cap_usd=10)
    other = await create_job(backend_pool, "research", "manual")
    await _step(backend_pool, other, "10.5", NOW)
    # Last month's spend does not count.
    await _step(backend_pool, other, "100", NOW - dt.timedelta(days=40))
    ctx = await _ctx(backend_pool, "daily_digest")
    with pytest.raises(BudgetExceeded) as exc:
        await budget_guard.before_request(ctx, "gpt-6.1-sol", now=NOW)
    assert str(exc.value) == (
        "monthly AI budget reached ($10.50 of $10.00); non-critical jobs paused"
    )
    critical = await _ctx(backend_pool, "earnings_check")
    assert await budget_guard.before_request(critical, "gpt-6.1-sol", now=NOW) == "gpt-6.1-sol"


@pytest.mark.db
async def test_alert_only_keeps_every_job_running(backend_pool: asyncpg.Pool) -> None:
    await _budget(backend_pool, monthly_cap_usd=10, at_cap="alert_only")
    other = await create_job(backend_pool, "research", "manual")
    await _step(backend_pool, other, "11", NOW)
    ctx = await _ctx(backend_pool, "daily_digest")
    assert await budget_guard.before_request(ctx, "gpt-6.1-sol", now=NOW) == "gpt-6.1-sol"


@pytest.mark.db
async def test_at_the_cap_non_critical_jobs_are_downgraded(backend_pool: asyncpg.Pool) -> None:
    await _budget(backend_pool, monthly_cap_usd=10, at_cap="downgrade_noncritical")
    other = await create_job(backend_pool, "research", "manual")
    await _step(backend_pool, other, "10", NOW)
    ctx = await _ctx(backend_pool, "research")
    model = await budget_guard.before_request(ctx, "gpt-6.1-sol", now=NOW)
    assert model == "gpt-6-luna"
    # The step is recorded and priced as the model actually used.
    async with ctx.step("write") as step:
        step.usage = StepUsage(model="gpt-6.1-sol", input_tokens=1_000_000)
    row = await backend_pool.fetchrow(
        "select model, cost_usd, detail from public.job_step where job_id = $1", ctx.job_id
    )
    assert row["model"] == "gpt-6-luna"
    # 1M input tokens at gpt-6-luna's $0.10 per million.
    assert row["cost_usd"] == Decimal("0.100000")
    assert json.loads(row["detail"])["budget_downgrade"]["from"] == "gpt-6.1-sol"


@pytest.mark.db
async def test_the_warning_and_the_cap_alert_once_a_month(backend_pool: asyncpg.Pool) -> None:
    await _budget(backend_pool, monthly_cap_usd=10, warn_at_fraction=0.5)
    other = await create_job(backend_pool, "research", "manual")
    await _step(backend_pool, other, "6", NOW)
    for _ in range(2):
        ctx = await _ctx(backend_pool, "earnings_check")
        await budget_guard.before_request(ctx, "gpt-6.1-sol", now=NOW)
    await _step(backend_pool, other, "5", NOW)
    ctx = await _ctx(backend_pool, "earnings_check")
    await budget_guard.before_request(ctx, "gpt-6.1-sol", now=NOW)
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
    assert await budget_guard.before_request(ctx, "gpt-6.1-sol", now=NOW) == "gpt-6.1-sol"
    # A response's cost counts before its step is recorded: 100k output tokens of
    # gpt-6.1-sol at $10/Mtok = $1.
    usage = type("U", (), {"input_tokens": 0, "output_tokens": 100_000})()
    budget_guard.after_response(ctx, "gpt-6.1-sol", usage)
    with pytest.raises(BudgetExceeded, match=r"one research run reached \(\$1.00 of \$1.00\)"):
        await budget_guard.before_request(ctx, "gpt-6.1-sol", now=NOW)
    # Other job types have no cap.
    other = await _ctx(backend_pool, "daily_digest")
    await budget_guard.before_request(other, "gpt-6.1-sol", now=NOW)


@pytest.mark.db
async def test_a_job_type_over_its_monthly_cap_is_stopped(backend_pool: asyncpg.Pool) -> None:
    await _budget(backend_pool, per_job_monthly_cap_usd={"news_analysis": 2.0})
    earlier = await create_job(backend_pool, "news_analysis", "manual")
    await _step(backend_pool, earlier, "1.5", NOW)
    # Last month's run of the same type does not count.
    await _step(backend_pool, earlier, "50", NOW - dt.timedelta(days=40))
    ctx = await _ctx(backend_pool, "news_analysis")
    assert await budget_guard.before_request(ctx, "gpt-6.1-sol", now=NOW) == "gpt-6.1-sol"
    # 50k output tokens of gpt-6.1-sol at $10/Mtok = $0.50: the month reaches $2.
    usage = type("U", (), {"input_tokens": 0, "output_tokens": 50_000})()
    budget_guard.after_response(ctx, "gpt-6.1-sol", usage)
    with pytest.raises(BudgetExceeded, match="monthly AI budget of news_analysis jobs reached"):
        await budget_guard.before_request(ctx, "gpt-6.1-sol", now=NOW)
    # Other job types, even a critical one, are not held by it.
    other = await _ctx(backend_pool, "daily_digest")
    await budget_guard.before_request(other, "gpt-6.1-sol", now=NOW)


@pytest.mark.db
async def test_the_monthly_job_cap_applies_to_critical_jobs_too(
    backend_pool: asyncpg.Pool,
) -> None:
    await _budget(backend_pool, per_job_monthly_cap_usd={"chat": 1.0})
    earlier = await create_job(backend_pool, "chat", "manual")
    await _step(backend_pool, earlier, "1", NOW)
    ctx = await _ctx(backend_pool, "chat")
    with pytest.raises(BudgetExceeded, match="monthly AI budget of chat jobs reached"):
        await budget_guard.before_request(ctx, "gpt-6.1-sol", now=NOW)


@pytest.mark.db
async def test_news_ingest_is_never_stopped_by_the_default_caps(
    backend_pool: asyncpg.Pool,
) -> None:
    await _budget(backend_pool)
    earlier = await create_job(backend_pool, "news_ingest", "manual")
    await _step(backend_pool, earlier, "100", NOW)
    ctx = await _ctx(backend_pool, "news_ingest")
    assert await budget_guard.before_request(ctx, "gpt-6.1-sol", now=NOW) == "gpt-6.1-sol"


@pytest.mark.db
async def test_the_client_applies_the_guard_inside_a_job(backend_pool: asyncpg.Pool) -> None:
    """The client every call site gets from steward.llm: a paused job fails before anything is
    sent; a downgraded request is sent to the cheaper model."""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        return httpx.Response(200, json=_openai_answer(10, 5))

    client = LLM(openai_key="test", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    req = Request(
        model="gpt-6.1-sol",
        system="s",
        messages=(Message(role="user", text="hi"),),
        effort="high",
        max_tokens=16,
    )
    # Outside a job: no guard.
    await client.complete(req)
    assert sent[-1]["model"] == "gpt-6.1-sol"

    await _budget(backend_pool, monthly_cap_usd=1, at_cap="downgrade_noncritical")
    other = await create_job(backend_pool, "research", "manual")
    await _step(backend_pool, other, "2", dt.datetime.now(dt.UTC))

    async def job(ctx: JobContext) -> None:
        async with ctx.step("ask") as step:
            response = await client.complete(req)
            step.usage = StepUsage(model="gpt-6.1-sol", input_tokens=10, output_tokens=5)
            assert response.model == "gpt-6-luna"

    assert await run_job(backend_pool, "daily_digest", job, trigger="manual") == "succeeded"
    assert sent[-1]["model"] == "gpt-6-luna"
    assert "reasoning" not in sent[-1]
    assert current_job.get() is None

    await _budget(backend_pool, monthly_cap_usd=1)
    count = len(sent)
    assert await run_job(backend_pool, "daily_digest", job, trigger="manual") == "failed"
    assert len(sent) == count
    error = await backend_pool.fetchval(
        "select error from public.job where job_type = 'daily_digest' order by id desc limit 1"
    )
    assert error.startswith("monthly AI budget reached ($2.00 of $1.00)")


@pytest.mark.db
async def test_a_failed_step_still_records_what_its_requests_cost(
    backend_pool: asyncpg.Pool,
) -> None:
    """The API bills a response whether or not the call site gets to count it: a step that
    raises after a response, and one whose call site counts less than came back, keep it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_answer(1_000_000, 100_000))

    client = LLM(openai_key="test", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    req = Request(
        model="gpt-6.1-sol", system="s", messages=(Message(role="user", text="hi"),), max_tokens=16
    )

    async def job(ctx: JobContext) -> None:
        async with ctx.step("fails_after_response"):
            await client.complete(req)
            raise RuntimeError("the process stopped before the call site counted it")

    assert await run_job(backend_pool, "daily_digest", job, trigger="manual") == "failed"
    row = await backend_pool.fetchrow(
        "select s.status, s.model, s.input_tokens, s.output_tokens, s.cost_usd, s.detail"
        " from public.job_step s join public.job j on j.id = s.job_id"
        " where j.job_type = 'daily_digest' and s.name = 'fails_after_response'"
        " order by s.id desc limit 1"
    )
    assert row["status"] == "failed"
    assert row["model"] == "gpt-6.1-sol"
    assert (row["input_tokens"], row["output_tokens"]) == (1_000_000, 100_000)
    # 1M input at $2 plus 100k output at $10 per million.
    assert row["cost_usd"] == Decimal("3.000000")
    assert json.loads(row["detail"])["usage_from_requests"] is True

    async def undercounted(ctx: JobContext) -> None:
        async with ctx.step("undercounted") as step:
            await client.complete(req)
            step.usage = StepUsage(model="gpt-6.1-sol", input_tokens=10, output_tokens=5)

    assert await run_job(backend_pool, "daily_digest", undercounted, trigger="manual") == (
        "succeeded"
    )
    cost = await backend_pool.fetchval(
        "select s.cost_usd from public.job_step s join public.job j on j.id = s.job_id"
        " where j.job_type = 'daily_digest' and s.name = 'undercounted'"
        " order by s.id desc limit 1"
    )
    assert cost == Decimal("3.000000")


class _RecordingPool:
    """Stands in for the pool: hands out step ids and keeps the last write of each step."""

    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}

    async def fetchval(self, query: str, *args: Any) -> int:
        self.rows[1] = {}
        return 1

    async def execute(self, query: str, step_id: int, *args: Any) -> None:
        if "cost_usd" in query:
            # (model, input, output, searches, cost) are the leading columns of both writes
            # that carry a cost: the running total and the step's close.
            self.rows[step_id] = {"query": query, "args": args}


async def test_a_failed_step_records_the_cost_of_its_requests_without_a_database() -> None:
    from steward.costs import PriceTable
    from steward.jobs.runner import record_response

    pool = _RecordingPool()
    ctx = JobContext(
        pool=pool,  # type: ignore[arg-type]
        job_id=1,
        job_type="daily_digest",
        trigger="manual",
        _prices=PriceTable(BudgetConfig()),
    )

    class Usage:
        input_tokens = 1_000_000
        output_tokens = 100_000
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0
        server_tool_use = None

    with pytest.raises(RuntimeError):
        async with ctx.step("ask"):
            await record_response("gpt-6.1-sol", Usage())
            raise RuntimeError("stopped before the call site counted it")

    args = pool.rows[1]["args"]
    # status, detail, error, model, input, output, searches, cost
    assert args[3:8] == ("gpt-6.1-sol", 1_000_000, 100_000, 0, Decimal("3.000000"))
    assert "usage_from_requests" in args[1]


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
    await _step(backend_pool, research[0], costs[0], NOW, "gpt-6.1-sol")
    await _step(backend_pool, research[0], costs[1], NOW, "gpt-6.1-sol")
    await _step(backend_pool, research[1], costs[2], NOW, "gpt-6.1-sol")
    await _step(backend_pool, digest, costs[3], NOW, "gpt-6.1-sol")
    await _step(backend_pool, digest, costs[4], NOW, "gpt-6.1-sol")
    await _step(backend_pool, digest, costs[5], NOW, "gpt-6.1-sol")
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


def test_a_downgrade_drops_effort_and_the_cache_key() -> None:
    from steward.llm.client import _downgraded
    from steward.llm.types import SearchSpec

    req = Request(
        model="gpt-6.1-sol",
        system="s",
        messages=(Message(role="user", text="hi"),),
        effort="high",
        search=SearchSpec(max_uses=3),
        cache_key="k",
    )
    same = _downgraded(req, "gpt-6-luna")
    assert (same.model, same.effort, same.cache_key) == ("gpt-6-luna", None, None)
    assert same.search == req.search
    # Another provider's search tool is not the same tool, so it is dropped.
    assert _downgraded(req, "gemini-3.8-flash").search is None


def _openai_answer(input_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "ok", "annotations": []}],
            }
        ],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
