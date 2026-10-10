import datetime as dt
from decimal import Decimal

import pytest

from steward.analytics.returns import DayValue
from steward.performance import evaluation as ev


def D(day: int, month: int = 1) -> dt.date:
    return dt.date(2026, month, day)


def series(*pairs: tuple[dt.date, str, str]) -> list[DayValue]:
    return [DayValue(d, Decimal(v), Decimal(f)) for d, v, f in pairs]


def test_period_effects_add_up_to_excess_return_within_one_basis_point():
    segments = {
        "Tech": series((D(1), "600", "0"), (D(31), "700", "0")),
        "Energy": series((D(1), "400", "0"), (D(31), "380", "0")),
    }
    p = ev.attribute_period("2026-01", segments, D(1), D(31), 0.03)
    assert p is not None
    assert p.portfolio_return == pytest.approx((700 + 380) / 1000 - 1)
    assert abs(p.reconciliation_error_bp) < 1e-6
    total = sum(s.contribution for s in p.segments) + p.opened_in_period
    assert total == pytest.approx(p.excess_return)
    assert p.allocation == pytest.approx(0.0) and p.interaction == pytest.approx(0.0)
    tech = next(s for s in p.segments if s.name == "Tech")
    assert tech.opening_weight == pytest.approx(0.6)
    assert tech.return_ == pytest.approx(700 / 600 - 1)


def test_a_deposit_into_a_segment_is_not_a_gain():
    # 100 of the 650 closing value was bought in the period
    segments = {"Tech": series((D(1), "500", "0"), (D(31), "650", "100"))}
    p = ev.attribute_period("m", segments, D(1), D(31), 0.0)
    assert p is not None
    assert p.portfolio_return == pytest.approx(0.1)


def test_position_opened_in_period_is_its_own_line_and_still_reconciles():
    segments = {
        "Old": series((D(1), "1000", "0"), (D(31), "1050", "0")),
        "New": series((D(1), "0", "0"), (D(31), "210", "200")),
    }
    p = ev.attribute_period("m", segments, D(1), D(31), 0.02)
    assert p is not None
    assert p.opened_in_period == pytest.approx(10 / 1000)
    assert p.portfolio_return == pytest.approx(60 / 1000)
    assert abs(p.reconciliation_error_bp) < 1e-6
    assert [s.name for s in p.segments] == ["Old"]


def test_nothing_held_at_the_start_gives_no_period():
    segments = {"New": series((D(1), "0", "0"), (D(31), "100", "100"))}
    assert ev.attribute_period("m", segments, D(1), D(31), 0.0) is None


def test_month_windows_start_on_the_last_day_before_the_month():
    days = [D(30, 1), D(31, 1), D(15, 2), D(28, 2), D(10, 3)]
    out = ev.month_windows(days, months=2)
    assert out == [
        ("2026-02", D(31, 1), D(28, 2)),
        ("2026-03", D(28, 2), D(10, 3)),
    ]
    assert ev.month_windows([D(1)]) == []


def test_weekly_pairs_remove_flows_and_align_the_benchmark():
    days = series(
        (D(1), "100", "0"),
        (D(8), "121", "10"),  # +10 deposit: return is (121-10)/100-1
        (D(15), "133.1", "0"),
    )
    bench = [(D(1), Decimal(100)), (D(8), Decimal(105)), (D(15), Decimal(110))]
    p, b = ev.weekly_pairs(days, bench)
    assert p == pytest.approx([0.11, 0.1])
    assert b == pytest.approx([0.05, 110 / 105 - 1])


def test_skill_report_needs_enough_weeks():
    out = ev.skill_report(series((D(1), "100", "0"), (D(8), "101", "0")), [])
    assert out.probability is None and "at least" in out.verdict
