"""Saved rebalance plans are scored against the prices that followed."""

import datetime as dt
import json
import uuid

import asyncpg
import pytest

from steward.engine import outcomes
from tests.conftest import MigratedDatabase

NOW = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.UTC)


def test_a_buy_that_beat_the_plan_average_and_a_sell_that_lagged_it_are_hits() -> None:
    scored = outcomes.score(
        {"A": 0.10, "B": -0.10, "C": 0.0},
        [("A", "buy"), ("B", "sell"), ("C", "hold")],
    )
    assert [s.ticker for s in scored] == ["A", "B"]
    assert all(s.hit for s in scored)
    assert outcomes.hit_rate(scored) == 1.0


def test_a_buy_that_lagged_and_a_sell_that_rose_are_misses() -> None:
    scored = outcomes.score({"A": -0.2, "B": 0.2}, [("A", "buy"), ("B", "sell")])
    assert outcomes.hit_rate(scored) == 0.0


def test_trades_are_also_scored_against_an_index() -> None:
    scored = outcomes.versus(
        {"A": 0.2, "B": 0.0, "C": 0.0}, [("A", "buy"), ("B", "sell"), ("C", "buy")], 0.05
    )
    assert [s.hit for s in scored] == [True, True, False]
    assert outcomes.versus({"A": 0.2}, [("Z", "buy")], 0.05) == []


def test_one_priced_position_has_nothing_to_compare_with() -> None:
    assert outcomes.score({"A": 0.1}, [("A", "buy")]) == []
    assert outcomes.hit_rate([]) is None


async def seed(
    admin_url: str, owner: uuid.UUID, trades: list[dict[str, str]], days_ago: int
) -> None:
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(
            "insert into auth.users (id, email) values ($1, 'o@example.com')", owner
        )
        start = (NOW - dt.timedelta(days=days_ago)).date()
        for i, t in enumerate(trades):
            await admin.execute(
                "insert into public.instrument (t212_ticker, price_symbol) values ($1, $2)",
                t["ticker"],
                f"SYM{i}",
            )
            # The first position rises 20%, the others stay flat.
            for day, close in (
                (start, 100.0),
                (start + dt.timedelta(days=60), 120.0 if i == 0 else 100.0),
            ):
                await admin.execute(
                    "insert into public.price_bar (symbol, date, close, bar_type)"
                    " values ($1, $2, $3, 'adjusted')",
                    f"SYM{i}",
                    day,
                    close,
                )
        await admin.execute(
            "insert into public.saved_analysis (owner_id, kind, name, payload, as_of, created_at)"
            " values ($1, 'rebalance_plan', 'p', $2::jsonb, $3, $4)",
            owner,
            json.dumps({"result": {"trades": trades}}),
            start,
            NOW - dt.timedelta(days=days_ago),
        )
    finally:
        await admin.close()


@pytest.mark.db
async def test_an_old_plan_is_scored_and_a_young_one_is_left(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    trades = [
        {"ticker": "AAA_US_EQ", "action": "buy"},
        {"ticker": "BBB_US_EQ", "action": "sell"},
        {"ticker": "CCC_US_EQ", "action": "buy"},
    ]
    await seed(migrated_db.admin_url, uuid.uuid4(), trades, days_ago=90)
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:  # the index (the first benchmark, ^spx) rose 5% over the same days
        start = (NOW - dt.timedelta(days=90)).date()
        for day, close in ((start, 100.0), (start + dt.timedelta(days=60), 105.0)):
            await admin.execute(
                "insert into public.price_bar (symbol, date, close, bar_type)"
                " values ('^spx', $1, $2, 'adjusted')",
                day,
                close,
            )
    finally:
        await admin.close()
    out = await outcomes.backtest(backend_pool, NOW)
    assert out["plans"] == 1 and out["trades"] == 3 and out["unpriced"] == 0
    # Against the index: buy A (+20% vs +5%) hits, sell B (flat) hits, buy C (flat) misses.
    assert out["index_trades"] == 3
    assert out["hit_rate_vs_index"] == pytest.approx(2 / 3, abs=0.001)
    # Buy A (+20%, above the average) hits; sell B (flat, below the average) hits; buy C misses.
    assert out["hit_rate"] == pytest.approx(2 / 3, abs=0.001)
    assert "flagged" not in out  # too few trades to judge the method


@pytest.mark.db
async def test_a_plan_younger_than_the_horizon_is_not_scored(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    trades = [{"ticker": "AAA_US_EQ", "action": "buy"}, {"ticker": "BBB_US_EQ", "action": "sell"}]
    await seed(migrated_db.admin_url, uuid.uuid4(), trades, days_ago=20)
    out = await outcomes.backtest(backend_pool, NOW)
    assert out["plans"] == 0 and out["hit_rate"] is None
