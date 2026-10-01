import ipaddress
import os
import secrets
import socket
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


# ---------------------------------------------------------------------------------------
# Offline guard (plan D8.1): CI must never need third-party keys or the internet. Any
# attempt to open a socket to a non-local address fails the test that made it. Loopback and
# unix sockets (the test Postgres, in-process ASGI) stay allowed. Set STEWARD_ALLOW_NETWORK=1
# for an explicit, budgeted live check run by hand.
# ---------------------------------------------------------------------------------------
class NetworkAccessBlocked(AssertionError):
    pass


def _is_local_host(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    if not isinstance(host, str) or host in {"", "localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _blocked(host: object) -> NetworkAccessBlocked:
    return NetworkAccessBlocked(
        f"test attempted real network access to {host!r}; use recorded fixtures "
        "(httpx.MockTransport) - CI must not need the internet or provider keys"
    )


# Credentials and connection strings of the developer's real accounts: tests must neither read
# nor use them (a stray DATABASE_URL would make "unconfigured" API paths dial a real database).
PROVIDER_KEY_ENV = (
    "DATABASE_URL",
    "SUPABASE_SECRET_KEY",
    "SUPABASE_JWT_SECRET",
    "T212_API_KEY",
    "T212_API_SECRET",
    "ANTHROPIC_API_KEY",
    "FINNHUB_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "EODHD_API_KEY",
    "FMP_API_KEY",
    "TWELVE_DATA_API_KEY",
    "UN_COMTRADE_API_KEY",
    "PEOPLE_DATA_LABS_API_KEY",
    "DIFFBOT_API_KEY",
    "EPO_OPS_CLIENT_ID",
    "EPO_OPS_CLIENT_SECRET",
    "FRED_API_KEY",
    "OPENFIGI_API_KEY",
    "STOOQ_API_KEY",
)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if os.environ.get("STEWARD_ALLOW_NETWORK") == "1":
        yield
        return
    for name in PROVIDER_KEY_ENV:
        monkeypatch.delenv(name, raising=False)
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: object) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
            if not _is_local_host(address[0]):
                raise _blocked(address[0])
        real_connect(self, address)  # type: ignore[arg-type]

    def guarded_connect_ex(self: socket.socket, address: object) -> int:
        if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
            if not _is_local_host(address[0]):
                raise _blocked(address[0])
        return real_connect_ex(self, address)  # type: ignore[arg-type]

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object) -> object:
        if not _is_local_host(host):
            raise _blocked(host)
        return real_getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    yield


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
