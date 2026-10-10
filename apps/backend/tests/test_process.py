import datetime as dt
from typing import Any

from steward.journal import process as pr
from steward.journal.entries import JournalEntry


def entry(id_: int, ticker: str, decision: str, day: dt.date, price: float | None, **kw: Any):
    base: dict[str, Any] = dict(
        id=id_, scope="security", ticker=ticker, decision=decision, parent_id=None,
        decided_on=day, hypothesis="h", valuation_assumptions=None, catalysts=[],
        disconfirming=[], review_date=None, conviction=3, main_reason=None,
        price_at_decision=price, price_currency="USD", snapshot={},
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )  # fmt: skip
    return JournalEntry(**{**base, **kw})


D = dt.date


def test_a_sell_pairs_with_the_first_open_buy_and_a_rebuy_starts_again():
    rows = [
        entry(1, "AAA", "buy", D(2025, 1, 10), 100),
        entry(2, "AAA", "add", D(2025, 2, 10), 110),
        entry(3, "AAA", "sell", D(2025, 6, 10), 130),
        entry(4, "AAA", "buy", D(2025, 8, 1), 120),
        entry(5, "BBB", "sell", D(2025, 3, 1), 50),  # nothing to close
    ]
    pairs = pr.closed_pairs(rows)
    assert [(p.opening.id, p.closing.id) for p in pairs] == [(1, 3)]


def test_return_vs_benchmark_and_loss_with_a_trigger_before_close():
    pairs = pr.closed_pairs(
        [
            entry(1, "AAA", "buy", D(2025, 1, 10), 100),
            entry(2, "AAA", "sell", D(2025, 3, 10), 90),
            entry(3, "BBB", "buy", D(2025, 1, 10), 100),
            entry(4, "BBB", "sell", D(2025, 3, 10), 130),
        ]
    )
    bench = [(D(2025, 1, 10), 100.0), (D(2025, 3, 10), 105.0)]
    triggers = {"AAA": [dt.datetime(2025, 2, 1, tzinfo=dt.UTC)]}
    out = {t.ticker: t for t in pr.build_theses(pairs, {"AAA": "core"}, bench, triggers)}
    a, b = out["AAA"], out["BBB"]
    assert abs(a.price_return + 0.1) < 1e-12 and a.benchmark_return == 0.05
    assert a.beat_benchmark is False and a.trigger_before_close is True and a.tier == "core"
    assert abs(b.price_return - 0.3) < 1e-12 and b.beat_benchmark is True
    assert b.trigger_before_close is None and b.tier == "Unclassified"


def test_a_trigger_after_the_sale_does_not_count():
    pairs = pr.closed_pairs(
        [entry(1, "AAA", "buy", D(2025, 1, 10), 100), entry(2, "AAA", "sell", D(2025, 3, 10), 90)]
    )
    triggers = {"AAA": [dt.datetime(2025, 4, 1, tzinfo=dt.UTC)]}
    (t,) = pr.build_theses(pairs, {}, [], triggers)
    assert t.trigger_before_close is False
    assert t.benchmark_return is None and t.reason == "no benchmark prices"


def test_different_currencies_give_no_return():
    pairs = pr.closed_pairs(
        [
            entry(1, "AAA", "buy", D(2025, 1, 10), 100),
            entry(2, "AAA", "sell", D(2025, 3, 10), 90, price_currency="EUR"),
        ]
    )
    (t,) = pr.build_theses(pairs, {}, [], {})
    assert t.price_return is None and "different currencies" in (t.reason or "")


def test_hit_rate_is_withheld_below_the_minimum_sample():
    def thesis(beat: bool) -> pr.ClosedThesis:
        return pr.ClosedThesis(
            ticker="X", tier="core", opened_on=D(2025, 1, 1), closed_on=D(2025, 2, 1),
            holding_days=31, price_return=0.1, benchmark_return=0.0,
            excess_return=0.1 if beat else -0.1, beat_benchmark=beat,
            trigger_before_close=None, reason=None,
        )  # fmt: skip

    few = pr.hit_rates([thesis(True), thesis(False)])[0]
    assert not few.enough and few.hit_rate is None
    many = pr.hit_rates([thesis(True)] * 4 + [thesis(False)])[0]
    assert many.enough and many.hit_rate == 0.8 and many.median_excess == 0.1
