# ruff: noqa: E501
"""Four-state company checks (insight plan I3.11): golden examples per axis, the bank, loss-maker,
non-payer and net-interest-earner fixtures, the scoring property and a sentence snapshot."""

import datetime as dt
import random
from decimal import Decimal as D

import pytest

from steward.checks import sentences
from steward.checks.contract import SCORED_AXES, Check, axis_score, score_text
from steward.checks.inputs import CheckInputs, RatioFacts, cagr
from steward.checks.service import assemble, evaluate, inputs_hash, verdict
from steward.config.checks import ChecksConfig
from steward.discovery.composite import Flag, check_state_of, flag_from_check

RULES = ChecksConfig()


def _healthy(**over: object) -> CheckInputs:
    years = list(range(2016, 2026))
    i = CheckInputs(
        ticker="AAPL_US_EQ",
        name="Apple",
        currency="USD",
        price=D(100),
        today=dt.date(2026, 6, 1),
        ratios={
            "pe": RatioFacts(latest=D(15), peer_median=D(20), peer_count=8, peer_scope="global"),
            "peg": RatioFacts(latest=D("1.2")),
            "fcf_yield": RatioFacts(latest=D("0.07")),
            "eps_diluted_cagr_5y": RatioFacts(latest=D("0.10"), peer_median=D("0.05"), peer_count=6, peer_scope="global"),
            "roe": RatioFacts(latest=D("0.25")),
            "roce": RatioFacts(latest=D("0.20")),
            "net_margin": RatioFacts(series=[(2024, D("0.20")), (2025, D("0.22"))]),
            "operating_margin": RatioFacts(series=[(y, D("0.30")) for y in years]),
            "accruals_ratio": RatioFacts(latest=D("0.02")),
            "debt_to_equity": RatioFacts(latest=D("0.30"), series=[(y, D("0.30")) for y in years]),
            "interest_coverage": RatioFacts(latest=D(20)),
            "net_debt_to_ebitda": RatioFacts(latest=D("0.5")),
            "dilution_1y": RatioFacts(latest=D("-0.02")),
            "payout_ratio": RatioFacts(latest=D("0.40")),
            "dividend_yield": RatioFacts(latest=D("0.03"), peer_median=D("0.02"), peer_count=7, peer_scope="global"),
            "total_shareholder_yield": RatioFacts(latest=D("0.05")),
            "cash_runway": RatioFacts(reason="not burning cash"),
        },
        fig={
            "current_assets": D(500), "current_liabilities": D(200), "total_liabilities": D(400),
            "cash_from_operations": D(300), "net_income": D(200), "capex": D(-50), "equity": D(800),
            "dividends_paid": D(-80), "interest_expense": D(10), "interest_income": D(2), "cash": D(100),
            "weighted_shares_diluted": D(10),
        },
        series={"fcf": [(2023, D(200)), (2024, D(220)), (2025, D(250))], "total_assets": [(2024, D(1000)), (2025, D(1100))]},
        debt=D(300),
        dcf_base=D(120),
        owner_value=D(110),
        owner_basis="your 'base' scenario (Main v2)",
        consensus_target=D(130),
        consensus_basis="fmp, 30 analysts",
        consensus_reason=None,
        analyst_count=30,
        eps_next=D(22),
        eps_trailing=D(20),
        revenue_next=D(1100),
        revenue_trailing=D(1000),
        estimates_reason=None,
        peer_eps_growth=D("0.05"),
        peer_revenue_growth=D("0.04"),
        peer_forecast_count=6,
        payer=True,
        dps_by_year=[(y, D("1.0") + D("0.1") * (y - 2014)) for y in range(2014, 2026)],
        years_without_cut=11,
        insider_has_filings=True,
        insider_net_sold_value=D(0),
        market_cap=D("3e12"),
        avg_turnover=D("5e9"),
        volatility_1y=D("0.20"),
        years_listed=D(40),
        amendments_12m=0,
        next_report=dt.date(2026, 7, 30),
        debt_due_1y=D(30),
        customer_known=True,
        customer_max_share=D("0.05"),
        cash_runway_reason="not burning cash",
    )  # fmt: skip
    for k, v in over.items():
        setattr(i, k, v)
    return i


def _by_id(checks: list[Check]) -> dict[str, Check]:
    return {c.id: c for c in checks}


def test_healthy_company_passes_every_scored_check() -> None:
    checks, risks = evaluate(_healthy(), RULES)
    failed = [c.id for c in checks if c.state != "pass"]
    assert failed == []
    for axis in SCORED_AXES:
        s = axis_score(axis, checks)
        assert (s.passes, s.applicable, s.score) == (6, 6, D("6.00"))
    risky = [c.id for c in risks if c.state != "pass"]
    assert risky == []


def test_each_axis_has_six_checks() -> None:
    checks, risks = evaluate(_healthy(), RULES)
    for axis in SCORED_AXES:
        assert sum(1 for c in checks if c.axis == axis) == 6
    assert len(risks) >= 14


def test_fail_states_and_sentences_use_the_figures() -> None:
    i = _healthy(price=D(200))
    i.ratios["pe"] = RatioFacts(latest=D(30), peer_median=D(20), peer_count=8, peer_scope="global")
    c = _by_id(evaluate(i, RULES)[0])
    assert c["value_multiple_vs_peers"].state == "fail"
    assert (
        c["value_multiple_vs_peers"].sentence
        == "P/E (30.0x) is above the peer median (20.0x, global, 8 peers)"
    )
    assert c["value_price_vs_dcf"].state == "fail"
    assert c["value_consensus"].state == "fail"


def test_unknown_when_estimates_missing() -> None:
    i = _healthy(
        eps_next=None,
        revenue_next=None,
        analyst_count=None,
        estimates_reason="no stored analyst estimates",
    )
    checks = evaluate(i, RULES)[0]
    future = [c for c in checks if c.axis == "future"]
    assert all(c.state == "unknown" for c in future)
    s = axis_score("future", checks)
    assert s.score is None and s.applicable == 0 and s.unknown == 6
    assert score_text(s) == "no data"
    assert "no stored analyst estimates" in future[0].sentence


def test_peer_median_needs_enough_peers() -> None:
    i = _healthy()
    i.ratios["pe"] = RatioFacts(
        latest=D(15), peer_median=D(20), peer_count=3, peer_reason="3 comparable peers (needs 5)"
    )
    c = _by_id(evaluate(i, RULES)[0])["value_multiple_vs_peers"]
    assert c.state == "unknown" and "needs 5" in c.sentence


def test_loss_maker_uses_ev_to_ebit_then_sales() -> None:
    i = _healthy()
    i.ratios["pe"] = RatioFacts(latest=None, reason="n/m: negative earnings")
    i.ratios["ev_to_ebit"] = RatioFacts(latest=D(-5))
    i.ratios["ev_to_sales"] = RatioFacts(
        latest=D(2), peer_median=D(3), peer_count=5, peer_scope="global"
    )
    c = _by_id(evaluate(i, RULES)[0])["value_multiple_vs_peers"]
    assert c.state == "pass" and c.sentence.startswith("EV/sales (2.0x)")


def test_non_payer_dividend_axis_is_not_applicable_not_zero() -> None:
    checks = evaluate(_healthy(payer=False), RULES)[0]
    div = [c for c in checks if c.axis == "dividend"]
    assert {c.state for c in div} == {"na"}
    s = axis_score("dividend", checks)
    assert s.score is None and s.na == 6
    assert score_text(s) == "not applicable"


def test_bank_balance_sheet_checks_are_not_applicable() -> None:
    i = _healthy(is_financial=True)
    i.ratios["roce"] = RatioFacts(latest=None, reason="n/m: not meaningful for banks")
    c = _by_id(evaluate(i, RULES)[0])
    for cid in (
        "health_short_term_assets",
        "health_long_term_liabilities",
        "health_cfo_covers_debt",
        "health_cash_vs_debt",
        "past_roce",
    ):
        assert c[cid].state == "na", cid
    assert "banks and insurers" in c["health_short_term_assets"].sentence


def test_net_interest_earner_skips_interest_coverage() -> None:
    i = _healthy()
    i.fig["interest_income"] = D(50)
    i.ratios["interest_coverage"] = RatioFacts(latest=D(-705))
    checks, risks = evaluate(i, RULES)
    assert _by_id(checks)["health_interest_coverage"].state == "na"
    assert _by_id(risks)["risk_interest_coverage"].state == "na"


def test_debt_to_equity_passes_when_falling() -> None:
    i = _healthy()
    i.ratios["debt_to_equity"] = RatioFacts(
        latest=D("0.6"),
        series=[
            (2021, D("1.0")),
            (2022, D("0.9")),
            (2023, D("0.8")),
            (2024, D("0.7")),
            (2025, D("0.6")),
        ],
    )
    c = _by_id(evaluate(i, RULES)[0])["health_debt_to_equity"]
    assert c.state == "pass" and "down from" in c.sentence


def test_dividend_cut_and_payout_failures() -> None:
    i = _healthy(years_without_cut=2, dps_by_year=[(2016, D(2)), (2025, D(1))])
    i.ratios["payout_ratio"] = RatioFacts(latest=D("0.9"))
    c = _by_id(evaluate(i, RULES)[0])
    assert c["dividend_no_cut"].state == "fail"
    assert c["dividend_growth"].state == "fail"
    assert c["dividend_payout_earnings"].state == "fail"


def test_yield_below_floor_is_not_applicable() -> None:
    i = _healthy()
    i.ratios["dividend_yield"] = RatioFacts(latest=D("0.004"), peer_median=D("0.02"), peer_count=9)
    c = _by_id(evaluate(i, RULES)[0])["dividend_yield_vs_peers"]
    assert c.state == "na" and "floor" in c.sentence


def test_risk_failures() -> None:
    i = _healthy(
        insider_net_sold_value=D(5_000_000),
        market_cap=D("1e9"),
        next_report=dt.date(2026, 6, 10),
        amendments_12m=2,
    )
    i.ratios["dilution_1y"] = RatioFacts(latest=D("0.12"))
    r = _by_id(evaluate(i, RULES)[1])
    assert r["risk_insider_selling"].state == "fail"
    assert r["risk_dilution"].state == "fail"
    assert r["risk_small_cap"].state == "fail"
    assert r["risk_restatement"].state == "fail"
    assert r["risk_earnings_soon"].state == "fail" and r["risk_earnings_soon"].informational


@pytest.mark.parametrize("seed", range(30))
def test_score_never_counts_na_or_unknown(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - deterministic test data
    checks = [
        Check(id=f"c{k}", axis="value", label="x", state=rng.choice(["pass", "fail", "na", "unknown"]), sentence="", method_version="1")
        for k in range(6)
    ]  # fmt: skip
    s = axis_score("value", checks)
    passes = sum(1 for c in checks if c.state == "pass")
    fails = sum(1 for c in checks if c.state == "fail")
    assert s.applicable == passes + fails
    assert (
        s.score == (D(6) * passes / (passes + fails)).quantize(D("0.01"))
        if passes + fails
        else s.score is None
    )


def test_sentence_snapshot() -> None:
    """Every template renders for every state; changing one needs a SENTENCES_VERSION bump."""
    assert sentences.SENTENCES_VERSION == "1.0.0"
    assert len(sentences.SENTENCES) == 47
    for cid in sentences.SENTENCES:
        for state in ("pass", "fail", "na", "unknown"):
            text = sentences.sentence(cid, state, v="1", t="2", basis="b", metric="m", reason="r")
            assert text and "{" not in text
    assert (
        sentences.sentence("health_debt_to_equity", "pass", v="9.0%", basis="below 40%")
        == "Debt to equity (9.0%) is below 40%"
    )
    assert sentences.fmt(D("0.09"), "fraction") == "9.0%"
    assert sentences.fmt(D("1500000000"), "money") == "1.5bn"


def test_assemble_verdict_and_hash() -> None:
    i = _healthy(eps_next=None, revenue_next=None, analyst_count=None)
    res = assemble(i, RULES, 3, inputs_hash(i, RULES), dt.datetime(2026, 6, 1, tzinfo=dt.UTC))
    assert res.status == "partial"
    assert res.verdict.startswith("Level across the 4 scored axes")
    j = _healthy()
    j.ratios["peg"] = RatioFacts(latest=D(3))
    assert assemble(j, RULES, 3, "h", res.computed_at).verdict.startswith("Strongest on")
    assert len(res.rewards) == 3
    assert inputs_hash(i, RULES) == inputs_hash(
        _healthy(eps_next=None, revenue_next=None, analyst_count=None), RULES
    )
    assert inputs_hash(i, RULES) != inputs_hash(_healthy(), RULES)
    assert verdict([]) == "Not enough data to score any axis yet."


def test_flag_adapter_round_trip() -> None:
    risks = _by_id(evaluate(_healthy(market_cap=D("1e9")), RULES)[1])
    flag = flag_from_check(risks["risk_small_cap"])
    assert flag.state == "flag" and flag.id == "risk_small_cap"
    assert check_state_of(Flag("x", "x", "unchecked", "")) == "unknown"


def test_cagr_rules() -> None:
    assert cagr([(2020, D(1)), (2025, D(2))], 5)[0] == pytest.approx(
        D(2) ** D("0.2") - 1, rel=D("1e-9")
    )
    assert cagr([(2025, D(1))], 5) == (None, "fewer than two fiscal years")
    assert cagr([(2020, D(0)), (2025, D(2))], 5)[0] is None
