"""B50: rollbacks against data-bearing databases.

A rollback must either succeed without silently destroying rows or stop with a clear
precondition error before it changes anything.  Each case runs inside a transaction that is
rolled back, so the shared migrated database is left untouched.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import asyncpg
import pytest

from tests.conftest import REPO_ROOT, MigratedDatabase

ROLLBACK_DIR = REPO_ROOT / "supabase" / "rollback"


def rollback_sql(stem: str) -> str:
    return (ROLLBACK_DIR / f"{stem}.down.sql").read_text()


class _Abort(Exception):
    pass


@pytest.fixture
async def admin(migrated_db: MigratedDatabase) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        yield conn
    finally:
        await conn.close()


async def _price_bar(conn: asyncpg.Connection, bar_type: str, close: str = "10") -> None:
    await conn.execute(
        """insert into public.price_bar(symbol,date,close,source,bar_type,session_date,currency)
           values('AAA','2026-09-01',$1::numeric,'stooq',$2,'2026-09-01','USD')""",
        close,
        bar_type,
    )


async def _run_in_rolled_back_tx(conn: asyncpg.Connection, sql: str, before: str = "") -> None:
    try:
        async with conn.transaction():
            if before:
                await conn.execute(before)
            await conn.execute(sql)
            raise _Abort
    except _Abort:
        return


@pytest.mark.db
async def test_price_bar_rollback_refuses_to_drop_raw_or_adjusted_bars_silently(
    admin: asyncpg.Connection,
) -> None:
    sql = rollback_sql("20261001111937_price_bar_basis_key")
    try:
        async with admin.transaction():
            await _price_bar(admin, "raw", "10")
            await _price_bar(admin, "adjusted", "9.5")
            with pytest.raises(asyncpg.RaiseError, match="rollback precondition failed"):
                async with admin.transaction():  # savepoint
                    await admin.execute(sql)
            assert await admin.fetchval("select count(*) from public.price_bar") == 2
            raise _Abort
    except _Abort:
        pass


@pytest.mark.db
async def test_price_bar_rollback_can_keep_adjusted_bars_when_asked(
    admin: asyncpg.Connection,
) -> None:
    sql = rollback_sql("20261001111937_price_bar_basis_key")
    try:
        async with admin.transaction():
            await _price_bar(admin, "raw", "10")
            await _price_bar(admin, "adjusted", "9.5")
            await admin.execute("set local steward.rollback_keep_adjusted = 'on'")
            await admin.execute(sql)
            rows = await admin.fetch("select bar_type, close from public.price_bar")
            assert [(r["bar_type"], str(r["close"])) for r in rows] == [("adjusted", "9.5")]
            raise _Abort
    except _Abort:
        pass


@pytest.mark.db
async def test_price_bar_rollback_keeps_every_row_when_there_is_no_conflict(
    admin: asyncpg.Connection,
) -> None:
    sql = rollback_sql("20261001111937_price_bar_basis_key")
    try:
        async with admin.transaction():
            await _price_bar(admin, "raw", "10")
            await admin.execute(
                """insert into public.price_bar(symbol,date,close,source,bar_type,
                     session_date,currency)
                   values('AAA','2026-09-02',11,'stooq','adjusted','2026-09-02','USD')"""
            )
            await admin.execute(sql)
            assert await admin.fetchval("select count(*) from public.price_bar") == 2
            raise _Abort
    except _Abort:
        pass


@pytest.mark.db
async def test_rebuilt_check_rollbacks_stop_with_a_precondition_error_not_a_raw_violation(
    admin: asyncpg.Connection,
) -> None:
    # macro_series.source allows 'cnb' today; the first free-source rollback narrows it again.
    await admin.execute(
        """insert into public.macro_series(series_id,source,period,value,url)
           values('S','cnb','2026-01-01',1,'https://x.test')"""
    )
    sql = rollback_sql("20260930182226_free_sources_phases_0_2")
    try:
        async with admin.transaction():
            with pytest.raises(asyncpg.RaiseError, match="rollback precondition failed"):
                await admin.execute(sql)
            raise _Abort
    except _Abort:
        pass
    # With the offending row gone the same precondition passes (the check only guards data).
    await admin.execute("delete from public.macro_series")
    await _run_in_rolled_back_tx(admin, "select 1")


@pytest.mark.db
async def test_source_type_rollback_reports_rows_that_use_the_newer_type(
    admin: asyncpg.Connection,
) -> None:
    await admin.execute(
        "insert into public.source(id,name,tier,type) values('ref-x','Ref',1,'reference')"
    )
    sql = rollback_sql("20261002130000_source_type_reference")
    try:
        async with admin.transaction():
            with pytest.raises(asyncpg.RaiseError, match="source_type_check"):
                await admin.execute(sql)
            raise _Abort
    except _Abort:
        pass


def test_every_rollback_that_restores_a_check_is_loud_or_discloses_its_deletions() -> None:
    """Re-adding a narrower CHECK fails on rows using a newer value.  The rollback must either
    stop with a clear PRECONDITION error before changing anything, or delete the offending rows
    explicitly and say so (DATA LOSS / DELETES), never leave the raw constraint violation."""
    offenders = []
    for path in sorted(ROLLBACK_DIR.glob("*.down.sql")):
        text = path.read_text()
        restores_check = re.search(r"add constraint \w+\s+check", text, re.IGNORECASE)
        if not restores_check:
            continue
        loud = "PRECONDITION" in text
        discloses = "delete from" in text.lower() and ("DATA LOSS" in text or "DELETES" in text)
        if not (loud or discloses):
            offenders.append(path.name)
    assert offenders == []


def test_no_rollback_claims_no_data_loss_while_it_can_destroy_rows() -> None:
    text = rollback_sql("20261001111937_price_bar_basis_key")
    assert "No row data is lost" not in text and "PRECONDITION" in text
