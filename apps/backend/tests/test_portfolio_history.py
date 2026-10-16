"""Portfolio value history vs. benchmark: flow-adjusted so a deposit or withdrawal never
shows up as a fake gain or loss.

Covered here:
- the pure index math (steward.portfolio.history.flow_adjusted_index) against manually
  worked examples, including a deposit and a withdrawal;
- the DB-backed service (steward.portfolio.read.portfolio_history), seeded with account
  snapshots, cash transactions and benchmark price bars via direct SQL, mirroring
  tests/test_reference_data.py's style for the equivalent per-holding endpoint;
- the route wired up end to end, mirroring tests/test_portfolio_sync.py's `api` fixture.
"""

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from steward.portfolio.history import (
    DailyValue,
    PositionDailyValue,
    flow_adjusted_index,
    position_organic_gain,
)
from steward.portfolio.read import contribution_to_return, portfolio_history

pytestmark_db = pytest.mark.db

OWNER = "00000000-0000-0000-0000-00000000000a"
D = dt.date


def day(n: int) -> dt.date:
    return D(2026, 9, 1) + dt.timedelta(days=n)


# ---------------------------------------------------------------------------
# Pure math
# ---------------------------------------------------------------------------


def test_compounds_daily_returns_from_100_with_no_flows() -> None:
    values = [
        DailyValue(day(0), Decimal(100), Decimal(0)),
        DailyValue(day(1), Decimal(110), Decimal(0)),
        DailyValue(day(2), Decimal(121), Decimal(0)),
    ]
    index = flow_adjusted_index(values)
    assert [d for d, _ in index] == [day(0), day(1), day(2)]
    assert [round(v, 4) for _, v in index] == [100.0, 110.0, 121.0]


def test_a_deposit_does_not_show_up_as_a_gain() -> None:
    # Day 1: total_base jumps from 100 to 150 purely because 50 was deposited. Day 2: the
    # 150 grows organically by 10% to 165, with no further flow.
    values = [
        DailyValue(day(0), Decimal(100), Decimal(0)),
        DailyValue(day(1), Decimal(150), Decimal(50)),
        DailyValue(day(2), Decimal(165), Decimal(0)),
    ]
    index = flow_adjusted_index(values)
    assert [round(v, 4) for _, v in index] == [100.0, 100.0, 110.0]


def test_a_withdrawal_does_not_show_up_as_a_loss() -> None:
    # Day 1: total_base drops from 100 to 40 purely because 60 was withdrawn (flow is
    # negative). Day 2: the 40 grows organically by 10% to 44.
    values = [
        DailyValue(day(0), Decimal(100), Decimal(0)),
        DailyValue(day(1), Decimal(40), Decimal(-60)),
        DailyValue(day(2), Decimal(44), Decimal(0)),
    ]
    index = flow_adjusted_index(values)
    assert [round(v, 4) for _, v in index] == [100.0, 100.0, 110.0]


def test_a_large_deposit_on_a_small_base_is_measured_against_the_new_base() -> None:
    # Production, 2026-08-03: 109 in the account, 10,000 deposited over the weekend and
    # invested before Monday's move, 10,159 at Monday's close. The gain is 50 on 10,109,
    # not 50 on 109.
    values = [
        DailyValue(day(0), Decimal(109), Decimal(0)),
        DailyValue(day(1), Decimal(10159), Decimal(10000)),
    ]
    index = flow_adjusted_index(values)
    assert [round(v, 2) for _, v in index] == [100.0, 100.49]


def test_a_real_loss_still_shows_as_a_loss() -> None:
    values = [
        DailyValue(day(0), Decimal(100), Decimal(0)),
        DailyValue(day(1), Decimal(90), Decimal(0)),
    ]
    index = flow_adjusted_index(values)
    assert [round(v, 4) for _, v in index] == [100.0, 90.0]


def test_zero_prior_base_carries_the_index_forward_without_dividing_by_zero() -> None:
    values = [
        DailyValue(day(0), Decimal(0), Decimal(0)),
        DailyValue(day(1), Decimal(0), Decimal(0)),
        DailyValue(day(2), Decimal(50), Decimal(50)),
    ]
    index = flow_adjusted_index(values)
    assert [round(v, 4) for _, v in index] == [100.0, 100.0, 100.0]


def test_empty_series_is_empty() -> None:
    assert flow_adjusted_index([]) == []


# ---------------------------------------------------------------------------
# Per-position organic gain (contribution to return)
# ---------------------------------------------------------------------------


def test_buying_more_of_a_position_does_not_read_as_a_gain() -> None:
    # Day 1: value rises from 100 to 160, but only 50 of that is a purchase (cost_base
    # +50); the rest (10) is organic. Day 2: the resulting 160 grows organically to 176
    # (+16), with no further trade. Total organic gain: 10 + 16 = 26.
    days = [
        PositionDailyValue(day(0), Decimal(100), Decimal(100)),
        PositionDailyValue(day(1), Decimal(160), Decimal(150)),
        PositionDailyValue(day(2), Decimal(176), Decimal(150)),
    ]
    assert position_organic_gain(days) == Decimal(26)


def test_selling_some_of_a_position_does_not_read_as_a_loss() -> None:
    # Day 1: value drops from 100 to 40 purely because 60 (at cost) was sold.
    # Day 2: the resulting 40 grows organically to 44.
    days = [
        PositionDailyValue(day(0), Decimal(100), Decimal(100)),
        PositionDailyValue(day(1), Decimal(40), Decimal(40)),
        PositionDailyValue(day(2), Decimal(44), Decimal(40)),
    ]
    assert position_organic_gain(days) == Decimal(4)


def test_a_real_gain_with_no_trading_is_the_full_value_change() -> None:
    days = [
        PositionDailyValue(day(0), Decimal(100), Decimal(80)),
        PositionDailyValue(day(1), Decimal(110), Decimal(80)),
    ]
    assert position_organic_gain(days) == Decimal(10)


def test_fewer_than_two_days_has_no_organic_gain() -> None:
    assert position_organic_gain([]) == Decimal(0)
    one_day = [PositionDailyValue(day(0), Decimal(100), Decimal(80))]
    assert position_organic_gain(one_day) == Decimal(0)


# ---------------------------------------------------------------------------
# DB-backed service
# ---------------------------------------------------------------------------


async def _job(pool: asyncpg.Pool) -> int:
    job_id: int = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values"
        " ('portfolio_sync', 'schedule', 'succeeded') returning id"
    )
    return job_id


async def _snapshot(
    pool: asyncpg.Pool,
    job_id: int,
    snapshot_date: dt.date,
    total_base: Decimal,
    fx_rate: Decimal = Decimal(1),
) -> None:
    await pool.execute(
        "insert into public.account_snapshot (snapshot_date, job_id, captured_at,"
        " t212_account_id, account_currency, total_value, cash_available, cash_in_pies,"
        " cash_reserved, invested_value, invested_cost, unrealized_ppl, realized_ppl,"
        " base_currency, fx_source, fx_rate, cash_base, invested_base, total_base,"
        " weight_basis)"
        " values ($1, $2, $3, 1, 'EUR', $4, 0, 0, 0, $4, $4, 0, 0, 'EUR', 'ecb', $5, 0, $4,"
        " $4, 'total')",
        snapshot_date,
        job_id,
        dt.datetime.combine(snapshot_date, dt.time(12), tzinfo=dt.UTC),
        total_base,
        fx_rate,
    )


async def _cash(
    pool: asyncpg.Pool, ref: str, subtype: str, amount: Decimal, occurred_at: dt.datetime
) -> None:
    await pool.execute(
        "insert into public.transaction (id, type, subtype, amount, currency, occurred_at, raw)"
        " values ($1, 'cash', $2, $3, 'EUR', $4, '{}'::jsonb)",
        f"cash:{ref}",
        subtype,
        amount,
        occurred_at,
    )


@pytestmark_db
async def test_no_snapshot_yet_reports_a_gap(backend_pool: asyncpg.Pool) -> None:
    result = await portfolio_history(backend_pool, 365)
    assert result.gap == "no portfolio snapshot yet"
    assert result.points == [] and result.as_of is None


@pytestmark_db
async def test_one_snapshot_is_not_enough_for_a_return(backend_pool: asyncpg.Pool) -> None:
    job_id = await _job(backend_pool)
    await _snapshot(backend_pool, job_id, day(0), Decimal(100))
    result = await portfolio_history(backend_pool, 365)
    assert result.gap == "fewer than two snapshots in this window"
    assert result.as_of == day(0)


@pytestmark_db
async def test_a_deposit_between_two_snapshots_does_not_inflate_the_index(
    backend_pool: asyncpg.Pool,
) -> None:
    job_id = await _job(backend_pool)
    await _snapshot(backend_pool, job_id, day(0), Decimal(100))
    await _cash(
        backend_pool, "dep-1", "DEPOSIT", Decimal(50),
        dt.datetime.combine(day(1), dt.time(9), tzinfo=dt.UTC),
    )  # fmt: skip
    await _snapshot(backend_pool, job_id, day(1), Decimal(150))
    await _snapshot(backend_pool, job_id, day(2), Decimal(165))

    result = await portfolio_history(backend_pool, 365)
    assert result.gap is None
    values = [round(p.close, 4) for p in result.points]
    assert values == [100.0, 100.0, 110.0]


@pytestmark_db
async def test_a_fee_is_not_excluded_it_is_a_real_cost(backend_pool: asyncpg.Pool) -> None:
    job_id = await _job(backend_pool)
    await _snapshot(backend_pool, job_id, day(0), Decimal(100))
    await _cash(
        backend_pool, "fee-1", "FEE", Decimal(-5),
        dt.datetime.combine(day(1), dt.time(9), tzinfo=dt.UTC),
    )  # fmt: skip
    await _snapshot(backend_pool, job_id, day(1), Decimal(95))

    result = await portfolio_history(backend_pool, 365)
    values = [round(p.close, 4) for p in result.points]
    assert values == [100.0, 95.0]  # the fee IS the return this day, not netted out


@pytestmark_db
async def test_days_window_excludes_snapshots_before_it(backend_pool: asyncpg.Pool) -> None:
    job_id = await _job(backend_pool)
    await _snapshot(backend_pool, job_id, day(0), Decimal(50))
    await _snapshot(backend_pool, job_id, day(60), Decimal(100))
    await _snapshot(backend_pool, job_id, day(61), Decimal(110))

    result = await portfolio_history(backend_pool, 30)
    assert [p.date for p in result.points] == [day(60), day(61)]


@pytestmark_db
async def test_benchmark_series_comes_from_the_first_configured_symbol(
    backend_pool: asyncpg.Pool,
) -> None:
    job_id = await _job(backend_pool)
    await _snapshot(backend_pool, job_id, day(0), Decimal(100))
    await _snapshot(backend_pool, job_id, day(1), Decimal(105))
    await backend_pool.execute(
        "insert into public.price_bar (symbol, date, close, source) values"
        " ('^spx', $1, 5000, 'stooq'), ('^spx', $2, 5100, 'stooq')",
        day(0),
        day(1),
    )

    result = await portfolio_history(backend_pool, 365)
    assert result.benchmark_symbol == "^spx"
    assert [b.close for b in result.benchmark] == [5000.0, 5100.0]


# ---------------------------------------------------------------------------
# The route, end to end
# ---------------------------------------------------------------------------


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytestmark_db
async def test_history_route_before_the_first_sync(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/history")).json()
    assert body["gap"] == "no portfolio snapshot yet"
    assert body["points"] == []


@pytestmark_db
async def test_history_route_returns_the_flow_adjusted_index(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    job_id = await _job(backend_pool)
    await _snapshot(backend_pool, job_id, day(0), Decimal(100))
    await _snapshot(backend_pool, job_id, day(1), Decimal(110))

    response = await api.get("/portfolio/history", params={"days": 30})
    assert response.status_code == 200
    body = response.json()
    assert body["gap"] is None
    assert [round(p["close"], 4) for p in body["points"]] == [100.0, 110.0]


# ---------------------------------------------------------------------------
# Per-holding contribution to a period's return, DB-backed
# ---------------------------------------------------------------------------


async def _position(
    pool: asyncpg.Pool,
    job_id: int,
    snapshot_date: dt.date,
    ticker: str,
    *,
    quantity: Decimal,
    avg_price: Decimal,
    value_base: Decimal,
    fx_rate: Decimal = Decimal(1),
) -> None:
    await pool.execute(
        "insert into public.instrument (t212_ticker, currency) values ($1, 'USD')"
        " on conflict do nothing",
        ticker,
    )
    await pool.execute(
        "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at,"
        " quantity, avg_price, current_price, instrument_currency, value, account_currency,"
        " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
        " values ($1, $2, $3, now(), $4, $5, $5, 'USD', $4::numeric * $5::numeric, 'EUR', $6,"
        " $6, 0, 'EUR', 'ecb', $7, $6)",
        snapshot_date,
        ticker,
        job_id,
        quantity,
        avg_price,
        value_base,
        fx_rate,
    )


@pytestmark_db
async def test_no_position_snapshot_yet_reports_a_gap(backend_pool: asyncpg.Pool) -> None:
    result = await contribution_to_return(backend_pool, 365)
    assert result.gap == "no position snapshot yet"
    assert result.holdings == [] and result.as_of is None


@pytestmark_db
async def test_buying_more_of_a_holding_does_not_inflate_its_contribution(
    backend_pool: asyncpg.Pool,
) -> None:
    job_id = await _job(backend_pool)
    await _snapshot(backend_pool, job_id, day(0), Decimal(1000))
    await _position(
        backend_pool, job_id, day(0), "AAPL_US_EQ",
        quantity=Decimal(10), avg_price=Decimal(10), value_base=Decimal(100),
    )  # fmt: skip
    await _snapshot(backend_pool, job_id, day(1), Decimal(1060))
    # Bought 5 more at 10 (cost +50): value_base rises from 100 to 160, only 10 of that
    # (160 - 100 - 50) is organic.
    await _position(
        backend_pool, job_id, day(1), "AAPL_US_EQ",
        quantity=Decimal(15), avg_price=Decimal(10), value_base=Decimal(160),
    )  # fmt: skip

    result = await contribution_to_return(backend_pool, 365)
    assert result.gap is None
    assert result.period_start == day(0)
    [holding] = result.holdings
    assert holding.ticker == "AAPL_US_EQ"
    assert round(holding.contribution_base, 4) == 10.0
    assert round(holding.contribution_pct or 0, 4) == round(10 / 1000, 4)


@pytestmark_db
async def test_holdings_are_ranked_by_contribution_largest_first(
    backend_pool: asyncpg.Pool,
) -> None:
    job_id = await _job(backend_pool)
    await _snapshot(backend_pool, job_id, day(0), Decimal(300))
    await _position(
        backend_pool, job_id, day(0), "UP_US_EQ",
        quantity=Decimal(10), avg_price=Decimal(10), value_base=Decimal(100),
    )  # fmt: skip
    await _position(
        backend_pool, job_id, day(0), "DOWN_US_EQ",
        quantity=Decimal(10), avg_price=Decimal(10), value_base=Decimal(100),
    )  # fmt: skip
    await _snapshot(backend_pool, job_id, day(1), Decimal(330))
    await _position(
        backend_pool, job_id, day(1), "UP_US_EQ",
        quantity=Decimal(10), avg_price=Decimal(10), value_base=Decimal(120),
    )  # fmt: skip
    await _position(
        backend_pool, job_id, day(1), "DOWN_US_EQ",
        quantity=Decimal(10), avg_price=Decimal(10), value_base=Decimal(90),
    )  # fmt: skip

    result = await contribution_to_return(backend_pool, 365)
    assert [h.ticker for h in result.holdings] == ["UP_US_EQ", "DOWN_US_EQ"]
    assert result.holdings[0].contribution_base > 0
    assert result.holdings[1].contribution_base < 0


@pytestmark_db
async def test_a_holding_with_one_snapshot_in_window_has_no_measurable_contribution(
    backend_pool: asyncpg.Pool,
) -> None:
    job_id = await _job(backend_pool)
    await _snapshot(backend_pool, job_id, day(0), Decimal(200))
    await _position(
        backend_pool, job_id, day(0), "NEW_US_EQ",
        quantity=Decimal(1), avg_price=Decimal(100), value_base=Decimal(100),
    )  # fmt: skip

    result = await contribution_to_return(backend_pool, 365)
    [holding] = result.holdings
    assert holding.contribution_base == 0.0


@pytestmark_db
async def test_contribution_route_before_the_first_sync(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/contribution")).json()
    assert body["gap"] == "no position snapshot yet"
    assert body["holdings"] == []


@pytestmark_db
async def test_contribution_route_ranks_holdings(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    job_id = await _job(backend_pool)
    await _snapshot(backend_pool, job_id, day(0), Decimal(200))
    await _position(
        backend_pool, job_id, day(0), "AAPL_US_EQ",
        quantity=Decimal(10), avg_price=Decimal(10), value_base=Decimal(100),
    )  # fmt: skip
    await _snapshot(backend_pool, job_id, day(1), Decimal(210))
    await _position(
        backend_pool, job_id, day(1), "AAPL_US_EQ",
        quantity=Decimal(10), avg_price=Decimal(10), value_base=Decimal(110),
    )  # fmt: skip

    response = await api.get("/portfolio/contribution", params={"days": 30})
    assert response.status_code == 200
    body = response.json()
    assert body["gap"] is None
    assert body["holdings"][0]["ticker"] == "AAPL_US_EQ"
    assert round(body["holdings"][0]["contribution_base"], 4) == 10.0
