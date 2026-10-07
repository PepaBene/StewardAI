"""P8.5 / P7.8: the estimate-revision scan. While the 20-pair review gate is unmet a large revision
opens a review task and no alert; once it is met the same revision alerts once and a rerun adds
nothing. Skipped without STEWARD_TEST_DATABASE_URL."""

import datetime as dt

import asyncpg
import pytest

from steward.disclosure import alerts
from tests.test_disclosure_estimates_db import setup, snapshot, value

pytestmark = pytest.mark.db

UTC = dt.UTC
EARLIER = dt.datetime(2026, 7, 15, tzinfo=UTC)
LATER = dt.datetime(2026, 7, 20, tzinfo=UTC)
SINCE = dt.datetime(2026, 7, 16, tzinfo=UTC)
NOW = dt.datetime(2026, 7, 25, tzinfo=UTC)


async def revision(pool: asyncpg.Pool, before: str, after: str) -> tuple[int, int, int]:
    issuer, source = await setup(pool)
    a = await snapshot(pool, issuer, source, EARLIER, digest="a")
    b = await snapshot(pool, issuer, source, LATER, digest="b")
    va = await value(pool, a, before)
    vb = await value(pool, b, after)
    return va, vb, issuer


async def pass_gate(pool: asyncpg.Pool, issuer: int, source_value: int) -> None:
    """Twenty distinct reviewed pairs, all labelled correct."""
    source = await pool.fetchval("select id from public.consensus_source limit 1")
    for i in range(20):
        snap = await snapshot(
            pool,
            issuer,
            source,
            dt.datetime(2026, 1, 1, tzinfo=UTC) + dt.timedelta(days=i),
            digest=f"old{i}",
        )
        old = await value(pool, snap, "100")
        await pool.execute(
            "insert into public.estimate_pair_review"
            " (earlier_value_id, later_value_id, correct, reviewer) values ($1,$2,true,'r')",
            old,
            source_value,
        )


async def alert_rows(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch(
        "select ticker, headline, dedupe_key from public.disclosure_alert"
        " where kind = 'estimate_revision'"
    )


async def reviews(pool: asyncpg.Pool) -> int:
    return int(
        await pool.fetchval(
            "select count(*) from public.quality_review where subject_type = 'estimate_value'"
        )
    )


async def test_a_large_revision_opens_a_review_and_no_alert_while_the_gate_is_unmet(
    backend_pool: asyncpg.Pool,
) -> None:
    await revision(backend_pool, "100", "110")
    first = await alerts.scan_estimate_revisions(backend_pool, "ACME", SINCE, NOW)
    assert first == (0, 1)
    assert await alert_rows(backend_pool) == []
    again = await alerts.scan_estimate_revisions(backend_pool, "ACME", SINCE, NOW)
    assert again == (0, 0)  # the same value is not queued twice
    assert await reviews(backend_pool) == 1


async def test_a_met_gate_alerts_once_and_a_rerun_adds_nothing(
    backend_pool: asyncpg.Pool,
) -> None:
    _va, vb, issuer = await revision(backend_pool, "100", "110")
    await pass_gate(backend_pool, issuer, vb)
    assert await alerts.scan_estimate_revisions(backend_pool, "ACME", SINCE, NOW) == (1, 0)
    (row,) = await alert_rows(backend_pool)
    assert row["ticker"] == "ACME" and "+10.0%" in row["headline"]
    assert await alerts.scan_estimate_revisions(backend_pool, "ACME", SINCE, NOW) == (0, 0)
    assert len(await alert_rows(backend_pool)) == 1


async def test_a_small_revision_or_one_outside_the_window_raises_nothing(
    backend_pool: asyncpg.Pool,
) -> None:
    _va, vb, issuer = await revision(backend_pool, "100", "102")  # 2%, under the 5% floor
    await pass_gate(backend_pool, issuer, vb)
    assert await alerts.scan_estimate_revisions(backend_pool, "ACME", SINCE, NOW) == (0, 0)
    late = dt.datetime(2026, 7, 21, tzinfo=UTC)  # the revision became known before this
    assert await alerts.scan_estimate_revisions(backend_pool, "ACME", late, NOW) == (0, 0)
    assert await alert_rows(backend_pool) == []
