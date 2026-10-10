"""Golden tests for the quant package (plan Q0, Q1): alignment, FX conversion, proxy backfill,
covariance estimators and risk decomposition, each against a hand-computed or independently
transcribed answer."""

import datetime as dt
import math

import numpy as np
import pytest

from steward.quant import covariance as cv
from steward.quant import decompose as dc
from steward.quant import proxy as px
from steward.quant import returns as rt

# 2025-01-01 is a Wednesday.
WED = dt.date(2025, 1, 1)


def wednesdays(n: int) -> list[dt.date]:
    return [WED + dt.timedelta(weeks=i) for i in range(n)]


def constant_fx(rates: dict[str, float]) -> rt.FxLookup:
    return lambda currency, day: rates.get(currency)


# ---------------------------------------------------------------------------
# Q0.2 alignment and FX conversion
# ---------------------------------------------------------------------------


def test_weekly_return_converts_each_close_at_that_days_rate() -> None:
    usd = rt.SeriesInput("AAA", [(WED, 100.0), (WED + dt.timedelta(weeks=1), 110.0)], "USD")

    def fx(currency: str, day: dt.date) -> float | None:
        return {WED: 20.0, WED + dt.timedelta(weeks=1): 22.0}.get(day)

    m = rt.build_return_matrix([usd], fx, "weekly", WED, WED + dt.timedelta(weeks=1))
    # 100 USD x 20 = 2000 CZK, then 110 USD x 22 = 2420 CZK: stock +10 % and USD +10 % compound.
    assert m.values[0, 0] == pytest.approx(math.log(2420 / 2000), abs=1e-12)
    assert m.values[0, 0] == pytest.approx(math.log(1.1) + math.log(1.1), abs=1e-12)


def test_holiday_carries_the_close_forward_as_a_zero_local_return() -> None:
    # No close on Wednesday 8 January (holiday): the 1 January close is carried, so the local
    # return is zero and only the currency moves.
    series = rt.SeriesInput("EEE", [(WED, 50.0), (WED + dt.timedelta(weeks=2), 55.0)], "EUR")
    rates = {WED: 25.0, WED + dt.timedelta(weeks=1): 26.0, WED + dt.timedelta(weeks=2): 26.0}
    m = rt.build_return_matrix(
        [series], lambda c, d: rates.get(d), "weekly", WED, WED + dt.timedelta(weeks=2)
    )
    assert m.values[0, 0] == pytest.approx(math.log(26 / 25))
    assert m.values[1, 0] == pytest.approx(math.log(55 / 50))


def test_stalled_series_has_no_level_past_the_carry_limit() -> None:
    series = rt.SeriesInput("OLD", [(WED, 10.0)], "CZK")
    m = rt.build_return_matrix(
        [series], constant_fx({"CZK": 1.0}), "weekly", WED, WED + dt.timedelta(weeks=3)
    )
    # The one close is carried to the next Wednesday (7 days, a zero return) and no further.
    assert m.values[0, 0] == 0.0 and np.isnan(m.values[1:, 0]).all()
    assert m.coverage["OLD"].observations == 1


def test_missing_rate_leaves_a_gap_not_a_zero() -> None:
    series = rt.SeriesInput("AAA", [(d, 100.0) for d in wednesdays(3)], "USD")
    m = rt.build_return_matrix([series], lambda c, d: None, "weekly", WED, wednesdays(3)[-1])
    assert np.isnan(m.values).all()


def test_daily_grid_is_weekdays_and_weekly_grid_is_wednesdays() -> None:
    days = rt.grid(dt.date(2025, 1, 1), dt.date(2025, 1, 14), "daily")
    assert all(d.weekday() < 5 for d in days) and len(days) == 10
    assert [d.weekday() for d in rt.grid(dt.date(2025, 1, 1), dt.date(2025, 1, 14), "weekly")] == [
        2,
        2,
    ]


def test_pairwise_correlation_uses_shared_rows_only() -> None:
    a = np.array([0.01, 0.02, np.nan, -0.01, 0.03, 0.00])
    b = np.array([0.02, 0.04, 0.05, -0.02, 0.06, np.nan])
    got = rt.pairwise_correlation(a, b, 4)
    assert got is not None and got[1] == 4 and got[0] == pytest.approx(1.0)
    assert rt.pairwise_correlation(a, b, 5) is None


# ---------------------------------------------------------------------------
# Q0.4 proxy backfill
# ---------------------------------------------------------------------------


def _matrix(columns: dict[str, np.ndarray]) -> rt.ReturnMatrix:
    keys = list(columns)
    values = np.column_stack([columns[k] for k in keys])
    dates = wednesdays(values.shape[0])
    return rt.ReturnMatrix("weekly", dates, keys, values, rt.coverage_of(keys, dates, values))


def test_backfill_recovers_beta_and_fills_the_missing_weeks() -> None:
    rng = np.random.default_rng(1)
    proxy = rng.normal(0.002, 0.02, 100)
    own = 0.8 * proxy
    own[:60] = np.nan  # a listing with 40 weeks of its own history
    filled, status = px.backfill(_matrix({"ETF": proxy, "NEW": own}), ["ETF"])
    s = status["NEW"]
    assert s.status == "proxied" and s.fit is not None
    assert s.fit.proxy == "ETF" and s.fit.beta == pytest.approx(0.8)
    assert s.fit.r_squared == pytest.approx(1.0) and s.fit.fit_weeks == 40
    assert s.backfilled_weeks == 60 and s.real_weeks == 40
    # An exact fit leaves no residual, so the backfill is beta x proxy.
    assert filled.column("NEW")[:60] == pytest.approx(0.8 * proxy[:60])
    assert not np.isnan(filled.values).any()
    # The candidate's own column is never altered.
    assert filled.column("ETF") == pytest.approx(proxy)


def test_backfill_picks_the_proxy_with_the_best_fit_and_is_reproducible() -> None:
    rng = np.random.default_rng(2)
    good, poor = rng.normal(0, 0.02, 120), rng.normal(0, 0.02, 120)
    own = 1.1 * good + rng.normal(0, 0.004, 120)
    own[:70] = np.nan
    m = _matrix({"POOR": poor, "GOOD": good, "NEW": own})
    first, status = px.backfill(m, ["POOR", "GOOD"])
    second, _ = px.backfill(m, ["POOR", "GOOD"])
    assert status["NEW"].fit is not None and status["NEW"].fit.proxy == "GOOD"
    assert 0.9 < status["NEW"].fit.r_squared < 1.0
    assert np.array_equal(first.values, second.values)
    # Resampled residuals keep the idiosyncratic variance: the filled weeks are not exactly
    # beta x proxy.
    assert not np.allclose(first.column("NEW")[:70], status["NEW"].fit.beta * good[:70])


def test_short_history_is_excluded_not_proxied() -> None:
    proxy = np.random.default_rng(3).normal(0, 0.02, 80)
    own = np.full(80, np.nan)
    own[-20:] = proxy[-20:]
    _, status = px.backfill(_matrix({"ETF": proxy, "IPO": own}), ["ETF"])
    assert status["IPO"].status == "excluded" and "20 weekly returns" in (
        status["IPO"].reason or ""
    )


def test_complete_column_is_real() -> None:
    x = np.random.default_rng(4).normal(0, 0.02, 60)
    _, status = px.backfill(_matrix({"A": x, "B": x * 2}), [])
    assert status["A"].status == "real" and status["B"].fit is None


# ---------------------------------------------------------------------------
# Q1.1 covariance
# ---------------------------------------------------------------------------


def test_ewma_matches_a_plain_loop_to_1e_9() -> None:
    r = np.random.default_rng(5).normal(0, 0.02, (80, 3))
    lam = 0.5 ** (1 / 26)
    weights = [lam ** (len(r) - 1 - t) for t in range(len(r))]
    total = sum(weights)
    expected = np.zeros((3, 3))
    for t, w in enumerate(weights):
        for i in range(3):
            for j in range(3):
                expected[i, j] += w / total * r[t, i] * r[t, j]
    assert cv.ewma_lambda() == pytest.approx(0.97369, abs=1e-5)
    assert np.abs(cv.ewma_covariance(r) - expected).max() < 1e-9


def test_ewma_weights_the_newest_week_most() -> None:
    r = np.zeros((52, 1))
    r[0, 0], r[-1, 0] = 0.1, 0.1
    old_only = np.zeros((52, 1))
    old_only[0, 0] = 0.1
    assert cv.ewma_covariance(r)[0, 0] > cv.ewma_covariance(old_only)[0, 0] * 3


def _lw_loop(x: np.ndarray) -> tuple[np.ndarray, float]:
    """The published constant-correlation estimator (Ledoit & Wolf's covCor), transcribed
    element by element. Returns the shrunk matrix and the shrinkage intensity."""
    t, n = x.shape
    x = x - x.mean(axis=0)
    s = [[sum(x[k, i] * x[k, j] for k in range(t)) / (t - 1) for j in range(n)] for i in range(n)]
    c = [[sum(x[k, i] * x[k, j] for k in range(t)) / t for j in range(n)] for i in range(n)]
    sd = [math.sqrt(s[i][i]) for i in range(n)]
    pairs = [(i, j) for i in range(n) for j in range(n) if i != j]
    r_bar = sum(s[i][j] / (sd[i] * sd[j]) for i, j in pairs) / (n * (n - 1))
    f = [[r_bar * sd[i] * sd[j] if i != j else s[i][i] for j in range(n)] for i in range(n)]

    def phi(i: int, j: int) -> float:
        fourth = sum(x[k, i] ** 2 * x[k, j] ** 2 for k in range(t)) / t
        return fourth - 2 * c[i][j] * s[i][j] + s[i][j] ** 2

    def theta(i: int, j: int) -> float:
        third = sum(x[k, i] ** 3 * x[k, j] for k in range(t)) / t
        return third - c[i][i] * s[i][j] - c[i][j] * s[i][i] + s[i][i] * s[i][j]

    pi_hat = sum(phi(i, j) for i in range(n) for j in range(n))
    rho_hat = sum(phi(i, i) for i in range(n)) + r_bar * sum(
        sd[j] / sd[i] * theta(i, j) for i, j in pairs
    )
    gamma = sum((s[i][j] - f[i][j]) ** 2 for i in range(n) for j in range(n))
    delta = max(0.0, min(1.0, (pi_hat - rho_hat) / gamma / t))
    shrunk = [[delta * f[i][j] + (1 - delta) * s[i][j] for j in range(n)] for i in range(n)]
    return np.array(shrunk), delta


def test_ledoit_wolf_matches_the_element_wise_transcription() -> None:
    rng = np.random.default_rng(10)
    loadings = np.array([[0.2, 0.5, 0.9, 1.3, 1.7, 2.1]])
    r = rng.normal(0, 0.02, (60, 1)) * loadings + rng.normal(0, 0.02, (60, 6))
    expected, delta = _lw_loop(r)
    got = cv.ledoit_wolf(r)
    assert 0.0 < delta < 1.0
    assert got.intensity == pytest.approx(delta, abs=1e-12)
    assert np.abs(got.covariance - expected).max() < 1e-12


def test_ledoit_wolf_properties() -> None:
    rng = np.random.default_rng(6)
    common = rng.normal(0, 0.02, (156, 1))
    r = 0.7 * common + rng.normal(0, 0.015, (156, 8))
    out = cv.ledoit_wolf(r)
    sample = np.cov(r, rowvar=False)
    assert 0.0 <= out.intensity <= 1.0
    # The target keeps the sample variances on the diagonal, so shrinking does not move them.
    assert np.diag(out.covariance) == pytest.approx(np.diag(sample), rel=1e-12)
    # Positive definite, symmetric, and between the sample and the target off the diagonal.
    assert np.linalg.eigvalsh(out.covariance).min() > 0
    assert np.allclose(out.covariance, out.covariance.T)
    sd = np.sqrt(np.diag(sample))
    target = out.mean_correlation * np.outer(sd, sd)
    off = ~np.eye(8, dtype=bool)
    lo = np.minimum(sample, target)[off] - 1e-12
    hi = np.maximum(sample, target)[off] + 1e-12
    assert ((out.covariance[off] >= lo) & (out.covariance[off] <= hi)).all()


def test_ledoit_wolf_shrinks_harder_when_history_is_short() -> None:
    rng = np.random.default_rng(7)
    loadings = np.linspace(0.2, 2.0, 12)[None, :]

    def sample(t: int) -> np.ndarray:
        return rng.normal(0, 0.02, (t, 1)) * loadings + rng.normal(0, 0.02, (t, 12))

    wide, long_ = sample(30), sample(2000)
    assert cv.ledoit_wolf(wide).intensity > cv.ledoit_wolf(long_).intensity


def test_ledoit_wolf_hand_computed_two_assets() -> None:
    # Two assets: with n = 2 the constant-correlation target equals the sample covariance
    # (one pair, so the average correlation is that pair's correlation). Shrinking changes nothing.
    r = np.random.default_rng(8).normal(0, 0.02, (100, 2))
    out = cv.ledoit_wolf(r)
    assert out.covariance == pytest.approx(np.cov(r, rowvar=False), rel=1e-12)


def test_ledoit_wolf_matches_pyportfolioopt_output() -> None:
    """Golden test: output of a published implementation (PyPortfolioOpt, constant correlation)."""
    import json
    from pathlib import Path

    fixture = json.loads((Path(__file__).parent / "fixtures" / "ledoit_wolf_cc.json").read_text())
    out = cv.ledoit_wolf(np.array(fixture["returns"]))
    assert out.intensity == pytest.approx(fixture["delta"], rel=1e-9)
    np.testing.assert_allclose(out.covariance, np.array(fixture["covariance"]), rtol=1e-9)


def test_portfolio_volatility_annualises_by_52() -> None:
    cov = np.array([[0.0004]])  # weekly sd 2 %
    assert cv.portfolio_volatility(np.array([1.0]), cov) == pytest.approx(0.02 * math.sqrt(52))
    # Cash is the weight that is not there: half invested halves the volatility.
    assert cv.portfolio_volatility(np.array([0.5]), cov) == pytest.approx(0.01 * math.sqrt(52))


# ---------------------------------------------------------------------------
# Q1.2 decomposition
# ---------------------------------------------------------------------------


def _annual_to_weekly(vols: list[float], corr: float) -> np.ndarray:
    sd = np.array(vols) / math.sqrt(52)
    c = np.full((len(vols), len(vols)), corr)
    np.fill_diagonal(c, 1.0)
    return c * np.outer(sd, sd)


def test_risk_shares_hand_computed_for_two_uncorrelated_assets() -> None:
    # 20 % and 10 % volatility, uncorrelated, 50/50: variance = .25(.04) + .25(.01) = .0125.
    d = dc.decompose(["A", "B"], np.array([0.5, 0.5]), _annual_to_weekly([0.20, 0.10], 0.0))
    assert d.volatility == pytest.approx(math.sqrt(0.0125))
    assert d.positions[0].risk_share == pytest.approx(0.8)
    assert d.positions[1].risk_share == pytest.approx(0.2)
    assert sum(p.risk_share for p in d.positions) == pytest.approx(1.0)
    # Diversification ratio (0.5 x 0.20 + 0.5 x 0.10) / sqrt(.0125).
    assert d.diversification_ratio == pytest.approx(0.15 / math.sqrt(0.0125))
    # Contributions in volatility units add to the portfolio volatility.
    assert sum(p.contribution for p in d.positions) == pytest.approx(d.volatility)


def test_risk_shares_sum_to_one_with_cash_and_many_assets() -> None:
    rng = np.random.default_rng(9)
    r = rng.normal(0, 0.02, (156, 10)) + rng.normal(0, 0.01, (156, 1))
    w = np.full(10, 0.07)  # 70 % invested, 30 % cash
    d = dc.decompose([str(i) for i in range(10)], w, np.cov(r, rowvar=False))
    assert sum(p.risk_share for p in d.positions) == pytest.approx(1.0, abs=1e-12)
    assert d.volatility == pytest.approx(cv.portfolio_volatility(w, np.cov(r, rowvar=False)))


def test_effective_bets_bounds() -> None:
    n = 5
    uncorrelated = dc.decompose(
        [str(i) for i in range(n)], np.full(n, 0.2), _annual_to_weekly([0.2] * n, 0.0)
    )
    assert uncorrelated.effective_bets == pytest.approx(n, abs=1e-9)
    one_factor = dc.decompose(
        [str(i) for i in range(n)], np.full(n, 0.2), _annual_to_weekly([0.2] * n, 0.999999)
    )
    assert one_factor.effective_bets is not None and one_factor.effective_bets < 1.01
    assert one_factor.diversification_ratio == pytest.approx(1.0, abs=1e-4)


def test_group_shares_add_by_label_and_name_the_unclassified() -> None:
    d = dc.decompose(["A", "B", "C"], np.array([0.4, 0.3, 0.3]), _annual_to_weekly([0.2] * 3, 0.3))
    rows = dc.group_shares(d.positions, {"A": "core", "B": "growth"})
    by = {g: (w, s) for g, w, s in rows}
    assert by["Unclassified"][0] == pytest.approx(0.3)
    assert sum(s for _, _, s in rows) == pytest.approx(1.0)


def test_zero_variance_portfolio_has_no_ratios() -> None:
    d = dc.decompose(["A"], np.array([0.0]), np.array([[0.0004]]))
    assert d.diversification_ratio is None and d.effective_bets is None
    assert d.positions[0].risk_share == 0.0
