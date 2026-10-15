"""Apply the SQL migrations to the database: `python -m steward.migrate [--status]`.

Railway runs it as the backend's pre-deploy command, so a deploy that needs a new column or
table either gets it first or does not go out. Each file in the migrations directory is applied
once, in name order, in its own transaction, and recorded in `private.schema_migration`.
A second runner that starts at the same time waits on an advisory lock.

It connects as the database owner (`MIGRATION_DATABASE_URL`), not as `app_backend`, which cannot
create objects. Nothing here rolls back: a failed file leaves the earlier ones applied, stops
the run with a non-zero exit, and the next run retries that file. The matching down files live
in `supabase/rollback` and are run by hand.

The first run against a database that already holds the app (it came over from Supabase with
everything up to `BASELINE` applied) records those files as applied without running them.
"""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

# The last migration the database copied from Supabase already contained.
BASELINE = "20261129100000"
LOCK_KEY = 7_303_122_011  # arbitrary, shared by every runner of this app


def default_dir() -> Path:
    """`/app/migrations` in the image; `supabase/migrations` in a checkout."""
    configured = os.environ.get("MIGRATIONS_DIR")
    if configured:
        return Path(configured)
    for candidate in (
        Path("/app/migrations"),
        Path(__file__).resolve().parents[4] / "supabase" / "migrations",
    ):
        if candidate.is_dir():
            return candidate
    raise SystemExit("no migrations directory: set MIGRATIONS_DIR")


def _sql_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.sql"))


def _read(path: Path) -> str:
    return path.read_text()


def version_of(path: Path) -> str:
    """The key a file is recorded under: its name (two files can share a timestamp)."""
    return path.stem


def _stamp(path: Path) -> str:
    return path.name.split("_", 1)[0]


async def applied_versions(conn: asyncpg.Connection) -> set[str]:
    await conn.execute(
        """
        create schema if not exists private;
        create table if not exists private.schema_migration (
            version text primary key,
            name text not null,
            applied_at timestamptz not null default now(),
            baseline boolean not null default false
        )
        """
    )
    return {r["version"] for r in await conn.fetch("select version from private.schema_migration")}


async def record_baseline(conn: asyncpg.Connection, files: list[Path]) -> list[Path]:
    """Mark the files up to BASELINE as applied; returns the ones marked."""
    old = [p for p in files if _stamp(p) <= BASELINE]
    await conn.executemany(
        "insert into private.schema_migration (version, name, baseline) values ($1, $2, true) "
        "on conflict do nothing",
        [(version_of(p), p.name) for p in old],
    )
    return old


async def run(url: str, directory: Path, *, status_only: bool = False) -> int:
    files = _sql_files(directory)
    if not files:
        print(f"no migrations in {directory}", file=sys.stderr)
        return 1
    conn = await asyncpg.connect(url)
    try:
        await conn.execute("select pg_advisory_lock($1)", LOCK_KEY)
        applied = await applied_versions(conn)
        has_app = await conn.fetchval("select to_regclass('public.job_queue') is not null")
        if not applied and has_app:
            if status_only:
                applied = {version_of(p) for p in files if _stamp(p) <= BASELINE}
                print(f"would baseline {len(applied)} files")
            else:
                marked = await record_baseline(conn, files)
                print(f"baseline: {len(marked)} files up to {BASELINE} recorded as applied")
                applied = {version_of(p) for p in marked}
        elif not applied:
            print("empty database: applying every migration", file=sys.stderr)
        pending = [p for p in files if version_of(p) not in applied]
        if status_only:
            print(f"{len(pending)} pending")
            for p in pending:
                print(f"  {p.name}")
            return 0
        for p in pending:
            print(f"applying {p.name}", flush=True)
            try:
                async with conn.transaction():
                    await conn.execute(_read(p))
                    await conn.execute(
                        "insert into private.schema_migration (version, name) values ($1, $2)",
                        version_of(p),
                        p.name,
                    )
            except Exception as exc:
                print(f"FAILED {p.name}: {exc}", file=sys.stderr)
                return 1
        print(f"done: {len(pending)} applied")
        return 0
    finally:
        await conn.close()


def main() -> None:
    url = os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        raise SystemExit("MIGRATION_DATABASE_URL is not set")
    sys.exit(asyncio.run(run(url, default_dir(), status_only="--status" in sys.argv)))


if __name__ == "__main__":
    main()
