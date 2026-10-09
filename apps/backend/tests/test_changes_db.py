# ruff: noqa: E501 - seed SQL stays readable
"""The change feed and the review alerts against a real database (audit R33, R50, R53).
Needs STEWARD_TEST_DATABASE_URL (skipped otherwise).

Snapshots on Monday 5, Wednesday 7 and Friday 9 January 2026; the owner holds a fund (FUND) and a
stock (AAA). A second fund (OTHER) has holdings snapshots but is not held."""

import datetime as dt

import asyncpg
import pytest

from steward.changes.alerts import find_review_changes
from steward.changes.feed import build_feed
from tests.conftest import MigratedDatabase

OWNER = "7f0c3d4e-0000-4000-8000-0000000000cc"
pytestmark = pytest.mark.db
MON, WED, FRI = dt.date(2026, 1, 5), dt.date(2026, 1, 7), dt.date(2026, 1, 9)
HELD_FUND, OTHER_FUND = "IE00B5BMR087", "IE00BK5BQT80"


def noon(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(12), tzinfo=dt.UTC)


async def _owner(admin_url: str) -> None:
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(
            "insert into auth.users (id, email) values ($1, 'o@example.com')", OWNER
        )
    finally:
        await admin.close()


async def _seed_portfolio(pool: asyncpg.Pool) -> None:
    job = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values ('portfolio_sync', 'schedule', 'succeeded') returning id"
    )
    await pool.execute(
        "insert into public.instrument (t212_ticker, name, currency, home_country, isin) values"
        " ('FUNDl_EQ', 'Held fund', 'USD', 'IE', $1), ('OTHERl_EQ', 'Other fund', 'USD', 'IE', $2),"
        " ('AAA_US_EQ', 'Alpha Inc', 'USD', 'US', 'US0000000001')",
        HELD_FUND, OTHER_FUND,
    )  # fmt: skip
    # AAA moves 100 -> 110 -> 120; the fund stays at 50.
    for day, aaa in ((MON, 100), (WED, 110), (FRI, 120)):
        for ticker, price in (("AAA_US_EQ", aaa), ("FUNDl_EQ", 50)):
            await pool.execute(
                "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity, avg_price, current_price,"
                " instrument_currency, value, account_currency, value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
                " values ($1, $2, $3, now(), 10, $4, $4, 'USD', 10 * $4::numeric, 'USD', 10 * $4::numeric, 10 * $4::numeric, 0, 'USD', 'ecb', 1, 10 * $4::numeric)",
                day, ticker, job, price,
            )  # fmt: skip


async def _etf_snapshot(pool: asyncpg.Pool, isin: str, day: dt.date, weight: str) -> int:
    sid: int = await pool.fetchval(
        "insert into public.etf_snapshot (fund_isin, provider, portfolio_id, source, as_of, raw_text, fetched_at)"
        " values ($1, 'ishares', 'p', 'ishares_json', $2, 'x', $3) returning id",
        isin, day, noon(day),
    )  # fmt: skip
    await pool.execute(
        "insert into public.etf_holding (snapshot_id, row_index, name, isin, asset_class, weight)"
        " values ($1, 0, 'Big Co', 'US0000000009', 'equity', $2::numeric),"
        " ($1, 1, 'Rest', null, 'other', 1 - $2::numeric)",
        sid, weight,
    )  # fmt: skip
    return sid


async def test_fund_changes_use_the_comparison_day_and_only_held_funds(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> None:
    await _owner(migrated_db.admin_url)
    await _seed_portfolio(backend_pool)
    await _etf_snapshot(backend_pool, HELD_FUND, MON, "0.05")
    on_wed = await _etf_snapshot(backend_pool, HELD_FUND, WED, "0.06")
    on_fri = await _etf_snapshot(backend_pool, HELD_FUND, FRI, "0.09")
    await _etf_snapshot(backend_pool, OTHER_FUND, WED, "0.10")
    await _etf_snapshot(backend_pool, OTHER_FUND, FRI, "0.20")

    feed = await build_feed(backend_pool, since=WED)
    assert feed is not None
    area = next(a for a in feed.areas if a.area == "fund_holdings")
    assert area.compared == 1  # the fund that is not held is not compared
    funds = [i for i in feed.items if i.area == "fund_holdings"]
    assert funds, "the held fund's Big Co weight moved from 6% to 9%"
    # Compared with the snapshot known at the end of Wednesday, not Monday's.
    assert {(i.before_ref.id if i.before_ref else None) for i in funds} == {str(on_wed)}
    assert {(i.after_ref.id if i.after_ref else None) for i in funds} == {str(on_fri)}
    assert all(HELD_FUND in i.subject or HELD_FUND in i.what for i in funds)


async def test_a_since_day_without_a_snapshot_compares_prices_with_the_one_before(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> None:
    await _owner(migrated_db.admin_url)
    await _seed_portfolio(backend_pool)

    feed = await build_feed(backend_pool, since=dt.date(2026, 1, 6))  # a Tuesday: no snapshot
    assert feed is not None
    price = next(a for a in feed.areas if a.area == "price")
    assert price.compared == 2  # both holdings, from Monday's snapshot
    assert price.note and "2026-01-05" in price.note
    moved = [i for i in feed.items if i.area == "price"]
    assert [i.subject for i in moved] == ["AAA_US_EQ"]  # 100 -> 120; the fund did not move

    before_any = await build_feed(backend_pool, since=dt.date(2025, 12, 31))
    assert before_any is not None
    none = next(a for a in before_any.areas if a.area == "price")
    assert none.compared == 0 and none.note and "No position snapshot" in none.note


async def test_review_dates_on_review_entries_alert_once_per_chain(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> None:
    await _owner(migrated_db.admin_url)
    today = dt.date(2026, 2, 1)

    async def entry(decision: str, parent: int | None, review: dt.date | None) -> int:
        new_id: int = await backend_pool.fetchval(
            "insert into public.decision_journal (owner_id, scope, ticker, decision, parent_id, hypothesis, review_date)"
            " values ($1, 'security', 'AAA', $2, $3, 'h', $4) returning id",
            OWNER, decision, parent, review,
        )  # fmt: skip
        return new_id

    # Chain 1: a buy, then a review that sets a new review date (due): the review alerts.
    buy = await entry("buy", None, dt.date(2026, 1, 10))
    review = await entry("review", buy, dt.date(2026, 1, 25))
    # Chain 2: a buy, a review with a due date, then a later review with none: nothing is due.
    buy2 = await entry("buy", None, dt.date(2026, 1, 12))
    mid = await entry("review", buy2, dt.date(2026, 1, 20))
    await entry("review", mid, None)
    # Chain 3: a lone buy that is due.
    lone = await entry("add", None, dt.date(2026, 1, 30))

    keys = {c.key for c in await find_review_changes(backend_pool, today)}
    journal = {k for k in keys if k.startswith("change:journal_review:")}
    assert journal == {
        f"change:journal_review:{review}:2026-01-25",
        f"change:journal_review:{lone}:2026-01-30",
    }
