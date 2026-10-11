"""Sizing and concentration arithmetic (plan Q5.2, Q5.3, Q5.5), by hand-worked examples."""

import datetime as dt
from types import SimpleNamespace

import pytest

from steward.config.strategy import PretradeThresholds
from steward.portfolio.sizing import conviction_confidence
from steward.quant import sizing as sz


def test_the_conviction_mapping_follows_the_strategy_settings() -> None:
    assert [conviction_confidence(c) for c in (1, 3, 5)] == pytest.approx([0.2, 0.5, 0.8])
    assert conviction_confidence(None) == pytest.approx(0.5)
    assert conviction_confidence(5, 0.3, 0.7) == pytest.approx(0.7)
    assert conviction_confidence(2, 0.3, 0.7) == pytest.approx(0.4)
    with pytest.raises(ValueError, match="confidence_low"):
        PretradeThresholds(confidence_low=0.9, confidence_high=0.4)
    assert PretradeThresholds().equity_premium == pytest.approx(0.05)


class _FakePool:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    async def fetch(self, *_: object) -> list[dict[str, object]]:
        return self.rows


async def test_the_start_size_is_cut_to_fit_the_largest_move_on_a_report_date() -> None:
    from steward.portfolio.sizing import _event_cap

    days = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(10)]
    closes = [(d, 100.0) for d in days]
    closes[5] = (days[5], 90.0)  # a 10 % fall over the report on day 4/5
    closes[6:] = [(d, 90.0) for d in days[6:]]
    pool = _FakePool([{"ticker": "AAA", "day": days[5]}])
    book = SimpleNamespace(daily_closes={"AAA": closes})
    cap = await _event_cap(pool, "AAA", 0.01, {}, book)  # type: ignore[arg-type]
    assert cap is not None
    move, weight = cap
    assert move == pytest.approx(0.10) and weight == pytest.approx(0.10)  # 1 % / 10 %
    assert await _event_cap(pool, "AAA", None, {}, book) is None  # type: ignore[arg-type]
    no_dates = _FakePool([])
    assert await _event_cap(no_dates, "AAA", 0.01, {}, book) is None  # type: ignore[arg-type]


def test_the_equilibrium_return_is_the_risk_free_rate_plus_beta_times_the_premium() -> None:
    assert sz.equilibrium_return(0.03, 1.2, 0.05) == pytest.approx(0.09)


def test_a_thesis_return_is_pulled_toward_the_equilibrium_by_its_confidence() -> None:
    assert sz.shrink_expected_return(0.30, 0.08, 1.0) == pytest.approx(0.30)
    assert sz.shrink_expected_return(0.30, 0.08, 0.0) == pytest.approx(0.08)
    assert sz.shrink_expected_return(0.30, 0.08, 0.5) == pytest.approx(0.19)
    assert sz.shrink_expected_return(0.30, 0.08, 7.0) == pytest.approx(0.30)  # clamped


def test_the_kelly_bound_is_a_quarter_of_excess_return_over_variance() -> None:
    k = sz.kelly_bound(0.15, 0.03, 0.30)
    assert k.full == pytest.approx(0.12 / 0.09)
    assert k.bound == pytest.approx(0.25 * 0.12 / 0.09)
    assert k.excess_return == pytest.approx(0.12)


def test_a_return_below_the_risk_free_rate_gives_a_zero_bound_and_no_vol_gives_none() -> None:
    assert sz.kelly_bound(0.01, 0.03, 0.2).bound == 0.0
    assert sz.kelly_bound(0.2, 0.03, 0.0).bound is None


def test_the_largest_feasible_weight_is_found_by_bisection() -> None:
    assert sz.largest_feasible(lambda w: w <= 0.0377, 0.05) == pytest.approx(0.0377, abs=1e-5)
    assert sz.largest_feasible(lambda w: True, 0.05) == 0.05  # the cap itself is fine
    assert sz.largest_feasible(lambda w: False, 0.05) == 0.0  # nothing fits
    assert sz.largest_feasible(lambda w: True, 0.0) == 0.0


def test_deposits_dilute_a_position_and_the_path_says_when_it_reaches_the_cap() -> None:
    # 12 of 100 held; adding 5 a month: 12/(100+5m) <= 0.10 from m = 4 (12/120).
    d = sz.dilution_path(12.0, 100.0, 5.0, 0.10, months=12)
    assert d.weight_now == pytest.approx(0.12)
    assert d.months_to_cap == 4
    assert d.path[4].weight == pytest.approx(0.10)
    assert [s.month for s in d.path][:3] == [0, 1, 2]


def test_no_deposit_never_dilutes() -> None:
    d = sz.dilution_path(12.0, 100.0, 0.0, 0.10, months=6)
    assert d.months_to_cap is None
    assert d.path[-1].weight == pytest.approx(0.12)
    assert sz.dilution_path(8.0, 100.0, 0.0, 0.10).months_to_cap == 0


def test_trimming_keeps_the_total_and_never_goes_negative() -> None:
    assert sz.trim_to(12.0, 100.0, 0.08) == pytest.approx(4.0)
    assert sz.trim_to(7.0, 100.0, 0.08) == 0.0
    assert sz.trim_to(1.0, 0.0, 0.08) == 0.0
