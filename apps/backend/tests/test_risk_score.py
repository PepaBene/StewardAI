# ruff: noqa: E501
"""Insight plan I12.4: the risk score is monotonic in each input, a hand-worked golden portfolio,
and unknown handling. Also the verdict phrases and the correlation pair lists."""

from decimal import Decimal

import pytest

from steward.decision.risk_report import CorrelationMatrix
from steward.decision.risk_score import WEIGHTS, Inputs, phrase, score, sub_score
from steward.decision.risk_score_view import pairs, top3_from_weights

D = Decimal
BENCH = {"volatility": D("0.16"), "max_drawdown": D("0.20"), "expected_shortfall": D("0.025")}
GOLDEN = {
    "volatility": D("0.24"),
    "max_drawdown": D("0.10"),
    "expected_shortfall": D("0.025"),
    "concentration": D("0.30"),
    "beta": D("1.2"),
}


def test_golden_portfolio() -> None:
    # volatility 0.24 against 0.16 (very high 0.40): 50 + 0.08/0.24 x 50 = 66.667
    # drawdown 0.10 against 0.20: 0.10/0.20 x 50 = 25
    # shortfall equal to the benchmark: 50
    # concentration 0.30 against 0.12 (very high 0.60): 50 + 0.18/0.48 x 50 = 68.75
    # beta 1.2 against 1 (very high 2): 60
    # 0.30 x 66.667 + 0.25 x 25 + 0.20 x 50 + 0.15 x 68.75 + 0.10 x 60 = 52.5625
    s = score(Inputs(GOLDEN, BENCH, {}))
    assert s.status == "complete"
    assert s.score == 52.6
    assert s.band == "About as risky as the market"
    assert s.benchmark_score == 50.0
    assert [x.score for x in s.sub_scores] == [66.7, 25.0, 50.0, 68.8, 60.0]
    assert sum(WEIGHTS.values()) == 1


@pytest.mark.parametrize("key", list(GOLDEN))
def test_score_is_monotonic_in_each_input(key: str) -> None:
    last = -1.0
    for step in range(0, 40):
        values = dict(GOLDEN)
        values[key] = D(step) * (D("0.05") if key != "beta" else D("0.1"))
        got = score(Inputs(values, BENCH, {})).score
        assert got is not None and got >= last
        last = got


def test_sub_score_is_clamped_and_anchored() -> None:
    assert sub_score(D(0), D("0.16"), D("0.40")) == 0
    assert sub_score(D("0.16"), D("0.16"), D("0.40")) == 50
    assert sub_score(D("0.40"), D("0.16"), D("0.40")) == 100
    assert sub_score(D("0.90"), D("0.16"), D("0.40")) == 100
    assert sub_score(D("-0.5"), D(1), D(2)) == 0  # a negative beta is no risk, not negative risk


def test_unknown_when_an_input_is_missing() -> None:
    values = dict(GOLDEN, expected_shortfall=None)
    s = score(
        Inputs(
            values, BENCH, {"expected_shortfall": "value at risk needs 250 observations, have 180"}
        )
    )
    assert (s.status, s.score, s.band) == ("unknown", None, None)
    assert s.reason == "Not scored: Expected shortfall (95 %, one day) is not available yet."
    es = next(x for x in s.sub_scores if x.input == "expected_shortfall")
    assert es.score is None and "250 observations" in (es.reason or "")


def test_missing_benchmark_figure_uses_the_fixed_reference_and_says_so() -> None:
    s = score(
        Inputs(
            GOLDEN,
            {"volatility": None, "max_drawdown": D("0.20"), "expected_shortfall": D("0.025")},
            {},
        )
    )
    vol = s.sub_scores[0]
    assert (vol.reference, vol.reference_basis) == (0.16, "fixed")
    assert "fixed market reference" in (vol.reason or "")


def test_verdict_phrases() -> None:
    v = phrase("sharpe", D("1.4"))
    assert v is not None and (v.band, v.tone) == ("good", "good")
    assert phrase("max_drawdown", D("-0.32")).band == "deep"  # type: ignore[union-attr]
    assert phrase("beta", D("0.6")).band == "defensive"  # type: ignore[union-attr]
    assert phrase("sharpe", None) is None
    assert phrase("no_such_metric", D(1)) is None


def test_correlation_pairs_and_fallback_concentration() -> None:
    m = CorrelationMatrix(
        tickers=["A", "B", "C"],
        values=[[1.0, 0.9, 0.1], [0.9, 1.0, None], [0.1, None, 1.0]],
        observations=[[100] * 3] * 3, average=0.5, pairs_measured=2, pairs_total=3, min_observations=60,
    )  # fmt: skip
    together, least = pairs(m, 1)
    assert (together[0].a, together[0].b, together[0].correlation) == ("A", "B", 0.9)
    assert (least[0].a, least[0].b) == ("A", "C")
    # 50 / 30 / 10 / 10 of 100: top three 90 %.
    assert top3_from_weights({"a": 50, "b": 30, "c": 10, "d": 10}) == D("0.9")
    assert top3_from_weights({}) is None
