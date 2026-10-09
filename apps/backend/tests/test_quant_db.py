"""The quant risk layer against a real database (quant plan gap G2): loading a book from stored
prices and rates, the ex-ante report, the limits built on it, the replay, the factor exposures,
the saved reports and the saved scenarios. Needs STEWARD_TEST_DATABASE_URL (see conftest)."""

import datetime as dt
import json

import asyncpg
import numpy as np
import pytest

from steward.config.strategy import RiskLimits, StrategyConfig
from steward.portfolio import factor_shock as fs
from steward.portfolio.exante import ex_ante_risk
from steward.portfolio.factors import factor_exposures
from steward.portfolio.replay import stress_replay
from steward.portfolio.risk_limits import evaluate_risk, gather_risk_inputs
from steward.portfolio.risk_snapshots import get_snapshot, list_snapshots
from steward.portfolio.snapshot import PositionRow

TODAY = dt.date.today()
DAYS = [
    TODAY - dt.timedelta(days=i)
    for i in range(5 * 365, -1, -1)
    if (TODAY - dt.timedelta(days=i)).weekday() < 5
]
SNAPSHOT_DATE = TODAY


def _walk(
    seed: int, start: float, drift: float, vol: float, common: np.ndarray, beta: float
) -> list[float]:
    rng = np.random.default_rng(seed)
    steps = drift + beta * common + rng.normal(0, vol, len(DAYS))
    return list(start * np.exp(np.cumsum(steps)))


async def _seed(pool: asyncpg.Pool) -> list[PositionRow]:
    rng = np.random.default_rng(1)
    market = rng.normal(0.0003, 0.009, len(DAYS))
    series = {
        "aaa.us": _walk(2, 100, 0.0002, 0.008, market, 1.1),
        "bbb.us": _walk(3, 50, 0.0001, 0.011, market, 0.7),
        "^spx": _walk(4, 4000, 0.0, 0.0, market, 1.0),
    }
    await pool.executemany(
        "insert into public.price_bar (symbol, date, close, source) values ($1, $2, $3, 'stooq')",
        [
            (sym, d, float(c))
            for sym, closes in series.items()
            for d, c in zip(DAYS, closes, strict=True)
        ],
    )
    # ECB reference rates: units of EUR for one unit of the currency
    fx_rows = []
    for i, d in enumerate(DAYS):
        for ccy, rate in (
            ("USD", 0.92 + 0.02 * np.sin(i / 40)),
            ("CZK", 0.040 + 0.0005 * np.cos(i / 55)),
        ):
            fx_rows.append((d, ccy, float(rate)))
    await pool.executemany(
        "insert into public.fx_rate (source, rate_date, currency, home_currency, rate,"
        " published_amount, published_rate) values ('ecb', $1, $2, 'EUR', $3, 1, $3)",
        fx_rows,
    )
    job_id = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values ('portfolio_sync', 'manual',"
        " 'succeeded') returning id"
    )
    await pool.execute(
        "insert into public.account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency,"
        " fx_source, fx_rate, cash_base, invested_base, total_base, weight_basis) values"
        " ($2, $1, now(), 1, 'CZK', 1000000, 100000, 0, 0, 900000, 900000, 0, 0, 'CZK', 'cnb',"
        " 1, 100000, 900000, 1000000, 'total')",
        job_id,
        SNAPSHOT_DATE,
    )
    rows: list[PositionRow] = []
    for ticker, name, symbol, value in (
        ("AAA_US_EQ", "Alpha", "aaa.us", 540000),
        ("BBB_US_EQ", "Beta", "bbb.us", 360000),
    ):
        await pool.execute(
            "insert into public.instrument (t212_ticker, isin, name, currency, price_symbol)"
            " values ($1, $2, $3, 'USD', $4)",
            ticker,
            "US" + ticker[:3] + "0000000",
            name,
            symbol,
        )
        await pool.execute(
            "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at,"
            " quantity, avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
            " values ($1, $2, $3, now(), 10, 1, 1, 'USD', 1, 'CZK', $4, $4, 0, 'CZK', 'cnb', 22,"
            " $4)",
            SNAPSHOT_DATE,
            ticker,
            job_id,
            value,
        )
        rows.append(_position(ticker, name, value))
    return rows


def _position(ticker: str, name: str, value: int) -> PositionRow:
    from decimal import Decimal

    return PositionRow(
        ticker, name, None, Decimal(10), Decimal(1), Decimal(1), "USD", Decimal(1), "CZK",
        Decimal(value), Decimal(value), Decimal(0), None, Decimal(22), None, Decimal(value), None,
    )  # fmt: skip


async def _seed_factors(pool: asyncpg.Pool) -> None:
    rng = np.random.default_rng(8)
    rows = []
    for d in DAYS:
        for factor, sd in (
            ("MKT_RF", 0.009),
            ("SMB", 0.004),
            ("HML", 0.004),
            ("RMW", 0.003),
            ("CMA", 0.003),
            ("MOM", 0.005),
        ):
            rows.append(("developed", factor, d, float(rng.normal(0, sd))))
        rows.append(("developed", "RF", d, 0.0001))
    await pool.executemany(
        "insert into public.factor_return (region, factor, date, value) values ($1, $2, $3, $4)",
        rows,
    )


@pytest.mark.db
async def test_the_ex_ante_report_loads_from_stored_prices_and_is_saved_once(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed(backend_pool)
    report = await ex_ante_risk(backend_pool)
    assert report.reason is None and report.volatility_forecast is not None
    assert {p.status for p in report.positions} == {"real"}
    assert sum(p.risk_share or 0 for p in report.positions) == pytest.approx(1.0)
    assert report.coverage is not None and report.coverage.uncovered_weight == pytest.approx(0.0)
    assert report.volatility_forecast_scaled == pytest.approx(report.volatility_forecast)
    # a second request for the same data date saves nothing new
    await ex_ante_risk(backend_pool)
    saved = await list_snapshots(backend_pool, "ex_ante")
    assert len(saved) == 1 and saved[0].method_version == report.method_version
    stored = await get_snapshot(backend_pool, saved[0].id)
    assert stored is not None and stored["volatility_forecast"] == pytest.approx(
        report.volatility_forecast
    )


@pytest.mark.db
async def test_the_volatility_limit_opens_a_forecast_finding_and_clears_when_it_is_met(
    backend_pool: asyncpg.Pool,
) -> None:
    positions = await _seed(backend_pool)
    tight = StrategyConfig(base_currency="CZK", risk=RiskLimits(max_volatility=0.01))
    inputs = await gather_risk_inputs(backend_pool, tight, positions)
    assert inputs.volatility_forecast is not None and inputs.volatility_forecast > 0.01
    assert "risk:volatility_forecast" in {f.key for f in evaluate_risk(inputs, tight.risk)}
    loose = StrategyConfig(base_currency="CZK", risk=RiskLimits(max_volatility=1.0))
    cleared = await gather_risk_inputs(backend_pool, loose, positions)
    assert "risk:volatility_forecast" not in {f.key for f in evaluate_risk(cleared, loose.risk)}
    # the sync path saved the report it measured
    assert [s.kind for s in await list_snapshots(backend_pool)] == ["ex_ante"]


@pytest.mark.db
async def test_the_replay_and_the_factor_exposures_run_and_are_saved(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed(backend_pool)
    await _seed_factors(backend_pool)
    replay = await stress_replay(backend_pool)
    assert replay.reason is None and len(replay.episodes) >= 5
    assert {e.status for e in replay.episodes} <= {"replayed", "no_data"}
    replayed = [e for e in replay.episodes if e.status == "replayed"]
    assert replayed and all(e.after == pytest.approx(e.before + (e.pnl or 0)) for e in replayed)

    exposures = await factor_exposures(backend_pool, "developed")
    assert exposures.reason is None and exposures.portfolio is not None
    assert {b.factor for b in exposures.portfolio.betas} >= {"MKT_RF", "HML", "USD"}
    empty = await factor_exposures(backend_pool, "europe")
    assert empty.portfolio is None and empty.reason is not None

    kinds = {(s.kind, s.scope) for s in await list_snapshots(backend_pool)}
    assert {("replay", ""), ("factors", "developed")} <= kinds


@pytest.mark.db
async def test_an_idea_with_stored_prices_is_scored_and_one_without_is_listed(
    backend_pool: asyncpg.Pool,
) -> None:
    from steward.portfolio.idea_fit import idea_fit

    await _seed(backend_pool)
    await _seed_factors(backend_pool)
    idea = _walk(9, 80, 0.0002, 0.012, np.random.default_rng(5).normal(0, 0.009, len(DAYS)), 0.5)
    await backend_pool.executemany(
        "insert into public.price_bar (symbol, date, close, source) values ('ccc.us', $1, $2,"
        " 'stooq')",
        [(d, float(c)) for d, c in zip(DAYS, idea, strict=True)],
    )
    report = await idea_fit(backend_pool, ["ccc.us", "nope.us"], "USD", "developed", 0.1)
    assert report.reason is None
    by = {i.symbol: i for i in report.ideas}
    assert by["nope.us"].status == "no_prices" and by["nope.us"].relief is None
    scored = by["ccc.us"]
    assert scored.status == "ok" and scored.relief is not None
    assert scored.relief == pytest.approx(scored.imbalance_before - scored.imbalance_after)
    assert scored.dominant_factor is not None and scored.tilts
    assert report.ideas[0].symbol == "ccc.us"  # scored ideas come before unpriced ones


@pytest.mark.db
async def test_derived_tiers_come_from_the_stocks_own_numbers_and_are_stored(
    backend_pool: asyncpg.Pool,
) -> None:
    from steward.portfolio.exante import book_from_snapshot
    from steward.portfolio.risk_tier import with_saved_tiers

    await _seed(backend_pool)
    await backend_pool.execute(
        "insert into public.fundamentals (ticker, source, basis, currency, document_ref,"
        " document_url, period_end, figures, raw, fetched_at) values ('AAA_US_EQ', 'alphavantage',"
        " 'ttm', 'USD', 'overview', 'https://example.com/overview', current_date, $1::jsonb,"
        " '{}'::jsonb, now())",
        json.dumps(
            {"AssetType": "Common Stock", "MarketCapitalization": "300000000000",
             "ProfitMargin": "0.2", "Currency": "USD"}
        ),
    )  # fmt: skip
    strategy = StrategyConfig(
        base_currency="CZK",
        categories=["risk"],
        risk=RiskLimits(auto_tiers=True, tier_speculative_volatility=0.35),
    )
    stored = await with_saved_tiers(backend_pool, strategy)
    assert stored.classifications == {}  # nothing derived yet
    from steward.config.store import save_config

    async with backend_pool.acquire() as conn:
        await save_config(conn, "strategy", strategy, created_by=None)
    _book, applied = await book_from_snapshot(backend_pool)
    assert applied is not None
    tiers = {t: b["risk"] for t, b in applied.classifications.items()}
    assert set(tiers) == {"AAA_US_EQ", "BBB_US_EQ"}  # BBB has no fundamentals: judged as a fund
    saved = await backend_pool.fetch("select ticker, tier, kind from public.holding_risk_tier")
    assert {r["ticker"]: r["tier"] for r in saved} == tiers
    assert {r["ticker"]: r["kind"] for r in saved}["AAA_US_EQ"] == "stock"
    again = await with_saved_tiers(backend_pool, strategy)
    assert {t: b["risk"] for t, b in again.classifications.items()} == tiers


@pytest.mark.db
async def test_a_saved_scenario_runs_and_each_run_is_kept_with_its_loadings(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed(backend_pool)
    await _seed_factors(backend_pool)
    spec = fs.ShockSpec(market=-0.2, usd=-0.1)
    saved = await fs.save_scenario(backend_pool, fs.ScenarioIn(name="Sell-off", spec=spec))
    again = await fs.save_scenario(
        backend_pool, fs.ScenarioIn(name="Sell-off", spec=fs.ShockSpec(market=-0.3))
    )
    assert again.id == saved.id and again.spec.market == -0.3  # same name replaces
    result = await fs.run_shock(backend_pool, again.spec, "developed", again.id)
    # the seeded factors are unrelated to the seeded prices, so the sign of the move is arbitrary
    assert result.reason is None and result.loss is not None and result.loss != 0
    assert result.after == pytest.approx(result.before + (result.pnl or 0))
    runs = [s for s in await list_snapshots(backend_pool, "shock")]
    assert len(runs) == 1 and runs[0].scope.startswith(f"{again.id}:developed:")
    payload = await get_snapshot(backend_pool, runs[0].id)
    assert payload is not None and json.loads(json.dumps(payload))["spec"]["market"] == -0.3
    assert await fs.delete_scenario(backend_pool, again.id) is True
    assert await fs.list_scenarios(backend_pool) == []
