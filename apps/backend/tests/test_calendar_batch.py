"""The calendar page read in code (shared by the direct and the batch path)."""

import datetime as dt

from steward.config.jobs import EarningsCalendarSettings
from steward.earnings.calendar import CalendarPage, calendar_request, date_from_page

TODAY = dt.date(2026, 10, 6)
URL = "https://ir.example.com/calendar"
TEXT = "Financial calendar. Q3 results: 12 November 2026. AGM: 5 May 2027."


def _page(date: str, snippet: str, event: str = "results") -> CalendarPage:
    return CalendarPage.model_validate(
        {"entries": [{"date": date, "event": event, "snippet": snippet}]}
    )


def test_date_quoted_on_the_page_is_accepted() -> None:
    page = _page("2026-11-12", "Q3 results: 12 November 2026")
    assert date_from_page(page, TEXT, TODAY, URL) == (dt.date(2026, 11, 12), URL)


def test_date_not_in_its_quote_is_rejected() -> None:
    page = _page("2026-11-13", "Q3 results: 12 November 2026")
    date, _ = date_from_page(page, TEXT, TODAY, URL)
    assert date is None


def test_quote_not_on_the_page_is_rejected() -> None:
    page = _page("2026-12-01", "Q3 results: 1 December 2026")
    assert date_from_page(page, TEXT, TODAY, URL)[0] is None


def test_no_answer_is_reported() -> None:
    assert date_from_page(None, TEXT, TODAY, URL) == (None, "no parseable answer")


def test_batch_is_off_by_default_and_request_carries_the_page() -> None:
    settings = EarningsCalendarSettings()
    assert settings.use_batch is True
    request = calendar_request(settings, "system", URL, TEXT, TODAY)
    assert request.schema is CalendarPage
    assert TEXT in request.messages[0].text
