import datetime as dt

from steward.changes.feed import insider_item, ownership_item

W = {"NVDA_US_EQ": 0.02}
Q1, Q2 = dt.date(2026, 3, 31), dt.date(2026, 6, 30)


def test_ownership_change_names_both_quarters_and_the_weight() -> None:
    item = ownership_item("NVDA_US_EQ", (Q1, 5871, 1.0e9), (Q2, 5963, 1.2e9), W)
    assert item is not None
    assert item.kind == "moved" and item.weight == 0.02
    assert "5,871" in (item.before or "") and "5,963" in (item.after or "")


def test_unchanged_ownership_gives_no_item() -> None:
    assert ownership_item("NVDA_US_EQ", (Q1, 10, 5.0), (Q2, 10, 5.0), W) is None


def test_first_quarter_is_new_not_moved() -> None:
    item = ownership_item("NVDA_US_EQ", None, (Q2, 10, 5.0), W)
    assert item is not None and item.kind == "new" and item.before is None


def test_missing_latest_quarter_gives_no_item() -> None:
    assert ownership_item("NVDA_US_EQ", (Q1, 10, 5.0), None, W) is None


def test_insider_net_flow_moves_between_windows() -> None:
    item = insider_item("NVDA_US_EQ", -4_332_118, -1_901_125, W)
    assert item is not None and item.after == "-4,332,118" and item.before == "-1,901,125"
    assert insider_item("NVDA_US_EQ", 0.0, 0.0, W) is None
