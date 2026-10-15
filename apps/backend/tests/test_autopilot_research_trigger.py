"""Which holdings the autopilot queues research for."""

import datetime as dt

from steward.autopilot.research_trigger import due_tickers
from steward.config.thesis import ThesisConfig

TODAY = dt.date(2026, 11, 9)


def thesis(ticker: str, review: dt.date | None) -> ThesisConfig:
    return ThesisConfig(ticker=ticker, why_owned="x", review_date=review)


def test_only_overdue_theses_are_due_most_overdue_first() -> None:
    theses = [
        thesis("B_US_EQ", dt.date(2026, 11, 1)),
        thesis("A_US_EQ", dt.date(2026, 10, 1)),
        thesis("C_US_EQ", dt.date(2026, 12, 1)),
        thesis("D_US_EQ", None),
        thesis("E_US_EQ", TODAY),
    ]
    assert due_tickers(theses, TODAY, set(), set(), 10) == ["A_US_EQ", "B_US_EQ", "E_US_EQ"]


def test_recent_reports_and_queued_runs_are_skipped_and_the_limit_applies() -> None:
    theses = [thesis(f"{c}_US_EQ", dt.date(2026, 10, 1)) for c in "ABCD"]
    assert due_tickers(theses, TODAY, {"A_US_EQ"}, {"B_US_EQ"}, 10) == ["C_US_EQ", "D_US_EQ"]
    assert due_tickers(theses, TODAY, {"A_US_EQ"}, {"B_US_EQ"}, 1) == ["C_US_EQ"]
    assert due_tickers([], TODAY, set(), set(), 5) == []
