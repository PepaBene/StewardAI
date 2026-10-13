"""Journal gaps: which detected trades still need the owner's reasons."""

import datetime as dt

import asyncpg
import pytest

from steward.journal import entries as journal_entries
from steward.ops import journal_gaps as jg
from steward.ops import tasks
from tests.conftest import MigratedDatabase

DAY = dt.date.today() - dt.timedelta(days=1)  # the journal refuses a date in the future


def fill(ticker: str, side: str, days_ago: int = 0) -> jg.Fill:
    return jg.Fill(ticker, side, DAY - dt.timedelta(days=days_ago), 1000.0, "CZK")


@pytest.mark.db
async def test_detected_trades_are_logged_once_and_never_graded(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        owner = str(
            await admin.fetchval(
                "insert into auth.users (id, email) values (gen_random_uuid(), 'o@example.com')"
                " returning id"
            )
        )
    finally:
        await admin.close()

    async def trade(fill_id: str, side: str, ticker: str, days_ago: int) -> None:
        when = dt.datetime.combine(DAY - dt.timedelta(days=days_ago), dt.time(10), tzinfo=dt.UTC)
        await backend_pool.execute(
            "insert into public.transaction (id, type, subtype, ticker, quantity, price, amount,"
            " currency, occurred_at, raw) values ($1, 'order', $2, $3, 1, 100, 100, 'USD', $4,"
            " '{}'::jsonb)",
            fill_id,
            side,
            ticker,
            when,
        )

    await trade("fill:1", "SELL", "OLD_US_EQ", 1)
    await trade("fill:2", "BUY", "NEW_US_EQ", 0)
    await trade("fill:3", "BUY", "AAPL_US_EQ", 10)

    out = await jg.check_journal_gaps(backend_pool, DAY, owner)
    assert out["logged"] == {"entries": 1, "switches": 1}
    again = await jg.check_journal_gaps(backend_pool, DAY, owner)
    assert again["logged"] == {"entries": 0, "switches": 0}
    assert len(again["gaps"]) == 2  # still asking for the owner's reasons

    entries = await journal_entries.list_entries(backend_pool, owner)
    assert [e.ticker for e in entries] == ["AAPL_US_EQ"]
    assert not journal_entries.written_by_owner(entries[0])
    row = await backend_pool.fetchrow("select snapshot from public.switch_log")
    assert row is not None and "auto" in row["snapshot"]


def test_a_trade_with_a_journal_entry_nearby_is_explained() -> None:
    assert jg.find_gaps([fill("A", "BUY")], [("A", DAY - dt.timedelta(days=2))], []) == []
    gaps = jg.find_gaps([fill("A", "BUY")], [("A", DAY - dt.timedelta(days=9))], [])
    assert [g.title for g in gaps] == ["Write down why you bought A"]


def test_a_sell_and_a_buy_of_different_stocks_make_one_switch_notice() -> None:
    gaps = jg.find_gaps([fill("A", "SELL", 1), fill("B", "BUY")], [], [])
    assert [g.title for g in gaps] == ["Did you switch A into B?"]
    logged = [("A", "B", DAY)]
    assert jg.find_gaps([fill("A", "SELL", 1), fill("B", "BUY")], [], logged) == []


def test_two_buys_are_not_a_switch_and_far_apart_trades_are_two_notices() -> None:
    assert len(jg.find_gaps([fill("A", "BUY"), fill("B", "BUY")], [], [])) == 2
    far = jg.find_gaps([fill("A", "SELL", 8), fill("B", "BUY")], [], [])
    assert [g.title for g in far] == ["Write down why you sold A", "Write down why you bought B"]


@pytest.mark.db
async def test_a_detected_trade_opens_a_notice_and_a_journal_entry_clears_it(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    when = dt.datetime.combine(DAY, dt.time(10), tzinfo=dt.UTC)
    for i in (1, 2):  # one order filled in two parts is still one notice
        await backend_pool.execute(
            "insert into public.transaction (id, type, subtype, ticker, quantity, price, amount,"
            " currency, occurred_at, raw) values ($1, 'order', 'BUY', 'AAPL_US_EQ', 1, 100, 100,"
            " 'USD', $2, '{}'::jsonb)",
            f"fill:{i}",
            when,
        )
    out = await jg.check_journal_gaps(backend_pool, DAY)
    assert len(out["gaps"]) == 1
    notice = (await tasks.needs_owner(backend_pool))[0]
    assert notice.kind == "journal_gap" and notice.href == "/journal"

    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        owner = await admin.fetchval(
            "insert into auth.users (id, email) values (gen_random_uuid(), 'o@example.com')"
            " returning id"
        )
    finally:
        await admin.close()
    await backend_pool.execute(
        "insert into public.decision_journal (owner_id, scope, ticker, decision, decided_on,"
        " hypothesis) values ($1, 'security', 'AAPL_US_EQ', 'buy', $2, 'services')",
        owner,
        DAY,
    )
    again = await jg.check_journal_gaps(backend_pool, DAY)
    assert again["gaps"] == [] and again["resolved"] == 1
