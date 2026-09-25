"""Migrations apply cleanly, and the access model holds for every role.

Phase 0 AC: connecting as app_backend can read/write the foundation tables and cannot
read tables it wasn't granted.
"""

import json
import uuid

import asyncpg
import pytest

from tests.conftest import MigratedDatabase

pytestmark = pytest.mark.db

OWNER = uuid.UUID("00000000-0000-0000-0000-00000000000a")
STRANGER = uuid.UUID("00000000-0000-0000-0000-00000000000b")


async def test_all_migrations_apply(migrated_db: MigratedDatabase) -> None:
    assert "20260925000100_app_backend_role.sql" in migrated_db.applied
    assert "20260925000300_foundation_tables.sql" in migrated_db.applied


async def test_backend_reads_and_writes_foundation_tables(migrated_db: MigratedDatabase) -> None:
    conn = await asyncpg.connect(migrated_db.backend_url)
    try:
        assert await conn.fetchval("select current_user") == "app_backend"
        first = await conn.fetchval(
            "insert into config_version (domain, body, schema_version, active)"
            " values ('strategy', $1::jsonb, 1, true) returning id",
            json.dumps({"base_currency": "CZK"}),
        )
        # Activating a new version: deactivate the old one first, in one transaction.
        async with conn.transaction():
            await conn.execute(
                "update config_version set active = false where domain = 'strategy' and active"
            )
            await conn.execute(
                "insert into config_version (domain, body, schema_version, parent_id, active)"
                " values ('strategy', $1::jsonb, 1, $2, true)",
                json.dumps({"base_currency": "EUR"}),
                first,
            )
        active = await conn.fetchval(
            "select body->>'base_currency' from config_version where domain = 'strategy' and active"
        )
        assert active == "EUR"

        await conn.execute(
            "insert into instrument (t212_ticker, isin, name, currency)"
            " values ('AAPL_US_EQ', 'US0378331005', 'Apple', 'USD')"
        )
        await conn.execute(
            "update instrument set cik = '0000320193' where t212_ticker = 'AAPL_US_EQ'"
        )
        row = await conn.fetchrow("select * from instrument where t212_ticker = 'AAPL_US_EQ'")
        assert row is not None and row["cik"] == "0000320193"
        assert row["updated_at"] >= row["created_at"]
        await conn.execute("delete from instrument where t212_ticker = 'AAPL_US_EQ'")
    finally:
        await conn.close()


@pytest.mark.parametrize(
    "query",
    [
        "select * from public.frontend_only",
        "select * from auth.users",
        "select * from private.app_owner",
        "delete from config_version",
        "create table public.sneaky (id int)",
    ],
)
async def test_backend_is_denied_everything_not_granted(
    migrated_db: MigratedDatabase, query: str
) -> None:
    conn = await asyncpg.connect(migrated_db.backend_url)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(query)
    finally:
        await conn.close()


async def test_config_versions_are_immutable(migrated_db: MigratedDatabase) -> None:
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        version_id = await conn.fetchval(
            "insert into config_version (domain, body, schema_version, active)"
            " values ('theses', '{}'::jsonb, 1, true) returning id"
        )
        with pytest.raises(asyncpg.RaiseError, match="immutable"):
            await conn.execute(
                "update config_version set body = '{\"x\": 1}' where id = $1", version_id
            )
        with pytest.raises(asyncpg.RaiseError, match="cannot be deleted"):
            await conn.execute("delete from config_version where id = $1", version_id)
        await conn.execute("update config_version set active = false where id = $1", version_id)
    finally:
        await conn.close()


async def test_one_active_version_per_domain(migrated_db: MigratedDatabase) -> None:
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        insert = (
            "insert into config_version (domain, body, schema_version, active)"
            " values ('jobs', '{}'::jsonb, 1, true)"
        )
        await conn.execute(insert)
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(insert)
    finally:
        await conn.close()


@pytest.mark.parametrize(
    ("domain", "body"),
    [("not_a_domain", "{}"), ("strategy", "[]")],
)
async def test_config_version_rejects_bad_rows(
    migrated_db: MigratedDatabase, domain: str, body: str
) -> None:
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "insert into config_version (domain, body, schema_version)"
                " values ($1, $2::jsonb, 1)",
                domain,
                body,
            )
    finally:
        await conn.close()


async def _as_frontend(conn: asyncpg.Connection, role: str, user_id: uuid.UUID | None) -> None:
    await conn.execute(f"set role {role}")
    await conn.execute(
        "select set_config('request.jwt.claim.sub', $1, false)", str(user_id) if user_id else ""
    )


async def test_frontend_reads_only_as_owner(migrated_db: MigratedDatabase) -> None:
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        await conn.execute(
            "insert into instrument (t212_ticker, name) values ('ASML_EQ', 'ASML');"
            "insert into config_version (domain, body, schema_version) values ('budget', '{}', 1);"
        )
        await conn.execute("insert into private.app_owner (user_id) values ($1)", OWNER)

        await _as_frontend(conn, "authenticated", STRANGER)
        assert await conn.fetchval("select count(*) from instrument") == 0
        assert await conn.fetchval("select count(*) from config_version") == 0

        await _as_frontend(conn, "authenticated", OWNER)
        assert await conn.fetchval("select count(*) from instrument") == 1
        assert await conn.fetchval("select count(*) from config_version") == 1
        # Writes go through the API, never directly from the browser.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("insert into instrument (t212_ticker) values ('X_EQ')")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "insert into config_version (domain, body, schema_version)"
                " values ('budget', '{}', 1)"
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("select * from private.app_owner")

        await conn.execute("reset role")
        await _as_frontend(conn, "anon", None)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("select * from instrument")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("select * from config_version")
    finally:
        await conn.close()


async def test_only_one_owner(migrated_db: MigratedDatabase) -> None:
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        await conn.execute("insert into private.app_owner (user_id) values ($1)", OWNER)
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute("insert into private.app_owner (user_id) values ($1)", STRANGER)
    finally:
        await conn.close()


async def test_pgmq_enabled_and_usable_by_backend(migrated_db: MigratedDatabase) -> None:
    """Phase 0 AC: `select * from pgmq.list_queues();` returns empty with no error.

    Runs where pgmq is installed (CI uses a pgmq Postgres image; Supabase ships it)."""
    if "20260925000200_enable_pgmq.sql" not in migrated_db.applied:
        pytest.skip("pgmq extension not available on this server")
    backend = await asyncpg.connect(migrated_db.backend_url)
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        assert await backend.fetch("select * from pgmq.list_queues()") == []
        # Queues are created by migrations (as the owner); the backend then uses them.
        await admin.execute("select pgmq.create('phase0_check')")
        msg_id = await backend.fetchval(
            "select * from pgmq.send('phase0_check', $1::jsonb)", json.dumps({"job": "noop"})
        )
        row = await backend.fetchrow("select * from pgmq.read('phase0_check', 30, 1)")
        assert row is not None and row["msg_id"] == msg_id
        assert await backend.fetchval("select pgmq.delete('phase0_check', $1::bigint)", msg_id)
    finally:
        await backend.close()
        await admin.close()
