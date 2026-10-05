"""D8.4: a backup restores to the same data.

Dumps the migrated test database with pg_dump, restores it into a second throwaway database and
checks that every public table has the same row count and that a stored price bar survives with
its exact value. Needs STEWARD_TEST_DATABASE_URL (like every DB test) and the PostgreSQL client
tools (`pg_dump`, `pg_restore`) on PATH; without them the test skips. The retention half of D8.4
(raw payloads expire, normalized observations stay) is covered in test_lifecycle_phase8.py.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit

import asyncpg
import pytest

from tests.conftest import MigratedDatabase, _with_database


def _client_env(url: str) -> dict[str, str]:
    """libpq settings for `url`, via the environment so no password is on a command line."""
    parts = urlsplit(url)
    env = {**os.environ, "PGHOST": parts.hostname or "localhost", "PGPORT": str(parts.port or 5432)}
    if parts.username:
        env["PGUSER"] = unquote(parts.username)
    if parts.password:
        env["PGPASSWORD"] = unquote(parts.password)
    return env


async def _run(*args: str, env: dict[str, str], ok: tuple[int, ...] = (0,)) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    assert proc.returncode in ok, f"{args[0]} failed: {err.decode(errors='replace')[-800:]}"


async def _table_counts(url: str) -> dict[str, int]:
    conn = await asyncpg.connect(url)
    try:
        tables = await conn.fetch(
            "select tablename from pg_tables where schemaname='public' order by tablename"
        )
        return {
            # Names come from pg_tables of the throwaway test database, not from input.
            t["tablename"]: await conn.fetchval(
                f'select count(*) from public."{t["tablename"]}"'  # noqa: S608
            )
            for t in tables
        }
    finally:
        await conn.close()


@pytest.mark.db
async def test_dump_and_restore_round_trips_every_public_table(
    migrated_db: MigratedDatabase, tmp_path: Path
) -> None:
    pg_dump, pg_restore = shutil.which("pg_dump"), shutil.which("pg_restore")
    if not pg_dump or not pg_restore:
        pytest.skip("pg_dump / pg_restore are not installed")

    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        await conn.execute(
            """insert into public.price_bar(symbol,date,close,source,bar_type,session_date,currency)
               values('BAK','2026-09-01',123.456789,'stooq','raw','2026-09-01','USD')"""
        )
    finally:
        await conn.close()
    before = await _table_counts(migrated_db.admin_url)
    assert before["price_bar"] >= 1

    base_url = os.environ["STEWARD_TEST_DATABASE_URL"]
    restored = f"steward_restore_{secrets.token_hex(4)}"
    restored_url = _with_database(base_url, restored)
    dump = tmp_path / "backup.dump"
    source_db = urlsplit(migrated_db.admin_url).path.lstrip("/")

    await _run(
        pg_dump,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "-f",
        str(dump),
        source_db,
        env=_client_env(base_url),
    )
    admin = await asyncpg.connect(base_url)
    try:
        await admin.execute(f'create database "{restored}"')
        # The Supabase shim's roles and schemas exist cluster-wide; the dump brings the rest.
        # Exit code 1 means "errors ignored" (e.g. an extension the server lacks); the row-count
        # comparison below is the actual assertion that nothing was lost.
        await _run(
            pg_restore,
            "--no-owner",
            "--no-privileges",
            "-d",
            restored,
            str(dump),
            env=_client_env(base_url),
            ok=(0, 1),
        )
        after = await _table_counts(restored_url)
        assert after == before

        check = await asyncpg.connect(restored_url)
        try:
            close = await check.fetchval("select close from public.price_bar where symbol='BAK'")
        finally:
            await check.close()
        assert str(close) == "123.456789"
    finally:
        await admin.execute(f'drop database if exists "{restored}" with (force)')
        await admin.close()
