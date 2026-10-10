"""Q5 against a real database: the before and after of a trade, the starting size, the deposit
plan and the above-cap helper. Needs STEWARD_TEST_DATABASE_URL (see conftest)."""

import asyncpg
import numpy as np
import pytest

from steward.config.store import save_config
from steward.config.strategy import PositionWeightRule, StrategyConfig
from steward.config.thesis import ThesesConfig
from steward.decision.allocator import allocate_deposit
from steward.decision.pretrade import TradeLeg
from steward.portfolio.concentration import concentration_report
from steward.portfolio.sizing import start_size
from steward.portfolio.trade_risk import trade_risk
from tests.test_quant_db import DAYS, _seed, _walk


async def _setup(pool: asyncpg.Pool) -> None:
    await _seed(pool)
    market = np.random.default_rng(1).normal(0.0003, 0.009, len(DAYS))
    closes = _walk(9, 80, 0.0001, 0.010, market, 0.8)
    await pool.executemany(
        "insert into public.price_bar (symbol, date, close, source) values ('ccc.us', $1, $2,"
        " 'stooq')",
        [(d, float(c)) for d, c in zip(DAYS, closes, strict=True)],
    )
    await pool.execute(
        "insert into public.instrument (t212_ticker, isin, name, currency, price_symbol)"
        " values ('CCC_US_EQ', 'USCCC0000000', 'Gamma', 'USD', 'ccc.us')"
    )
    await pool.execute(
        "update public.instrument set instrument_type = 'ETF' where t212_ticker = 'AAA_US_EQ'"
    )
    strategy = StrategyConfig(
        base_currency="CZK",
        categories=["risk"],
        classifications={
            "AAA_US_EQ": {"risk": "core"},
            "BBB_US_EQ": {"risk": "growth"},
            "CCC_US_EQ": {"risk": "growth"},
        },
        rules=[
            PositionWeightRule(
                id="max-stock",
                type="position_weight",
                operator="<=",
                value=0.10,
                ticker="BBB_US_EQ",
            )
        ],
    )
    async with pool.acquire() as conn:
        await save_config(conn, "strategy", strategy, created_by=None)


@pytest.mark.db
async def test_a_trade_into_a_stock_not_held_is_modelled_before_and_after(
    backend_pool: asyncpg.Pool,
) -> None:
    await _setup(backend_pool)
    risk = await trade_risk(
        backend_pool, [TradeLeg(side="buy", ticker="CCC_US_EQ", value_base=100_000)]
    )
    assert risk.available and risk.reason is None
    line = next(x for x in risk.lines if x.ticker == "CCC_US_EQ")
    assert line.weight_before == 0.0 and line.weight_after == pytest.approx(0.10)
    assert line.risk_share_before is None and line.risk_share_after is not None
    assert risk.not_modelled  # Expected Shortfall and the drawdown probability are named
    missing = await trade_risk(
        backend_pool, [TradeLeg(side="buy", ticker="NOPE_US_EQ", value_base=50_000)]
    )
    assert missing.available and any("NOPE_US_EQ" in w for w in missing.warnings)


@pytest.mark.db
async def test_the_starting_size_is_the_smaller_of_the_tier_cap_and_the_risk_ceiling(
    backend_pool: asyncpg.Pool,
) -> None:
    await _setup(backend_pool)
    out = await start_size(backend_pool, "CCC_US_EQ")
    assert out.status == "ok" and out.tier == "growth" and out.tier_cap == pytest.approx(0.03)
    assert out.suggested is not None and out.suggested <= out.tier_cap + 1e-9
    assert out.suggested_value == pytest.approx(out.suggested * 1_000_000)
    assert out.kelly is not None and not out.kelly.available  # no thesis stored
    unknown = await start_size(backend_pool, "NOPE_US_EQ")
    assert unknown.status == "unavailable" and unknown.reason


@pytest.mark.db
async def test_scenarios_feed_the_kelly_bound_and_the_score_scales_the_start_size(
    backend_pool: asyncpg.Pool,
) -> None:
    await _setup(backend_pool)
    price = float(
        await backend_pool.fetchval(
            "select close from public.price_bar where symbol = 'ccc.us' order by date desc limit 1"
        )
    )
    theses = ThesesConfig.model_validate(
        {
            "theses": [
                {
                    "ticker": "CCC_US_EQ",
                    "why_owned": "test",
                    "conviction": 4,
                    "price_target": {
                        "currency": "USD",
                        "method": "DCF",
                        "set_on": "2026-10-01",
                        "scenarios": {
                            "bear": round(price * 0.9, 2),
                            "base": round(price * 1.4, 2),
                            "bull": round(price * 1.8, 2),
                        },
                    },
                }
            ]
        }
    )
    strategy = StrategyConfig(
        base_currency="CZK",
        categories=["risk"],
        classifications={"AAA_US_EQ": {"risk": "core"}, "CCC_US_EQ": {"risk": "growth"}},
    )
    strategy.pretrade.score_for_full_start = 10.0  # a score no stock reaches: starts tiny
    await backend_pool.execute(
        "insert into public.macro_series (series_id, source, period, value, label, unit, url)"
        " values ('czeonia', 'cnb', current_date, 3.5, 'CZEONIA', '%', 'https://x.test')"
    )
    async with backend_pool.acquire() as conn:
        await save_config(conn, "theses", theses, created_by=None)
        await save_config(conn, "strategy", strategy, created_by=None)
    out = await start_size(backend_pool, "CCC_US_EQ")
    assert out.kelly is not None
    if not out.kelly.available:  # e.g. the test database stores no cash rate
        pytest.skip(f"no Kelly bound in the test database: {out.kelly.reason}")
    assert out.kelly.thesis_return is not None and out.kelly.thesis_return > 0
    assert out.score is not None and out.score_size is not None
    assert out.score_size == pytest.approx(0.03 * min(out.score / 10.0, 1.0))
    assert out.binding == "score" and out.suggested == pytest.approx(out.score_size)


@pytest.mark.db
async def test_a_deposit_goes_to_the_core_fund_in_whole_shares(
    backend_pool: asyncpg.Pool,
) -> None:
    await _setup(backend_pool)
    plan = await allocate_deposit(backend_pool, 200_000)
    assert plan.spent <= 200_000 and plan.left_over == pytest.approx(200_000 - plan.spent)
    bought = {line.ticker: line for line in plan.lines}
    # BBB has no thesis (principle 8a), so only the core fund is eligible; CCC has none either,
    # so it is not on the bench at all.
    assert set(bought) <= {"AAA_US_EQ"}
    reasons = {s.ticker: s.reason for s in plan.set_aside}
    assert "thesis" in reasons["BBB_US_EQ"] and "CCC_US_EQ" not in reasons
    for line in bought.values():
        assert line.value == pytest.approx(line.shares * line.price_base)


@pytest.mark.db
async def test_a_stock_far_above_the_cap_is_listed_and_a_fund_is_exempt(
    backend_pool: asyncpg.Pool,
) -> None:
    await _setup(backend_pool)
    report = await concentration_report(backend_pool, monthly_deposit=20_000)
    assert report.reason is None and report.cap == pytest.approx(0.10)
    over = {p.ticker: p for p in report.positions}
    assert "AAA_US_EQ" not in over and report.exempt == ["AAA_US_EQ"]
    beta = over["BBB_US_EQ"]
    assert beta.zone == "above_backstop" and beta.trim_weight == pytest.approx(0.10)
    assert beta.trim_value == pytest.approx(360_000 - 100_000)
