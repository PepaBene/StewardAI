"""The overview's 'What happened to your holdings' feed reads values, not raw strings."""

import pytest

from steward.feed.updates import MATERIAL_MOVE, readable, relative_move


def test_relative_move_reads_plain_numbers_only() -> None:
    assert relative_move("20.61", "20.59") == pytest.approx(20.59 / 20.61 - 1)
    assert relative_move("4.979e+11", "4.987e+11") == pytest.approx(4.987 / 4.979 - 1)
    assert relative_move("1,000", "1,100") == pytest.approx(0.1)
    assert relative_move("0", "5") is None
    assert relative_move("5,405 managers, 4 shares", "5,410 managers, 4 shares") is None
    assert relative_move(None, "5") is None


def test_a_tenth_of_a_percent_is_drift_and_large_numbers_read_in_words() -> None:
    assert abs(relative_move("20.61", "20.59") or 0) < MATERIAL_MOVE
    assert readable("4.979e+11") == "497.9 bn"
    assert readable("2.5e12") == "2.5 tn"
    assert readable("20.61") == "20.61"
    assert readable("Gone") == "Gone"
    assert readable(None) is None
