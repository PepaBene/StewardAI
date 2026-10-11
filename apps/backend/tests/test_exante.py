"""Ex-ante risk report and the limits built on it (quant plan Q0.5, Q1.3, Q1.4), on a synthetic
book with no database."""

import datetime as dt

import numpy as np
import pytest
from pydantic import ValidationError

from steward.config.strategy import RiskLimits, StrategyConfig
from steward.portfolio import exante as ex
from steward.portfolio.risk_limits import RiskInputs, TierFlag, evaluate_risk
from steward.quant import returns as rt

START = dt.date(2025, 1, 1)  # a Wednesday


def _book(
    columns: dict[str, np.ndarray],
    values: dict[str, float],
    closes: dict[str, list[tuple[dt.date, float]]] | None = None,
    cash: float = 0.0,
    labels: dict[str, str] | None = None,
) -> ex.LoadedBook:
    keys = list(columns)
    data = np.column_stack([columns[k] for k in keys])
    dates = [START + dt.timedelta(weeks=i) for i in range(data.shape[0])]
    matrix = rt.ReturnMatrix("weekly", dates, keys, data, rt.coverage_of(keys, dates, data))
    held = [ex.BookPosition(t, t.title(), "CZK", v) for t, v in values.items()]
    return ex.LoadedBook(
        "CZK", sum(values.values()) + cash, held, matrix, closes or {}, labels or {}, {}
    )


def _factor_book() -> ex.LoadedBook:
    rng = np.random.default_rng(11)
    market = rng.normal(0.002, 0.02, 156)
    cols = {
        "AAA": 1.0 * market + rng.normal(0, 0.01, 156),
        "BBB": 0.6 * market + rng.normal(0, 0.012, 156),
        "CCC": rng.normal(0.001, 0.025, 156),
        ex.PROXY_PREFIX + "xlk.us": market,
    }
    return _book(
        cols,
        {"AAA": 400_000.0, "BBB": 300_000.0, "CCC": 100_000.0},
        cash=200_000.0,
        labels={ex.PROXY_PREFIX + "xlk.us": "US technology"},
    )


def test_report_shares_sum_to_one_and_cash_counts_at_zero_volatility() -> None:
    strategy = StrategyConfig(
        base_currency="CZK",
        categories=["sector", "geography", "currency", "risk"],
        classifications={
            "AAA": {"risk": "core", "sector": "Tech", "geography": "US"},
            "BBB": {"risk": "growth", "sector": "Tech"},
        },
    )
    report = ex.build_report(_factor_book(), strategy)
    assert report.reason is None and report.volatility_forecast is not None
    assert report.volatility_forecast == pytest.approx(
        max(report.volatility_ewma or 0, report.volatility_ledoit_wolf or 0)
    )
    assert sum(p.risk_share or 0 for p in report.positions) == pytest.approx(1.0)
    assert sum(g.risk_share for g in report.by_tier) == pytest.approx(1.0)
    by_tier = {g.label: g for g in report.by_tier}
    assert by_tier["core"].weight == pytest.approx(0.4)  # 400k of 1,000k total, cash included
    assert by_tier["Unclassified"].weight == pytest.approx(0.1)
    assert report.coverage is not None
    assert report.coverage.invested_weight == pytest.approx(0.8)
    assert report.coverage.real_weight == pytest.approx(0.8)
    assert report.coverage.uncovered_weight == pytest.approx(0.0)
    assert report.effective_bets is not None and 1.0 <= report.effective_bets <= 3.0
    assert report.window_end == report.as_of and report.weeks == 156
    assert report.method_version == ex.METHOD_VERSION


def test_a_short_history_is_proxied_and_labelled_and_a_tiny_one_is_left_out() -> None:
    book = _factor_book()
    values = book.weekly.values.copy()
    values[:100, book.weekly.keys.index("BBB")] = np.nan  # 56 weeks of its own
    values[:140, book.weekly.keys.index("CCC")] = np.nan  # 16 weeks of its own
    book.weekly = rt.ReturnMatrix(
        "weekly",
        book.weekly.dates,
        book.weekly.keys,
        values,
        rt.coverage_of(book.weekly.keys, book.weekly.dates, values),
    )
    report = ex.build_report(book, StrategyConfig(base_currency="CZK"))
    rows = {p.ticker: p for p in report.positions}
    assert rows["BBB"].status == "proxied" and rows["BBB"].proxy is not None
    assert rows["BBB"].proxy.label == "US technology" and rows["BBB"].real_weeks == 56
    assert rows["BBB"].proxy.backfilled_weeks == 100
    assert rows["CCC"].status == "excluded" and "16 weekly returns" in (rows["CCC"].reason or "")
    assert rows["CCC"].risk_share is None
    cov = report.coverage
    assert cov is not None and cov.proxied_weight == pytest.approx(0.3)
    assert cov.uncovered_weight == pytest.approx(0.1)
    assert any("left out of the model" in w for w in report.warnings)
    # The shares still add to 100 % over the covered positions.
    assert sum(p.risk_share or 0 for p in report.positions) == pytest.approx(1.0)


def test_no_usable_history_gives_a_reason_not_a_number() -> None:
    nan = np.full(156, np.nan)
    report = ex.build_report(
        _book({"AAA": nan}, {"AAA": 100.0}), StrategyConfig(base_currency="CZK")
    )
    assert report.volatility_forecast is None and report.reason is not None
    assert report.positions[0].status == "excluded"


def test_empty_portfolio() -> None:
    book = _book({"AAA": np.zeros(10)}, {})
    assert ex.build_report(book, StrategyConfig(base_currency="CZK")).reason == "no open positions"


def test_weekly_correlation_uses_shared_real_weeks_and_skips_short_pairs() -> None:
    rng = np.random.default_rng(12)
    base = rng.normal(0, 0.02, 156)
    short = base.copy()
    short[:130] = np.nan
    book = _book(
        {"AAA": base, "BBB": base * 2, "CCC": short},
        {"AAA": 100.0, "BBB": 100.0, "CCC": 100.0},
    )
    pairs = {(a, b): (c, n) for a, b, c, n in ex.weekly_pair_correlations(book)}
    assert pairs[("AAA", "BBB")] == (pytest.approx(1.0), 104)
    # CCC has 26 shared weeks, under the 40 needed: no correlation is reported for it.
    assert not any("CCC" in pair for pair in pairs)


def test_weekly_returns_remove_the_closing_time_bias_of_daily_ones() -> None:
    """A US stock that closes after a European one reacts a day later: its daily returns
    correlate weakly with the European stock's, its weekly returns almost fully."""
    rng = np.random.default_rng(13)
    shock = rng.normal(0, 0.01, 2000)
    eu = shock
    us = np.roll(shock, 1)  # the same news, one trading day later
    daily = np.corrcoef(eu[1:], us[1:])[0, 1]
    weekly = np.corrcoef(
        eu[:1995].reshape(-1, 5).sum(axis=1), us[:1995].reshape(-1, 5).sum(axis=1)
    )[0, 1]
    assert daily < 0.1 and weekly > 0.7


def test_tier_checks_flag_the_tier_criteria_with_the_holding_pages_volatility() -> None:
    rng = np.random.default_rng(14)
    days = [dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(300)]
    calm = np.cumprod(1 + rng.normal(0, 0.01, 300)) * 100  # ~16 % annualised
    wild = np.cumprod(1 + rng.normal(0, 0.03, 300)) * 100  # ~48 % annualised
    closes = {
        "CALM": list(zip(days, calm, strict=True)),
        "WILD": list(zip(days, wild, strict=True)),
    }
    book = _book(
        {"CALM": np.zeros(10), "WILD": np.zeros(10), "NEW": np.zeros(10)},
        {"CALM": 1.0, "WILD": 1.0, "NEW": 1.0},
        closes,
    )
    strategy = StrategyConfig(
        base_currency="CZK",
        categories=["risk"],
        classifications={"CALM": {"risk": "core"}, "WILD": {"risk": "core"}},
        risk=RiskLimits(tier_speculative_volatility=0.35, tier_core_max_volatility=0.25),
    )
    checks = ex.tier_checks(book, strategy)
    flagged = {(c.ticker, c.rule) for c in checks if c.breached}
    assert flagged == {("WILD", "speculative_above"), ("WILD", "core_above")}
    calm_checks = [c for c in checks if c.ticker == "CALM"]
    assert calm_checks and not any(c.breached for c in calm_checks)
    assert all(c.window_days == 252 and c.observations == 252 for c in checks)
    # A holding with no assigned tier is never flagged.
    assert not any(c.ticker == "NEW" for c in checks)


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_new_limits_default_to_off() -> None:
    inputs = RiskInputs(
        volatility_forecast=9.0,
        tier_risk_shares={"core": 0.99},
        effective_bets=0.1,
        tier_flags=[TierFlag("X", "core", 0.9, 0.25, "core_above")],
    )
    keys = {f.key for f in evaluate_risk(inputs, RiskLimits())}
    assert keys == {"risk:tier:X"}  # a tier flag is only produced when its limit is set


def test_a_holding_that_breaches_both_tier_rules_opens_one_finding() -> None:
    inputs = RiskInputs(
        tier_flags=[
            TierFlag("X", "core", 0.5, 0.25, "core_above"),
            TierFlag("X", "core", 0.5, 0.35, "speculative_above"),
        ]
    )
    found = evaluate_risk(inputs, RiskLimits())
    assert [f.key for f in found] == ["risk:tier:X"]
    assert "core ceiling" in found[0].detail and "speculative" in found[0].detail


def test_forecast_volatility_has_its_own_finding_key() -> None:
    limits = RiskLimits(max_volatility=0.20)
    inputs = RiskInputs(volatility=0.15, volatility_forecast=0.23, forecast_model="ewma")
    found = evaluate_risk(inputs, limits)
    assert [f.key for f in found] == ["risk:volatility_forecast"]
    assert "EWMA" in found[0].detail and found[0].observed == pytest.approx(0.23)
    # Both measures can breach together, each under its own key.
    both = evaluate_risk(RiskInputs(volatility=0.25, volatility_forecast=0.23), limits)
    assert {f.key for f in both} == {"risk:volatility", "risk:volatility_forecast"}


def test_tier_budgets_and_effective_bets() -> None:
    limits = RiskLimits(
        tier_risk_budgets={"core": 0.45, "growth": 0.45, "speculative": 0.25},
        min_effective_bets=4,
    )
    inputs = RiskInputs(
        tier_risk_shares={"core": 0.40, "growth": 0.30, "speculative": 0.30},
        effective_bets=3.2,
    )
    found = {f.key: f for f in evaluate_risk(inputs, limits)}
    assert set(found) == {"risk:tier_budget:speculative", "risk:effective_bets"}
    assert found["risk:tier_budget:speculative"].observed == pytest.approx(0.30)
    assert found["risk:effective_bets"].limit == pytest.approx(4)
    ok = RiskInputs(tier_risk_shares={"core": 0.45}, effective_bets=4.0)
    assert evaluate_risk(ok, limits) == []  # at the line is not a breach


def test_tier_budget_for_an_unlisted_tier_is_unchecked() -> None:
    limits = RiskLimits(tier_risk_budgets={"core": 0.45})
    assert evaluate_risk(RiskInputs(tier_risk_shares={"growth": 0.9}), limits) == []


def test_risk_limit_schema_rejects_impossible_values() -> None:
    with pytest.raises(ValidationError):
        RiskLimits(tier_risk_budgets={"core": 1.5})
    with pytest.raises(ValidationError):
        RiskLimits(min_effective_bets=0)
