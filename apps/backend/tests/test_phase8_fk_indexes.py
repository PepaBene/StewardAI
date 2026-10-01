"""P8-14: every foreign key has an index that starts with its columns.

A foreign key without a covering index makes every delete or key update on the referenced table
scan the referencing table, and every join through the key a sequential scan.  This test fails
when a NEW foreign key lacks a covering index.  Keys that were already unindexed when the test
was written are listed in KNOWN_UNINDEXED (audit B52); the core stream's index migration
(20261005100xxx_..._indexes) removes them, and this test tolerates that landing: entries that
have gained an index are simply no longer reported, and once a migration whose name contains
"fk" and "index" exists, the list must be empty.
"""

from __future__ import annotations

import re

import asyncpg
import pytest

from tests.conftest import MIGRATIONS_DIR, MigratedDatabase

# (table, constraint) pairs that had no covering index when this test was written.
KNOWN_UNINDEXED: frozenset[tuple[str, str]] = frozenset()

FK_QUERY = """
select c.conrelid::regclass::text as tbl, c.conname, c.conkey::int2[] as cols,
       c.conrelid as relid
  from pg_constraint c join pg_namespace n on n.oid = c.connamespace
 where c.contype = 'f' and n.nspname = 'public'
"""
INDEX_QUERY = """
select i.indrelid as relid, i.indkey::int2[] as cols, i.indisvalid,
       pg_get_expr(i.indpred, i.indrelid) as predicate
  from pg_index i
  join pg_class t on t.oid = i.indrelid
  join pg_namespace n on n.oid = t.relnamespace
 where n.nspname = 'public'
"""


# `where fk_col is not null` loses nothing: a NULL key never matches a lookup.  Any other partial
# index may not contain the rows a delete on the parent has to find.
_NOT_NULL = re.compile(r"\s*\w+ IS NOT NULL\s*")


def usable_predicate(predicate: str | None) -> bool:
    if predicate is None:
        return True
    parts = re.split(r"\s+AND\s+", predicate.replace("(", " ").replace(")", " ").strip())
    return all(_NOT_NULL.fullmatch(part) for part in parts)


def covered(fk_cols: list[int], index_cols: list[int]) -> bool:
    """An index covers a key when its leading columns are exactly the key's columns (any order)."""
    lead = index_cols[: len(fk_cols)]
    return 0 not in lead and set(lead) == set(fk_cols) and len(lead) == len(fk_cols)


async def unindexed_foreign_keys(conn: asyncpg.Connection) -> set[tuple[str, str]]:
    indexes: dict[int, list[list[int]]] = {}
    for r in await conn.fetch(INDEX_QUERY):
        if r["indisvalid"] and usable_predicate(r["predicate"]):
            indexes.setdefault(r["relid"], []).append(list(r["cols"]))
    missing = set()
    for fk in await conn.fetch(FK_QUERY):
        if not any(covered(list(fk["cols"]), idx) for idx in indexes.get(fk["relid"], [])):
            missing.add((fk["tbl"], fk["conname"]))
    return missing


def test_partial_index_rule() -> None:
    assert usable_predicate(None)
    assert usable_predicate("(news_item_id IS NOT NULL)")
    assert usable_predicate("((a IS NOT NULL) AND (b IS NOT NULL))")
    assert not usable_predicate("(status = 'open'::text)")


def test_covering_rule() -> None:
    assert covered([2], [2, 3])
    assert covered([2, 3], [3, 2, 9])
    assert not covered([2, 3], [2])  # prefix shorter than the key
    assert not covered([3], [2, 3])  # key column is not leading
    assert not covered([2], [0, 2])  # expression column
    assert not covered([2], [])


def core_index_migration_landed() -> bool:
    return any(
        "fk" in p.name and "index" in p.name and p.name >= "20261005100000"
        for p in MIGRATIONS_DIR.glob("*.sql")
    )


@pytest.mark.db
async def test_every_foreign_key_has_a_covering_index(migrated_db: MigratedDatabase) -> None:
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        missing = await unindexed_foreign_keys(conn)
    finally:
        await conn.close()
    new = sorted(missing - KNOWN_UNINDEXED)
    assert new == [], (
        "foreign keys without a covering index (add `create index ... on <table>(<fk columns>)` "
        f"in the migration that adds the key): {new}"
    )
    if core_index_migration_landed():
        assert sorted(missing) == [], "the FK index migration landed: no foreign key may be left"


@pytest.mark.db
async def test_the_check_notices_a_new_unindexed_key(migrated_db: MigratedDatabase) -> None:
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        await conn.execute("create table public.zz_parent(id bigint primary key)")
        await conn.execute(
            "create table public.zz_child(id bigint primary key,"
            " parent_id bigint references public.zz_parent(id))"
        )
        assert ("zz_child", "zz_child_parent_id_fkey") in await unindexed_foreign_keys(conn)
        await conn.execute("create index zz_child_parent on public.zz_child(parent_id)")
        assert ("zz_child", "zz_child_parent_id_fkey") not in await unindexed_foreign_keys(conn)
    finally:
        await conn.close()
