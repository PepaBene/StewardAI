"""Hand-worked golden examples for the insight features (insight plan I14.1) not already pinned
elsewhere: the portfolio scorecard aggregate and the past-performance check axis. The expected
figures were worked out on paper; the method documents in docs/methods/ cite these tests."""

from decimal import Decimal as D

from api.check_routes import aggregate
from steward.checks.contract import Check, axis_score


def _axis(passes: int, fails: int, unknown: int = 0, na: int = 0) -> list[Check]:
    states = ["pass"] * passes + ["fail"] * fails + ["unknown"] * unknown + ["na"] * na
    return [
        Check(id=f"c{i}", axis="past", label="x", state=s, sentence="", method_version="1")  # type: ignore[arg-type]
        for i, s in enumerate(states)
    ]


def test_scorecard_axis_is_the_value_weighted_mean_over_scored_holdings() -> None:
    # A 50 % holding scores 6/6, a 30 % holding passes 3 of 6 (3.00), a 20 % holding has no
    # applicable check. Score = (0.5 * 6 + 0.3 * 3) / (0.5 + 0.3) = 3.9 / 0.8 = 4.875 -> 4.88.
    rows = [
        ("A", "Alpha", D("0.5"), axis_score("past", _axis(6, 0))),
        ("B", "Beta", D("0.3"), axis_score("past", _axis(3, 3))),
        ("C", "Gamma", D("0.2"), axis_score("past", _axis(0, 0, unknown=6))),
    ]
    agg = aggregate("past", rows)
    assert agg.score == D("4.88")
    assert (agg.holdings_covered, agg.holdings_total) == (2, 3)
    assert agg.value_covered == D("0.8000")
    assert agg.coverage_text == "2 of 3 holdings, 80% of value"
    assert [line.ticker for line in agg.lifting] == ["A", "B"]


def test_past_axis_scores_passes_over_applicable_checks_only() -> None:
    # 4 passes, 1 fail, 1 not applicable: 6 * 4 / 5 = 4.80; coverage 5 of 5 relevant = 1.
    s = axis_score("past", _axis(4, 1, na=1))
    assert (s.score, s.applicable, s.na, s.coverage) == (D("4.80"), 5, 1, D("1.0000"))
    # 2 passes, 1 fail, 3 unknown: 6 * 2 / 3 = 4.00; coverage 3 of 6 relevant = 0.5.
    s = axis_score("past", _axis(2, 1, unknown=3))
    assert (s.score, s.coverage) == (D("4.00"), D("0.5000"))
