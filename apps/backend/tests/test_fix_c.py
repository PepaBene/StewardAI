"""Regression tests for the group C audit fixes (portfolio sync, reference data, signoff)."""

import datetime as dt
import json
import math
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import asyncpg
import httpx
import pytest
from pydantic import ValidationError

from steward.config.strategy import StrategyConfig
from steward.portfolio.sync import _sync_history_kind, cash_row
from steward.reference import risk
from steward.reference.identity import holding_tickers
from steward.reference.prices import PriceDeps, collect_symbol, latest_move, plausibility
from steward.signoff import signoff
from steward.trading212.models import HistoryTransaction
from tests.test_phase1b import seed_snapshot


def _cash(ref: str, day: int) -> HistoryTransaction:
    return HistoryTransaction(
        reference=ref,
        type="DEPOSIT",
        amount=Decimal(100),
        currency="CZK",
        date_time=dt.datetime(2026, 9, day, 10, tzinfo=dt.UTC),
    )


class _Pages:
    """A newest-first page iterator that can fail when asked for a given page."""

    def __init__(self, pages: list[list[HistoryTransaction]], fail_at: int | None = None):
        self.pages = pages
        self.fail_at = fail_at
        self.read = 0

    async def __call__(self) -> AsyncIterator[list[HistoryTransaction]]:
        for i, page in enumerate(self.pages):
            if i == self.fail_at:
                raise RuntimeError("HTTP 429")
            self.read += 1
            yield page


async def _new_job(pool: asyncpg.Pool) -> int:
    job_id: int = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values"
        " ('portfolio_sync', 'schedule', 'running') returning id"
    )
    return job_id


@pytest.mark.db
async def test_history_paging_resumes_past_pages_stored_by_a_failed_run(
    backend_pool: asyncpg.Pool,
) -> None:
    # Backfill: two old items on one page.
    old = [[_cash("a", 1), _cash("b", 1)]]
    job = await _new_job(backend_pool)
    await _sync_history_kind(backend_pool, "cash", _Pages(old)(), cash_row, job)

    # Five new items arrive across two pages; this run stores page 1 then fails on page 2.
    history = [
        [_cash("g", 20), _cash("f", 19)],
        [_cash("e", 18), _cash("d", 17)],
        [_cash("c", 16), _cash("b", 1)],
        [_cash("a", 1)],
    ]
    with pytest.raises(RuntimeError):
        await _sync_history_kind(backend_pool, "cash", _Pages(history, fail_at=1)(), cash_row, job)

    # The next run must read past the already-stored page 1 instead of stopping there.
    pages = _Pages(history)
    detail = await _sync_history_kind(backend_pool, "cash", pages(), cash_row, job)
    assert detail == {"cash_new": 3, "cash_pages": 3}
    stored = await backend_pool.fetch("select id from public.transaction order by id")
    assert [r["id"] for r in stored] == [f"cash:{c}" for c in "abcdefg"]

    # Once a pass has finished cleanly, the next run stops at the first known page again.
    pages = _Pages(history)
    detail = await _sync_history_kind(backend_pool, "cash", pages(), cash_row, job)
    assert detail == {"cash_new": 0, "cash_pages": 1}


# ---------------------------------------------------------------------------
# Stooq restatements, plausibility currency, split-looking Trading212 moves
# ---------------------------------------------------------------------------


def _csv(days: list[dt.date], close: str) -> str:
    rows = [f"{d.isoformat()},{close},{close},{close},{close},1000" for d in days]
    return "Date,Open,High,Low,Close,Volume\n" + "\n".join(rows)


@pytest.mark.db
async def test_restated_stooq_history_is_refetched_in_full(backend_pool: asyncpg.Pool) -> None:
    today = dt.date(2026, 9, 25)
    days = [today - dt.timedelta(days=n) for n in range(40, -1, -1)]
    await backend_pool.executemany(
        "insert into public.price_bar (symbol, date, close, source) values ('xyz.us', $1, 200,"
        " 'stooq')",
        [(d,) for d in days[:-1]],
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        d1 = dt.datetime.strptime(request.url.params["d1"], "%Y%m%d").date()
        requests.append(request.url.params["d1"])
        # A 2:1 split: Stooq now reports every day at half the old price.
        return httpx.Response(200, text=_csv([d for d in days if d >= d1], "100"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        notes: dict[str, str] = {}
        count, gap = await collect_symbol(
            backend_pool, PriceDeps(http=http, today=today), "xyz.us", 1, notes
        )
    assert gap is None and count == len(days)
    assert len(requests) == 2  # the recent window, then the full history
    assert "restated" in notes["xyz.us"]
    closes = await backend_pool.fetch(
        "select distinct close from public.price_bar where symbol = 'xyz.us'"
    )
    assert [Decimal(r["close"]) for r in closes] == [Decimal(100)]

    # Consistent recent bars: only the recent window is fetched.
    requests.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await collect_symbol(backend_pool, PriceDeps(http=http, today=today), "xyz.us", 1)
    assert len(requests) == 1


async def _snapshot(
    pool: asyncpg.Pool, day: dt.date, ticker: str, price: str, currency: str
) -> None:
    job = await _new_job(pool)
    await pool.execute(
        "insert into instrument (t212_ticker, name) values ($1, $1) on conflict do nothing",
        ticker,
    )
    await pool.execute(
        "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
        " avg_price, current_price, instrument_currency, value, account_currency, value_account,"
        " cost_account, ppl, base_currency, fx_source, fx_rate, value_base) values"
        " ($1, $2, $3, now(), 1, 1, $4, $5, 1, 'EUR', 1, 1, 0, 'EUR', 'ecb', 1, 1)",
        day,
        ticker,
        job,
        Decimal(price),
        currency,
    )


@pytest.mark.db
async def test_plausibility_skips_a_line_quoted_in_another_currency(
    backend_pool: asyncpg.Pool,
) -> None:
    day = dt.date(2026, 9, 25)
    await backend_pool.execute(
        "insert into public.price_bar (symbol, date, close, source) values"
        " ('aapl.us', $1, 230, 'stooq')",
        day,
    )
    await _snapshot(backend_pool, day, "AAPL_EUR_EQ", "196", "EUR")
    await _snapshot(backend_pool, day, "AAPL_US_EQ", "229", "USD")
    notes: dict[str, str] = {}
    assert await plausibility(backend_pool, "AAPL_EUR_EQ", "aapl.us", 2.0, notes) is None
    assert "EUR" in notes["AAPL_EUR_EQ"] and "USD" in notes["AAPL_EUR_EQ"]
    same = await plausibility(backend_pool, "AAPL_US_EQ", "aapl.us", 2.0)
    assert same is not None and same["flagged"] is False


@pytest.mark.db
async def test_trading212_fallback_move_skips_split_ratios(backend_pool: asyncpg.Pool) -> None:
    today = dt.date(2026, 9, 25)
    await _snapshot(backend_pool, today - dt.timedelta(days=1), "SPLT_EQ", "300", "USD")
    await _snapshot(backend_pool, today, "SPLT_EQ", "100.5", "USD")  # a 3:1 split
    notes: dict[str, str] = {}
    assert await latest_move(backend_pool, "SPLT_EQ", None, today, notes) is None
    assert "split" in notes["SPLT_EQ"]

    await _snapshot(backend_pool, today - dt.timedelta(days=1), "DROP_EQ", "300", "USD")
    await _snapshot(backend_pool, today, "DROP_EQ", "240", "USD")  # a real -20 % move
    move = await latest_move(backend_pool, "DROP_EQ", None, today)
    assert move is not None and round(move[1]) == -20


# ---------------------------------------------------------------------------
# Sign-off, strategy validation, beta pairing, current holdings
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_signoff_skips_a_run_whose_day_was_replaced_by_a_later_sync(
    backend_pool: asyncpg.Pool,
) -> None:
    day = dt.date(2026, 3, 2)
    # The first run of the day wrote 5 positions; a later re-sync replaced all its rows.
    first = await _new_job(backend_pool)
    await backend_pool.execute("update job set status = 'succeeded' where id = $1", first)
    await backend_pool.execute(
        "insert into job_step (job_id, step_index, name, status, detail) values"
        " ($1, 0, 'trading212_positions', 'succeeded', '{\"positions\": 5}'),"
        " ($1, 1, 'snapshot', 'succeeded', $2::jsonb)",
        first,
        json.dumps({"snapshot_date": day.isoformat()}),
    )
    await seed_snapshot(backend_pool, day)
    report = await signoff(backend_pool, required_runs=2)
    check = next(c for c in report.checks if c.name == "one snapshot row per position per day")
    assert check.ok, check.detail


def test_cash_weight_rule_needs_the_total_weight_basis() -> None:
    body = {
        "base_currency": "CZK",
        "weight_basis": "invested",
        "rules": [{"id": "max-cash", "type": "cash_weight", "operator": "<=", "value": 0.2}],
    }
    with pytest.raises(ValidationError, match="cash_weight rule, which needs weight_basis"):
        StrategyConfig.model_validate(body)
    StrategyConfig.model_validate({**body, "weight_basis": "total"})


def test_seeded_placeholder_strategy_still_validates() -> None:
    sql = (
        Path(__file__).parents[3]
        / "supabase/migrations/20260925153814_seed_placeholder_strategy.sql"
    ).read_text()
    body = sql.split("$j$")[1]
    StrategyConfig.model_validate_json(body)


def test_beta_pairs_returns_over_the_same_days() -> None:
    start = dt.date(2026, 1, 1)
    days = [start + dt.timedelta(days=i) for i in range(60)]
    bench = [(d, 100.0 + 5 * math.sin(i)) for i, d in enumerate(days)]
    # The holding is the benchmark exactly, but misses every fifth day.
    holding = [(d, c) for i, (d, c) in enumerate(bench) if i % 5 != 3]
    assert risk.beta(holding, bench) == pytest.approx(1.0)
    assert risk.correlation(holding, bench) == pytest.approx(1.0)


@pytest.mark.db
async def test_holdings_are_empty_once_every_position_is_closed(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_snapshot(backend_pool, dt.date(2026, 3, 2))
    assert await holding_tickers(backend_pool) == ["AAPL_US_EQ", "SAP_EQ", "VOD_EQ"]
    # The next day's sync found no positions: an account row, no position rows.
    job = await _new_job(backend_pool)
    await backend_pool.execute(
        "insert into account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency, fx_source,"
        " fx_rate, cash_base, invested_base, total_base, weight_basis)"
        " values ('2026-03-03', $1, now(), 1, 'EUR', 100, 100, 0, 0, 0, 0, 0, 0, 'CZK', 'cnb',"
        " 25, 2500, 0, 2500, 'total')",
        job,
    )
    assert await holding_tickers(backend_pool) == []
