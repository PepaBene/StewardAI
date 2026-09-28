"""Sharpe ratio (steward.portfolio.risk): the math against hand-worked and independently
computed values, and GET /portfolio/risk over seeded snapshots, CZEONIA and a benchmark."""

import datetime as dt
import math
import statistics
from collections.abc import AsyncIterator
from decimal import Decimal
from itertools import pairwise

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from steward.portfolio.risk import (
    DailyReturn,
    RateSeries,
    benchmark_currency,
    sharpe_ratio,
    trading_day_returns,
)

OWNER = "00000000-0000-0000-0000-00000000000a"
D = dt.date
FRI, SAT, SUN, MON = D(2026, 9, 18), D(2026, 9, 19), D(2026, 9, 20), D(2026, 9, 21)


def test_returns_use_weekdays_only() -> None:
    points = [(FRI, 100.0), (SAT, 101.0), (SUN, 101.0), (MON, 102.0), (D(2026, 9, 22), 0.0)]
    returns = trading_day_returns(points)
    assert returns == [DailyReturn(FRI, MON, pytest.approx(0.02))]  # type: ignore[arg-type]


def test_rates_are_forward_filled_up_to_a_max_age() -> None:
    rates = RateSeries([(FRI, 3.5), (MON, 3.6)], max_age=dt.timedelta(days=10))
    assert rates.on(D(2026, 9, 17)) is None
    assert rates.on(SUN) == 3.5 and rates.on(MON) == 3.6
    assert rates.on(D(2026, 10, 2)) is None  # 11 days after the last fixing
    step = RateSeries([(D(2020, 1, 1), 2.0)], max_age=None)
    assert step.on(D(2026, 1, 1)) == 2.0


def test_hand_worked_sharpe() -> None:
    returns = [DailyReturn(FRI, MON, 0.01), DailyReturn(MON, D(2026, 9, 22), 0.03)]
    rates = RateSeries([(FRI, 2.52)], max_age=None)  # 2.52% / 252 = 0.0001 a day
    r = sharpe_ratio(returns, rates, min_observations=2)
    # Excess 0.0099 and 0.0299: mean 0.0199, sample stdev 0.0141421.
    assert r.sharpe == pytest.approx(0.0199 / math.sqrt(0.0002) * math.sqrt(252))
    assert r.mean_excess_return == pytest.approx(0.0199 * 252)
    assert r.risk_free_average == pytest.approx(0.0252)
    assert r.annualised_volatility == pytest.approx(math.sqrt(0.0002) * math.sqrt(252))
    assert r.annualised_return == pytest.approx(0.02 * 252)
    assert (r.observations, r.window_start, r.window_end) == (2, FRI, D(2026, 9, 22))


def test_matches_an_independent_computation() -> None:
    start = D(2025, 1, 6)  # a Monday
    days = [start + dt.timedelta(days=i) for i in range(200)]
    level, points = 100.0, []
    for i, d in enumerate(days):
        level *= 1 + 0.004 * math.sin(i) + 0.0003
        points.append((d, level))
    rates = RateSeries([(start, 3.5), (D(2025, 4, 1), 3.25)], max_age=None)
    r = sharpe_ratio(trading_day_returns(points), rates)
    weekdays = [(d, v) for d, v in points if d.weekday() < 5]
    excess = [
        b / a - 1 - (3.5 if pd < D(2025, 4, 1) else 3.25) / 100 / 252
        for (pd, a), (_, b) in pairwise(weekdays)
    ]
    expected = statistics.mean(excess) / statistics.stdev(excess) * math.sqrt(252)
    assert r.sharpe == pytest.approx(expected)
    assert r.observations == len(excess)


def test_too_few_observations_or_no_rate_give_a_reason() -> None:
    returns = [DailyReturn(FRI, MON, 0.01)] * 10
    few = sharpe_ratio(returns, RateSeries([(FRI, 3.0)], max_age=None))
    assert few.sharpe is None and few.observations == 10
    assert few.reason is not None and "at least 60" in few.reason
    none = sharpe_ratio(returns, RateSeries([], max_age=None))
    assert none.sharpe is None and none.reason == "no risk-free rate stored for the window"
    flat = sharpe_ratio(returns, RateSeries([(FRI, 3.0)], max_age=None), min_observations=2)
    assert flat.sharpe is None and flat.reason is not None and "do not vary" in flat.reason
    assert sharpe_ratio([], RateSeries([], max_age=None)).reason == "no daily returns in the window"


def test_benchmark_currency() -> None:
    assert benchmark_currency("^spx") == "USD"
    assert benchmark_currency("^px") == "CZK"
    assert benchmark_currency("sap.de") == "EUR"
    assert benchmark_currency("^zzz") is None


# ---------------------------------------------------------------------------
# GET /portfolio/risk
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


START = D(2026, 1, 5)  # a Monday
N_DAYS = 140


def _level(i: int) -> float:
    return 100_000 * math.prod(1 + 0.003 * math.sin(k) + 0.0004 for k in range(1, i + 1))


async def _seed(pool: asyncpg.Pool, with_rate: bool = True) -> None:
    job_id = await pool.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'schedule',"
        " 'succeeded') returning id"
    )
    deposit_day = START + dt.timedelta(days=50)
    for i in range(N_DAYS):
        day = START + dt.timedelta(days=i)
        # A 50 000 deposit lands on deposit_day: the total jumps, the index must not.
        total = _level(i) + (50_000 * _level(i) / _level(50) if day >= deposit_day else 0)
        await pool.execute(
            "insert into account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
            " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
            " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency,"
            " fx_source, fx_rate, cash_base, invested_base, total_base, weight_basis) values"
            " ($1, $2, now(), 1, 'CZK', $3, 0, 0, 0, $3, $3, 0, 0, 'CZK', 'cnb', 1, 0, $3, $3,"
            " 'total')",
            day,
            job_id,
            Decimal(str(round(total, 6))),
        )
        await pool.execute(
            "insert into price_bar (symbol, source, date, close) values ('^spx', 'stooq', $1, $2)",
            day,
            Decimal(str(round(5000 * (1 + 0.001 * i), 4))),
        )
        await pool.execute(
            "insert into fx_rate (source, rate_date, currency, home_currency, rate,"
            " published_amount, published_rate) values ('cnb', $1, 'USD', 'CZK', 20, 1, 20)",
            day,
        )
    await pool.execute(
        "insert into transaction (id, type, subtype, amount, currency, occurred_at, raw)"
        " values ('cash:dep', 'cash', 'DEPOSIT', 50000, 'CZK', $1, '{}')",
        dt.datetime.combine(deposit_day, dt.time(9), tzinfo=dt.UTC),
    )
    if with_rate:
        # A fixing every business day (as CZEONIA is published): 3.50, then 3.25 from 2 Mar.
        fixings = [
            (d, "3.50" if d < D(2026, 3, 2) else "3.25")
            for d in (START - dt.timedelta(days=3 - i) for i in range(N_DAYS + 3))
            if d.weekday() < 5
        ]
        for day, value in fixings:
            await pool.execute(
                "insert into macro_series (series_id, source, period, value, label, unit, url)"
                " values ('czeonia', 'cnb', $1, $2, 'CZEONIA', '%', 'https://www.cnb.cz/x')",
                day,
                Decimal(value),
            )


@pytest.mark.db
async def test_risk_endpoint(api: httpx.AsyncClient, backend_pool: asyncpg.Pool) -> None:
    await _seed(backend_pool)
    body = (await api.get("/portfolio/risk", params={"days": 365})).json()
    assert body["base_currency"] == "CZK"
    assert body["risk_free"]["series_id"] == "czeonia"
    assert body["risk_free"]["latest_rate"] == pytest.approx(0.0325)
    latest = START + dt.timedelta(days=N_DAYS - 1)
    while latest.weekday() >= 5:
        latest -= dt.timedelta(days=1)
    assert body["risk_free"]["latest_date"] == latest.isoformat()
    assert not body["risk_free"]["fallback"]

    # Independent: the deposit-free level series on weekdays, excess over the rate in force.
    weekdays = [
        (START + dt.timedelta(days=i), _level(i))
        for i in range(N_DAYS)
        if (START + dt.timedelta(days=i)).weekday() < 5
    ]
    excess = [
        b / a - 1 - (3.5 if pd < D(2026, 3, 2) else 3.25) / 100 / 252
        for (pd, a), (_, b) in pairwise(weekdays)
    ]
    port = body["portfolio"]
    assert port["observations"] == len(excess) == 99
    assert port["sharpe"] == pytest.approx(
        statistics.mean(excess) / statistics.stdev(excess) * math.sqrt(252), rel=1e-6
    )
    assert port["window_start"] == START.isoformat() and port["reason"] is None
    assert port["currency"] == "CZK"

    bench = body["benchmark"]
    assert body["benchmark_symbol"] == "^spx" and bench["currency"] == "CZK"
    assert bench["observations"] == 99 and bench["sharpe"] is not None

    # A shorter window than 60 trading days: no ratio, and why.
    short = (await api.get("/portfolio/risk", params={"days": 30})).json()
    assert short["portfolio"]["sharpe"] is None
    assert "at least 60" in short["portfolio"]["reason"]


@pytest.mark.db
async def test_risk_without_a_rate_or_history(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    empty = (await api.get("/portfolio/risk")).json()
    assert empty["portfolio"]["sharpe"] is None
    assert empty["portfolio"]["reason"] == "no portfolio snapshot yet"
    await _seed(backend_pool, with_rate=False)
    body = (await api.get("/portfolio/risk")).json()
    assert body["risk_free"] is None and body["portfolio"]["sharpe"] is None
    assert "no risk-free rate stored for CZK" in body["portfolio"]["reason"]
    # The repo rate stands in when CZEONIA has never been collected, and says so.
    await backend_pool.execute(
        "insert into macro_series (series_id, source, period, value, label, unit, url)"
        " values ('cnb_repo_2w', 'cnb', '2025-05-09', 3.5, 'ČNB 2W repo', '%', 'https://x')"
    )
    body = (await api.get("/portfolio/risk")).json()
    assert body["risk_free"]["fallback"] is True and body["portfolio"]["sharpe"] is not None
