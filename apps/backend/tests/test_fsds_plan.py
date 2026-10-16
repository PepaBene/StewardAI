"""Which SEC Financial Statement and Notes data sets the scheduled backfill still reads."""

import datetime as dt

import pytest

from steward.fundamentals.fsds import plan_datasets, token_months
from worker.backfill import parse_params

TODAY = dt.date(2026, 10, 16)  # newest set: 2026_09; monthly back to 2025_10, quarters before
A, B = "0000000001", "0000000002"


def tokens(plan: list[tuple[str, set[str]]]) -> list[str]:
    return [token for token, _ in plan]


def test_token_months() -> None:
    assert token_months("2026_09") == [(2026, 9)]
    assert token_months("2025q3") == [(2025, 7), (2025, 8), (2025, 9)]
    assert token_months("2024q1") == [(2024, 1), (2024, 2), (2024, 3)]
    for bad in ("2026_13", "2025q5", "notes", "2025-09"):
        with pytest.raises(ValueError):
            token_months(bad)


def test_fresh_plan_is_twelve_months_then_quarters_newest_first() -> None:
    plan = plan_datasets(TODAY, 24, {A}, {}, {})
    assert tokens(plan) == [
        "2026_09", "2026_08", "2026_07", "2026_06", "2026_05", "2026_04",
        "2026_03", "2026_02", "2026_01", "2025_12", "2025_11", "2025_10",
        "2025q3", "2025q2", "2025q1", "2024q4",
    ]  # fmt: skip
    assert all(need == {A} for _, need in plan)


def test_read_sets_drop_out_and_monthly_reads_cover_their_quarter() -> None:
    loaded = {"2026_09": {A}, "2025q3": {A}, "2025_10": {A}, "2025_11": {A}, "2025_12": {A}}
    plan = plan_datasets(TODAY, 24, {A}, loaded, {})
    assert "2026_09" not in tokens(plan) and "2025q3" not in tokens(plan)
    # A year on, Oct-Dec 2025 have aged into 2025q4, but the months were read already.
    later = plan_datasets(dt.date(2027, 2, 1), 24, {A}, loaded, {})
    assert "2025q4" not in tokens(later)


def test_a_new_holding_is_read_from_the_sets_already_read() -> None:
    plan = dict(plan_datasets(TODAY, 15, {A, B}, {"2025q3": {A}, "2026_09": {A, B}}, {}))
    assert "2026_09" not in plan
    assert plan["2025q3"] == {B}
    assert plan["2026_08"] == {A, B}


def test_a_set_that_was_not_published_is_rechecked_after_three_days() -> None:
    missing = {"2026_09": dt.date(2026, 10, 15)}
    plan = plan_datasets(TODAY, 3, {A}, {}, missing)
    # The month's own set is missing, so its quarter is tried (it 404s too until consolidated).
    assert tokens(plan) == ["2026q3", "2026_08", "2026_07"]
    both = {"2026_09": dt.date(2026, 10, 15), "2026q3": dt.date(2026, 10, 15)}
    assert tokens(plan_datasets(TODAY, 3, {A}, {}, both)) == ["2026_08", "2026_07"]
    assert tokens(plan_datasets(dt.date(2026, 10, 19), 1, {A}, {}, missing)) == ["2026_09"]


def test_around_the_one_year_line_the_other_kind_of_set_is_tried() -> None:
    # 2025_10 is 12 months old on 2026-11-02: its quarter 2025q4 is preferred but not out yet.
    plan = plan_datasets(dt.date(2026, 11, 2), 13, {A}, {}, {"2025q4": dt.date(2026, 11, 1)})
    assert tokens(plan)[-1] == "2025_10"


def test_nothing_to_plan_without_holdings() -> None:
    assert plan_datasets(TODAY, 60, set(), {}, {}) == []


def test_backfill_command_params_for_one_data_set() -> None:
    assert parse_params(["quarter=2024q4", " ticker = KO_US_EQ "]) == {
        "quarter": "2024q4",
        "ticker": "KO_US_EQ",
    }
    for bad in (["quarter"], ["=2024q4"]):
        with pytest.raises(ValueError):
            parse_params(bad)
