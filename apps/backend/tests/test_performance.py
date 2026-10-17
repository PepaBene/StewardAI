"""Phase 4 calculators against hand-worked figures (plan M4.1 to M4.9 exit checks).

Expected values are written out by hand, not produced by the code under test."""

import datetime as dt
from decimal import Decimal as D

import pytest

from steward.analytics.returns import DayValue
from steward.performance.attribution import (
    AccountDay,
    Amount,
    PositionDay,
    Trade,
    attribute,
    group_contributions,
    ticker_fx_lookup,
)
from steward.performance.benchmark import blended_index, compare_window, series_return
from steward.performance.ledger import (
    Kind,
    Merger,
    RawTransaction,
    SpinOff,
    Split,
    apply_mergers,
    apply_splits,
    build_ledger,
    external_flows,
    position_breaks,
    reconcile_cash,
    spin_off_units,
    summarize,
)
from steward.performance.lots import (
    build_lots,
    exemption_year,
    holding_periods,
    time_test_date,
)
from steward.performance.sleeves import compare_sleeves, reconciliation_error, sleeve_days
from steward.performance.windows import annualize, window_return, window_start


def day(y: int, m: int, d: int) -> dt.date:
    return dt.date(y, m, d)


def at(y: int, m: int, d: int) -> dt.datetime:
    return dt.datetime(y, m, d, 12, tzinfo=dt.UTC)


def raw(
    id_: str, type_: str, subtype: str | None, amount: str, when: dt.datetime, **kw
) -> RawTransaction:
    return RawTransaction(
        id=id_, type=type_, subtype=subtype, ticker=kw.pop("ticker", None),
        quantity=kw.pop("quantity", None), price=kw.pop("price", None), amount=D(amount),
        currency=kw.pop("currency", "EUR"), occurred_at=when, **kw,
    )  # fmt: skip


def fill(
    id_: str, side: str, qty: str, price: str, net: str, when: dt.datetime, **kw
) -> RawTransaction:
    return raw(id_, "order", side, net, when, ticker="AAA", quantity=D(qty), price=D(price), **kw)


# ---------------------------------------------------------------------------
# M4.1 ledger
# ---------------------------------------------------------------------------


def test_fee_lines_in_another_currency_are_listed_as_excluded_not_dropped_silently():
    entries = build_ledger(
        [
            fill("b1", "BUY", "1", "10", "-10", at(2026, 1, 2), foreign_fee_count=1),
            fill("b2", "BUY", "1", "10", "-10", at(2026, 1, 3)),
        ]
    )
    summary = summarize(entries)
    assert len(summary.fees_excluded) == 1
    assert (
        summary.fees_excluded[0].startswith("b1:")
        and "another currency" in summary.fees_excluded[0]
    )


def test_ledger_separates_external_flow_from_income_and_fees():
    entries = build_ledger(
        [
            raw("cash:1", "cash", "DEPOSIT", "1000", at(2026, 1, 2)),
            raw("cash:2", "cash", "WITHDRAW", "-200", at(2026, 1, 20)),
            raw("cash:3", "cash", "FEE", "-1.5", at(2026, 1, 21)),
            raw("dividend:1", "dividend", "ORDINARY", "12", at(2026, 1, 22), ticker="AAA"),
        ],
        "EUR",
    )
    by_id = {e.id: e for e in entries}
    assert by_id["cash:1"].cash_effect == D("1000")
    assert by_id["cash:2"].cash_effect == D("-200")
    assert by_id["cash:3"].cash_effect == D("-1.5")
    assert by_id["dividend:1"].cash_effect == D("12")
    # Only the deposit and the withdrawal are external flows.
    assert external_flows(entries) == [(day(2026, 1, 2), D("1000")), (day(2026, 1, 20), D("-200"))]


def test_ledger_fee_basis_decides_whether_fees_are_added():
    buy = dict(side="BUY", qty="10", price="50", when=at(2026, 2, 1))
    excluded = build_ledger(
        [
            fill(
                "f1",
                buy["side"],
                "10",
                "50",
                "-500",
                buy["when"],
                fee_basis="excluded",
                fee_amounts=(D("1.5"),),
            )
        ]
    )[0]
    included = build_ledger(
        [
            fill(
                "f2",
                "BUY",
                "10",
                "50",
                "-501.5",
                buy["when"],
                fee_basis="included",
                fee_amounts=(D("1.5"),),
            )
        ]
    )[0]
    assert excluded.cash_effect == D("-501.5")
    assert included.cash_effect == D("-501.5")
    sell = build_ledger(
        [
            fill(
                "f3",
                "SELL",
                "4",
                "60",
                "240",
                at(2026, 3, 1),
                fee_basis="excluded",
                fee_amounts=(D("2"),),
            )
        ]
    )[0]
    assert sell.cash_effect == D("238") and sell.quantity == D("-4")


def test_ledger_flags_unknown_subtype_and_foreign_currency_rather_than_guessing():
    entries = build_ledger(
        [
            raw("cash:9", "cash", "CARD_DEBIT", "-30", at(2026, 1, 3)),
            raw("cash:10", "cash", "DEPOSIT", "100", at(2026, 1, 4), currency="USD"),
        ],
        "EUR",
    )
    unknown, foreign = entries
    assert unknown.kind is Kind.UNSUPPORTED and unknown.cash_effect == 0
    assert foreign.needs_fx and foreign.cash_effect == 0 and foreign.external_flow == 0
    summary = summarize(entries)
    assert summary.unsupported == ["cash:9"] and summary.needs_fx == 1


def test_position_breaks_report_what_fills_cannot_explain():
    entries = build_ledger(
        [
            fill("f1", "BUY", "10", "5", "-50", at(2026, 1, 5)),
            fill("f2", "SELL", "3", "6", "18", at(2026, 1, 9)),
        ]
    )
    assert position_breaks(entries, {"AAA": D("7")}) == []
    (gap,) = position_breaks(entries, {"AAA": D("20")})
    assert gap.ticker == "AAA" and gap.difference == D("13")
    both = {b.ticker: b for b in position_breaks(entries, {"AAA": D("7"), "BBB": D("4")})}
    assert both["BBB"].ledger_quantity == 0 and "no fills" in both["BBB"].reason


def test_freshness_flags_old_prices_and_an_old_benchmark_close():
    from steward.performance.service import freshness

    bench = [(day(2026, 3, 1), D("100")), (day(2026, 3, 6), D("101"))]
    fresh = freshness(day(2026, 3, 9), bench, day(2026, 3, 10))
    assert (fresh.prices_age_days, fresh.benchmark_age_days, fresh.stale) == (1, 4, False)
    old = freshness(day(2026, 3, 2), bench, day(2026, 3, 10))
    assert old.prices_age_days == 8 and old.stale
    assert freshness(day(2026, 3, 9), [], day(2026, 3, 10)).benchmark_as_of is None


def test_benchmark_relative_contribution_is_the_gain_less_the_benchmark_on_opening_money():
    from steward.performance.attribution import HoldingContribution, benchmark_relative

    held = HoldingContribution("AAA", price=D("120"), income=D("5"), fees=D("-1"))  # total 124
    bought_later = HoldingContribution("BBB", price=D("30"))
    broken = HoldingContribution("CCC", attributed=False)
    rel = {
        x.ticker: x
        for x in benchmark_relative([held, bought_later, broken], {"AAA": D("1000")}, D("0.10"))
    }
    assert rel["AAA"].benchmark_equivalent == D("100.00") and rel["AAA"].active == D("24.00")
    assert rel["BBB"].active is None and rel["BBB"].reason == "not held at the start"
    assert rel["CCC"].active is None and rel["CCC"].reason == "not attributed"


def test_an_entered_split_restates_earlier_fills_and_closes_the_position_break():
    entries = build_ledger(
        [
            fill("f1", "BUY", "10", "100", "-1000", at(2025, 1, 6)),
            fill("f2", "BUY", "5", "30", "-150", at(2026, 3, 2)),  # after the split: untouched
            fill("f3", "SELL", "2", "110", "220", at(2025, 6, 2)),
        ]
    )
    # a broker-side 4-for-1 on 2026-01-15: 8 old units became 32, plus the 5 bought after
    restated = apply_splits(entries, [Split("AAA", day(2026, 1, 15), D("4"))])
    by_id = {e.id: e for e in restated}
    assert by_id["f1"].quantity == D("40") and by_id["f1"].price == D("25")
    assert by_id["f1"].gross == D("1000") and by_id["f1"].cash_effect == D("-1000")
    assert by_id["f2"].quantity == D("5") and by_id["f2"].price == D("30")
    assert by_id["f3"].quantity == D("-8") and by_id["f3"].price == D("27.5")
    assert any("restated" in f for f in by_id["f1"].flags)
    assert position_breaks(restated, {"AAA": D("37")}) == []
    assert position_breaks(entries, {"AAA": D("37")}) != []


def test_splits_on_other_tickers_and_no_splits_change_nothing():
    entries = build_ledger([fill("f1", "BUY", "10", "100", "-1000", at(2025, 1, 6))])
    assert apply_splits(entries, []) == entries
    assert apply_splits(entries, [Split("ZZZ", day(2026, 1, 15), D("2"))]) == entries


def test_a_merger_carries_earlier_fills_to_the_new_ticker_with_cost_and_date():
    entries = build_ledger(
        [
            fill("f1", "BUY", "10", "100", "-1000", at(2025, 1, 6)),
            fill(
                "f2",
                "BUY",
                "3",
                "50",
                "-150",
                at(2026, 6, 1),
            ),  # after the merger: untouched
        ]
    )
    merged = apply_mergers(entries, [Merger("AAA", "BBB", day(2026, 3, 2), D("1.5"))])
    by_id = {e.id: e for e in merged}
    assert by_id["f1"].ticker == "BBB" and by_id["f1"].quantity == D("15")
    assert by_id["f1"].price == D("100") / D("1.5") and by_id["f1"].cash_effect == D("-1000")
    assert by_id["f1"].day == day(2025, 1, 6) and any("merger" in f for f in by_id["f1"].flags)
    assert by_id["f2"].ticker == "AAA"
    lots = build_lots(merged).lots
    assert [(x.ticker, x.bought_on, x.remaining_quantity) for x in lots if x.ticker == "BBB"] == [
        ("BBB", day(2025, 1, 6), D("15"))
    ]


def test_a_spin_off_moves_a_share_of_each_open_lot_cost_to_the_child_and_keeps_its_date():
    entries = build_ledger(
        [
            fill("f1", "BUY", "10", "100", "-1000", at(2025, 1, 6)),
            fill("f2", "SELL", "4", "120", "480", at(2025, 9, 1)),
            fill("f3", "BUY", "5", "100", "-500", at(2026, 6, 1)),  # after the spin-off
        ]
    )
    spin = SpinOff("AAA", "CCC", day(2026, 3, 2), D("0.5"), D("0.2"))
    book = build_lots(entries, "fifo", [spin])
    by_ticker = {t: [x for x in book.lots if x.ticker == t] for t in ("AAA", "CCC")}
    child = by_ticker["CCC"]
    assert len(child) == 1 and child[0].bought_on == day(2025, 1, 6)
    assert child[0].remaining_quantity == D("3")  # 6 parent units still held x 0.5
    assert child[0].remaining_quantity * child[0].unit_cost == D("120")  # 20% of 6 x 100
    parent_old = next(x for x in by_ticker["AAA"] if x.bought_on == day(2025, 1, 6))
    assert parent_old.unit_cost == D("80")  # 80% of the cost stays
    parent_new = next(x for x in by_ticker["AAA"] if x.bought_on == day(2026, 6, 1))
    assert parent_new.unit_cost == D("100")
    assert spin_off_units(entries, [spin]) == {"CCC": D("3")}
    assert position_breaks(entries, {"AAA": D("11"), "CCC": D("3")}, received={"CCC": D("3")}) == []


def test_a_spin_off_leaves_gains_on_the_parent_correct_after_the_cost_moved():
    entries = build_ledger(
        [
            fill("f1", "BUY", "10", "100", "-1000", at(2025, 1, 6)),
            fill("f2", "SELL", "10", "100", "1000", at(2026, 6, 1)),
        ]
    )
    book = build_lots(entries, "fifo", [SpinOff("AAA", "CCC", day(2026, 3, 2), D("1"), D("0.25"))])
    sale = book.realized[0]
    assert sale.cost == D("750") and sale.gain == D("250")
    assert [x.ticker for x in book.lots] == ["CCC"] and book.lots[0].unit_cost == D("25")


def test_cash_reconciliation_within_and_outside_tolerance():
    entries = build_ledger(
        [
            raw("cash:1", "cash", "DEPOSIT", "500", at(2026, 1, 6)),
            fill("f1", "BUY", "10", "20", "-200", at(2026, 1, 7)),
            raw("dividend:1", "dividend", None, "5", at(2026, 1, 8), ticker="AAA"),
        ]
    )
    ok = reconcile_cash(entries, start_date=day(2026, 1, 5), end_date=day(2026, 1, 8),
                        opening_cash=D("100"), closing_cash=D("405"))  # fmt: skip
    assert ok.expected_change == D("305") and ok.within_tolerance and ok.difference == 0
    off = reconcile_cash(entries, start_date=day(2026, 1, 5), end_date=day(2026, 1, 8),
                         opening_cash=D("100"), closing_cash=D("395"))  # fmt: skip
    assert off.difference == D("-10") and not off.within_tolerance


def test_cash_in_a_pie_is_cash_not_a_break():
    from steward.performance.service import _cash_checks

    # Real shape from 27-28 Sep 2026: a 400 pie order waiting to fill moves its cash into the
    # pie, which Trading212 reports inside the invested value; the fill lands the next day.
    entries = build_ledger([fill("f1", "BUY", "10", "40", "-400.53", at(2026, 1, 8))])
    snaps = [
        {"snapshot_date": day(2026, 1, 6), "total_value": D("1000"), "invested_value": D("0"),
         "cash_in_pies": D("0")},
        {"snapshot_date": day(2026, 1, 7), "total_value": D("1000"), "invested_value": D("400.59"),
         "cash_in_pies": D("400.59")},
        {"snapshot_date": day(2026, 1, 8), "total_value": D("1000"), "invested_value": D("400.59"),
         "cash_in_pies": D("0.06")},
    ]  # fmt: skip
    checks = _cash_checks(snaps, entries, D("1"))  # type: ignore[arg-type]
    assert [c.difference for c in checks] == [D("0"), D("0")]
    assert all(c.within_tolerance for c in checks)


# ---------------------------------------------------------------------------
# M4.3 windows
# ---------------------------------------------------------------------------


def test_window_starts():
    end = day(2026, 3, 31)
    assert window_start("1M", end) == day(2026, 2, 28)
    assert window_start("YTD", end) == day(2025, 12, 31)
    assert window_start("1Y", end) == day(2025, 3, 31)
    assert window_start("inception", end) is None


def test_twr_is_not_distorted_by_a_deposit_but_xirr_reflects_timing():
    # 1000 grows 10 % to 1100; then 1000 is deposited and the 2100 total falls 5 % to 1995.
    days = [
        DayValue(day(2026, 1, 1), D("1000")),
        DayValue(day(2026, 1, 31), D("1100")),
        DayValue(day(2026, 2, 1), D("2100"), flow=D("1000")),
        DayValue(day(2026, 2, 28), D("1995")),
    ]
    w = window_return(days, "inception")
    # (1100/1000) * ((2100-1000)/1100) * (1995/2100) - 1 = 1.1 * 1.0 * 0.95 - 1 = 0.045
    assert w.twr.value == D("0.045")
    assert w.gain == D("1995") - D("1000") - D("1000")
    assert w.net_flows == D("1000")
    # The owner lost money on the deposit, so the money-weighted return is below the TWR.
    assert w.xirr.value is not None and w.xirr.value < w.twr.value
    assert w.twr_annualized is None  # 58 days: not annualized


def test_short_window_xirr_is_the_period_return_and_survives_a_big_month():
    days = [DayValue(day(2026, 1, 1), D("100")), DayValue(day(2026, 1, 31), D("103"))]
    w = window_return(days, "inception")
    assert w.xirr_annualized is False
    assert w.xirr.value == pytest.approx(D("0.03"), abs=D("1e-6"))  # not ~ +43 %
    big = window_return([DayValue(day(2026, 1, 1), D("100")), DayValue(day(2026, 1, 31), D("150"))],
                        "inception")  # fmt: skip
    assert big.xirr.value == pytest.approx(D("0.5"), abs=D("1e-6"))
    year = window_return([DayValue(day(2025, 1, 1), D("100")), DayValue(day(2026, 1, 1), D("110"))],
                         "inception")  # fmt: skip
    assert year.xirr_annualized is True
    assert year.xirr.value == pytest.approx(D("0.1"), abs=D("1e-6"))


def test_window_without_enough_history_is_unavailable_not_shortened():
    days = [DayValue(day(2026, 1, 1), D("100")), DayValue(day(2026, 6, 1), D("110"))]
    w = window_return(days, "1Y")
    assert w.twr.value is None and "history starts 2026-01-01" in (w.reason or "")


def test_annualize_only_from_one_year():
    assert annualize(D("0.1"), 364) is None
    assert annualize(D("0.21"), 730) == pytest.approx(D("0.1"), abs=D("1e-9"))


def test_incomplete_ledger_blocks_a_precise_return():
    days = [DayValue(day(2026, 1, 1), D("100")), DayValue(day(2026, 1, 20), D("104"))]
    w = window_return(days, "inception", missing_weight=D("0.08"))
    assert w.twr.value is None and w.xirr.value is None
    assert "8.00%" in w.twr.warnings[0]


# ---------------------------------------------------------------------------
# M4.5 benchmark
# ---------------------------------------------------------------------------


def test_blended_benchmark_rebalancing_changes_the_path():
    a = [(day(2026, 1, 1), D("100")), (day(2026, 2, 2), D("200")), (day(2026, 3, 2), D("200"))]
    b = [(day(2026, 1, 1), D("100")), (day(2026, 2, 2), D("100")), (day(2026, 3, 2), D("200"))]
    weights = {"A": D("0.5"), "B": D("0.5")}
    drift = blended_index({"A": a, "B": b}, weights, "none")
    # No rebalance: 50 in A doubles to 100, 50 in B flat -> 150; then B doubles -> 200.
    assert [v for _, v in drift] == [D(100), D(150), D(200)]
    monthly = blended_index({"A": a, "B": b}, weights, "monthly")
    # At the 2 Feb month change the 150 resets to 75/75; B then doubles -> 75 + 150 = 225.
    assert [v for _, v in monthly] == [D(100), D(150), D(225)]


def test_blend_rejects_bad_weights():
    s = [(day(2026, 1, 1), D(1)), (day(2026, 1, 2), D(1))]
    with pytest.raises(ValueError):
        blended_index({"A": s}, {"A": D("0.9")})


def test_settings_blend_that_validates_also_builds():
    from steward.config.performance import BenchmarkSpec

    spec = BenchmarkSpec.model_validate({
        "components": [
            {"symbol": s, "weight": "0.3333333", "currency": "USD", "basis": "total"}
            for s in "ABC"
        ],
    })  # fmt: skip
    assert sum(c.weight for c in spec.components) == pytest.approx(D(1), abs=D("1e-20"))
    s = [(day(2026, 1, 1), D(1)), (day(2026, 1, 2), D(2))]
    # The raw (un-normalised) weights are within the shared tolerance too.
    raw = {x: D("0.3333333") for x in "ABC"}
    out = blended_index({x: s for x in "ABC"}, raw)
    assert out[-1][1] == pytest.approx(D(200), abs=D("1e-9"))


def _twr_window(start: dt.date, end: dt.date, a: str, b: str):
    return window_return(
        [DayValue(start, D(100)), DayValue(end, D(100) * (1 + D(a)))], "inception"
    ), D(b)


def test_active_return_needs_matching_currency_and_flags_price_basis():
    window, _ = _twr_window(day(2026, 1, 1), day(2026, 1, 31), "0.05", "0")
    bench = [(day(2026, 1, 1), D(200)), (day(2026, 1, 31), D(210))]  # +5 %...
    bench = [(day(2026, 1, 1), D(200)), (day(2026, 1, 31), D(204))]  # +2 %
    same = compare_window(window, "ACWI", bench, benchmark_currency="EUR",
                          portfolio_currency="EUR", basis="total")  # fmt: skip
    assert same.benchmark_return == D("0.02")
    assert same.active_return.value == D("3.0000")  # 5 % - 2 % = 3 percentage points
    assert same.active_return.warnings == []
    price = compare_window(window, "ACWI", bench, benchmark_currency="EUR",
                           portfolio_currency="EUR", basis="price")  # fmt: skip
    assert (
        price.active_return.value == D("3.0000") and "dividends" in price.active_return.warnings[0]
    )
    other = compare_window(window, "SPX", bench, benchmark_currency="USD",
                           portfolio_currency="EUR", basis="total")  # fmt: skip
    assert other.active_return.value is None and "currency" in other.active_return.warnings[0]
    assert other.benchmark_return == D("0.02")  # both returns stay visible


def test_series_return_needs_closes_near_both_endpoints():
    s = [(day(2026, 1, 10), D(100)), (day(2026, 1, 31), D(110))]
    assert series_return(s, day(2026, 1, 1), day(2026, 1, 31)) is None  # start 9 days before
    assert series_return(s, day(2026, 1, 12), day(2026, 1, 31)) == D("0.1")


# ---------------------------------------------------------------------------
# M4.4 and M4.8 lots and holding period
# ---------------------------------------------------------------------------


def _two_buys():
    return build_ledger(
        [
            fill(
                "b1",
                "BUY",
                "10",
                "10",
                "-100",
                at(2023, 6, 1),
                fee_basis="excluded",
                fee_amounts=(D("1"),),
            ),
            fill("b2", "BUY", "10", "20", "-200", at(2024, 6, 1)),
        ]
    )


def test_fifo_partial_sale_consumes_oldest_lot_first():
    sale = build_ledger([fill("s1", "SELL", "15", "30", "450", at(2025, 1, 10))])
    book = build_lots(sorted(_two_buys() + sale, key=lambda e: e.occurred_at), "fifo")
    (realized,) = book.realized
    # Lot 1: 10 units at (100 + 1)/10 = 10.1; lot 2: 5 units at 20. Cost 101 + 100 = 201.
    assert realized.cost == D("201") and realized.gain == D("450") - D("201")
    assert realized.lot_ids == ("b1", "b2")
    (left,) = book.lots
    assert left.lot_id == "b2" and left.remaining_quantity == D("5")
    # A partial sale does not move the remaining lot's 3-year date.
    assert time_test_date(left.bought_on) == day(2027, 6, 2)


def test_average_cost_differs_from_fifo():
    sale = build_ledger([fill("s1", "SELL", "10", "30", "300", at(2025, 1, 10))])
    entries = sorted(_two_buys() + sale, key=lambda e: e.occurred_at)
    fifo = build_lots(entries, "fifo").realized[0]
    avg = build_lots(entries, "average").realized[0]
    assert fifo.cost == D("101")
    assert avg.cost == D("150.5")  # (101 + 200) / 20 * 10


def test_sale_larger_than_the_ledger_is_unmatched_not_given_zero_cost():
    entries = build_ledger(
        [
            fill("b1", "BUY", "5", "10", "-50", at(2025, 1, 1)),
            fill("s1", "SELL", "8", "12", "96", at(2025, 2, 1)),
        ]
    )
    book = build_lots(entries)
    assert book.unmatched[0].quantity == D("3")
    (realized,) = book.realized
    assert realized.quantity == D("5") and realized.proceeds == D("60")  # 5/8 of 96
    assert realized.cost == D("50") and realized.gain == D("10")


def test_time_test_date_and_months_remaining():
    assert time_test_date(day(2023, 6, 1)) == day(2026, 6, 2)
    assert time_test_date(day(2024, 2, 29)) == day(2027, 3, 1)
    book = build_lots(_two_buys())
    periods = holding_periods(
        book.lots, day(2025, 12, 1), sell_targets={"AAA": D("25")}, prices={"AAA": D("26")}
    )
    first = periods[0]
    assert first.lot_id == "b1" and first.passes_on == day(2026, 6, 2)
    assert first.months_remaining == D("6.0") and not first.passed
    assert first.target_reached is True and first.forfeits_exemption_warning
    # The second lot is 18 months away: target reached, but no warning yet.
    assert periods[1].forfeits_exemption_warning is False


def test_warning_needs_target_reached_and_no_target_means_no_warning():
    book = build_lots(_two_buys())
    below = holding_periods(book.lots, day(2025, 12, 1), sell_targets={"AAA": D("40")},
                            prices={"AAA": D("26")})  # fmt: skip
    assert not any(h.forfeits_exemption_warning for h in below)
    none = holding_periods(book.lots, day(2025, 12, 1))
    assert all(h.target_reached is None and not h.forfeits_exemption_warning for h in none)
    passed = holding_periods(book.lots, day(2026, 7, 1), sell_targets={"AAA": D("1")},
                             prices={"AAA": D("26")})  # fmt: skip
    assert passed[0].passed and passed[0].months_remaining == 0
    assert not passed[0].forfeits_exemption_warning


def test_exemption_counts_only_the_part_sold_before_the_time_test():
    sale = build_ledger([fill("s1", "SELL", "15", "30", "450", at(2026, 5, 1))])
    book = build_lots(sorted(_two_buys() + sale, key=lambda e: e.occurred_at))
    (realized,) = book.realized
    # Lot 1 (10 units) is still inside its 3 years on 2026-05-01; so is lot 2 (5 units): all 15.
    assert realized.gross_within_time_test == D("450")
    late = build_ledger([fill("s2", "SELL", "10", "30", "300", at(2026, 7, 1))])
    book2 = build_lots(sorted(_two_buys() + late, key=lambda e: e.occurred_at))
    assert book2.realized[0].gross_within_time_test == D("0")  # lot 1 had passed
    year = exemption_year(2026, [(day(2026, 5, 1), D("450")), (day(2025, 5, 1), D("99")),
                                  (day(2026, 6, 1), None)], D("1000"))  # fmt: skip
    assert year.gross_proceeds == D("450") and year.headroom == D("550")
    assert year.sales_without_rate == 1 and not year.exceeded


def test_exemption_uses_the_owners_time_test_years():
    # Lot 1 was bought 2023-06-01: inside a 5-year test on 2026-07-01, past a 3-year one.
    late = build_ledger([fill("s2", "SELL", "10", "30", "300", at(2026, 7, 1))])
    entries = sorted(_two_buys() + late, key=lambda e: e.occurred_at)
    assert build_lots(entries).realized[0].gross_within_time_test == D("0")
    five = build_lots(entries, time_test_years=5)
    assert five.realized[0].gross_within_time_test == D("300")


# ---------------------------------------------------------------------------
# M4.6 attribution
# ---------------------------------------------------------------------------


def test_attribution_reconciles_price_fx_income_and_fees():
    # AAA in USD, base EUR. Day 0: 10 units at 10 USD, rate 0.90. Day 1: price 11, rate 0.80,
    # one more unit bought at 11 USD, a 2 EUR dividend and a 0.5 EUR fee.
    #   price = (11*11 - 10*10 - 1*11) * 0.80 = (121 - 100 - 11) * 0.8 = 8.0
    #   fx    = 10*10 * (0.80 - 0.90) = -10.0
    #   start 90 + cash 100 = 190; end 11 units * 11 * 0.8 = 96.8, cash 100 - 8.8 + 2 - 0.5 = 92.7
    days = [AccountDay(day(2026, 1, 1), D("190")), AccountDay(day(2026, 1, 2), D("189.5"))]
    positions = [
        PositionDay(day(2026, 1, 1), "AAA", D(10), D(10), D("0.9"), D("90")),
        PositionDay(day(2026, 1, 2), "AAA", D(11), D(11), D("0.8"), D("96.8")),
    ]
    report = attribute(
        days, positions, [Trade(day(2026, 1, 2), "AAA", D(1), D(11))],
        [Amount(day(2026, 1, 2), D(2), "AAA")], [Amount(day(2026, 1, 2), D("0.5"), "AAA")],
    )  # fmt: skip
    assert report is not None
    comps = report.bridge.components
    assert comps == {"price": D("8.00"), "income": D("2.00"), "fx": D("-10.00"), "fees": D("-0.50")}
    # gain = 189.5 - 190 = -0.5; components sum = -0.5; the residual is zero here.
    assert report.bridge.value == D("-0.50") and report.bridge.residual == D("0.00")
    (holding,) = report.holdings
    assert holding.total == D("-0.5")
    assert report.net_trading == D("8.8")


def test_attribution_residual_is_disclosed_and_unexplained_quantity_is_partial():
    days = [AccountDay(day(2026, 1, 1), D("100")), AccountDay(day(2026, 1, 2), D("130"))]
    positions = [
        PositionDay(day(2026, 1, 1), "AAA", D(10), D(10), D(1), D("100")),
        # Quantity doubled with no stored fill: a split or a missing fill.
        PositionDay(day(2026, 1, 2), "AAA", D(20), D("6.5"), D(1), D("130")),
    ]
    report = attribute(days, positions, [], [], [])
    assert report is not None
    assert report.unattributed_tickers == ["AAA"]
    assert report.bridge.components["price"] == 0
    assert report.bridge.residual == D("30.00")  # nothing was assigned, so all of it is residual
    assert report.bridge.status.value == "partial"
    assert report.bridge.coverage is not None and report.bridge.coverage.fraction == 0


def test_a_pence_quoted_holding_attributes_in_pence_with_a_per_penny_rate():
    # GBX: the stored rate is base units per penny (GBP/100), so pence prices and a per-penny
    # rate multiply to the base value without any extra scaling.
    rate = D("0.0115")  # EUR per penny, i.e. 1.15 EUR per pound
    days = [AccountDay(day(2026, 1, 1), D("115.00")), AccountDay(day(2026, 1, 2), D("126.50"))]
    positions = [
        PositionDay(day(2026, 1, 1), "VOD", D(10), D(1000), rate, D("115.00")),
        PositionDay(day(2026, 1, 2), "VOD", D(10), D(1100), rate, D("126.50")),
    ]
    report = attribute(days, positions, [], [], [])
    assert report is not None
    assert report.bridge.components["price"] == D("11.5000")  # 10 * 100 pence * 0.0115
    assert report.bridge.components["fx"] == 0 and report.bridge.residual == 0


def test_a_position_bought_and_sold_between_two_snapshots_still_counts_its_gain():
    # Day 1 and day 3 hold nothing in AAA; on day 2 it was bought at 10 and sold at 12.
    days = [AccountDay(day(2026, 1, 1), D("100")), AccountDay(day(2026, 1, 3), D("120"))]
    trades = [
        Trade(day(2026, 1, 3), "AAA", D(10), D(10)),
        Trade(day(2026, 1, 3), "AAA", D(-10), D(12)),
    ]
    report = attribute(days, [], trades, [], [], lambda ticker, when: D(1))
    assert report is not None
    assert report.unattributed_tickers == []
    (holding,) = report.holdings
    assert holding.price == D(20)  # (0 - 0 - (10*10 - 10*12)) * 1
    assert report.net_trading == D(-20)
    # with no rate for the ticker anywhere the fills cannot be valued: left out, not valued at zero
    unpriced = attribute(days, [], trades, [], [])
    assert unpriced is not None and unpriced.unattributed_tickers == ["AAA"]
    assert unpriced.holdings[0].attributed is False
    assert unpriced.bridge.status.value == "partial"  # nothing held at the end, still partial


def test_ticker_unattributable_in_one_interval_but_sold_before_the_end_is_partial():
    days = [
        AccountDay(day(2026, 1, 1), D("100")),
        AccountDay(day(2026, 1, 2), D("130")),
        AccountDay(day(2026, 1, 3), D("130")),
    ]
    positions = [
        PositionDay(day(2026, 1, 1), "AAA", D(10), D(10), D(1), D("100")),
        PositionDay(day(2026, 1, 2), "AAA", D(20), D("6.5"), D(1), D("130")),  # unexplained
    ]
    report = attribute(days, positions, [Trade(day(2026, 1, 3), "AAA", D(-20), D("6.5"))], [], [])
    assert report is not None
    assert report.unattributed_tickers == ["AAA"]
    assert report.bridge.status.value == "partial"


def test_ticker_rate_lookup_takes_the_nearest_snapshot_day_and_prefers_the_earlier():
    rows = [
        PositionDay(day(2026, 1, 1), "AAA", D(1), D(1), D("0.9"), D("0.9")),
        PositionDay(day(2026, 1, 5), "AAA", D(1), D(1), D("0.8"), D("0.8")),
    ]
    look = ticker_fx_lookup(rows)
    assert look("AAA", day(2026, 1, 2)) == D("0.9")
    assert look("AAA", day(2026, 1, 4)) == D("0.8")
    assert look("AAA", day(2026, 1, 3)) == D("0.9")  # equidistant: the earlier day
    assert look("BBB", day(2026, 1, 3)) is None


def test_a_fill_in_another_currency_than_the_account_is_left_out_not_guessed():
    usd_fill = fill("1", "BUY", "1", "10", "-10", at(2026, 1, 2), currency="USD")
    (entry,) = build_ledger([usd_fill], "EUR")
    assert entry.needs_fx  # reports skip it rather than convert at an unknown rate


def test_attribution_excludes_external_flows_from_the_gain():
    days = [AccountDay(day(2026, 1, 1), D("100")), AccountDay(day(2026, 1, 2), D("160"), D("50"))]
    positions = [
        PositionDay(day(2026, 1, 1), "AAA", D(10), D(10), D(1), D("100")),
        PositionDay(day(2026, 1, 2), "AAA", D(10), D(11), D(1), D("110")),
    ]
    report = attribute(days, positions, [], [], [])
    assert report is not None and report.bridge.value == D("10.00")  # 160 - 100 - 50
    assert report.bridge.components["price"] == D("10.00") and report.bridge.residual == 0


def test_group_contributions_keep_unknown_as_its_own_line():
    days = [AccountDay(day(2026, 1, 1), D("200")), AccountDay(day(2026, 1, 2), D("215"))]
    positions = [
        PositionDay(day(2026, 1, 1), "AAA", D(10), D(10), D(1), D("100")),
        PositionDay(day(2026, 1, 1), "BBB", D(10), D(10), D(1), D("100")),
        PositionDay(day(2026, 1, 2), "AAA", D(10), D(11), D(1), D("110")),
        PositionDay(day(2026, 1, 2), "BBB", D(10), D("10.5"), D(1), D("105")),
    ]
    report = attribute(days, positions, [], [], [])
    assert report is not None
    grouped = group_contributions(report.holdings, {"AAA": "Tech"})
    assert grouped == {"Tech": D(10), "Unknown": D(5)}
    assert sum(grouped.values()) == sum(h.total for h in report.holdings)


# ---------------------------------------------------------------------------
# M4.9 sleeves
# ---------------------------------------------------------------------------


def _daily_sleeve_inputs():
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=30 * i) for i in range(30)]
    positions, core_price, stock_price = [], D(100), D(100)
    for d in dates:
        core_price *= D("1.005")
        stock_price *= D("1.002")
        positions.append(PositionDay(d, "CORE", D(10), core_price, D(1), D(10) * core_price))
        positions.append(PositionDay(d, "STK", D(10), stock_price, D(1), D(10) * stock_price))
    return dates, positions


def test_sleeves_reconcile_to_the_invested_portfolio_and_flag_a_lagging_stock_sleeve():
    dates, positions = _daily_sleeve_inputs()
    core = sleeve_days({"CORE"}, dates, positions, [], [], [], {})
    stocks = sleeve_days({"STK"}, dates, positions, [], [], [], {})
    assert reconciliation_error(core, stocks) < D("1e-20")
    cmp = compare_sleeves(core, stocks)
    assert not cmp.too_early and cmp.annual_gap_points is not None and cmp.annual_gap_points > 2
    assert cmp.review_item and "trail" in cmp.message


def test_history_shorter_than_the_lookback_is_reported_and_raises_no_shift_item():
    dates, positions = _daily_sleeve_inputs()  # 870 days of history
    core = sleeve_days({"CORE"}, dates, positions, [], [], [], {})
    stocks = sleeve_days({"STK"}, dates, positions, [], [], [], {})
    cmp = compare_sleeves(core, stocks, lookback_years=3)
    assert cmp.history_days == 870 and cmp.too_early and not cmp.review_item
    assert "870 days" in cmp.message and "3 years" in cmp.message


def test_sleeve_buys_are_flows_not_gains_and_short_history_is_too_early():
    dates = [dt.date(2026, 1, 1) + dt.timedelta(days=30 * i) for i in range(4)]
    positions = [
        PositionDay(dates[0], "STK", D(10), D(10), D(1), D(100)),
        PositionDay(
            dates[1], "STK", D(20), D(10), D(1), D(200)
        ),  # bought 10 more at the same price
        PositionDay(dates[2], "STK", D(20), D(10), D(1), D(200)),
        PositionDay(dates[3], "STK", D(20), D(10), D(1), D(200)),
        *[PositionDay(d, "CORE", D(10), D(10), D(1), D(100)) for d in dates],
    ]
    trades = [Trade(dates[1], "STK", D(10), D(10))]
    rates = {(dates[1], "STK"): D(1)}
    stocks = sleeve_days({"STK"}, dates, positions, trades, [], [], rates)
    core = sleeve_days({"CORE"}, dates, positions, [], [], [], {})
    cmp = compare_sleeves(core, stocks)
    assert cmp.stocks.total_return == 0  # the purchase added value but was no gain
    assert cmp.too_early and not cmp.review_item and "Too early to judge" in cmp.message


def test_sale_that_closes_a_position_still_counts_as_a_flow_out_of_the_sleeve():
    dates = [dt.date(2026, 1, 1), dt.date(2026, 1, 31)]
    positions = [PositionDay(dates[0], "STK", D(10), D(10), D(1), D(100))]  # none on day 2
    trades = [Trade(dates[1], "STK", D(-10), D(12))]  # sold all 10 at 12
    # the closed position's own last rate stands in for the missing row of the sale day
    stocks = sleeve_days({"STK"}, dates, positions, trades, [], [], {}, ticker_fx_lookup(positions))
    assert stocks[1].value == 0 and stocks[1].flow == D(-120)
    # (0 - (-120)) / 100 - 1: the sale proceeds are the sleeve's gain of 20%, not a total loss.
    assert (stocks[1].value - stocks[1].flow) / stocks[0].value - 1 == D("0.2")
