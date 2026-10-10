"""Tail risk, the drawdown bootstrap and the downside statistics (plan Q2.1 to Q2.3).

The exit check of Q2: on a synthetic i.i.d. normal series the bootstrap and closed-form VaR agree
within sampling error, and the drawdown probability is stable (within 1 point) across seeds."""

from statistics import NormalDist

import numpy as np
import pytest

from steward.quant import bootstrap as bs
from steward.quant import downside as dn
from steward.quant import tail as tl

MU, SD = 0.002, 0.02


def _normal(n: int = 4000, seed: int = 7) -> np.ndarray:
    return np.random.default_rng(seed).normal(MU, SD, n)


def test_historical_var_and_es_match_the_normal_formulas_on_a_large_normal_sample() -> None:
    r = _normal()
    out = tl.historical(r, 0.95)
    assert out is not None
    z = NormalDist().inv_cdf(0.05)
    var = -(MU + SD * z)
    es = -(MU - SD * NormalDist().pdf(z) / 0.05)
    assert out.var == pytest.approx(var, abs=0.002)
    assert out.es == pytest.approx(es, abs=0.003)
    assert out.es > out.var


def test_cornish_fisher_reduces_to_the_normal_quantile_for_a_normal_series() -> None:
    out = tl.cornish_fisher(_normal(), 0.99)
    assert out is not None
    z = NormalDist().inv_cdf(0.01)
    assert out.var == pytest.approx(-(MU + SD * z), abs=0.003)
    assert out.es >= out.var


def test_cornish_fisher_raises_the_loss_for_a_fat_left_tail() -> None:
    rng = np.random.default_rng(3)
    base = rng.normal(0.0, 0.02, 3000)
    crashy = base.copy()
    crashy[::100] -= 0.12  # a few large falls: negative skew, fat tails
    plain = tl.cornish_fisher(base, 0.99)
    fat = tl.cornish_fisher(crashy, 0.99)
    assert plain is not None and fat is not None
    assert fat.var > plain.var and fat.es > plain.es


def test_filtered_historical_scales_to_todays_volatility() -> None:
    rng = np.random.default_rng(11)
    calm = rng.normal(0, 0.01, 300)
    stormy_now = np.concatenate([calm, rng.normal(0, 0.04, 40)])
    quiet_now = np.concatenate([rng.normal(0, 0.04, 40), calm])
    storm = tl.filtered_historical(stormy_now, 0.95)
    quiet = tl.filtered_historical(quiet_now, 0.95)
    assert storm is not None and quiet is not None
    assert storm.var > 2 * quiet.var  # the same data mix, read at a different current volatility


def test_horizon_returns_compound_overlapping_periods() -> None:
    r = np.array([0.1, -0.1, 0.05, 0.02])
    got = tl.horizon_returns(r, 2)
    assert got == pytest.approx([1.1 * 0.9 - 1, 0.9 * 1.05 - 1, 1.05 * 1.02 - 1])
    assert tl.horizon_returns(r, 9).size == 0


def test_too_few_observations_give_nothing() -> None:
    assert tl.historical(_normal(30), 0.95) is None
    assert tl.cornish_fisher(_normal(30), 0.95) is None
    assert bs.simulate(_normal(30), 52) is None
    assert dn.downside_stats(_normal(10)) is None


def test_es_contributions_add_up_to_the_portfolio_es() -> None:
    rng = np.random.default_rng(5)
    data = rng.normal(0.001, 0.02, (1500, 3))
    data[:, 1] += 0.5 * data[:, 0]
    w = np.array([0.5, 0.3, 0.2])
    got = tl.es_contributions(w, data, 0.95)
    assert got is not None
    contributions, count = got
    portfolio = data @ w
    cut = np.quantile(portfolio, 0.05)
    assert count == int((portfolio <= cut).sum())
    assert contributions.sum() == pytest.approx(-portfolio[portfolio <= cut].mean())


def test_bootstrap_drawdown_probability_is_stable_across_seeds_and_reproducible() -> None:
    r = _normal(1200, seed=21)
    a = bs.simulate(r, 156, threshold=0.20, paths=4000, seed=1)
    b = bs.simulate(r, 156, threshold=0.20, paths=4000, seed=2)
    again = bs.simulate(r, 156, threshold=0.20, paths=4000, seed=1)
    assert a is not None and b is not None and again is not None
    assert again == a
    assert abs(a.probability_of_drawdown - b.probability_of_drawdown) < 0.03
    assert a.conditional_drawdown_at_risk >= a.drawdown_quantiles["p95"] - 1e-9
    assert a.terminal_quantiles["p05"] < a.terminal_quantiles["p50"] < a.terminal_quantiles["p95"]


def test_bootstrap_terminal_value_matches_the_closed_form_median_for_iid_returns() -> None:
    r = _normal(1500, seed=9)
    out = bs.simulate(r, 104, paths=6000, seed=4, block=1.0001)
    assert out is not None
    expected = float(np.exp(104 * np.mean(np.log1p(r))))
    assert out.terminal_quantiles["p50"] == pytest.approx(expected, rel=0.06)


def test_the_fan_starts_at_one_ends_at_the_terminal_quantiles_and_is_ordered() -> None:
    out = bs.simulate(_normal(900, seed=3), 104, paths=2000, seed=5)
    assert out is not None
    fan = out.fan
    assert all(path[0] == 1.0 and len(path) == 104 // bs.FAN_STEP + 1 for path in fan.values())
    for i in range(len(fan["p50"])):
        assert fan["p05"][i] <= fan["p25"][i] <= fan["p50"][i] <= fan["p75"][i] <= fan["p95"][i]
    assert fan["p50"][-1] == pytest.approx(out.terminal_quantiles["p50"])
    assert fan["p05"][-1] == pytest.approx(out.terminal_quantiles["p05"])


def test_a_riskier_book_has_a_higher_drawdown_probability() -> None:
    calm = bs.simulate(np.random.default_rng(1).normal(0.002, 0.01, 800), 104, paths=3000)
    wild = bs.simulate(np.random.default_rng(1).normal(0.002, 0.04, 800), 104, paths=3000)
    assert calm is not None and wild is not None
    assert wild.probability_of_drawdown > calm.probability_of_drawdown + 0.3


def test_a_book_already_below_its_peak_has_a_higher_drawdown_probability() -> None:
    r = _normal(900, seed=7)
    fresh = bs.simulate(r, 104, threshold=0.20, paths=3000, seed=3)
    down = bs.simulate(r, 104, threshold=0.20, paths=3000, seed=3, start_drawdown=0.12)
    assert fresh is not None and down is not None
    assert down.probability_of_drawdown > fresh.probability_of_drawdown
    # Already past the threshold: every path counts.
    gone = bs.simulate(r, 52, threshold=0.20, paths=500, seed=3, start_drawdown=0.25)
    assert gone is not None and gone.probability_of_drawdown == 1.0


def test_current_drawdown_is_measured_from_the_highest_of_the_last_year() -> None:
    from steward.portfolio.tail import current_drawdown

    assert current_drawdown(np.array([0.10, 0.10, -0.20, -0.05])) == pytest.approx(1 - 0.8 * 0.95)
    assert current_drawdown(np.array([0.01, 0.02])) == 0.0
    assert current_drawdown(np.array([])) == 0.0


def test_downside_statistics_by_hand() -> None:
    r = np.array([0.02, -0.01, 0.03, -0.02, 0.01] * 6)  # 30 periods
    out = dn.downside_stats(r, periods_per_year=52)
    assert out is not None
    shortfalls = np.minimum(r, 0)
    assert out.downside_deviation == pytest.approx(np.sqrt((shortfalls**2).mean() * 52))
    # The deepest fall in the repeating pattern is the -2 % week after a +3 % high.
    assert out.max_drawdown == pytest.approx(0.02 + 0.0, abs=0.012)
    assert out.sortino is not None and out.sortino > 0
    assert out.calmar is not None and out.calmar > 0
    assert out.ulcer_index > 0
    assert dn.drawdown_path(np.array([0.1, -0.5, 0.2])) == pytest.approx([0.0, 0.5, 0.4])
