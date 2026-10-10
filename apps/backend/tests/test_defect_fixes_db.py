"""Migration 20261008100000: relabelling keeps the first label; one source can be backed out."""

import json
from types import SimpleNamespace

import asyncpg
import pytest

from steward.disclosure import adjudication, foreign_job
from tests.test_disclosure_esef_discovery import LEI_A, entry
from tests.test_disclosure_foreign_job_db import got, lei_issuer

pytestmark = pytest.mark.db


async def test_a_relabel_keeps_the_label_it_replaced(backend_pool: asyncpg.Pool) -> None:
    key = {
        "dataset": "relationship_claim",
        "sample_key": "s1",
        "item_ref": "claim:1",
        "machine_value": "true_relationship",
        "by": "pat",
    }
    first = await adjudication.record(backend_pool, human_value="ambiguous", **key)
    second = await adjudication.record(backend_pool, human_value="negated", **key)
    assert first == second
    kept = await backend_pool.fetch(
        "select human_value from public.disclosure_adjudication_history where adjudication_id = $1",
        first,
    )
    assert [r["human_value"] for r in kept] == ["ambiguous"]
    current = await backend_pool.fetchval(
        "select human_value from public.disclosure_adjudication where id = $1", first
    )
    assert current == "negated"
    with pytest.raises(asyncpg.PostgresError):
        await backend_pool.execute("delete from public.disclosure_adjudication_history")


async def test_one_source_can_be_backed_out_and_nothing_else_is_touched(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer = await lei_issuer(backend_pool)
    index = json.dumps({"data": [entry("f1")]}).encode()

    async def fetch(url: str) -> SimpleNamespace:
        return got(index if "/entities/" in url else b'{"facts": {}}')

    await foreign_job.collect_esef(backend_pool, [{"id": issuer, "lei": LEI_A}], fetch, fetch)
    with pytest.raises(asyncpg.PostgresError):
        await backend_pool.execute("delete from public.source_document where regulator = 'ESEF'")
    report = json.loads(await backend_pool.fetchval("select public.purge_source_documents('ESEF')"))
    # The report keys are schema-qualified table names (purge_rows runs with an empty search path).
    assert report["public.source_document"] == 1
    assert report["public.source_document_version"] >= 1
    left = await backend_pool.fetchval(
        "select count(*) from public.source_document where regulator = 'ESEF'"
    )
    assert left == 0
