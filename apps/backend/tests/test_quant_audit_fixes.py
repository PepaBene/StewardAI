"""Fixes from the 10 October 2026 quant audit (docs/revision/quant_risk_audit_2026-10-10.md)."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from steward.decision import allocator as al
from steward.portfolio.risk_limits import unmeasured_keys
from steward.quant import french, french_store


def test_a_failed_measurement_keeps_the_open_breaches_it_stands_behind() -> None:
    keys = unmeasured_keys({"es": "x", "drawdown": "y", "volatility_forecast": "z"})
    assert {"risk:es", "risk:drawdown", "risk:drawdown_warning", "risk:volatility_forecast"} <= keys
    assert unmeasured_keys({}) == frozenset()

    def held(key: str) -> bool:  # the rule record_breaches applies
        return any(key == p or key.startswith(p + ":") for p in keys)

    assert held("risk:tier:AAPL_US_EQ") and held("risk:drawdown_warning:10")
    assert not held("risk:tier_budget:core") and not held("risk:volatility")


def test_a_gbx_quote_is_scaled_against_a_gbp_target() -> None:
    book = SimpleNamespace(positions=[])
    gbp = SimpleNamespace(
        price_target=SimpleNamespace(currency="GBP", scenarios=None, sell_target=12.0)
    )
    got = al._candidate(book, gbp, "LLOYl_EQ", "Lloyds", "GBX", 0.5, 1000.0, False, 0.0)  # type: ignore[arg-type]
    assert got.thesis_return == pytest.approx(0.2)  # 12 GBP against 1000 pence = 10 GBP
    usd = SimpleNamespace(
        price_target=SimpleNamespace(currency="USD", scenarios=None, sell_target=12.0)
    )
    other = al._candidate(book, usd, "X", "X", "GBX", 0.5, 1000.0, False, 0.0)  # type: ignore[arg-type]
    assert other.thesis_return is None


def test_expected_return_scales_gbx_against_a_gbp_target() -> None:
    from steward.decision import pretrade as pt

    facts = pt.TickerFacts(
        ticker="X", has_thesis=True, sell_target=12.0, price=1000.0,
        quote_currency="GBX", target_currency="GBP",
    )  # fmt: skip
    got, why = pt.expected_return(facts)
    assert why is None and got == pytest.approx(0.2)
    usd = pt.TickerFacts(
        ticker="X", has_thesis=True, sell_target=12.0, price=10.0,
        quote_currency="GBX", target_currency="USD",
    )  # fmt: skip
    assert pt.expected_return(usd)[0] is None


def test_impossible_trades_are_named() -> None:
    from steward.portfolio import exante as ea
    from steward.portfolio.trade_risk import impossible_trades

    book = SimpleNamespace(
        total_value=1000.0,
        positions=[ea.BookPosition("A", None, "USD", 600.0)],  # 400 cash
    )
    assert impossible_trades(book, {"A": -200.0}, 0.0) == []  # type: ignore[arg-type]
    assert impossible_trades(book, {"B": 300.0}, 0.0) == []  # type: ignore[arg-type]
    over_sale = impossible_trades(book, {"A": -700.0}, 0.0)  # type: ignore[arg-type]
    assert len(over_sale) == 1 and "whole position" in over_sale[0]
    over_buy = impossible_trades(book, {"B": 500.0}, 0.0)  # type: ignore[arg-type]
    assert len(over_buy) == 1 and "negative cash" in over_buy[0]
    assert impossible_trades(book, {"B": 500.0}, 100.0) == []  # type: ignore[arg-type]


def test_a_stock_without_fundamentals_cannot_be_core() -> None:
    from steward.config.strategy import RiskLimits
    from steward.portfolio.risk_tier import Fundamentals, derive_tier

    limits = RiskLimits()
    bare = derive_tier("X", 0.15, Fundamentals(True, None, None), limits)
    assert bare is not None and bare.tier == "growth" and "fundamentals" in bare.reason
    assert derive_tier("F", 0.15, None, limits).tier == "core"  # type: ignore[union-attr]


def test_cornish_fisher_gives_nothing_where_the_expansion_folds_over() -> None:
    import numpy as np

    from steward.quant import tail as tl

    rng = np.random.default_rng(1)
    calm = rng.normal(0.002, 0.01, 156)
    assert tl.cornish_fisher(calm, 0.95) is not None
    wild = calm.copy()
    wild[:3] = -0.35
    assert tl.cornish_fisher(wild, 0.95) is None


async def test_chat_tools_reject_amounts_outside_the_route_bounds() -> None:
    from steward.chat import analytics_tools as at

    assert "error" in await at.deposit_allocation(None, None, float("nan"))  # type: ignore[arg-type]
    assert "error" in await at.deposit_allocation(None, None, -5.0)  # type: ignore[arg-type]
    assert "error" in await at.risk_whatif(None, None, [], 0.0)  # type: ignore[arg-type]
    assert "error" in await at.risk_whatif(None, None, [], 2e9)  # type: ignore[arg-type]


def test_the_stored_security_type_says_what_an_instrument_is() -> None:
    from steward.portfolio.risk_tier import instrument_is_fund, instrument_is_stock

    # The live instrument table has no instrument_type; only security_type is filled.
    assert instrument_is_stock(None, "Common Stock") and instrument_is_stock(
        None, "Depositary Receipt"
    )
    assert instrument_is_fund(None, "Mutual Fund") and not instrument_is_fund(None, "Common Stock")
    assert instrument_is_stock("STOCK", None) and instrument_is_fund("ETF", None)
    assert not instrument_is_stock(None, None) and not instrument_is_fund(None, None)


async def test_loss_years_use_the_companyfacts_history_beyond_the_filed_facts() -> None:
    import datetime as dt

    from steward.portfolio import risk_tier

    def year(y: int) -> dt.date:
        return dt.date(y, 12, 31)

    class Pool:
        async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
            if "companyfacts" in sql:
                # an older loss year, and 2024 again with a different value (the filed fact wins)
                return [
                    {"ticker": "X", "period_end": year(2021), "value": -5.0},
                    {"ticker": "X", "period_end": year(2024), "value": -9.0},
                ]
            return [
                {"ticker": "X", "period_end": year(2023), "value": 4.0},
                {"ticker": "X", "period_end": year(2024), "value": 6.0},
                {"ticker": "X", "period_end": year(2025), "value": 8.0},
            ]

    got = await risk_tier._loss_years(Pool(), ["X"])  # type: ignore[arg-type]
    assert got == {"X": 1}  # 2021 counts; the filed 2024 profit is not overridden


def test_drawdown_rungs_in_one_percent_are_rejected() -> None:
    from steward.config.strategy import RiskLimits

    with pytest.raises(ValueError, match="percentage point"):
        RiskLimits(drawdown_warnings=[0.075, 0.08])
    assert RiskLimits(drawdown_warnings=[0.05, 0.10]).drawdown_warnings == [0.05, 0.10]


def test_an_order_filled_in_several_rows_still_pairs_as_a_switch() -> None:
    import datetime as dt

    from steward.journal.switches import Fill, pair_switches

    day = dt.date(2026, 3, 2)
    fills = [
        Fill(ticker="A", side="SELL", on=day, amount=1000.0),
        Fill(ticker="B", side="BUY", on=day, amount=300.0),
        Fill(ticker="B", side="BUY", on=day, amount=300.0),
        Fill(ticker="B", side="BUY", on=day, amount=380.0),
    ]
    found = pair_switches(fills, set())
    assert len(found) == 1 and found[0].bought_value_base == 980.0


async def test_a_response_that_is_not_a_zip_fails_one_file_not_the_job() -> None:
    steps: dict[str, dict[str, object]] = {}

    class Ctx:
        pool = None

        @asynccontextmanager
        async def step(self, name: str):
            detail: dict[str, object] = {}
            steps[name] = detail
            yield SimpleNamespace(detail=detail)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>maintenance</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await french_store.refresh_factors(Ctx(), http)  # type: ignore[arg-type]
    assert len(steps) == len(french.FILES)
    assert all("error" in d for d in steps.values())
