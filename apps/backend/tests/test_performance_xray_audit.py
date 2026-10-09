"""Deep-audit fixes P1 to P12 (performance and X-Ray): hand-worked expectations."""

import datetime as dt
from decimal import Decimal as D

from steward.analytics.returns import DayValue, attribution_bridge
from steward.performance.ledger import (
    Kind,
    RawTransaction,
    bucket_snapshot,
    build_ledger,
    in_interval,
)
from steward.performance.lots import build_lots
from steward.performance.sleeves import compare_sleeves, sleeve_return
from steward.performance.windows import window_return


def raw(id_: str, type_: str, subtype: str | None, amount: str, when: dt.datetime, **kw):
    return RawTransaction(
        id=id_, type=type_, subtype=subtype, ticker=kw.pop("ticker", None),
        quantity=kw.pop("quantity", None), price=kw.pop("price", None), amount=D(amount),
        currency="EUR", occurred_at=when, **kw,
    )  # fmt: skip


def at(y: int, m: int, d: int, hour: int = 12) -> dt.datetime:
    return dt.datetime(y, m, d, hour, tzinfo=dt.UTC)


# P1 -----------------------------------------------------------------------


def test_sleeve_annualises_over_the_days_it_held_something() -> None:
    d0 = dt.date(2025, 1, 1)
    # Empty for 300 days, then 100 invested that grows 10 % in the last 100 days.
    stocks = [
        DayValue(d0, D(0)),
        DayValue(d0 + dt.timedelta(days=300), D(100), D(100)),
        DayValue(d0 + dt.timedelta(days=400), D(110)),
    ]
    got = sleeve_return("stocks", stocks)
    assert got.days == 400
    assert got.invested_days == 100
    assert got.total_return == D("0.1")
    assert got.annualized is None  # 100 invested days are not a year


def test_part_time_sleeve_is_too_early_even_inside_a_long_history() -> None:
    d0 = dt.date(2025, 1, 1)
    core = [DayValue(d0, D(1000)), DayValue(d0 + dt.timedelta(days=400), D(1100))]
    stocks = [
        DayValue(d0, D(0)),
        DayValue(d0 + dt.timedelta(days=300), D(100), D(100)),
        DayValue(d0 + dt.timedelta(days=400), D(110)),
    ]
    cmp = compare_sleeves(core, stocks)
    assert cmp.history_days == 100
    assert cmp.too_early and not cmp.review_item


# P2 -----------------------------------------------------------------------


def test_negative_dividend_and_deposit_rows_reduce_cash_instead_of_adding() -> None:
    entries = build_ledger(
        [
            raw("d1", "dividend", None, "-5", at(2026, 1, 2), ticker="AAA"),
            raw("d2", "dividend", None, "8", at(2026, 1, 3), ticker="AAA"),
            raw("c1", "cash", "DEPOSIT", "-20", at(2026, 1, 4)),
            raw("w1", "cash", "WITHDRAW", "-30", at(2026, 1, 5)),
            raw("w2", "cash", "WITHDRAW", "30", at(2026, 1, 6)),
        ]
    )
    by = {e.id: e for e in entries}
    assert by["d1"].cash_effect == D(-5) and by["d1"].kind is Kind.DIVIDEND
    assert any("negative dividend" in f for f in by["d1"].flags)
    assert by["d2"].cash_effect == D(8)
    assert by["c1"].cash_effect == D(-20)
    assert by["w1"].cash_effect == D(-30) and by["w2"].cash_effect == D(-30)
    # Trading212 sends withdrawals negative, so a positive one is flagged as a possible reversal.
    assert not by["w1"].flags and any("positive withdrawal" in f for f in by["w2"].flags)


# P3 -----------------------------------------------------------------------


def test_deposit_after_the_evening_snapshot_belongs_to_the_next_interval() -> None:
    (entry,) = build_ledger([raw("c1", "cash", "DEPOSIT", "100", at(2026, 3, 10, 22))])
    # Snapshots at 20:30 UTC on the 10th and 11th: the 22:00 UTC deposit is after the first.
    first, second = at(2026, 3, 10, 20), at(2026, 3, 11, 20)
    d10, d11, d9 = dt.date(2026, 3, 10), dt.date(2026, 3, 11), dt.date(2026, 3, 9)
    ninth = at(2026, 3, 9, 20)
    assert not in_interval(entry, d9, d10, ninth, first)
    assert in_interval(entry, d10, d11, first, second)
    # Without capture times the calendar day decides, as before.
    assert in_interval(entry, d9, d10)


def test_trade_and_income_use_the_same_interval_as_flows() -> None:
    (entry,) = build_ledger([raw("c1", "cash", "DEPOSIT", "100", at(2026, 3, 10, 22))])
    snaps = [
        (dt.date(2026, 3, 9), at(2026, 3, 9, 20)),
        (dt.date(2026, 3, 10), at(2026, 3, 10, 20)),
        (dt.date(2026, 3, 11), at(2026, 3, 11, 20)),
    ]
    # 22:00 on the 10th is after the 10th's snapshot, so it belongs to the interval ending the 11th.
    assert bucket_snapshot(entry, snaps) == dt.date(2026, 3, 11)
    # Without capture times the calendar day decides.
    plain = [(d, None) for d, _ in snaps]
    assert bucket_snapshot(entry, plain) == dt.date(2026, 3, 10)
    assert bucket_snapshot(entry, snaps[:2]) is None  # after the last snapshot: not yet valued


# P7 -----------------------------------------------------------------------


def test_bridge_components_and_residual_add_up_to_the_rounded_gain() -> None:
    parts = {"a": D("1.004"), "b": D("1.004"), "c": D("1.004")}
    bridge = attribution_bridge(D(100), D("103.016"), D(0), parts)
    assert sum(bridge.components.values()) + bridge.residual == bridge.value


# P8 -----------------------------------------------------------------------


def test_window_reports_how_far_before_its_start_it_opened() -> None:
    days = [
        DayValue(dt.date(2025, 1, 5), D(1000)),
        DayValue(dt.date(2025, 7, 1), D(1050)),
        DayValue(dt.date(2026, 1, 10), D(1100)),
    ]
    got = window_return(days, "1Y")
    assert got.start == dt.date(2025, 1, 5)
    assert got.opening_gap_days == 5
    assert window_return(days, "inception").opening_gap_days == 0


# P10 ----------------------------------------------------------------------


def test_average_cost_open_lots_show_the_pooled_unit_cost() -> None:
    entries = build_ledger(
        [
            raw("b1", "order", "BUY", "-100", at(2026, 1, 2), ticker="AAA",
                quantity=D(10), price=D(10)),
            raw("b2", "order", "BUY", "-200", at(2026, 1, 3), ticker="AAA",
                quantity=D(10), price=D(20)),
        ]
    )  # fmt: skip
    assert [lot.unit_cost for lot in build_lots(entries, "fifo").lots] == [D(10), D(20)]
    assert [lot.unit_cost for lot in build_lots(entries, "average").lots] == [D(15), D(15)]
