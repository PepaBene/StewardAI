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
    assert any("tail-loss contribution" in n for n in pack.notes)
    assert "AAA is" in pack.summary and "moves with BBB" in pack.summary
    with_es = pp.build_pack("AAA", report, corr, None, 0.42)
    assert with_es.expected_shortfall_contribution == 0.42
    assert "42% of the book's loss in its worst weeks" in with_es.summary
    assert not any("tail-loss contribution" in n for n in with_es.notes)


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
    assert "not modelled" not in text  # the tail lines come from `tail_lines`


def test_review_tail_lines_state_the_bad_month_and_the_drawdown_odds() -> None:
    from steward.portfolio.tail import DrawdownOdds, TailLine, TailReport
    from steward.review.monthly import tail_lines

    month = TailLine(
        horizon_weeks=4, level=0.95, method="historical", var=0.05, es=0.07,
        var_value=50_000.0, es_value=70_000.0,
    )  # fmt: skip
    odds = DrawdownOdds(
        years=3, probability=0.18, threshold=0.2, drawdown_p50=0.1, drawdown_p95=0.25,
        conditional_drawdown_at_risk=0.3, terminal_p05=0.8, terminal_p50=1.3, terminal_p95=1.9,
    )  # fmt: skip
    report = TailReport(
        available=True, base_currency="CZK", lines=[month], es_95_month=0.07, drawdown=[odds]
    )
    lines = tail_lines(report, 0.2)
    assert "about 7.0% on average (70,000 CZK)" in lines[0]
    assert (
        lines[1]
        == "Simulated chance of a 20% fall from a peak within 3 years: 18% (the freeze is at 20%)."
    )
    gone = tail_lines(TailReport(available=False, reason="only 30 weeks of history"), None)
    assert gone == ["No tail-loss figure this month: only 30 weeks of history."]


def test_unknown_ticker_is_not_available():
    pack = pp.build_pack("ZZZ", _report(), [], None)
    assert not pack.available and "not in the latest portfolio" in (pack.reason or "")
