from steward.config.strategy import StrategyConfig
from steward.portfolio import exante as ex
from steward.portfolio import position_pack as pp
from tests.test_exante import _factor_book


def _report() -> ex.ExAnteRisk:
    return ex.build_report(_factor_book(), StrategyConfig(base_currency="CZK"))


def test_pack_reports_risk_share_cluster_and_the_missing_tail_measure():
    report = _report()
    corr = [("AAA", "BBB", 0.82, 100), ("AAA", "CCC", 0.1, 100), ("BBB", "CCC", 0.9, 100)]
    pack = pp.build_pack("AAA", report, corr, None)
    assert pack.available and pack.risk_share is not None
    assert [m.ticker for m in pack.cluster] == ["BBB"]  # CCC is below 0.6; BBB-CCC is not AAA's
    assert pack.cluster_weight is not None and pack.cluster_weight > pack.weight
    assert pack.risk_to_weight is not None
    assert pack.expected_shortfall_contribution is None
    assert any("Expected Shortfall" in n for n in pack.notes)
    assert "AAA is" in pack.summary and "moves with BBB" in pack.summary


def test_review_risk_section_lists_contributors_tiers_and_what_is_not_modelled():
    from steward.review.monthly import risk_section_lines

    report = _report()
    report.realized_volatility = 0.12
    report.limits.tier_risk_budgets = {"Unclassified": 0.5}
    text = "\n".join(risk_section_lines(report, None))
    assert "Forecast volatility from today's holdings" in text
    assert "Realised volatility over the last year was 12.0%" in text
    assert "Largest risk contributors: " in text and "% of risk" in text
    assert "Unclassified" in text and "budget 50%" in text
    assert "Expected Shortfall and the probability of a drawdown are not modelled" in text


def test_unknown_ticker_is_not_available():
    pack = pp.build_pack("ZZZ", _report(), [], None)
    assert not pack.available and "not in the latest portfolio" in (pack.reason or "")
