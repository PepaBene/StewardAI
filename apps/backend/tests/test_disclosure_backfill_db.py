"""The backfill reads only recent, archived, unquarantined SEC versions with no linked facts."""

from typing import Any

import asyncpg
import pytest

from steward.disclosure.jobs import backfill_stored

pytestmark = pytest.mark.db


class Store:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.reads: list[str] = []

    async def get(self, path: str) -> bytes:
        self.reads.append(path)
        return self.data


async def version(pool: asyncpg.Pool, *, archive: str | None, quarantined: bool) -> int:
    issuer = await pool.fetchval(
        "insert into public.issuer (legal_name) values ('Acme') returning id"
    )
    doc = await pool.fetchval(
        """insert into public.source_document (regulator, external_id, stable_url, issuer_id, form)
           values ('SEC', $1, 'https://sec.example/x', $2, '10-K') returning id""",
        f"0001-26-{issuer}",
        issuer,
    )
    return await pool.fetchval(
        """insert into public.source_document_version
             (document_id, content_hash, known_at, archive_path, quarantined, quarantine_reason)
           values ($1, md5(random()::text), now(), $2, $3, $4) returning id""",
        doc,
        archive,
        quarantined,
        "unparseable" if quarantined else None,
    )


async def test_only_archived_unquarantined_versions_are_read(backend_pool: asyncpg.Pool) -> None:
    await version(backend_pool, archive=None, quarantined=False)
    await version(backend_pool, archive="a/b.html", quarantined=True)
    wanted = await version(backend_pool, archive="c/d.html", quarantined=False)
    store: Any = Store(b"<html><body>no facts here</body></html>")
    out = await backfill_stored(backend_pool, store)
    assert store.reads == ["c/d.html"], wanted
    assert out["checked"] == 1 and out["mirrored"] == 0
