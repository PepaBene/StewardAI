"""The Phase 8 job handlers run through the real job runner."""

from __future__ import annotations

import datetime as dt
import json

import asyncpg
import pytest

from steward.jobs.runner import run_job
from steward.verify_sources.jobs import (
    raw_retention_handler,
    shadow_report_handler,
    shadow_snapshot_handler,
    zero_cost_gate_handler,
)

DAY = dt.date(2026, 9, 20)


async def _steps(pool: asyncpg.Pool, job_type: str) -> list[dict[str, object]]:
    rows = await pool.fetch(
        """select s.name, s.status, s.detail from public.job_step s
             join public.job j on j.id = s.job_id where j.job_type = $1 order by s.step_index""",
        job_type,
    )
    return [
        {"name": r["name"], "status": r["status"], "detail": json.loads(r["detail"] or "{}")}
        for r in rows
    ]


@pytest.mark.db
async def test_snapshot_then_report_jobs(backend_pool: asyncpg.Pool) -> None:
    await backend_pool.execute(
        "insert into public.source_observation(domain,metric,period_start,period_end,value,unit,"
        "source,source_url,known_at,access_state) values('macro','gdp','2025-01-01','2025-12-31',"
        "1,'pct','world_bank','https://x',$1,'available')",
        dt.datetime.combine(DAY, dt.time(10), dt.UTC),
    )
    result = await run_job(
        backend_pool,
        "shadow_snapshot",
        shadow_snapshot_handler,
        trigger="manual",
        params={"date": DAY.isoformat(), "days_back": 1},
    )
    assert result == "succeeded"
    assert (
        await backend_pool.fetchval(
            "select count(distinct snapshot_date) from public.source_shadow_snapshot"
        )
        >= 1
    )
    assert (await _steps(backend_pool, "shadow_snapshot"))[0]["detail"]["day"] == DAY.isoformat()  # type: ignore[index]

    result = await run_job(
        backend_pool,
        "shadow_report",
        shadow_report_handler,
        trigger="manual",
        params={"date": DAY.isoformat()},
    )
    assert result == "succeeded"
    detail = (await _steps(backend_pool, "shadow_report"))[0]["detail"]
    assert detail["sign_off_ready"] is False  # one day measured, not thirty  # type: ignore[index]
    assert await backend_pool.fetchval("select count(*) from public.source_shadow_report") == 1


@pytest.mark.db
async def test_retention_job_reports_counts_and_honours_dry_run(backend_pool: asyncpg.Pool) -> None:
    await backend_pool.execute(
        "insert into public.source_observation(domain,metric,period_start,period_end,value,unit,"
        "source,source_url,known_at,access_state,raw) values('macro','gdp','2020-01-01',"
        "'2020-12-31',1,'pct','world_bank','https://x','2020-06-01','available','{\"a\":1}')"
    )
    assert (
        await run_job(
            backend_pool,
            "raw_retention",
            raw_retention_handler,
            trigger="manual",
            params={"dry_run": True},
        )
        == "succeeded"
    )
    assert (await _steps(backend_pool, "raw_retention"))[0]["detail"]["source_observation_raw"] == 1  # type: ignore[index]
    assert await backend_pool.fetchval("select raw::text from public.source_observation") != "{}"
    await run_job(backend_pool, "raw_retention", raw_retention_handler, trigger="manual")
    assert await backend_pool.fetchval("select raw::text from public.source_observation") == "{}"


@pytest.mark.db
async def test_zero_cost_job_fails_on_a_reachable_paid_endpoint(backend_pool: asyncpg.Pool) -> None:
    assert (
        await run_job(backend_pool, "zero_cost_gate", zero_cost_gate_handler, trigger="manual")
        == "succeeded"
    )
    await backend_pool.execute(
        "update public.provider_access set state='paid_only', hard_blocked=false"
        " where provider='fmp' and endpoint='profile'"
    )
    assert (
        await run_job(backend_pool, "zero_cost_gate", zero_cost_gate_handler, trigger="manual")
        == "failed"
    )
    error = await backend_pool.fetchval(
        "select error from public.job where job_type='zero_cost_gate' order by id desc limit 1"
    )
    assert "fmp/profile: paid_reachable" in error
