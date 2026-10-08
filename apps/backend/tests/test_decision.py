"""Phase 6 (risk, scenarios, rebalancing): hand-worked cases for the pure calculators.

Risk statistics use short series worked by hand; scenario, theme and rebalance cases use the
Phase 5 test portfolio (1000 EUR: AAPL direct 200, CSPX 500, VWCE 200 not looked through, cash
100). Expected figures are written out in the comments, not taken from the code under test.
"""

import datetime as dt
from decimal import Decimal

import pytest

from steward.analytics import risk as stats
from steward.analytics.thresholds import THRESHOLDS
from steward.config.jobs import CSPX_ISIN
from steward.config.strategy import (
    CashWeightRule,
    PositionTarget,
    PositionWeightRule,
    RiskLimits,
    StrategyConfig,
    TargetBand,
)
from steward.decision import pretrade as pt
from steward.decision import rebalance as rb
from steward.decision.lot_impact import OpenLot, consume
from steward.decision.shocks import (
    MeasuredInputs,
    Sensitivity,
    ShockAssumptions,
    ShockSet,
    apply_shocks,
)
from steward.decision.themes import ThemeDefinition, ThemeSelector, theme_exposure
from steward.portfolio.drift import HoldingValue, compute_drift
from steward.portfolio.risk_limits import RiskInputs, ValuationFlag, evaluate_risk
from tests.test_xray import ctx_for, data

D = Decimal


def dec(values: list[float]) -> list[Decimal]:
    return [D(str(v)) for v in values]


# --------------------------------------------------------------------------- risk statistics


def test_correlation_of_a_series_with_a_scaled_copy_is_one_and_with_its_negative_minus_one() -> (
    None
):
    n = THRESHOLDS.min_obs_correlation
    a = dec([((-1) ** i) * 0.01 * (1 + i % 5) for i in range(n)])
    assert stats.correlation(a, [x * 2 for x in a]).value == D("1.000000")
    assert stats.correlation(a, [-x for x in a]).value == D("-1.000000")


def test_correlation_is_withheld_below_the_minimum_with_the_reason() -> None:
    got = stats.correlation(dec([0.01] * 10), dec([0.02] * 10))
    assert got.value is None
    assert "minimum" in got.warnings[0]


def test_tracking_error_is_the_annualised_stdev_of_the_active_return() -> None:
    n = THRESHOLDS.min_obs_tracking_error
    bench = dec([0.0] * n)
    # active return alternates +1% / -1%: sample stdev = sqrt(n/(n-1)) x 0.01 for even n
    port = dec([0.01 if i % 2 == 0 else -0.01 for i in range(n)])
    expected = (D(n) / D(n - 1)).sqrt() * D("0.01") * D(252).sqrt()
    got = stats.tracking_error(port, bench)
    assert got.value == pytest.approx(expected, abs=D("1e-6"))
    assert stats.tracking_error(port[:-1], bench).value is None  # not aligned


def test_sortino_uses_only_the_shortfall_below_the_risk_free_rate() -> None:
    n = THRESHOLDS.min_obs_volatility
    returns = dec([0.02 if i % 2 == 0 else -0.01 for i in range(n)])
    zero = dec([0.0] * n)
    # mean = 0.005; shortfall = sqrt(mean(min(r,0)^2)) = sqrt(0.5 x 0.0001) = 0.0070710678
    expected = D("0.005") * 252 / (D("0.5").sqrt() * D("0.01") * D(252).sqrt())
    assert stats.sortino(returns, zero).value == pytest.approx(expected, abs=D("1e-4"))
    # a series with no losing period has no downside to divide by
    assert stats.sortino(dec([0.01] * n), zero).value is None


def test_diversification_benefit_is_zero_for_identical_holdings_and_high_for_opposites() -> None:
    n = THRESHOLDS.min_obs_diversification
    a = dec([0.01 if i % 2 == 0 else -0.01 for i in range(n)])
    same = stats.diversification_benefit([D("0.5"), D("0.5")], [a, list(a)])
    assert same.value == D("0.000000")
    opposite = stats.diversification_benefit([D("0.5"), D("0.5")], [a, [-x for x in a]])
    assert opposite.value == D("1.000000")  # the blend has no variance at all


def test_value_at_risk_and_shortfall_are_historical_and_need_250_observations() -> None:
    returns = dec([(-i) / 1000 for i in range(1, 251)])  # -0.001 ... -0.250
    var95, es95 = stats.var_es(returns, D("0.95"))
    # k = ceil(250 x 0.05) = 13 smallest: -0.250 ... -0.238; VaR = 0.238, ES = mean = 0.244
    assert var95.value == D("0.23800000")
    assert es95.value == D("0.24400000")
    assert stats.var_es(returns[:100])[0].value is None


def test_regression_sensitivity_needs_a_sample_and_a_fit() -> None:
    n = THRESHOLDS.min_obs_sensitivity
    factor = dec([0.01 * ((i % 7) - 3) for i in range(n)])
    beta, r2 = stats.regression_sensitivity([f * D("1.5") for f in factor], factor)
    assert beta.value == D("1.500000")
    assert r2 == D(1)
    short, _ = stats.regression_sensitivity(factor[:50], factor[:50])
    assert short.value is None and "minimum" in short.warnings[0]
    noise = dec([0.01 * (((i * 5) % 11) - 5) for i in range(n)])
    unrelated, fit = stats.regression_sensitivity(noise, factor)
    assert fit is not None and fit < D("0.05") and unrelated.value is None


def test_rolling_correlation_fills_only_full_windows() -> None:
    n = 100
    a = dec([0.01 * ((i % 7) - 3) for i in range(n)])
    out = stats.rolling_correlation(a, [x * 3 for x in a], 60)
    assert out[:59] == [None] * 59
    assert out[59] == D("1.000000") and out[-1] == D("1.000000")
    with pytest.raises(ValueError, match="at least"):
        stats.rolling_correlation(a, a, 10)


# ------------------------------------------------------------------------------ scenario shocks


def shocked(shocks: ShockSet, assumptions: ShockAssumptions | None = None, **measured: float):  # type: ignore[no-untyped-def]
    ctx = ctx_for(data())
    inputs = MeasuredInputs(
        equity_beta={
            t: Sensitivity(value=v, source="measured", detail="test") for t, v in measured.items()
        }
    )
    return apply_shocks(ctx.leafset, shocks, assumptions or ShockAssumptions(), inputs)


def test_equity_shock_uses_a_measured_beta_then_the_fallback_and_leaves_unknown_weight_alone() -> (
    None
):
    res = shocked(ShockSet(equity_pct=-0.2), AAPL_US_EQ=1.5)
    # AAPL direct 0.20 x (1.5 x -0.2) = -0.06; CSPX equity rows (0.9 of 0.5 = 0.45) x fallback
    # beta 1 x -0.2 = -0.09. The fund's own cash (0.025), the unknown part (0.025) and VWCE (0.20,
    # not looked through) have no valid sensitivity: they stay put and are listed.
    assert res.pnl_pct == pytest.approx(-0.15)
    assert res.after_total == pytest.approx(1000 * (1 - 0.15))
    assert res.unshocked_weight == pytest.approx(0.25)
    assert res.reconciles
    assert {u.reason.split(":")[0] for u in res.unshocked} >= {"the fund's own cash"}
    sources = {s for p in res.positions for s in p.sensitivities}
    assert any("measured" in s for s in sources) and any("fallback" in s for s in sources)


def test_with_no_fallback_a_holding_without_a_beta_is_not_shocked() -> None:
    res = shocked(
        ShockSet(equity_pct=-0.2), ShockAssumptions(unmeasured_equity_beta=None), AAPL_US_EQ=1.0
    )
    assert res.pnl_pct == pytest.approx(-0.04)  # only AAPL direct, 0.20 x -0.2
    # everything else that carries equity risk but has no beta stays put: CSPX 0.45 + VWCE 0.20
    # (and the fund's cash and unknown part, 0.05)
    assert res.unshocked_weight == pytest.approx(0.70)
    rows = {p.key: p for p in res.positions}
    assert rows["CSPX"].pnl == 0
    assert any("no valid beta" in u.reason for u in res.unshocked)


def test_an_owner_beta_overrides_a_measured_one_and_reaches_a_fund_that_is_not_looked_through() -> (
    None
):
    res = shocked(
        ShockSet(equity_pct=-0.1),
        ShockAssumptions(equity_beta={"VWCE": 0.5, "AAPL_US_EQ": 2.0}),
        AAPL_US_EQ=1.0,
    )
    rows = {p.key: p for p in res.positions}
    assert rows["AAPL_US_EQ"].pnl == pytest.approx(0.2 * -0.2)
    assert rows["VWCE"].pnl == pytest.approx(0.2 * -0.05)


def test_currency_move_translates_only_the_leaves_quoted_in_it() -> None:
    res = shocked(ShockSet(fx={"USD": -0.10}))
    # USD-quoted: AAPL direct 0.20, AAPL row 0.05, MSFT row 0.15 = 0.40 x -0.10
    assert res.pnl_pct == pytest.approx(-0.04)
    assert res.by_factor["fx"] == pytest.approx(-0.04)


def test_combined_shocks_keep_the_cross_term_so_the_factors_add_to_the_total() -> None:
    res = shocked(ShockSet(equity_pct=-0.2, fx={"USD": -0.1}), AAPL_US_EQ=1.0)
    assert sum(res.by_factor.values()) == pytest.approx(res.pnl_pct)
    assert sum(p.pnl for p in res.positions) == pytest.approx(res.pnl_pct)
    # AAPL direct: (1 - 0.2)(1 - 0.1) - 1 = -0.28, of which interaction = -0.28 + 0.2 + 0.1 = +0.02
    aapl = next(p for p in res.positions if p.key == "AAPL_US_EQ")
    assert aapl.pnl == pytest.approx(0.2 * -0.28)
    assert aapl.effects["interaction"] == pytest.approx(0.2 * 0.02)


def test_sector_and_country_moves_apply_only_to_labelled_leaves() -> None:
    res = shocked(ShockSet(sector={"Consumer Staples": -0.10}, country={"CH": -0.05}))
    # Nestle row in CSPX: 0.50 x 0.5 = 0.25 of the portfolio, sector -10 % and country -5 %
    assert res.pnl_pct == pytest.approx(0.25 * -0.15)
    nothing = shocked(ShockSet(sector={"Utilities": -0.3}))
    assert nothing.pnl_pct == 0
    assert nothing.unshocked_weight > 0  # leaves that were asked about and could not be placed


def test_rate_shock_needs_a_duration_and_otherwise_lists_what_it_could_not_move() -> None:
    res = shocked(ShockSet(rate_bp=200))
    assert res.pnl_pct == 0
    assert res.unshocked_weight > 0


def test_empty_shock_is_reported_not_silently_zero() -> None:
    res = shocked(ShockSet())
    assert res.pnl == 0 and any("No shock was set" in w for w in res.warnings)


# --------------------------------------------------------------------------------------- themes


def test_theme_counts_a_holding_once_and_keeps_every_path_and_the_unknown() -> None:
    ctx = ctx_for(data())
    theme = ThemeDefinition(
        name="US technology",
        selectors=[
            ThemeSelector(kind="sector", value="Information Technology"),
            ThemeSelector(kind="country", value="US"),
        ],
    )
    out = theme_exposure(theme, ctx)
    # AAPL 0.20 direct + 0.05 fund, MSFT 0.15 fund: each counted once although two selectors match
    assert out.exposed_weight == pytest.approx(0.40)
    assert out.direct_weight == pytest.approx(0.20) and out.fund_weight == pytest.approx(0.20)
    apple = next(i for i in out.issuers if "Apple" in i.issuer)
    assert apple.exposed == pytest.approx(0.25) and len(apple.paths) == 2
    assert all(len(p.selectors) == 2 for p in apple.paths)
    # VWCE is not looked through (0.20) and CSPX's unreported part is 0.025: unknown, not zero
    assert out.unknown.weight == pytest.approx(0.225)
    assert out.upper_bound == pytest.approx(0.625)
    assert out.confidence == "low"


def test_theme_with_no_match_is_zero_exposure_with_the_same_unknown() -> None:
    out = theme_exposure(
        ThemeDefinition(
            name="Utilities", selectors=[ThemeSelector(kind="sector", value="Utilities")]
        ),
        ctx_for(data()),
    )
    assert out.exposed_weight == 0 and out.unknown.weight == pytest.approx(0.225)


def test_a_leaf_without_a_label_is_unknown_for_a_selector_on_that_dimension() -> None:
    d = data()
    out = theme_exposure(
        ThemeDefinition(
            name="Revenue from Germany",
            selectors=[ThemeSelector(kind="revenue_country", value="DE")],
        ),
        ctx_for(d),
    )
    # no company has revenue geography stored: every equity leaf may be exposed, none is
    assert out.exposed_weight == 0
    assert out.unknown.weight >= out.equity_weight - 1e-9
    assert any("Revenue" in w for w in out.warnings)


# ---------------------------------------------------------------------------------- rebalance


def strategy(
    *targets: tuple[str, float, float], cash: tuple[float, float] | None = None
) -> StrategyConfig:
    return StrategyConfig(
        base_currency="EUR",
        target_positions=[PositionTarget(ticker=t, target_weight=w, band=b) for t, w, b in targets],
        cash_target=TargetBand(target_weight=cash[0], band=cash[1]) if cash else None,
    )


def holdings(**values: float) -> list[rb.Holding]:
    return [rb.Holding(t, t, v, v / 10, "EUR") for t, v in values.items()]


def test_rebalance_moves_out_of_band_positions_to_target_and_funds_the_buy_from_the_sale() -> None:
    plan = rb.plan(
        holdings(A=600, B=200),
        200,
        strategy(("A", 0.40, 0.05), ("B", 0.30, 0.05)),
        rb.RebalanceRequest(),
    )
    trades = {t.ticker: t for t in plan.trades}
    # A is 60 % against 40 +/- 5: sell 200. B is 20 % against 30 +/- 5: buy 100. No cost in EUR.
    assert trades["A"].trade_value == pytest.approx(-200)
    assert trades["B"].trade_value == pytest.approx(100)
    assert plan.cash_after == pytest.approx(300)
    assert trades["A"].weight_after == pytest.approx(0.40)
    assert plan.drift_before.out_of_band == 2 and plan.drift_after.out_of_band == 0


def test_a_position_inside_its_band_does_not_trade_and_to_band_trades_less() -> None:
    inside = rb.plan(
        holdings(A=420, B=380),
        200,
        strategy(("A", 0.40, 0.05), ("B", 0.40, 0.05)),
        rb.RebalanceRequest(),
    )
    assert all(t.action == "hold" for t in inside.trades)
    edge = rb.plan(
        holdings(A=600, B=200),
        200,
        strategy(("A", 0.40, 0.05), ("B", 0.30, 0.05)),
        rb.RebalanceRequest(mode="to_band"),
    )
    trades = {t.ticker: t for t in edge.trades}
    assert trades["A"].trade_value == pytest.approx(-150)  # to 45 %
    assert trades["B"].trade_value == pytest.approx(50)  # to 25 %


def test_buys_the_cash_cannot_fund_are_scaled_down_together_and_the_shortfall_is_shown() -> None:
    plan = rb.plan(
        holdings(A=300, B=300, C=400),
        0,
        strategy(("A", 0.45, 0.01), ("B", 0.45, 0.01)),
        rb.RebalanceRequest(),
    )
    # A and B each want +150 to reach 45 %; C has no target (never traded), there is no cash
    # and no sale proceeds: nothing can be bought and the whole 300 is unfunded
    assert plan.unfunded == pytest.approx(300)
    assert all(abs(t.trade_value) < 1e-9 for t in plan.trades)
    assert any("fund" in w for w in plan.warnings)
    assert plan.unrelated_positions == 1


def test_costs_apply_to_foreign_quoted_instruments_and_a_deposit_funds_the_buy() -> None:
    hs = [rb.Holding("A", "A", 600, 6, "EUR"), rb.Holding("B", "B", 200, 2, "USD")]
    plan = rb.plan(
        hs,
        200,
        strategy(("A", 0.40, 0.05), ("B", 0.30, 0.05)),
        rb.RebalanceRequest(deposit=100, fx_cost_bps=15),
    )
    b = next(t for t in plan.trades if t.ticker == "B")
    # total after the deposit is 1100: B to 30 % = 330, buy 130, cost 130 x 0.0015 = 0.195
    assert b.trade_value == pytest.approx(130)
    assert b.cost == pytest.approx(0.195)
    assert plan.costs == pytest.approx(0.195)
    assert plan.total_after_costs == pytest.approx(1100 - 0.195)


def test_the_cash_floor_keeps_the_cash_band_before_any_buy() -> None:
    plan = rb.plan(
        holdings(A=300, B=100, C=300),
        300,
        strategy(("A", 0.20, 0.05), ("B", 0.50, 0.05), cash=(0.15, 0.05)),
        rb.RebalanceRequest(),
    )
    a = next(t for t in plan.trades if t.ticker == "A")
    b = next(t for t in plan.trades if t.ticker == "B")
    # A is 30 % against 20 +/- 5: sell 100. B is 10 % against 50 +/- 5: wants +400.
    # Floor = 10 % of 1000 = 100; spendable = 300 cash + 100 sale - 100 floor = 300 of the 400
    assert a.trade_value == pytest.approx(-100)
    assert plan.cash_floor == pytest.approx(100)
    assert b.trade_value == pytest.approx(300) and b.funded == pytest.approx(0.75)
    assert plan.unfunded == pytest.approx(100)
    assert plan.cash_after == pytest.approx(100)


# --------------------------------------------------------------------------------- lot impact


def test_a_sale_consumes_the_oldest_lots_first_and_flags_a_lot_near_the_time_test() -> None:
    lots = [
        OpenLot("new", dt.date(2024, 2, 1), D(10), D(120)),
        OpenLot("old", dt.date(2023, 6, 1), D(10), D(100)),
    ]
    today = dt.date(2026, 3, 1)
    out = consume("X", lots, D(15), D(150), today, years=3, warn_months=D(6))
    assert [lot.lot_id for lot in out.lots] == ["old", "new"]
    # old: 10 units, cost 1000, proceeds 1500, passes 2026-06-02 (3 months away)
    # new: 5 units, cost 600, proceeds 750, passes 2027-02-02
    assert out.gain == pytest.approx(650)
    assert out.gross_proceeds == pytest.approx(2250)
    assert out.gross_within_time_test == pytest.approx(2250)
    assert out.near_test_lots == 1 and out.earliest_pass == dt.date(2026, 6, 2)
    assert out.units_unmatched == 0
    over = consume("X", lots, D(30), D(150), today)
    assert over.units_unmatched == 10


# ---------------------------------------------------------------------------------- pre-trade


def pretrade_context(
    strategy_config: StrategyConfig,
    facts: dict[str, pt.TickerFacts],
    holdings_value: dict[str, float],
    cash: float,
    after_value: dict[str, float],
    cash_after: float,
) -> pt.PretradeContext:
    def drift(values: dict[str, float], c: float):  # type: ignore[no-untyped-def]
        return compute_drift(
            [HoldingValue(k, "EUR", D(str(v))) for k, v in values.items() if v > 0],
            D(str(c)),
            strategy_config,
        )

    return pt.PretradeContext(
        strategy=strategy_config,
        rules=pt.PretradeRules(),
        base_currency="EUR",
        total_value=sum(holdings_value.values()) + cash,
        cash_base=cash,
        facts=facts,
        drift_before=drift(holdings_value, cash),
        drift_after=drift(after_value, cash_after),
    )


def by_id(result: pt.PretradeResult) -> dict[str, pt.PretradeCheck]:
    return {c.id: c for c in result.checks}


def test_buy_is_tested_against_the_return_hurdle_start_size_and_the_post_trade_cap() -> None:
    config = StrategyConfig(
        base_currency="EUR",
        classifications={"NEW": {"risk": "growth"}},
        categories=["risk"],
        rules=[PositionWeightRule(id="cap", type="position_weight", operator="<=", value=0.10)],
    )
    facts = {
        "NEW": pt.TickerFacts(
            "NEW",
            tier="growth",
            price=100,
            quote_currency="EUR",
            sell_target=140,
            target_currency="EUR",
            has_thesis=True,
            currency="EUR",
        )
    }
    # 1000 total, 5 % buy (50) into a growth stock whose start cap is 3 %
    ctx = pretrade_context(config, facts, {"A": 900}, 100, {"A": 900, "NEW": 50}, 50)
    got = by_id(pt.run([pt.TradeLeg(side="buy", ticker="NEW", value_base=50)], ctx))
    assert got["buy.hurdle"].status == "pass"  # 40 % >= 30 %
    assert got["buy.start_size"].status == "flag"  # 5 % > 3 %
    assert got["buy.asymmetry"].status == "unavailable"
    assert got["buy.risk_budget"].status == "unavailable"
    # A already breaches the 10 % cap before the trade (90 %): still breached, not a new breach
    assert any(c.status == "info" and "Already breached" in c.title for c in got.values())

    facts["NEW"].sell_target = 120
    low = by_id(pt.run([pt.TradeLeg(side="buy", ticker="NEW", value_base=20)], ctx))
    assert low["buy.hurdle"].status == "flag"  # 20 % < 30 %
    assert low["buy.start_size"].status == "pass"  # 2 % <= 3 %


def test_new_breach_after_the_trade_is_flagged_and_a_cleared_one_is_credited() -> None:
    config = StrategyConfig(
        base_currency="EUR",
        rules=[PositionWeightRule(id="cap", type="position_weight", operator="<=", value=0.30)],
    )
    facts = {
        "A": pt.TickerFacts("A", held=True, value_now=250),
        "B": pt.TickerFacts("B", held=True, value_now=500),
    }
    ctx = pretrade_context(config, facts, {"A": 250, "B": 500}, 250, {"A": 350, "B": 500}, 150)
    got = pt.run([pt.TradeLeg(side="buy", ticker="A", value_base=100)], ctx)
    new = [c for c in got.checks if c.status == "flag" and c.id.startswith("after.rule")]
    # A goes from 25 % to 35 % against a 30 % cap: a new breach. B (50 %) was already over it.
    assert len(new) == 1 and "A weight" in new[0].detail
    assert any(c.status == "info" and "Already breached" in c.title for c in got.checks)
    assert not any(c.status == "pass" for c in got.checks if c.id == "after.rules")


def test_sale_reports_lots_the_time_test_and_exemption_headroom() -> None:
    config = StrategyConfig(base_currency="EUR")
    facts = {"S": pt.TickerFacts("S", held=True, value_now=400)}
    lots = [OpenLot("l1", dt.date(2023, 6, 1), D(10), D(30))]
    impact = consume("S", lots, D(5), D(40), dt.date(2026, 3, 1))
    ctx = pretrade_context(config, facts, {"S": 400}, 100, {"S": 200}, 300)
    ctx.sale_impacts["S"] = impact
    ctx.exemption_limit, ctx.exemption_currency = 100_000.0, "CZK"
    ctx.year_proceeds_so_far = 95_000.0
    ctx.sale_within_test_exemption_ccy["S"] = 8_000.0
    got = by_id(pt.run([pt.TradeLeg(side="sell", ticker="S", value_base=200)], ctx))
    assert got["sell.time_test"].status == "flag"  # passes 2026-06-02, 3 months away
    assert got["sell.exemption"].status == "flag"  # 100000 - 95000 - 8000 = -3000
    assert got["sell.exemption"].values["headroom"] == pytest.approx(-3000)
    assert got["sell.lots"].values["gain"] == pytest.approx(50)  # 5 x (40 - 30)

    ctx.sale_within_test_exemption_ccy["S"] = None
    assert (
        by_id(pt.run([pt.TradeLeg(side="sell", ticker="S", value_base=200)], ctx))[
            "sell.exemption"
        ].status
        == "unavailable"
    )


def test_switch_compares_the_gap_with_tax_costs_and_margin_and_sizes_for_equal_risk() -> None:
    config = StrategyConfig(base_currency="EUR")
    old = pt.TickerFacts(
        "OLD", held=True, value_now=400, price=100, quote_currency="EUR", sell_target=110,
        target_currency="EUR", has_thesis=True, volatility=0.20, currency="EUR",
    )  # fmt: skip
    new = pt.TickerFacts(
        "NEW", price=50, quote_currency="EUR", sell_target=80, target_currency="EUR",
        has_thesis=True, volatility=0.40, currency="EUR",
    )  # fmt: skip
    ctx = pretrade_context(config, {"OLD": old, "NEW": new}, {"OLD": 400}, 100, {"NEW": 400}, 100)
    ctx.risk_free, ctx.horizon_years = 0.02, 3.0
    trades = [
        pt.TradeLeg(side="sell", ticker="OLD", value_base=400),
        pt.TradeLeg(side="buy", ticker="NEW", value_base=400),
    ]
    got = pt.run(trades, ctx)
    assert got.is_switch
    checks = by_id(got)
    # expected returns: OLD 10 %, NEW 60 %; gap 50 points against a 5-point margin (no lots: no tax)
    assert checks["switch.gap"].status == "pass"
    assert checks["switch.gap"].values["gap"] == pytest.approx(0.5)
    assert checks["switch.gap"].values["hurdle"] == pytest.approx(0.05)
    # equal-risk size: 400 x 0.20 / 0.40 = 200, the proposed 400 carries twice the risk
    assert checks["switch.size"].values["equal_risk_value"] == pytest.approx(200)
    assert checks["switch.size"].status == "flag"
    # annualised over 3 years: OLD 3.2 %, NEW 17.0 %; scores (r - 0.02) / vol
    assert checks["switch.ranking"].values["score_sell"] == pytest.approx(
        ((1.10 ** (1 / 3) - 1) - 0.02) / 0.20
    )
    assert checks["switch.ranking"].status == "pass"
    assert checks["switch.portfolio"].status == "unavailable"


def test_missing_inputs_make_a_check_unavailable_not_a_pass() -> None:
    config = StrategyConfig(base_currency="EUR")
    bare = pt.TickerFacts("NEW", price=10)
    ctx = pretrade_context(config, {"NEW": bare}, {"A": 100}, 100, {"A": 100, "NEW": 10}, 90)
    got = by_id(pt.run([pt.TradeLeg(side="buy", ticker="NEW", value_base=10)], ctx))
    assert got["buy.hurdle"].status == "unavailable"
    assert got["buy.thesis"].status == "flag"


# --------------------------------------------------------------------------------- risk limits


def test_new_risk_limits_raise_findings_through_the_existing_breach_path() -> None:
    limits = RiskLimits(
        max_var_95=0.03,
        max_tracking_error=0.08,
        max_stress_loss=0.15,
        credit_spread_alert_bp=500,
        max_valuation_terminal_growth=0.03,
    )
    inputs = RiskInputs(
        var_95=0.042,
        tracking_error=0.05,
        stress_loss=0.21,
        credit_spread_bp=620,
        valuation_flags=[ValuationFlag("AAPL_US_EQ", "base", "terminal growth", 0.05)],
    )
    keys = {f.key for f in evaluate_risk(inputs, limits)}
    assert keys == {
        "risk:var",
        "risk:stress",
        "risk:credit_spread",
        "risk:valuation:AAPL_US_EQ:base:terminal",
    }  # tracking error 5 % is inside its 8 % limit
    assert evaluate_risk(inputs, RiskLimits()) == []  # every limit off: nothing is raised


def test_a_limit_that_is_off_or_a_measurement_that_is_missing_raises_nothing() -> None:
    assert evaluate_risk(RiskInputs(), RiskLimits(max_var_95=0.01, max_stress_loss=0.1)) == []


def test_cash_weight_rule_still_validates_with_the_new_risk_block() -> None:
    config = StrategyConfig(
        base_currency="EUR",
        rules=[CashWeightRule(id="cash", type="cash_weight", operator="<=", value=0.2)],
    )
    assert config.risk.stress_equity_fall == 0.20
    assert CSPX_ISIN  # fixture import used by the shared portfolio
