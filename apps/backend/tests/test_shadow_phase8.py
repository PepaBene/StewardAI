"""D8.2: the shadow-collection recorder and sign-off report."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from steward.verify_sources.shadow import (
    classify_provider,
    generate_shadow_report,
    record_shadow_snapshot,
    render_markdown,
)
from tests.conftest import MigratedDatabase

DAY = dt.date(2026, 9, 20)
NOON = dt.datetime.combine(DAY, dt.time(12), dt.UTC)


def summary(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "provider": "x",
        "days_with_data": 20,
        "observations": 100,
        "fetch_attempts": 40,
        "denials": 0,
        "failure_rate": 0.05,
        "value_added_ratio": 0.4,
        "disagreement_rate": 0.01,
        "quota_pressure": 0.2,
    }
    return base | over


# ---------------------------------------------------------------------------------------
# Classification (pure)
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("over", "verdict"),
    [
        ({}, "keep"),
        ({"days_with_data": 0, "observations": 0}, "not_measured"),
        ({"days_with_data": 3}, "not_measured"),
        ({"observations": 0}, "dormant"),
        ({"value_added_ratio": 0.01}, "low_value"),
        ({"failure_rate": 0.8}, "unreliable"),
        ({"observations": 0, "denials": 30, "fetch_attempts": 40}, "disabled_by_access"),
        ({"value_added_ratio": None, "disagreement_rate": 0.3}, "keep"),
    ],
)
def test_classification_never_invents_a_verdict(over: dict[str, Any], verdict: str) -> None:
    assert classify_provider(summary(**over), 30).classification == verdict


def test_low_value_and_dormant_explain_gap_fill_only() -> None:
    low = classify_provider(summary(value_added_ratio=0.0), 30)
    assert any("gap-fill" in r for r in low.reasons)
    dormant = classify_provider(summary(observations=0), 30)
    assert any("gap-fill" in r for r in dormant.reasons)


# ---------------------------------------------------------------------------------------
# Recorder + report on a database
# ---------------------------------------------------------------------------------------
async def _observe(
    pool: asyncpg.Pool,
    source: str,
    metric: str,
    value: str,
    *,
    security_id: int | None = None,
    geography: str | None = "CZ",
    at: dt.datetime = NOON,
) -> int:
    return int(
        await pool.fetchval(
            """insert into public.source_observation
               (domain,metric,security_id,geography,period_start,period_end,value,unit,source,
                source_url,known_at,access_state)
               values('fundamentals',$1,$2,$3,'2025-01-01','2025-12-31',$4,'EUR',$5,'https://x.test',
                      $6,'available') returning id""",
            metric,
            security_id,
            geography,
            Decimal(value),
            source,
            at,
        )
    )


async def _seed_provider(pool: asyncpg.Pool, provider: str, endpoint: str = "e") -> None:
    await pool.execute(
        "insert into public.provider_access(provider,endpoint,state,quota,quota_period)"
        " values($1,$2,'available',100,'day') on conflict do nothing",
        provider,
        endpoint,
    )


@pytest.mark.db
async def test_recorder_measures_value_added_disagreement_failures_and_quota(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await _seed_provider(pool, "alpha")
    await _seed_provider(pool, "beta")
    # alpha and beta both report revenue; only alpha reports profit -> alpha adds 1 unique of 2
    a_rev = await _observe(pool, "alpha", "revenue", "100")
    await _observe(pool, "alpha", "profit", "10")
    b_rev = await _observe(pool, "beta", "revenue", "103")
    await pool.execute(
        "insert into public.source_conflict(metric,preferred_observation_id,"
        "alternative_observation_id,tolerance,difference,created_at)"
        " values('revenue',$1,$2,1,3,$3)",
        a_rev,
        b_rev,
        NOON,
    )
    await pool.execute(
        "insert into public.provider_fetch_audit(provider,endpoint,attempted_at,state,credits)"
        " values('alpha','e',$1,'available',1),('alpha','e',$1,'temporarily_failed',0),"
        "('alpha','e',$1,'quota_exhausted',0),('beta','e',$1,'available',1)",
        NOON,
    )
    await pool.execute(
        "insert into public.provider_quota_usage"
        "(provider,endpoint,window_kind,window_start,window_end,credits,requests)"
        " values('alpha','e','day',$1,$2,40,40)",
        dt.datetime.combine(DAY, dt.time(), dt.UTC),
        dt.datetime.combine(DAY + dt.timedelta(days=1), dt.time(), dt.UTC),
    )
    await pool.execute(
        "insert into public.coverage_status(provider,dataset,entity_key,state)"
        " values('alpha','d','k1','available'),('alpha','d','k2','paid_only')"
    )
    await pool.execute(
        "insert into public.job(job_type,trigger,status,created_at)"
        " values('macro_collect','schedule','failed',$1),"
        "('macro_collect','schedule','succeeded',$1)",
        NOON,
    )

    assert await record_shadow_snapshot(pool, DAY) >= 2
    alpha = await pool.fetchrow(
        "select * from public.source_shadow_snapshot where snapshot_date=$1 and provider='alpha'",
        DAY,
    )
    assert alpha is not None
    assert alpha["observations_new"] == 2
    assert alpha["unique_observations"] == 1  # profit only; revenue is also reported by beta
    assert alpha["conflicts_opened"] == 1
    assert alpha["fetch_attempts"] == 3 and alpha["fetch_failures"] == 1 and alpha["denials"] == 1
    assert alpha["credits_used"] == Decimal(40) and alpha["quota_limit"] == Decimal(100)
    assert (alpha["coverage_available"], alpha["coverage_total"]) == (1, 2)
    assert alpha["metrics_covered"] == 2
    detail = json.loads(alpha["detail"]) if isinstance(alpha["detail"], str) else alpha["detail"]
    assert detail["metrics"] == ["profit", "revenue"] and detail["geographies"] == ["CZ"]
    beta = await pool.fetchrow(
        "select * from public.source_shadow_snapshot where snapshot_date=$1 and provider='beta'",
        DAY,
    )
    assert beta is not None and beta["unique_observations"] == 0 and beta["conflicts_opened"] == 1
    job = await pool.fetchrow(
        "select * from public.source_shadow_job_snapshot where job_type='macro_collect'"
    )
    assert job is not None and (job["runs"], job["failed"]) == (2, 1)

    # re-recording a day replaces it rather than doubling it
    await record_shadow_snapshot(pool, DAY)
    assert await pool.fetchval("select count(*) from public.source_shadow_snapshot") >= 2
    again = await pool.fetchval(
        "select observations_new from public.source_shadow_snapshot"
        " where snapshot_date=$1 and provider='alpha'",
        DAY,
    )
    assert again == 2


@pytest.mark.db
async def test_report_with_no_data_says_so_and_is_never_signed_off(
    backend_pool: asyncpg.Pool,
) -> None:
    report = await generate_shadow_report(backend_pool, DAY, persist=True)
    assert report["sign_off_ready"] is False
    assert report["days_observed"] == 0 and report["providers"] == []
    assert any("no daily snapshots" in b for b in report["blockers"])
    markdown = render_markdown(report)
    assert "Sign-off ready: no" in markdown
    assert "nothing can be signed off" in markdown
    stored = await backend_pool.fetchrow("select * from public.source_shadow_report")
    assert stored is not None and stored["sign_off_ready"] is False and stored["days_observed"] == 0


async def _full_window(pool: asyncpg.Pool, *, days: int = 30) -> None:
    await _seed_provider(pool, "useful")
    await _seed_provider(pool, "redundant")
    await _seed_provider(pool, "silent")
    for offset in range(days):
        day = DAY - dt.timedelta(days=offset)
        for provider, obs, unique, attempts in (
            ("useful", 20, 15, 2),
            ("redundant", 20, 0, 2),
            ("silent", 0, 0, 2),
        ):
            await pool.execute(
                """insert into public.source_shadow_snapshot
                   (snapshot_date,provider,observations_new,metrics_covered,unique_observations,
                    fetch_attempts,credits_used,quota_limit,quota_period,freshness_hours,
                    coverage_available,coverage_total,detail)
                   values($1,$2,$3,2,$4,$5,$6,100,'day',6,1,2,$7::jsonb)""",
                day,
                provider,
                obs,
                unique,
                attempts,
                Decimal(attempts),
                json.dumps(
                    {
                        "metrics": ["revenue", "profit"] if obs else [],
                        "geographies": ["CZ"] if obs else [],
                        "endpoints": {"e": {"credits": attempts, "quota": 100, "period": "day"}},
                    }
                ),
            )
        await pool.execute(
            "insert into public.source_shadow_job_snapshot(snapshot_date,job_type,runs,failed)"
            " values($1,'macro_collect',1,$2)",
            day,
            1 if offset == 0 else 0,
        )


async def _scope(pool: asyncpg.Pool, admin_url: str) -> None:
    user = uuid.uuid4()
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute("insert into auth.users(id,email) values($1,'o@example.com')", user)
    finally:
        await admin.close()
    await pool.execute("insert into public.instrument(t212_ticker,name) values('AAA_US_EQ','AAA')")
    await pool.execute(
        "insert into public.watchlist_item(owner_id,ticker) values($1,'AAA_US_EQ')", user
    )
    sec = await pool.fetchval(
        "insert into public.security(kind,name) values('equity','AAA') returning id"
    )
    await pool.execute(
        "insert into public.instrument_security_link(ticker,security_id,status)"
        " values('AAA_US_EQ',$1,'confirmed')",
        sec,
    )
    await _observe(pool, "useful", "revenue", "5", security_id=sec)


@pytest.mark.db
async def test_full_window_report_signs_off_and_lists_dormant_and_low_value(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    await _full_window(backend_pool)
    await _scope(backend_pool, migrated_db.admin_url)
    report = await generate_shadow_report(backend_pool, DAY)
    assert report["days_observed"] == 30
    assert report["blockers"] == [], report["blockers"]
    assert report["sign_off_ready"] is True
    by = {p["provider"]: p for p in report["providers"]}
    assert by["useful"]["recommendation"] == "keep"
    assert by["useful"]["value_added_ratio"] == 0.75
    assert by["redundant"]["recommendation"] == "low_value"
    assert by["silent"]["recommendation"] == "dormant"
    assert by["useful"]["quota_pressure"] == 0.02
    assert by["useful"]["scope_securities_with_data"] == 1
    dormant = {d["provider"] for d in report["recommendations"]["dormant"]}
    assert dormant == {"redundant", "silent"}
    assert report["jobs"] == [
        {"job_type": "macro_collect", "runs": 30, "failed": 1, "failure_rate": 0.0333}
    ]
    markdown = render_markdown(report)
    assert "Sign-off ready: yes" in markdown and "redundant" in markdown
    assert "gap-fill" in markdown
    json.dumps(report, default=str)  # serialisable


@pytest.mark.db
async def test_partial_window_blocks_sign_off_with_the_measured_day_count(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    await _full_window(backend_pool, days=12)
    await _scope(backend_pool, migrated_db.admin_url)
    report = await generate_shadow_report(backend_pool, DAY, persist=False)
    assert report["sign_off_ready"] is False
    assert any("12 of 30" in b for b in report["blockers"])
    assert await backend_pool.fetchval("select count(*) from public.source_shadow_report") == 0
