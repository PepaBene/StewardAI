"""Ken French parsing, factor regressions and the exposure report (quant plan Q4.1 to Q4.3), on
synthetic data with known loadings."""

import datetime as dt
import io
import math
import zipfile

import numpy as np
import pytest

from steward.portfolio import exante as ex
from steward.portfolio import factors as pf
from steward.quant import factors as fc
from steward.quant import french
from steward.quant import returns as rt

FILE = """This file was created by CMPT_ME_BEME_OP_INV_RETS_DAILY using 202508 data.

,Mkt-RF,SMB,HML,RMW,CMA,RF
20250102,  1.00, -0.50,  0.25,  0.10, -0.20,  0.02
20250103, -99.99,  0.10,  0.00,  0.00,  0.00,  0.02
20250106,  2.00,  0.00,  0.00,  0.00,  0.00,  0.02

Copyright 2025 Kenneth R. French
"""


def test_the_parser_reads_percent_as_fractions_skips_missing_and_stops_at_the_notes() -> None:
    got = french.parse_daily(FILE)
    assert got["MKT_RF"] == [(dt.date(2025, 1, 2), 0.01), (dt.date(2025, 1, 6), 0.02)]
    assert got["SMB"][0] == (dt.date(2025, 1, 2), -0.005)
    assert len(got["RF"]) == 3
    assert "Copyright" not in str(got)


def test_momentum_columns_are_named_alike_and_a_zip_is_unpacked() -> None:
    for header in (",Mom   ", ",WML"):
        got = french.parse_daily(f"notes\n\n{header}\n20250102,  1.50\n")
        assert got == {"MOM": [(dt.date(2025, 1, 2), 0.015)]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("F-F.csv", FILE)
    assert "HML" in french.parse_zip(buffer.getvalue())
    with pytest.raises(french.FrenchFormatError):
        french.parse_daily("no header here\n20250102, 1.0\n")


def test_regression_recovers_known_loadings_and_a_zero_loading_is_insignificant() -> None:
    rng = np.random.default_rng(3)
    n = 200
    a, b, c = rng.normal(0, 0.02, n), rng.normal(0, 0.01, n), rng.normal(0, 0.01, n)
    y = 0.001 + 1.1 * a - 0.4 * b + rng.normal(0, 0.004, n)
    fit = fc.regress(y, {"A": a, "B": b, "C": c})
    assert fit is not None
    assert fit.betas["A"] == pytest.approx(1.1, abs=0.05)
    assert fit.betas["B"] == pytest.approx(-0.4, abs=0.1)
    assert abs(fit.t_stats["C"]) < 3 and abs(fit.t_stats["A"]) > 20
    assert fit.r_squared > 0.95 and fit.observations == n
    # a row with a gap drops out; too few rows or collinear factors give no fit
    y2 = y.copy()
    y2[:5] = np.nan
    again = fc.regress(y2, {"A": a, "B": b})
    assert again is not None and again.observations == n - 5
    assert fc.regress(y[:20], {"A": a[:20]}) is None
    assert fc.regress(y, {"A": a, "twice": 2 * a}) is None


def test_newey_west_matches_ols_variance_when_errors_are_independent() -> None:
    rng = np.random.default_rng(8)
    n = 4000
    x = np.column_stack([np.ones(n), rng.normal(0, 1, n)])
    e = rng.normal(0, 1, n)
    nw = fc.newey_west_covariance(x, e)
    ols = np.linalg.inv(x.T @ x) * float(e @ e) / n
    assert nw[1, 1] == pytest.approx(ols[1, 1], rel=0.15)


def test_weekly_factor_returns_compound_the_days_and_leave_thin_weeks_missing() -> None:
    wed = dt.date(2025, 1, 8)
    days = [
        wed - dt.timedelta(days=i)
        for i in range(6, -1, -1)
        if (wed - dt.timedelta(days=i)).weekday() < 5
    ]
    daily = {"MKT_RF": [(d, 0.01) for d in days]}
    got = fc.weekly_factor_returns(daily, [wed, wed + dt.timedelta(days=7)])
    assert got["MKT_RF"][0] == pytest.approx(len(days) * math.log(1.01))
    assert math.isnan(got["MKT_RF"][1])  # a week with no daily rows


START = dt.date(2024, 1, 3)  # a Wednesday
WEEKS = 120


def _rate(currency: str, day: dt.date) -> float | None:
    if currency == "CZK":
        return 1.0
    wobble = math.sin(day.toordinal() / 17) if currency == "USD" else math.cos(day.toordinal() / 23)
    return (22.0 if currency == "USD" else 25.0) + 0.6 * wobble


def _scenario() -> tuple[ex.LoadedBook, dict[str, list[tuple[dt.date, float]]]]:
    rng = np.random.default_rng(21)
    first = START - dt.timedelta(days=14)
    last = START + dt.timedelta(weeks=WEEKS)
    days = [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    daily = {
        "MKT_RF": [(d, float(rng.normal(0, 0.008))) for d in days],
        "SMB": [(d, float(rng.normal(0, 0.004))) for d in days],
        "HML": [(d, float(rng.normal(0, 0.004))) for d in days],
        "RMW": [(d, float(rng.normal(0, 0.003))) for d in days],
        "CMA": [(d, float(rng.normal(0, 0.003))) for d in days],
        "MOM": [(d, float(rng.normal(0, 0.005))) for d in days],
        "RF": [(d, 0.0001) for d in days],
    }
    dates = [START + dt.timedelta(weeks=i) for i in range(WEEKS)]
    fw = fc.weekly_factor_returns(daily, dates)
    rf = fw["RF"]
    cols = {
        # a value-tilted, small-cap holding and the benchmark, which is the market itself
        "VAL": rf
        + 1.0 * fw["MKT_RF"]
        + 0.8 * fw["HML"]
        + 0.5 * fw["SMB"]
        + rng.normal(0, 0.004, WEEKS),
        "MIX": rf + 0.9 * fw["MKT_RF"] + rng.normal(0, 0.004, WEEKS),
        ex.BENCHMARK_KEY: rf + 1.0 * fw["MKT_RF"] + rng.normal(0, 0.0015, WEEKS),
    }
    keys = list(cols)
    data = np.column_stack(list(cols.values()))
    matrix = rt.ReturnMatrix("weekly", dates, keys, data, rt.coverage_of(keys, dates, data))
    held = [
        ex.BookPosition("VAL", "Value", "CZK", 600.0),
        ex.BookPosition("MIX", "Mix", "CZK", 200.0),
    ]
    book = ex.LoadedBook("CZK", 1000.0, held, matrix, {}, {ex.BENCHMARK_KEY: "Benchmark"}, {})
    return book, daily


def test_the_benchmark_regressed_on_its_own_market_has_beta_one_and_a_high_r_squared() -> None:
    book, daily = _scenario()
    report = pf.build_exposures(book, daily, _rate, "developed")
    assert report.reason is None and report.benchmark is not None
    beta = {b.factor: b for b in report.benchmark.betas}
    assert beta["MKT_RF"].beta == pytest.approx(1.0, abs=0.05)
    assert report.benchmark.r_squared > 0.9


def test_the_portfolio_tilts_toward_value_and_small_caps_and_says_so() -> None:
    book, daily = _scenario()
    report = pf.build_exposures(book, daily, _rate, "developed")
    tilts = {t.factor: t for t in report.tilts}
    # 600/800 in VAL: HML about 0.6, SMB about 0.375, against a benchmark with none
    assert tilts["HML"].tilt == pytest.approx(0.6, abs=0.1) and tilts["HML"].flagged
    assert tilts["SMB"].flagged and tilts["SMB"].note and "small companies" in tilts["SMB"].note
    assert not tilts["RMW"].flagged and tilts["RMW"].note is None
    assert not tilts["USD"].flagged  # currencies are reported, never flagged
    assert [p.ticker for p in report.positions] == ["VAL", "MIX"]
    assert all(p.fit is not None and p.basis == "real" for p in report.positions)
    assert report.coverage is not None and report.coverage.weeks_without_factors == 0
    assert report.method_version == pf.METHOD_VERSION


def test_without_stored_factors_the_report_says_how_to_get_them() -> None:
    book, _ = _scenario()
    report = pf.build_exposures(book, {}, _rate, "europe")
    assert report.portfolio is None and report.reason is not None
    assert "factor returns job" in report.reason and "europe" in report.reason


def test_weeks_after_the_last_factor_date_are_reported_and_left_out() -> None:
    book, daily = _scenario()
    cut = START + dt.timedelta(weeks=WEEKS - 6)  # the last week with factor data is index 114
    short = {k: [(d, v) for d, v in s if d <= cut] for k, s in daily.items()}
    report = pf.build_exposures(book, short, _rate, "developed")
    missing = 5  # weeks 115 to 119 of the 120 have no daily factor rows
    assert report.coverage is not None and report.coverage.weeks_without_factors == missing
    assert any("end on" in w for w in report.warnings)
    assert report.portfolio is not None and report.portfolio.observations == 104 - missing
