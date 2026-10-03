"""Browser-pass items 3 and 4: contribution needs two snapshots; no float noise in coverage."""

import datetime as dt
from decimal import Decimal

import asyncpg
import pytest

from steward.portfolio.read import contribution_to_return
from steward.reference.coverage_scorecard import price_coverage
from tests.test_coverage_scorecard import bars, instrument
from tests.test_portfolio_history import _job, _position, _snapshot

pytestmark = pytest.mark.db


async def test_contribution_with_one_snapshot_is_a_gap_not_zeros(
    backend_pool: asyncpg.Pool,
) -> None:
    job = await _job(backend_pool)
    day = dt.date(2026, 9, 1)
    await _snapshot(backend_pool, job, day, Decimal(1000))
    for ticker in ("AAA", "BBB"):
        await _position(
            backend_pool,
            job,
            day,
            ticker,
            quantity=Decimal(1),
            avg_price=Decimal(100),
            value_base=Decimal(120),
        )
    out = await contribution_to_return(backend_pool, 365)
    assert out.holdings == []  # never a column of 0.0
    assert out.gap is not None and "at least two snapshots" in out.gap
    assert out.as_of == day


async def test_contribution_omits_a_holding_seen_once_instead_of_reporting_zero(
    backend_pool: asyncpg.Pool,
) -> None:
    job = await _job(backend_pool)
    d1, d2 = dt.date(2026, 9, 1), dt.date(2026, 9, 2)
    await _snapshot(backend_pool, job, d1, Decimal(1000))
    await _snapshot(backend_pool, job, d2, Decimal(1100))
    for day, value in ((d1, 100), (d2, 110)):
        await _position(
            backend_pool,
            job,
            day,
            "TWICE",
            quantity=Decimal(1),
            avg_price=Decimal(100),
            value_base=Decimal(value),
        )
    await _position(
        backend_pool,
        job,
        d2,
        "ONCE",
        quantity=Decimal(1),
        avg_price=Decimal(50),
        value_base=Decimal(60),
    )
    out = await contribution_to_return(backend_pool, 365)
    assert [h.ticker for h in out.holdings] == ["TWICE"] and out.gap is None


async def test_unknown_value_is_exactly_zero_when_everything_is_usable(
    backend_pool: asyncpg.Pool,
) -> None:
    snap = dt.date(2026, 10, 1)
    job = await _job(backend_pool)
    await _snapshot(backend_pool, job, snap, Decimal(1000))
    # Values with more digits than a float keeps: float(denominator - usable) was -8.9e-14.
    values = {
        "A_US_EQ": Decimal("333.3333333333333333"),
        "B_US_EQ": Decimal("333.3333333333333334"),
        "C_US_EQ": Decimal("0.1234567890123456"),
    }
    for ticker in values:
        await instrument(backend_pool, ticker, symbol=ticker.split("_")[0].lower() + ".us")
    for ticker, value in values.items():
        await backend_pool.execute(
            "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
            " avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
            " values ($1,$2,$3,now(),1,1,1,'USD',$4,'USD',$4,$4,0,'USD','ecb',1,$4)",
            snap,
            ticker,
            job,
            value,
        )
        await bars(backend_pool, ticker.split("_")[0].lower() + ".us", [snap], mic="XNYS")
    out = await price_coverage(backend_pool, dt.date(2026, 10, 2))
    assert out.holdings_usable == 3
    assert out.unknown_value_base == 0.0 and out.unknown_share == 0.0
    assert out.usable_share == 1.0
