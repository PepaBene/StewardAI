import os
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
SHIM = Path(__file__).parent / "sql" / "supabase_shim.sql"

# Alert channel credentials from the developer's environment must not change what tests see
# (a test that routes alerts would otherwise treat email or Telegram as configured).
CHANNEL_ENV = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "RESEND_API_KEY",
    "ALERT_EMAIL_FROM",
    "ALERT_EMAIL_TO",
)


@pytest.fixture(autouse=True)
def _no_channel_credentials(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from steward.settings import get_settings

    for name in CHANNEL_ENV:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@dataclass(frozen=True)
class MigratedDatabase:
    admin_url: str
    backend_url: str
    applied: list[str]
    skipped: list[str]


def _with_database(url: str, database: str, user: str | None = None, pw: str | None = None) -> str:
    parts = urlsplit(url)
    netloc = parts.netloc
    if user is not None:
        host = netloc.rsplit("@", 1)[-1]
        netloc = f"{user}:{pw}@{host}"
    return urlunsplit((parts.scheme, netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
async def migrated_db() -> AsyncIterator[MigratedDatabase]:
    """A fresh database with the Supabase shim and every migration applied.

    Needs STEWARD_TEST_DATABASE_URL pointing at a Postgres superuser, e.g.
    postgresql://postgres:postgres@localhost:5432/postgres. The pgmq migration is skipped
    when the server doesn't have the extension available (plain Postgres); Supabase does.
    """
    base_url = os.environ.get("STEWARD_TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("STEWARD_TEST_DATABASE_URL is not set")

    name = f"steward_test_{secrets.token_hex(4)}"
    admin = await asyncpg.connect(base_url)
    await admin.execute(f'create database "{name}"')
    await admin.close()

    admin_url = _with_database(base_url, name)
    backend_password = secrets.token_urlsafe(16)
    applied: list[str] = []
    skipped: list[str] = []
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(SHIM.read_text())
        has_pgmq = await conn.fetchval(
            "select exists (select 1 from pg_available_extensions where name = 'pgmq')"
        )
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if "pgmq" in migration.name and not has_pgmq:
                skipped.append(migration.name)
                continue
            async with conn.transaction():
                await conn.execute(migration.read_text())
            applied.append(migration.name)
        # What the README tells the operator to run after migrating.
        await conn.execute(f"alter role app_backend with login password '{backend_password}'")
    finally:
        await conn.close()

    try:
        yield MigratedDatabase(
            admin_url=admin_url,
            backend_url=_with_database(base_url, name, "app_backend", backend_password),
            applied=applied,
            skipped=skipped,
        )
    finally:
        admin = await asyncpg.connect(base_url)
        await admin.execute(f'drop database "{name}" with (force)')
        await admin.close()


@pytest.fixture
async def backend_pool(migrated_db: MigratedDatabase) -> AsyncIterator[asyncpg.Pool]:
    """A pool connected as app_backend, the way the API and worker connect."""
    pool = await asyncpg.create_pool(migrated_db.backend_url, min_size=1, max_size=8)
    try:
        yield pool
    finally:
        await pool.close()
