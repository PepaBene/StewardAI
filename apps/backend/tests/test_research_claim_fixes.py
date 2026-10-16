"""Research claim checking on the cases a 2026-10-16 thesis run lost most of its report to."""

from decimal import Decimal

from steward.chat.data_tools import _plain
from steward.research.read_tools import full_data_calls
from steward.verify.claims import missing_numbers


def test_a_dates_day_is_not_a_figure() -> None:
    sentence = (
        "Net revenue for the fiscal year ended 31 July 2026 grew 13.9% to 21,448 million USD;"
        " the update came on 2026-10-14 and again on October 15, 2026."
    )
    evidence = ["Revenue growth 0.139. Total revenue 21448000000 USD."]
    assert missing_numbers(sentence, evidence) == []


def test_a_segment_figure_stored_as_a_decimal_matches_its_millions() -> None:
    stored = _plain(Decimal("1.286400E+10"))
    assert stored == "12864000000"
    sentence = "Global Business Solutions generated 12,864 million USD in FY2026 revenue."
    assert missing_numbers(sentence, [f"Global Business Solutions (segment) {stored} USD"]) == []


def test_plain_keeps_fractions_and_drops_trailing_zeros() -> None:
    assert _plain(Decimal("0.13900")) == "0.139"
    assert _plain(Decimal("1.64E+10")) == "16400000000"
    assert _plain(7) == "7"


def test_risk_history_is_called_with_a_kind() -> None:
    calls = {c.tool: c.args for c in full_data_calls("INTU_US_EQ", "Intuit")}
    assert calls["risk_history"] == {"kind": "ex_ante"}
