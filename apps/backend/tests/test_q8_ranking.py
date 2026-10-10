"""Q8.1 to Q8.5: scenarios, expected return, the risk profile, the score and the switching rule.

The golden case is the owner's: a holding at 220 USD with scenarios 170 / 230 / 270 at
25 / 50 / 25 % and a candidate at 50 USD with 40 / 70 / 90."""

import datetime as dt
import math

import pytest
from pydantic import ValidationError

from steward.config.thesis import ThesisPriceTarget, ThesisScenarios
from steward.decision import expected_return as er
from steward.decision import ranking as rk
from steward.decision.pretrade import TickerFacts, expected_return

TODAY = dt.date(2026, 10, 10)


def _scenarios(bear: float, base: float, bull: float, **p: float) -> ThesisScenarios:
    return ThesisScenarios(bear=bear, base=base, bull=bull, **p)


def _calc(sc: ThesisScenarios, price: float, **kw: object) -> er.ExpectedReturn:
    args: dict[str, object] = {
        "catalyst": None, "today": TODAY, "hurdle": 0.30, "asymmetry_gate": 2.0,
    }  # fmt: skip
    args.update(kw)
    return er.expected_return(sc, price, **args)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------- scenarios


def test_scenario_values_must_rise_from_bear_to_bull() -> None:
    with pytest.raises(ValidationError, match="rise from bear"):
        _scenarios(230, 170, 270)


def test_scenario_probabilities_must_add_up_to_one() -> None:
    with pytest.raises(ValidationError, match="100 %"):
        _scenarios(170, 230, 270, p_bear=0.3, p_base=0.5, p_bull=0.3)


def test_default_probabilities_are_25_50_25() -> None:
    sc = _scenarios(170, 230, 270)
    assert (sc.p_bear, sc.p_base, sc.p_bull) == (0.25, 0.50, 0.25)
    assert sc.expected_value == pytest.approx(225.0)


def _target(**kw: object) -> ThesisPriceTarget:
    base: dict[str, object] = {"currency": "USD", "method": "DCF", "set_on": TODAY}
    base.update(kw)
    return ThesisPriceTarget.model_validate(base)


def test_a_target_may_hold_scenarios_only() -> None:
    target = _target(scenarios={"bear": 170, "base": 230, "bull": 270}, catalyst_date="2028-01-01")
    assert target.sell_target is None
    text = target.describe()
    assert "scenarios bear 170 / base 230 / bull 270 USD at 25% / 50% / 25%" in text
    assert "catalyst 2028-01-01" in text


def test_a_target_with_nothing_is_rejected() -> None:
    with pytest.raises(ValidationError, match="scenarios"):
        _target()


# ---------------------------------------------------------------------------- expected return


def test_the_owners_holding_expects_about_two_percent() -> None:
    r = _calc(_scenarios(170, 230, 270), 220)
    assert r.expected_value == pytest.approx(225.0)
    assert r.total == pytest.approx(225 / 220 - 1)  # +2.27 %
    assert r.horizon_years == 2.0 and r.horizon_source == "default"
    assert r.annualized == pytest.approx(math.sqrt(225 / 220) - 1)
    assert r.meets_hurdle is False


def test_the_owners_candidate_expects_thirty_five_percent() -> None:
    r = _calc(_scenarios(40, 70, 90), 50)
    assert r.total == pytest.approx(0.35)
    assert r.annualized == pytest.approx(math.sqrt(1.35) - 1)
    assert r.meets_hurdle is True


def test_asymmetry_is_base_upside_over_bear_loss() -> None:
    r = _calc(_scenarios(40, 70, 90), 50)
    assert r.upside == pytest.approx(0.40)
    assert r.loss == pytest.approx(0.20)
    assert r.asymmetry == pytest.approx(2.0)
    assert r.meets_asymmetry is True
    held = _calc(_scenarios(170, 230, 270), 220)
    assert held.asymmetry == pytest.approx((230 / 220 - 1) / (1 - 170 / 220))
    assert held.meets_asymmetry is False


def test_a_bear_case_above_the_price_has_no_loss_and_no_ratio() -> None:
    r = _calc(_scenarios(55, 70, 90), 50)
    assert r.loss == 0 and r.asymmetry is None and r.meets_asymmetry is None


def test_a_switched_off_threshold_gives_no_flag() -> None:
    r = _calc(_scenarios(40, 70, 90), 50, hurdle=None, asymmetry_gate=None)
    assert r.meets_hurdle is None and r.meets_asymmetry is None


def test_the_horizon_runs_to_the_catalyst_with_a_three_month_floor() -> None:
    sc = _scenarios(40, 70, 90)
    far = _calc(sc, 50, catalyst=dt.date(2028, 10, 10))
    assert far.horizon_source == "catalyst date"
    assert far.horizon_years == pytest.approx(2.0, abs=0.01)
    near = _calc(sc, 50, catalyst=TODAY + dt.timedelta(days=10))
    assert near.horizon_years == 0.25
    assert near.annualized == pytest.approx(1.35**4 - 1)


def test_the_pretrade_return_uses_the_scenarios_when_the_thesis_has_them() -> None:
    facts = TickerFacts(
        "X", has_thesis=True, price=50.0, sell_target=100.0, expected_value=67.5,
        target_currency="USD", quote_currency="USD",
    )  # fmt: skip
    assert expected_return(facts)[0] == pytest.approx(0.35)
    facts.expected_value = None
    assert expected_return(facts)[0] == pytest.approx(1.0)


# ------------------------------------------------------------------------------ risk profile


def _walk(days: int, daily: float, start: float = 100.0) -> list[tuple[dt.date, float]]:
    """Alternating up and down days so the volatility is known: ±daily."""
    out, level, day = [], start, dt.date(2025, 1, 1)
    for i in range(days):
        level *= 1 + (daily if i % 2 == 0 else -daily)
        out.append((day, level))
        day += dt.timedelta(days=1)
    return out


def test_the_profile_reports_volatility_drawdown_and_correlation() -> None:
    series = _walk(300, 0.01)
    p = rk.risk_profile(series, series)
    assert p.stage == "A" and p.observations == 300
    assert p.volatility_1y == pytest.approx(0.01 * math.sqrt(252), rel=0.05)
    assert p.correlation == pytest.approx(1.0)
    assert p.marginal_risk == pytest.approx(p.volatility_1y)
    assert p.marginal_stage == "A"
    assert p.max_drawdown is not None and p.max_drawdown < 0


def test_a_short_history_has_no_volatility_and_says_nothing_else() -> None:
    p = rk.risk_profile(_walk(10, 0.01), None)
    assert p.volatility_1y is None and p.marginal_risk is None and p.correlation is None


def test_the_portfolio_index_follows_the_weighted_holdings() -> None:
    a = _walk(60, 0.01)
    b = [(d, 100.0) for d, _ in a]  # flat
    index = rk.portfolio_index({"A": a, "B": b}, {"A": 50.0, "B": 50.0})
    ratio = index[1][1] / index[0][1] - 1
    assert ratio == pytest.approx(0.5 * (a[2][1] / a[1][1] - 1))


# ------------------------------------------------------------------------------ score, switch


def test_the_score_is_excess_return_per_unit_of_volatility() -> None:
    assert rk.risk_adjusted_score(0.1619, 0.035, 0.45) == pytest.approx(0.2820, abs=1e-3)


def test_an_idea_improves_the_portfolio_when_it_beats_sharpe_times_correlation() -> None:
    assert rk.improves_portfolio(0.30, portfolio_sharpe=0.8, correlation=0.5) is False
    assert rk.improves_portfolio(0.45, portfolio_sharpe=0.8, correlation=0.5) is True


def test_the_switch_scales_with_how_far_the_gap_clears_the_hurdle() -> None:
    assert rk.switch_action(0.08, 0.10) == ("hold", 0.0)
    assert rk.switch_action(0.10, 0.10) == ("hold", 0.0)
    action, share = rk.switch_action(0.15, 0.10)
    assert action == "partial" and share == pytest.approx(0.5)
    assert rk.switch_action(0.20, 0.10) == ("full", 1.0)
    # The owner's case: a 33 point gap against a hurdle of about 10 points.
    assert rk.switch_action(0.35 - 0.0227, 0.10) == ("full", 1.0)


def test_the_replacement_is_sized_to_carry_the_same_risk() -> None:
    # 8 % in a 28 %-volatility stock replaced by one at 45 %: about 5 %.
    assert rk.equal_risk_value(0.08, 0.28, 0.45) == pytest.approx(0.0498, abs=1e-4)


def test_the_bear_case_is_set_against_the_worst_past_fall() -> None:
    assert "milder" in (rk.bear_vs_history(0.20, -0.45) or "")
    assert "at least as deep" in (rk.bear_vs_history(0.50, -0.45) or "")
    assert rk.bear_vs_history(0.20, None) is None
