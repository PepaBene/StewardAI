"""The scorecard's figures are recomputed a second way; a figure that differs is reported."""

import datetime as dt

from steward.journal import recompute
from steward.journal.scorecard import DecisionOutcome, SwitchOutcome, build_scorecard

TODAY = dt.date(2026, 10, 14)


def outcome(i: int, decision: str, then: float, now: float, conviction: int = 3) -> DecisionOutcome:
    return DecisionOutcome(
        id=i,
        ticker=f"T{i}",
        decision=decision,
        decided_on=TODAY - dt.timedelta(days=30),
        price_at_decision=then,
        price_now=now,
        price_now_date=TODAY,
        currency_at_decision="USD",
        currency_now="USD",
        conviction=conviction,
        main_reason="value",
    )


def data() -> list[DecisionOutcome]:
    buys = [
        outcome(i, "buy", 100, p, conviction=i % 2)
        for i, p in enumerate((90, 110, 120, 100, 105, 95))
    ]
    sells = [outcome(10 + i, "sell", 100, p) for i, p in enumerate((90, 80, 110, 70, 100, 95))]
    return buys + sells


def card(outcomes: list[DecisionOutcome]):  # type: ignore[no-untyped-def]
    return build_scorecard(outcomes, since=TODAY - dt.timedelta(days=365), until=TODAY)


def test_the_two_computations_agree_on_an_ordinary_scorecard() -> None:
    outcomes = data()
    built = card(outcomes)
    assert any(s.status == "ok" and s.id in recompute.CHECKED for s in built.sections)
    assert recompute.check(built, outcomes) == []


def test_a_figure_that_was_changed_is_reported() -> None:
    outcomes = data()
    built = card(outcomes)
    section = next(s for s in built.sections if s.id == "after_sell")
    section.rows[0].median_change = 0.5
    problems = recompute.check(built, outcomes)
    assert len(problems) == 1 and "after_sell" in problems[0] and "median" in problems[0]


def targets() -> list[DecisionOutcome]:
    out = []
    for i, (low, hit) in enumerate(
        ((80, True), (95, False), (90, True), (99, False), (70, True), (85, False))
    ):
        o = outcome(20 + i, "buy", 100, 110)
        o.sell_target = 120
        o.hit_on = TODAY - dt.timedelta(days=5) if hit else None
        o.bear_case = 85
        o.low_since = low
        out.append(o)
    return out


def switches() -> list[SwitchOutcome]:
    return [
        SwitchOutcome(
            id=i,
            switched_on=TODAY - dt.timedelta(days=20),
            sold="A",
            bought="B",
            sold_price=100,
            sold_currency="USD",
            sold_now=100 + sold,
            sold_currency_now="USD",
            bought_price=100,
            bought_currency="USD",
            bought_now=100 + bought,
            bought_currency_now="USD",
        )
        for i, (sold, bought) in enumerate(((0, 10), (5, 2), (-3, 4), (1, 8), (6, -2), (0, 1)))
    ]


def test_targets_risk_and_switches_are_recomputed_too() -> None:
    outcomes = data() + targets()
    sw = switches()
    built = build_scorecard(
        outcomes, since=TODAY - dt.timedelta(days=365), until=TODAY, switches=sw
    )
    ids = {s.id for s in built.sections if s.status == "ok"}
    assert {"sell_targets", "risk_held", "switches"} <= ids
    assert recompute.check(built, outcomes, sw) == []
    risk = next(s for s in built.sections if s.id == "risk_held")
    risk.rows[0].share = 0.99
    switched = next(s for s in built.sections if s.id == "switches")
    switched.rows[1].median_change = 0.5
    problems = recompute.check(built, outcomes, sw)
    assert any("risk_held" in p for p in problems) and any("switches" in p for p in problems)


def test_an_entry_missing_from_the_data_is_reported() -> None:
    outcomes = data()
    built = card(outcomes)
    problems = recompute.check(built, [o for o in outcomes if o.id != 10])
    assert problems and "not in the data" in problems[0]
