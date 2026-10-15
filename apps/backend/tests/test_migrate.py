"""The migration runner: applies each file once, in order, and baselines a copied database."""

import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from steward import migrate
from tests.conftest import _with_database

pytestmark = pytest.mark.db


@pytest.fixture
async def empty_db_url() -> AsyncIterator[str]:
    base_url = os.environ.get("STEWARD_TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("STEWARD_TEST_DATABASE_URL is not set")
    name = f"steward_mig_{secrets.token_hex(4)}"
    admin = await asyncpg.connect(base_url)
    await admin.execute(f'create database "{name}"')
    await admin.close()
    try:
        yield _with_database(base_url, name)
    finally:
        admin = await asyncpg.connect(base_url)
        await admin.execute(f'drop database "{name}" with (force)')
        await admin.close()


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql)


async def _versions(url: str) -> list[tuple[str, bool]]:
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch("select version, baseline from private.schema_migration order by 1")
        return [(r["version"], r["baseline"]) for r in rows]
    finally:
        await conn.close()


async def test_applies_in_order_once(empty_db_url: str, tmp_path: Path) -> None:
    _write(tmp_path, "20300101000000_b.sql", "alter table t add column y int;")
    _write(tmp_path, "20290101000000_a.sql", "create table t (x int);")
    assert await migrate.run(empty_db_url, tmp_path) == 0
    assert [v for v, _ in await _versions(empty_db_url)] == [
        "20290101000000_a",
        "20300101000000_b",
    ]
    # A second run has nothing to do (and would fail if it re-ran `create table t`).
    assert await migrate.run(empty_db_url, tmp_path) == 0


async def test_failed_file_rolls_back_and_stops(empty_db_url: str, tmp_path: Path) -> None:
    _write(tmp_path, "20290101000000_a.sql", "create table t (x int);")
    _write(tmp_path, "20290102000000_bad.sql", "create table u (x int); select 1/0;")
    _write(tmp_path, "20290103000000_c.sql", "create table w (x int);")
    assert await migrate.run(empty_db_url, tmp_path) == 1
    conn = await asyncpg.connect(empty_db_url)
    try:
        assert await conn.fetchval("select to_regclass('public.t') is not null")
        assert not await conn.fetchval("select to_regclass('public.u') is not null")
        assert not await conn.fetchval("select to_regclass('public.w') is not null")
    finally:
        await conn.close()
    assert [v for v, _ in await _versions(empty_db_url)] == ["20290101000000_a"]
    # Fixing the file lets the next run continue from it.
    _write(tmp_path, "20290102000000_bad.sql", "create table u (x int);")
    assert await migrate.run(empty_db_url, tmp_path) == 0
    assert len(await _versions(empty_db_url)) == 3


async def test_copied_database_is_baselined(
    empty_db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(migrate, "BASELINE", "20290102000000")
    conn = await asyncpg.connect(empty_db_url)
    try:
        await conn.execute("create table public.job_queue (id int)")
    finally:
        await conn.close()
    _write(tmp_path, "20290101000000_old.sql", "create table must_not_run (x int);")
    _write(tmp_path, "20290102000000_old2.sql", "create table must_not_run2 (x int);")
    _write(tmp_path, "20290103000000_new.sql", "create table ran (x int);")
    assert await migrate.run(empty_db_url, tmp_path) == 0
    assert await _versions(empty_db_url) == [
        ("20290101000000_old", True),
        ("20290102000000_old2", True),
        ("20290103000000_new", False),
    ]
    conn = await asyncpg.connect(empty_db_url)
    try:
        assert await conn.fetchval("select to_regclass('public.ran') is not null")
        assert not await conn.fetchval("select to_regclass('public.must_not_run') is not null")
    finally:
        await conn.close()
