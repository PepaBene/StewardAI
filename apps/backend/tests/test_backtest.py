"""Plan M1.7: the historical screen test. Expected figures are worked out by hand below."""

import datetime as dt
from decimal import Decimal

import pytest

from steward.discovery import universe
from steward.discovery.backtest import run_backtest

D = Decimal


def day(y: int, m: int, d: int) -> dt.date:
    return dt.date(y, m, d)


def stamp(d: dt.date) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC)


def member(ticker: str, **kw: object) -> universe.UniverseMember:
    return universe.UniverseMember(ticker=ticker, **kw)  # type: ignore[arg-type]


R1, R2, END = day(2021, 1, 4), day(2021, 7, 1), day(2022, 1, 3)
# The signal is read at a rebalance day's close and the trade fills at the next session's, so
# every fill price sits one day after the rebalance date.
F1, F2 = day(2021, 1, 5), day(2021, 7, 2)
CLOSES = {
    "A": [(R1, D(1)), (F1, D(100)), (R2, D(1)), (F2, D(120)), (END, D(150))],
    "GONE": [(R1, D(1)), (F1, D(50)), (day(2021, 6, 15), D(40))],
    "B": [(R2, D(1)), (F2, D(10)), (END, D(9))],
}


def plan(cost_bps: int) -> universe.FrozenBacktestPlan:
    members = [
        member("A"),
        member("B"),
        member("GONE", status="terminated", active_to=day(2021, 6, 15)),
        member("NOPE"),
    ]
    p = universe.FrozenBacktestPlan(transaction_cost_bps=cost_bps)
    for r in (R1, R2):
        p.add(r, universe.freeze(members, r, stamp(r)), stamp(r))
    return p


def test_net_return_after_costs_and_delisted_name() -> None:
    # Period 1: A +20 %, GONE held at its last price 40 vs 50 = -20 %; equal weight => gross 0.
    #   Buy-in turnover 1.0 => cost 10 bps => net (1 - .001) x 1 - 1 = -0.001.
    #   Drifted weights: A .5 x 1.2 = .6, GONE .5 x .8 = .4.
    # Period 2: A +25 %, B -10 %, weights .5/.5 => gross .075. Traded |.5-.6|+|0-.4|+|.5-0| = 1.0
    #   => cost .001 => net .999 x 1.075 - 1 = .073925.
    # Cumulative net = .999 x 1.073925 - 1 = 0.072851075 (half-even at 8 places: 0.07285108).
    r = run_backtest(
        plan(10),
        {R1: ["A", "GONE"], R2: ["A", "B"]},
        CLOSES,
        END,
        current_members=[member("A"), member("B")],
    )
    p1, p2 = r.periods
    assert p1.gross_return == D(0)
    assert p1.net_return == D("-0.001")
    assert [h.ended_early for h in p1.held] == [False, True]
    assert p2.gross_return == D("0.075")
    assert p2.turnover == D(1)
    assert p2.net_return == D("0.073925")
    assert r.cumulative_net == D("0.07285108")
    assert r.cumulative_gross == D("0.07500000")
    assert r.cost_drag == D("0.00214892")
    assert any("delisted" in w for w in r.warnings)
    assert r.annualised_net is None  # 364 days: under a year
    assert not any("survivorship" in w for w in r.warnings)  # GONE left and was held


def test_zero_cost_is_stated_and_name_without_price_is_dropped_not_zeroed() -> None:
    r = run_backtest(plan(0), {R1: ["A", "NOPE"], R2: ["A"]}, CLOSES, END)
    assert r.periods[0].dropped_no_price == ["NOPE"]
    assert [h.ticker for h in r.periods[0].held] == ["A"]  # NOPE is not valued at zero
    assert any("no transaction cost" in w for w in r.warnings)


def test_pick_outside_the_frozen_universe_is_refused() -> None:
    with pytest.raises(ValueError, match="outside the frozen universe"):
        run_backtest(plan(10), {R1: ["ZZZ"], R2: []}, CLOSES, END)


def test_a_universe_frozen_after_the_rebalance_is_refused() -> None:
    p = plan(10)
    late = universe.freeze([member("A")], day(2021, 2, 1), stamp(day(2021, 2, 1)))
    p.add(R1, late, stamp(R1))
    with pytest.raises(ValueError, match="later than rebalance"):
        run_backtest(p, {}, CLOSES, END)


def test_empty_selection_stays_in_cash_and_says_so() -> None:
    r = run_backtest(plan(10), {R1: [], R2: []}, CLOSES, END)
    assert r.cumulative_net is None  # nothing was ever held: no figure, not zero
    assert all(p.note for p in r.periods)


def test_benchmark_chain() -> None:
    bench = [(F1, D(100)), (F2, D(110)), (END, D(121))]
    r = run_backtest(plan(10), {R1: ["A"], R2: ["A"]}, CLOSES, END, benchmark=bench)
    assert r.benchmark_cumulative == D("0.21000000")  # 1.10 x 1.10 - 1
