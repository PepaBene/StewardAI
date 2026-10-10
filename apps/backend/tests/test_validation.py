import math

import numpy as np
import pytest

from steward.quant import skill as sk
from steward.quant import validation as v


def test_chi2_survival_matches_known_critical_values():
    assert v.chi2_sf(3.841458820694124, 1) == pytest.approx(0.05, abs=1e-9)
    assert v.chi2_sf(5.991464547107979, 2) == pytest.approx(0.05, abs=1e-9)
    assert v.chi2_sf(-1.0, 1) == 1.0
    with pytest.raises(ValueError):
        v.chi2_sf(1.0, 3)


def test_kupiec_golden_value():
    lr, p = v.kupiec_pof(250, 10, 0.01)
    assert lr == pytest.approx(12.955491062356018, rel=1e-12)
    assert p == pytest.approx(0.00031898450821338356, rel=1e-9)


def test_kupiec_exact_rate_has_zero_statistic():
    lr, p = v.kupiec_pof(200, 10, 0.05)
    assert lr == pytest.approx(0.0, abs=1e-9)
    assert p == 1.0
    assert v.kupiec_pof(0, 0, 0.05) == (0.0, 1.0)


def test_independence_flags_clusters_and_accepts_spread_hits():
    clustered = [False] * 40 + [True] * 4 + [False] * 40
    spread = ([False] * 19 + [True]) * 4
    assert v.christoffersen_independence(clustered)[1] < 0.05
    assert v.christoffersen_independence(spread)[1] > 0.05
    assert v.christoffersen_independence([False] * 30) == (0.0, 1.0)


def test_backtest_passes_a_correct_model_and_fails_an_understated_one():
    rng = np.random.default_rng(7)
    sigma = 0.02
    r = rng.normal(0.0, sigma, 1500)
    ok = v.var_backtest(list(r), [sigma] * len(r), 0.95)
    assert ok.passed
    assert ok.hit_rate == pytest.approx(0.05, abs=0.02)
    bad = v.var_backtest(list(r), [sigma / 2] * len(r), 0.95)
    assert not bad.passed
    assert bad.reason is not None and "hits" in bad.reason


def test_backtest_skips_weeks_without_a_forecast():
    out = v.var_backtest([0.01, -0.5], [None, None], 0.95)
    assert out.weeks == 0 and out.passed and out.reason


def test_rolling_sigma_uses_only_earlier_weeks():
    r = [0.01] * 60 + [0.5] + [0.01] * 5
    s = v.rolling_ewma_sigma(r, 26, min_history=52)
    assert s[:52] == [None] * 52
    assert s[60] == pytest.approx(0.01)
    assert s[61] > 0.05  # the shock shows only in the weeks after it


def test_calibration_flags_a_forecast_that_is_too_low():
    rng = np.random.default_rng(3)
    r = list(rng.normal(0, 0.02, 400))
    good = v.calibration(r, [0.02] * 400)
    assert good.realised_over_forecast == pytest.approx(1.0, abs=0.1)
    assert not good.biased
    low = v.calibration(r, [0.01] * 400)
    assert low.biased and low.direction == "understates"
    assert v.calibration(r[:5], [0.02] * 5).realised_over_forecast is None


def test_psr_golden_values():
    rng = np.random.default_rng(1)
    r = list(rng.normal(0.002, 0.02, 200))
    psr = sk.probabilistic_sharpe(r, 0.0)
    assert psr is not None and psr.probability is not None
    # the closed form, recomputed here from the same moments
    sr, g3, g4 = sk.sharpe_moments(r)  # type: ignore[misc]
    spread = 1 - g3 * sr + (g4 - 1) / 4 * sr * sr
    from statistics import NormalDist

    expected = NormalDist().cdf(sr * math.sqrt(199) / math.sqrt(spread))
    assert psr.probability == pytest.approx(expected, rel=1e-12)
    assert psr.min_track_record == pytest.approx(
        1 + spread * (NormalDist().inv_cdf(0.95) / sr) ** 2, rel=1e-12
    )


def test_psr_says_not_distinguishable_for_a_short_noisy_record():
    rng = np.random.default_rng(5)
    r = list(rng.normal(0.001, 0.03, 30))
    psr = sk.probabilistic_sharpe(r, 0.05)
    assert psr is not None and psr.distinguishable is False
    assert sk.probabilistic_sharpe([0.01, 0.01, 0.01], 0.0) is None


def test_brinson_effects_sum_to_excess_return():
    segs = [
        sk.Segment("a", 0.5, 0.08, 0.4, 0.05),
        sk.Segment("b", 0.3, -0.02, 0.4, 0.01),
        sk.Segment("c", 0.2, 0.04, 0.2, 0.03),
    ]
    b = sk.brinson_fachler(segs)
    assert b.benchmark_return == pytest.approx(0.4 * 0.05 + 0.4 * 0.01 + 0.2 * 0.03)
    assert abs(b.reconciliation_error) < 1e-12
    assert b.allocation + b.selection + b.interaction == pytest.approx(b.excess_return)
    # segment a: (0.5-0.4)(0.05-R_B), selection 0.4*(0.03)
    r_b = b.benchmark_return
    a = b.segments[0]
    assert a.allocation == pytest.approx(0.1 * (0.05 - r_b))
    assert a.selection == pytest.approx(0.4 * 0.03)
    assert a.interaction == pytest.approx(0.1 * 0.03)


def test_brinson_with_equal_weights_is_pure_selection():
    segs = [sk.Segment("a", 0.6, 0.1, 0.6, 0.04), sk.Segment("b", 0.4, 0.0, 0.4, 0.04)]
    b = sk.brinson_fachler(segs)
    assert b.allocation == pytest.approx(0.0)
    assert b.interaction == pytest.approx(0.0)
    assert b.selection == pytest.approx(b.excess_return)
