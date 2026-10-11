"""D8.4: retention, recompute from archived observations, key expiration, source removal and
migration rollback."""

from __future__ import annotations

import datetime as dt
import os
import secrets
from collections.abc import AsyncIterator
from dataclasses import fields
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from steward.free_sources import calculate_and_store_returns
from steward.sources.base import AccessResult, AccessState
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.sources.store import CoverageUpdate, record_access, update_coverage
from steward.verify_sources.lifecycle import (
    RetentionPolicy,
    catalog_lines,
    catalog_snapshot,
    diff_snapshots,
    free_source_migrations,
    latest_migration_prefix,
    missing_rollbacks,
    prune_raw_payloads,
    rollback_notes,
    rollback_path,
)
from tests.conftest import MIGRATIONS_DIR, REPO_ROOT, SHIM, MigratedDatabase, _with_database

ROLLBACK_DIR = REPO_ROOT / "supabase" / "rollback"
NOW = dt.datetime(2026, 10, 4, 12, 0, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------------------
# Rollback files
# ---------------------------------------------------------------------------------------
def test_rollback_range_follows_the_newest_migration(tmp_path: Path) -> None:
    (tmp_path / "m").mkdir()
    for name in (
        "20260930182226_a.sql",
        "20261001100000_transaction_fees.sql",
        "20291231235959_z.sql",
    ):
        (tmp_path / "m" / name).write_text("select 1;")
    (tmp_path / "r").mkdir()
    assert latest_migration_prefix(tmp_path / "m") == "20291231235959"
    assert missing_rollbacks(tmp_path / "m", tmp_path / "r") == [
        "20260930182226_a.sql",
        "20291231235959_z.sql",
    ]
    (tmp_path / "r" / "20291231235959_z.down.sql").write_text("select 1;")
    assert missing_rollbacks(tmp_path / "m", tmp_path / "r") == ["20260930182226_a.sql"]


def test_every_free_source_migration_has_a_documented_rollback() -> None:
    # `free_source_migrations` discovers the newest migration instead of stopping at a fixed
    # timestamp, so ANY migration landing without a rollback file fails here (B50).
    assert missing_rollbacks(MIGRATIONS_DIR, ROLLBACK_DIR) == []
    assert (ROLLBACK_DIR / "20261004120000_free_sources_verification.down.sql").exists()
    for migration in free_source_migrations(MIGRATIONS_DIR):
        path = rollback_path(ROLLBACK_DIR, migration)
        notes = rollback_notes(path)
        assert f"{migration.stem}.sql" in notes, migration.name
        assert "proven by" in notes.casefold(), migration.name
        if "drop table" in path.read_text():
            assert "DATA LOSS" in notes, f"{migration.name}: dropped tables must be disclosed"


def test_rollbacks_live_beside_not_inside_the_migrations() -> None:
    assert not list(MIGRATIONS_DIR.glob("*.down.sql"))


@pytest.fixture
async def stepwise_url() -> AsyncIterator[str]:
    base = os.environ.get("STEWARD_TEST_DATABASE_URL")
    if not base:
        pytest.skip("STEWARD_TEST_DATABASE_URL is not set")
    name = f"steward_roll_{secrets.token_hex(4)}"
    admin = await asyncpg.connect(base)
    await admin.execute(f'create database "{name}"')
    await admin.close()
    try:
        yield _with_database(base, name)
    finally:
        admin = await asyncpg.connect(base)
        await admin.execute(f'drop database "{name}" with (force)')
        await admin.close()


@pytest.mark.db
async def test_each_rollback_restores_the_schema_its_migration_changed(
    stepwise_url: str, migrated_db: MigratedDatabase
) -> None:
    """Apply every migration in order; for each Phase 0-6 free-source one also run its rollback
    and require the catalog fingerprint to equal the one from just before the migration,
    then re-apply it.  The end state must equal a straight application."""
    tracked = {m.name for m in free_source_migrations(MIGRATIONS_DIR)}
    conn = await asyncpg.connect(stepwise_url)
    try:
        await conn.execute(SHIM.read_text())
        has_pgmq = await conn.fetchval(
            "select exists (select 1 from pg_available_extensions where name = 'pgmq')"
        )
        proven: list[str] = []
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if "pgmq" in migration.name and not has_pgmq:
                continue
            sql = migration.read_text()
            if migration.name not in tracked:
                async with conn.transaction():
                    await conn.execute(sql)
                continue
            before = await catalog_snapshot(conn)
            async with conn.transaction():
                await conn.execute(sql)
            applied = await catalog_snapshot(conn)
            data_only = (
                "nothing to undo"
                in rollback_notes(rollback_path(ROLLBACK_DIR, migration)).casefold()
            )
            if data_only:
                # A data-only migration (config versions): the schema must not move either way.
                assert diff_snapshots(before, applied) == [], migration.name
                async with conn.transaction():
                    await conn.execute(rollback_path(ROLLBACK_DIR, migration).read_text())
                proven.append(migration.name)
                continue
            assert diff_snapshots(before, applied), f"{migration.name} changed nothing"
            async with conn.transaction():
                await conn.execute(rollback_path(ROLLBACK_DIR, migration).read_text())
            drift = diff_snapshots(before, await catalog_snapshot(conn))
            assert drift == [], f"{migration.name}: rollback left differences in {drift}"
            async with conn.transaction():
                await conn.execute(sql)
            assert diff_snapshots(applied, await catalog_snapshot(conn)) == [], migration.name
            proven.append(migration.name)
        assert len(proven) == len(tracked)
        final = await catalog_snapshot(conn)
        stepwise_functions = await catalog_lines(conn, "functions")
    finally:
        await conn.close()
    straight = await asyncpg.connect(migrated_db.admin_url)
    try:
        assert diff_snapshots(await catalog_snapshot(straight), final) == []
        assert await catalog_lines(straight, "functions") == stepwise_functions
    finally:
        await straight.close()


# ---------------------------------------------------------------------------------------
# Retention of raw payloads
# ---------------------------------------------------------------------------------------
async def _obs(
    pool: asyncpg.Pool, metric: str, known_at: dt.datetime, raw: str, value: str = "10.5"
) -> int:
    return int(
        await pool.fetchval(
            """insert into public.source_observation
               (domain,metric,period_start,period_end,value,unit,source,source_url,known_at,
                access_state,raw,normalized)
               values('macro',$1,'2024-01-01','2024-12-31',$2,'pct','world_bank','https://x.test',
                      $3,'available',$4::jsonb,'{"k":"v"}'::jsonb) returning id""",
            metric,
            Decimal(value),
            known_at,
            raw,
        )
    )


async def _raw_present(pool: asyncpg.Pool, observation_id: int) -> bool:
    return bool(
        await pool.fetchval(
            "select raw::text <> '{}' from public.source_observation where id=$1", observation_id
        )
    )


@pytest.mark.db
async def test_retention_drops_expired_raw_but_keeps_normalized_observation(
    backend_pool: asyncpg.Pool,
) -> None:
    old = await _obs(backend_pool, "gdp", NOW - dt.timedelta(days=500), '{"payload":"big"}')
    fresh = await _obs(backend_pool, "cpi", NOW - dt.timedelta(days=10), '{"payload":"big"}')
    held = await _obs(backend_pool, "ppi", NOW - dt.timedelta(days=500), '{"payload":"big"}')
    other = await _obs(backend_pool, "ppi2", NOW - dt.timedelta(days=500), '{"payload":"alt"}')
    await backend_pool.execute(
        "insert into public.source_conflict(metric,preferred_observation_id,"
        "alternative_observation_id,status) values('ppi',$1,$2,'open')",
        held,
        other,
    )
    await backend_pool.execute(
        """insert into public.macro_payload_archive
           (source,dataset,content_hash,known_at,source_url,payload)
           values('ecb','old','h1',$1,'https://x.test','{"a":1}'::jsonb),
                 ('ecb','new','h2',$2,'https://x.test','{"a":2}'::jsonb)""",
        NOW - dt.timedelta(days=200),
        NOW - dt.timedelta(days=5),
    )
    await backend_pool.execute(
        "insert into public.provider_access(provider,endpoint,state) values('p','e','available')"
    )
    await backend_pool.execute(
        "insert into public.provider_fetch_audit(provider,endpoint,attempted_at,state)"
        " values('p','e',$1,'available'),('p','e',$2,'available')",
        NOW - dt.timedelta(days=500),
        NOW - dt.timedelta(days=1),
    )

    dry = await prune_raw_payloads(backend_pool, now=NOW, dry_run=True)
    assert dry == {
        "source_observation_raw": 1,
        "macro_payload_archive": 1,
        "provider_fetch_audit": 1,
    }
    assert await _raw_present(backend_pool, old)

    counts = await prune_raw_payloads(backend_pool, now=NOW)
    assert counts == dry
    row = await backend_pool.fetchrow("select * from public.source_observation where id=$1", old)
    assert row is not None
    assert not await _raw_present(backend_pool, old)
    assert row["value"] == Decimal("10.5") and row["unit"] == "pct"
    assert row["source_url"] == "https://x.test" and row["known_at"] == NOW - dt.timedelta(days=500)
    assert "raw_pruned_at" in str(row["validation"])
    assert "v" in str(row["normalized"])
    # recent raw and open-conflict raw survive
    for keep in (fresh, held, other):
        assert await _raw_present(backend_pool, keep), keep
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.macro_payload_archive where payload is null"
        )
        == 1
    )
    assert await backend_pool.fetchval("select count(*) from public.provider_fetch_audit") == 1
    again = await prune_raw_payloads(backend_pool, now=NOW)
    assert again == {
        "source_observation_raw": 0,
        "macro_payload_archive": 0,
        "provider_fetch_audit": 0,
    }


def test_retention_policy_refuses_tiny_horizons_and_has_no_indefinite_default() -> None:
    RetentionPolicy().validate()
    with pytest.raises(ValueError):
        RetentionPolicy(macro_payload_days=3).validate()
    policy = RetentionPolicy()
    assert all(30 <= getattr(policy, f.name) <= 400 for f in fields(policy))


# ---------------------------------------------------------------------------------------
# Recompute derived metrics from archived observations
# ---------------------------------------------------------------------------------------
@pytest.mark.db
async def test_derived_returns_recompute_identically_after_derived_rows_and_raw_are_gone(
    backend_pool: asyncpg.Pool,
) -> None:
    for i, close in enumerate(["100", "102", "104", "52"]):  # a 2:1 split on day 4
        day = dt.date(2026, 9, 1) + dt.timedelta(days=i)
        await backend_pool.execute(
            """insert into public.price_bar(symbol,date,close,source,bar_type,session_date,
                 currency) values('AAA',$1,$2,'stooq','raw',$1,'USD')""",
            day,
            Decimal(close),
        )
    await backend_pool.execute(
        """insert into public.corporate_action
           (symbol,action_type,ex_date,ratio,source,source_url,status)
           values('AAA','split','2026-09-04',2,'issuer','https://x.test','confirmed')"""
    )
    args = {
        "symbol": "AAA",
        "start": dt.date(2026, 9, 1),
        "end": dt.date(2026, 9, 4),
        "source": "stooq",
        "currency": "USD",
        "actions_complete": True,
    }
    first = await calculate_and_store_returns(backend_pool, **args)  # type: ignore[arg-type]
    stored = await backend_pool.fetch(
        "select adjustment_basis,value from public.price_return_series order by 1"
    )
    assert stored, "the derived metric was stored"
    # Lose the derived rows and every expired raw payload; the archived inputs remain.
    await backend_pool.execute("delete from public.price_return_series")
    await prune_raw_payloads(backend_pool, now=NOW + dt.timedelta(days=2000))
    second = await calculate_and_store_returns(backend_pool, **args)  # type: ignore[arg-type]
    again = await backend_pool.fetch(
        "select adjustment_basis,value from public.price_return_series order by 1"
    )
    assert second["price_raw"] == first["price_raw"]
    assert [tuple(r) for r in again] == [tuple(r) for r in stored]


# ---------------------------------------------------------------------------------------
# Key expiration and source removal
# ---------------------------------------------------------------------------------------
async def _seed_provider(
    pool: asyncpg.Pool, provider: str = "fmp", endpoint: str = "profile"
) -> int:
    await pool.execute(
        """insert into public.provider_access(provider,endpoint,state,credential_env,quota,
             quota_period) values($1,$2,'available','FMP_API_KEY',250,'day')
           on conflict(provider,endpoint) do update set state='available', hard_blocked=false""",
        provider,
        endpoint,
    )
    return await _obs(pool, "profile_marker", NOW - dt.timedelta(days=3), "{}", "7")


async def _blocked(pool: asyncpg.Pool) -> bool:
    return bool(
        await pool.fetchval(
            "select hard_blocked from public.provider_access"
            " where provider='fmp' and endpoint='profile'"
        )
    )


@pytest.mark.db
async def test_expired_key_blocks_the_collector_but_keeps_the_last_verified_fact(
    backend_pool: asyncpg.Pool,
) -> None:
    good = await _seed_provider(backend_pool)
    await update_coverage(
        backend_pool,
        CoverageUpdate(
            "fmp",
            "profile",
            "AAA",
            AccessResult(AccessState.AVAILABLE),
            last_good_observation_id=good,
        ),
    )
    # The key expires: a 401 arrives.  The endpoint is hard-blocked, never an empty success.
    await record_access(
        backend_pool,
        "fmp",
        "profile",
        AccessResult(AccessState.ACCESS_UNVERIFIED, "credential rejected"),
        status_code=401,
    )
    await update_coverage(
        backend_pool,
        CoverageUpdate(
            "fmp", "profile", "AAA", AccessResult(AccessState.ACCESS_UNVERIFIED, "key expired")
        ),
    )
    assert await _blocked(backend_pool) is True
    with pytest.raises(QuotaExceeded):
        await PostgresRequestGovernor(backend_pool).reserve(
            "fmp", "profile", Decimal(1), Budget(daily=Decimal(250))
        )
    # The previously verified observation and its pointer are untouched.
    row = await backend_pool.fetchrow(
        "select state,last_good_observation_id from public.coverage_status"
        " where provider='fmp' and dataset='profile' and entity_key='AAA'"
    )
    assert row is not None
    assert row["state"] == "access_unverified" and row["last_good_observation_id"] == good
    assert await backend_pool.fetchval(
        "select value from public.source_observation where id=$1", good
    ) == Decimal("7")
    # A fresh key that passes the probe re-opens the endpoint.
    await record_access(backend_pool, "fmp", "profile", AccessResult(AccessState.AVAILABLE))
    assert await _blocked(backend_pool) is False


@pytest.mark.db
async def test_removing_a_source_keeps_its_published_observations(
    backend_pool: asyncpg.Pool,
) -> None:
    good = await _seed_provider(backend_pool, "world_bank", "indicators")
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.provider_endpoint_manifest where provider='world_bank'"
        )
        == 1  # seeded by the Phase 0-2 migration
    )
    await backend_pool.execute(
        "delete from public.provider_access where provider='world_bank' and endpoint='indicators'"
    )
    # manifest rows cascade with the access row; the observation and its evidence stay.
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.provider_endpoint_manifest where provider='world_bank'"
        )
        == 0
    )
    assert (
        await backend_pool.fetchval(
            "select source from public.source_observation where id=$1", good
        )
        == "world_bank"
    )


@pytest.mark.db
async def test_temporary_failure_does_not_disable_an_endpoint(backend_pool: asyncpg.Pool) -> None:
    await _seed_provider(backend_pool)
    await record_access(
        backend_pool,
        "fmp",
        "profile",
        AccessResult(AccessState.TEMPORARILY_FAILED, "HTTP 503"),
        status_code=503,
    )
    assert await _blocked(backend_pool) is False
    assert await backend_pool.fetchval("select count(*) from public.source_observation") == 1
