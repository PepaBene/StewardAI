"""Opportunity notices: new matches of the owner's saved screens, announced once."""

import json
from typing import Any

import asyncpg
import pytest

from steward.ops import opportunities as op
from steward.ops import tasks
from tests.conftest import MigratedDatabase


def test_the_first_look_announces_nothing_and_held_stocks_are_skipped() -> None:
    assert op.new_matches(["A", "B"], None, set()) == []
    assert op.new_matches(["A", "B", "C"], ["A"], {"C"}) == ["B"]
    assert op.new_matches(["A"], ["A"], set()) == []


def test_the_notice_names_the_stocks() -> None:
    title, detail = op.describe("Quality", ["A", "B"], {"A": "Acme"})
    assert title == "2 new stocks pass Quality"
    assert "Acme, B" in detail
    assert op.describe("Q", ["A"], {})[0] == "1 new stock passes Q"


def test_watchlist_names_are_named_first() -> None:
    title, detail = op.describe("Quality", ["A", "W"], {"W": "Watched Co"}, {"W"})
    assert title == "2 new stocks pass Quality"
    assert detail.startswith("Newly matching your saved screen: Watched Co (watchlist), A")
    assert (
        op.describe("Q", ["W"], {"W": "Watched Co"}, {"W"})[0]
        == "Watched Co from your watchlist now passes Q"
    )
    assert op.describe("Q", ["W", "V"], {}, {"W", "V"})[0] == "2 watchlist stocks now pass Q"


@pytest.mark.db
async def test_a_stock_that_newly_matches_is_announced_once(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        owner = await admin.fetchval(
            "insert into auth.users (id, email) values (gen_random_uuid(), 'o@example.com')"
            " returning id"
        )
    finally:
        await admin.close()
    screen = await backend_pool.fetchval(
        "insert into public.screen_definition (owner_id, name, kind) values ($1, 'Quality',"
        " 'stock') returning id",
        owner,
    )
    await backend_pool.execute(
        "insert into public.screen_definition_version (screen_id, version, definition,"
        " definition_hash, method_version) values ($1, 1, $2::jsonb, 'h', 'm')",
        screen,
        json.dumps({"filters": []}),
    )
    matches = {"now": ["A"]}

    async def run(_p: Any, _o: Any, _d: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
        return matches["now"], {"B": "Beta Inc"}

    calls: list[int] = []

    async def backtest(_p: Any, _o: Any, _d: dict[str, Any]) -> str:
        calls.append(1)
        return "Backtest said so."

    first = await op.check_opportunities(backend_pool, owner, run, backtest)
    assert first["announced"] == {} and await tasks.needs_owner(backend_pool) == []

    matches["now"] = ["A", "B"]
    second = await op.check_opportunities(backend_pool, owner, run, backtest)
    assert second["announced"] == {"Quality": ["B"]}
    notice = (await tasks.needs_owner(backend_pool))[0]
    assert notice.kind == "opportunity" and "Beta Inc" in notice.detail
    assert notice.detail.endswith("Backtest said so.")

    assert (await op.check_opportunities(backend_pool, owner, run, backtest))["announced"] == {}
    # A second announcement in the same month reuses the stored line.
    matches["now"] = ["A", "B", "C"]
    await op.check_opportunities(backend_pool, owner, run, backtest)
    assert len(calls) == 1


def test_the_backtest_line_states_the_past_or_why_there_is_none() -> None:
    from decimal import Decimal

    line = op.backtest_line(Decimal("0.42"), Decimal("0.03"), Decimal("0.30"), 3, [])
    assert "+42% after costs" in line and "benchmark +30%" in line and "not a forecast" in line
    assert op.backtest_line(None, None, None, 0, ["no prices"]) == "Not backtested: no prices."
