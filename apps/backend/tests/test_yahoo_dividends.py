"""Yahoo dividend events (`events=div` on the chart API): parsing and the currency mapping."""

import datetime as dt
from decimal import Decimal

import pytest

from steward.sources.yahoo import YahooError, iso_currency, parse_dividend_events


def chart(events: object, currency: str | None = "USD") -> dict[str, object]:
    result: dict[str, object] = {"meta": {"currency": currency}}
    if events is not None:
        result["events"] = {"dividends": events}
    return {"chart": {"result": [result], "error": None}}


def test_dividends_are_parsed_sorted_by_ex_date() -> None:
    body = chart(
        {
            "1717113600": {"amount": 0.51, "date": 1717113600},  # 2024-05-31
            "1709251200": {"amount": 0.485, "date": 1709251200},  # 2024-03-01
        }
    )
    currency, dividends = parse_dividend_events(body)
    assert currency == "USD"
    assert [d.ex_date for d in dividends] == [dt.date(2024, 3, 1), dt.date(2024, 5, 31)]
    assert dividends[0].amount == Decimal("0.485")


def test_rows_without_a_date_or_a_positive_amount_are_left_out() -> None:
    body = chart(
        {
            "a": {"amount": 0, "date": 1709251200},
            "b": {"amount": 0.2},
            "c": "not a row",
            "d": {"amount": 0.3, "date": 1717113600},
        }
    )
    _, dividends = parse_dividend_events(body)
    assert [d.amount for d in dividends] == [Decimal("0.3")]


def test_a_chart_without_events_has_no_dividends() -> None:
    assert parse_dividend_events(chart(None)) == ("USD", [])


def test_a_chart_error_is_raised() -> None:
    body = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data"}}}
    with pytest.raises(YahooError):
        parse_dividend_events(body)


@pytest.mark.parametrize(
    ("code", "iso"),
    [("GBp", "GBX"), ("usd", "USD"), ("EUR", "EUR"), (None, None), ("", None), ("US$", None)],
)
def test_currency_codes_map_to_iso(code: str | None, iso: str | None) -> None:
    assert iso_currency(code) == iso
