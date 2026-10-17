"""A results filing gives a holding with no known report date its report date."""

import asyncio
import datetime as dt
from typing import Any

import pytest

from steward.earnings import calendar
from steward.sources.edgar import EdgarError, Filing

TODAY = dt.date(2026, 10, 17)


class FakePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        assert args == (TODAY - dt.timedelta(days=4),)
        return self.rows


class FakeEdgar:
    def __init__(self, hits: dict[str, Any]) -> None:
        self.hits = hits
        self.asked: list[tuple[str, dt.date, int]] = []

    async def find_earnings_release(self, cik: str, since: dt.date, window_days: int) -> Any:
        self.asked.append((cik, since, window_days))
        hit = self.hits.get(cik)
        if isinstance(hit, Exception):
            raise hit
        return hit


def filing(cik: str, day: str) -> Filing:
    return Filing(cik, "6-K", "0001-26-1", dt.date.fromisoformat(day), None, "pr.htm", "")


def test_a_recent_results_filing_sets_the_report_date(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: list[tuple[str, dt.date, str]] = []

    async def fake_set(pool: Any, ticker: str, found: dt.date, source: str, *_: Any) -> None:
        stored.append((ticker, found, source))

    monkeypatch.setattr(calendar, "set_report_date", fake_set)
    pool = FakePool(
        [
            {"t212_ticker": "ASML_US_EQ", "cik": "937966", "next_report_date": None},
            {"t212_ticker": "AZN_US_EQ", "cik": "901832", "next_report_date": None},
            {"t212_ticker": "TSM_US_EQ", "cik": "1046179", "next_report_date": None},
        ]
    )
    edgar = FakeEdgar(
        {
            "937966": (filing("937966", "2026-10-14"), "https://sec.gov/asml-pr.htm"),
            "1046179": EdgarError("EDGAR returned HTTP 503"),
        }
    )
    found = asyncio.run(calendar.dates_from_filings(pool, edgar, TODAY, 4))  # type: ignore[arg-type]
    assert found == ["ASML_US_EQ (6-K 2026-10-14)"]
    assert stored == [("ASML_US_EQ", dt.date(2026, 10, 14), "https://sec.gov/asml-pr.htm")]
    assert {a[0] for a in edgar.asked} == {"937966", "901832", "1046179"}
    assert all(a[1:] == (dt.date(2026, 10, 13), 4) for a in edgar.asked)
