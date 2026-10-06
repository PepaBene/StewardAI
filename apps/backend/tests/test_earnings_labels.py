import datetime as dt

from steward.earnings.periods import label_year_for_end
from steward.earnings.units import currency_markers_present

TOL = 7


def test_year_named_by_its_start_is_moved_to_the_year_it_ends_in() -> None:
    # Daiichi Sankyo: FY2026 Q1 ended 2026-06-30; fiscal year ends in March.
    assert label_year_for_end(2026, "Q1", 3, dt.date(2026, 6, 30), TOL) == 2027
    assert label_year_for_end(2026, "FY", 3, dt.date(2027, 3, 31), TOL) == 2027


def test_a_label_that_already_fits_is_kept() -> None:
    assert label_year_for_end(2026, "Q2", 12, dt.date(2026, 6, 30), TOL) == 2026
    assert label_year_for_end(2026, "Q1", 3, dt.date(2025, 6, 30), TOL) == 2026


def test_no_stated_date_or_no_fit_keeps_the_label() -> None:
    assert label_year_for_end(2026, "Q1", 3, None, TOL) == 2026
    assert label_year_for_end(2026, "Q1", 3, dt.date(2026, 2, 28), TOL) == 2026


def test_taiwan_dollar_spellings() -> None:
    assert currency_markers_present("Net revenue was NT$933.8 billion", "TWD")
    assert currency_markers_present("in New Taiwan dollars", "TWD")
    assert not currency_markers_present("revenue was US$10 billion", "TWD")
