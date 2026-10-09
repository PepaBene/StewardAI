"""P4.6: corrections, split-adjusted shares, zero-price grants, derivative conversion, option
exercise plus same-day sale, indirect trust holdings, plan indicators and reviewer overrides."""

import datetime as dt
from decimal import Decimal as D

import pytest

from steward.disclosure.insiders import (
    InsiderTx,
    Override,
    adjust_for_splits,
    alerts,
    build_view,
    by_class,
    caveats,
    clusters,
    effective_class,
    find_bundles,
    net_by_role,
    plan_status,
    position_change,
    role_of,
    visible,
)

UTC = dt.UTC
AS_OF = dt.datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC)


def day(m: int, d: int, y: int = 2026) -> dt.date:
    return dt.date(y, m, d)


def tx(
    n: int,
    cls: str,
    shares: str | None = "100",
    price: str | None = "10",
    *,
    insider: str = "0000000001",
    role: str = "officer",
    when: dt.date | None = dt.date(2026, 6, 1),
    plan: bool | None = False,
    derivative: bool = False,
    di: str = "D",
    confirmed: bool = True,
    superseded_at: dt.datetime | None = None,
    after: str | None = None,
    accession: str | None = None,
) -> InsiderTx:
    filed = (when or day(6, 1)) + dt.timedelta(days=2)
    return InsiderTx(
        accession or f"0000000001-26-{n:06d}",
        0,
        insider,
        f"Person {insider[-1]}",
        role,  # type: ignore[arg-type]
        when,
        filed,
        dt.datetime.combine(filed, dt.time(23, 59, 59), tzinfo=UTC),
        cls,
        derivative,
        None if shares is None else D(shares),
        None if price is None else D(price),
        None if after is None else D(after),
        di,
        plan,
        None,
        superseded_at,
        confirmed,
    )


def row(out, window: int, role: str):
    return next(r for r in out if r.window_days == window and r.role == role)


def test_only_open_market_trades_are_discretionary() -> None:
    rows = [
        tx(1, "open_market_buy", "1000", "10"),
        tx(2, "open_market_sell", "400", "12"),
        tx(3, "grant_award", "5000", "0"),
        tx(4, "gift", "200", None),
        tx(5, "tax_withholding", "300", "11"),
        tx(6, "conversion", "700", "0"),
        tx(7, "other", "50", "9"),
        tx(8, "holding_snapshot", "9000", None),
        tx(9, None, "10", "9"),  # the classifier gave nothing
    ]
    allr = row(net_by_role(rows, AS_OF), 30, "all")
    assert allr.buy_shares == D(1000) and allr.sell_shares == D(400)
    assert allr.net_shares == D(600) and allr.net_value == D(10_000 - 4_800)
    assert allr.trades == 2


def test_zero_price_missing_shares_and_derivative_lines_are_not_counted() -> None:
    rows = [
        tx(1, "open_market_buy", "100", "0"),  # a zero-price "purchase" is not a market trade
        tx(2, "open_market_buy", None, "10"),
        tx(3, "open_market_buy", "100", "10", derivative=True),
        tx(4, "open_market_buy", "0", "10"),
    ]
    assert row(net_by_role(rows, AS_OF), 365, "all").trades == 0


def test_an_exercise_and_same_day_sale_is_one_bundle_not_a_choice_to_sell() -> None:
    d = day(6, 10)
    rows = [
        tx(1, "option_exercise", "1000", "2", when=d, derivative=True),
        tx(2, "open_market_sell", "1000", "30", when=d),
        tx(3, "open_market_buy", "200", "29", when=day(6, 11)),
    ]
    (bundle,) = find_bundles(rows, [], AS_OF)
    assert bundle.exercised == D(1000) and bundle.sold == D(1000) and bundle.proceeds == D(30_000)
    assert bundle.retained == D(0)
    allr = row(net_by_role(rows, AS_OF), 30, "all")
    assert allr.sell_shares == D(0) and allr.bundled_sales_excluded == 1
    assert allr.buy_shares == D(200)


def test_a_sale_larger_than_the_exercise_is_not_bundled() -> None:
    d = day(6, 10)
    rows = [
        tx(1, "option_exercise", "500", "2", when=d, derivative=True),
        tx(2, "open_market_sell", "800", "30", when=d),
    ]
    assert find_bundles(rows, [], AS_OF) == []
    assert row(net_by_role(rows, AS_OF), 30, "all").sell_shares == D(800)


def test_plan_trades_are_counted_apart_and_unknown_plans_are_visible() -> None:
    rows = [
        tx(1, "open_market_sell", "100", "10", plan=True),
        tx(2, "open_market_sell", "50", "10", plan=None),
        tx(3, "open_market_sell", "25", "10", plan=False),
    ]
    allr = row(net_by_role(rows, AS_OF), 30, "all")
    assert allr.sell_shares == D(75)  # the plan trade is out of the net
    assert allr.plan_trades_excluded == 1 and allr.plan_unknown_trades == 1
    assert [plan_status(r) for r in rows] == ["plan", "unknown", "not_plan"]


def test_windows_count_back_by_transaction_date_and_skip_undated_rows() -> None:
    rows = [
        tx(1, "open_market_buy", when=day(6, 20)),
        tx(2, "open_market_buy", when=day(4, 15)),
        tx(3, "open_market_buy", when=day(1, 5)),
        tx(4, "open_market_buy", when=dt.date(2025, 3, 1)),
        tx(5, "open_market_buy", when=None),
    ]
    out = net_by_role(rows, AS_OF)
    assert [row(out, w, "all").trades for w in (30, 90, 365)] == [1, 2, 3]
    assert build_view(rows, AS_OF).undated == 1


def test_a_row_is_invisible_before_it_was_filed_and_after_it_was_restated() -> None:
    late = dt.datetime(2026, 6, 20, 23, 59, 59, tzinfo=UTC)
    original = tx(1, "open_market_buy", "100", "10", superseded_at=late)
    amended = tx(2, "open_market_buy", "90", "10")
    early = dt.datetime(2026, 6, 10, tzinfo=UTC)
    assert [t.accession for t in visible([original, amended], AS_OF)] == [amended.accession]
    # before the amendment was known, the original is the record
    assert original in visible([original], early)
    assert visible([original], dt.datetime(2026, 5, 1, tzinfo=UTC)) == []


def test_split_adjustment_applies_only_splits_effective_by_the_date() -> None:
    splits = [(day(3, 1), D(2)), (day(9, 1), D(3))]
    assert adjust_for_splits(D(100), D(10), day(1, 15), splits, day(6, 1)) == (D(200), D(5))
    assert adjust_for_splits(D(100), D(10), day(1, 15), splits, day(10, 1)) == (
        D(600),
        D(10) / D(6),
    )
    assert adjust_for_splits(D(100), D(10), day(4, 1), splits, day(6, 1)) == (D(100), D(10))
    assert adjust_for_splits(None, None, None, splits, day(6, 1)) == (None, None)


def test_a_reviewer_override_applies_from_when_it_was_made_and_needs_a_reason() -> None:
    t = tx(1, "open_market_sell", "100", "10")
    made = dt.datetime(2026, 6, 15, tzinfo=UTC)
    o = Override(
        t.accession,
        0,
        "tax_withholding",
        "Footnote 2 says shares withheld for tax",
        "reviewer",
        made,
    )
    assert effective_class(t, [o], dt.datetime(2026, 6, 1, tzinfo=UTC)) == "open_market_sell"
    assert effective_class(t, [o], AS_OF) == "tax_withholding"
    assert row(net_by_role([t], AS_OF, overrides=[o]), 30, "all").trades == 0
    assert by_class([t], [o], AS_OF) == {"tax_withholding": 1}
    with pytest.raises(ValueError):
        Override(t.accession, 0, "gift", "short", "reviewer", made)
    with pytest.raises(ValueError):
        Override(t.accession, 0, "not_a_class", "a long enough reason", "reviewer", made)
    with pytest.raises(ValueError):
        Override(t.accession, 0, "gift", "a long enough reason", " ", made)


def test_a_cluster_needs_independent_people_and_ignores_indirect_duplicates() -> None:
    a, b, c = "0000000001", "0000000002", "0000000003"
    rows = [
        tx(1, "open_market_buy", insider=a, when=day(6, 1)),
        tx(2, "open_market_buy", insider=a, when=day(6, 2), di="I"),  # the same person via a trust
        tx(3, "open_market_buy", insider=b, when=day(6, 5)),
    ]
    assert clusters(rows, AS_OF) == []  # two people, not three
    rows.append(tx(4, "open_market_buy", insider=c, when=day(6, 12), shares="50", price="20"))
    (cl,) = clusters(rows, AS_OF)
    assert cl.insiders == (a, b, c) and cl.start == day(6, 1) and cl.end == day(6, 12)
    assert cl.shares == D(350) and cl.value == D(100 * 10 * 3 + 50 * 20)
    plan_only = [tx(i, "open_market_buy", insider=f"000000000{i}", plan=True) for i in (1, 2, 3)]
    assert clusters(plan_only, AS_OF) == []
    spread = [tx(i, "open_market_buy", insider=f"000000000{i}", when=day(i, 1)) for i in (1, 3, 5)]
    assert clusters(spread, AS_OF) == []  # months apart


def test_alerts_need_material_confirmed_open_market_trades_and_collapse_duplicates() -> None:
    big = tx(1, "open_market_buy", "10000", "10")
    twin = tx(2, "open_market_buy", "10000", "10")  # an unlinked amendment repeating the trade
    small = tx(3, "open_market_buy", "10", "10", insider="0000000002")
    guess = tx(4, "open_market_buy", "10000", "10", insider="0000000003", confirmed=False)
    plan = tx(5, "open_market_sell", "10000", "10", insider="0000000004", plan=True)
    out = alerts([big, twin, small, guess, plan], AS_OF, min_value=D(50_000))
    assert [a.tx.accession for a in out] == [big.accession]
    assert out[0].value == D(100_000) and out[0].reason == "open-market purchase"
    assert alerts([big], AS_OF, min_value=D(50_000), since=big.known_at) == []  # already alerted
    assert alerts([], AS_OF, min_value=D(1)) == []  # a failed collection alerts nothing
    reviewed = Override(
        guess.accession,
        0,
        "open_market_buy",
        "Reviewed against the filing text",
        "reviewer",
        dt.datetime(2026, 6, 20, tzinfo=UTC),
    )
    assert len(alerts([guess], AS_OF, min_value=D(50_000), overrides=[reviewed])) == 1


def test_position_change_uses_the_post_trade_holding() -> None:
    buy = tx(1, "open_market_buy", "100", "10", after="1100")
    assert position_change(buy) == D(10)  # 100 on a base of 1,000
    sell = tx(2, "open_market_sell", "250", "10", after="750")
    assert position_change(sell) == D(-25)  # 250 off a base of 1,000
    assert position_change(tx(3, "open_market_buy", "100", "10", after=None)) is None
    assert (
        position_change(tx(4, "open_market_buy", "100", "10", after="100")) is None
    )  # no prior base


def test_caveats_state_facts_without_motive() -> None:
    notes = caveats(
        tx(1, "open_market_buy", plan=None, di="I", price="0", derivative=True, when=None)
    )
    text = " ".join(notes)
    assert "no plan indicator" in text and "indirectly" in text and "price of zero" in text
    assert "derivative" in text and "No transaction date" in text
    assert not any(w in text.lower() for w in ("bullish", "bearish", "confidence", "insider knows"))
    assert caveats(tx(2, "open_market_buy", plan=True)) == [
        "Reported under a Rule 10b5-1 trading plan."
    ]


def test_a_person_with_several_roles_is_counted_once() -> None:
    assert role_of(True, True, True) == "officer"
    assert role_of(False, True, True) == "director"
    assert role_of(False, False, True) == "ten_percent_owner"
    assert role_of(False, False, False) == "other"
    rows = [
        tx(1, "open_market_buy", role="officer"),
        tx(2, "open_market_buy", role="director", insider="0000000002"),
    ]
    out = net_by_role(rows, AS_OF)
    assert row(out, 90, "officer").insiders == 1 and row(out, 90, "director").insiders == 1
    assert row(out, 90, "all").insiders == 2
