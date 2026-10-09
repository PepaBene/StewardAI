"""'Draft from research' under the Budget config's caps, through the real job runner and database
(audit R13). Needs STEWARD_TEST_DATABASE_URL (skipped otherwise).

The model call is replaced by one that asks the budget guard exactly as the Anthropic client does
inside a job (`steward.ai.BudgetedAnthropic.post`), so the job, its context and the spend
records are real."""

from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from steward import budget_guard
from steward.config.budget import BudgetConfig
from steward.config.store import save_config
from steward.jobs.runner import current_job
from steward.thesis import draft as thesis_draft

pytestmark = pytest.mark.db
REPORT = type("Report", (), {"ticker": "AAPL"})()


async def _guarded(llm: Any, model: str, report: Any) -> str:
    ctx = current_job.get()
    assert ctx is not None and ctx.job_type == "thesis_draft"
    await budget_guard.before_request(ctx, model)
    return "DRAFT"


async def _spent(pool: asyncpg.Pool, usd: str) -> None:
    job = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values ('research', 'manual',"
        " 'succeeded') returning id"
    )
    await pool.execute(
        "insert into public.job_step (job_id, step_index, name, status, started_at, model,"
        " cost_usd) values ($1, 0, 'ai', 'succeeded', now(), 'm', $2)",
        job,
        Decimal(usd),
    )


async def _cap(pool: asyncpg.Pool, usd: float) -> None:
    async with pool.acquire() as conn:
        await save_config(conn, "budget", BudgetConfig(monthly_cap_usd=usd), created_by=None)


async def _draft_jobs(pool: asyncpg.Pool) -> list[str]:
    rows = await pool.fetch(
        "select status from public.job where job_type = 'thesis_draft' order by id"
    )
    return [r["status"] for r in rows]


async def test_a_draft_under_the_cap_runs_as_a_recorded_job(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(thesis_draft, "draft_thesis", _guarded)
    await _cap(backend_pool, 10)
    await _spent(backend_pool, "2")

    out = await thesis_draft.draft_within_budget(backend_pool, None, "claude-sonnet-5", REPORT)  # type: ignore[arg-type]
    assert out == "DRAFT"
    assert await _draft_jobs(backend_pool) == ["succeeded"]


async def test_at_the_monthly_cap_the_draft_is_refused_and_the_job_fails(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(thesis_draft, "draft_thesis", _guarded)
    await _cap(backend_pool, 10)
    await _spent(backend_pool, "10.50")

    with pytest.raises(thesis_draft.DraftBudgetRefused, match="monthly AI budget reached"):
        await thesis_draft.draft_within_budget(backend_pool, None, "claude-sonnet-5", REPORT)  # type: ignore[arg-type]
    assert await _draft_jobs(backend_pool) == ["failed"]
    error = await backend_pool.fetchval(
        "select error from public.job where job_type = 'thesis_draft'"
    )
    assert error and "budget" in error
