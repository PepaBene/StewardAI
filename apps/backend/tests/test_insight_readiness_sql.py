"""The insight data-readiness query (docs/data/insight_data_readiness.sql) runs against the
migrated schema, so a renamed table or column fails here rather than in front of the owner.
Needs STEWARD_TEST_DATABASE_URL (skipped otherwise)."""

from pathlib import Path

import asyncpg
import pytest

from tests.conftest import MigratedDatabase

pytestmark = pytest.mark.db

SQL = Path(__file__).resolve().parents[3] / "docs" / "data" / "insight_data_readiness.sql"


def statements(text: str) -> list[str]:
    """The SQL statements, without comments and psql meta-commands (\\echo, \\pset)."""
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("--", "\\"))
    )
    return [s.strip() for s in body.split(";") if s.strip()]


async def test_readiness_query_runs_on_the_schema(migrated_db: MigratedDatabase) -> None:
    parts = statements(SQL.read_text(encoding="utf-8"))
    assert len(parts) >= 7
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        for sql in parts:
            assert sql.lower().startswith(("select", "with")), sql[:60]
            await conn.fetch(sql)
    finally:
        await conn.close()
