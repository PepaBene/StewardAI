"""Plan M2.13: the assessment rubric. Expected scores are worked out by hand in the comments."""

import datetime as dt
from decimal import Decimal

from steward.company.ratios import RatioGroup, RatioPoint, RatioRow, RatiosView
from steward.company.rubric import build_rubric, recompute_problems
from steward.company.statements import Column, StatementCheck, StatementsView

D = Decimal
TODAY = dt.date(2026, 10, 7)


def row(rid: str, value: str | None, median: str | None = None) -> RatioRow:
    latest = RatioPoint(column="FY25", label="FY25", value=None if value is None else D(value))
    return RatioRow.model_construct(
        id=rid,
        latest=latest,
        own_median_5y=None if median is None else D(median),
    )


def ratios_view(rows: list[RatioRow]) -> RatiosView:
    group = RatioGroup(id="all", label="All", ratios=[])
    group.ratios = rows
    return RatiosView.model_construct(groups=[group], input_hash="r1")


def statements(n_years: int, passed: list[bool]) -> StatementsView:
    cols = [
        Column(id=f"FY{y}", kind="annual", end=dt.date(2026 - n_years + y, 12, 31), label=f"FY{y}")
        for y in range(n_years)
    ]
    checks = [
        StatementCheck(name="c", statement="bs", column="FY0", passed=p, detail="") for p in passed
    ]
    return StatementsView.model_construct(columns=cols, checks=checks, input_hash="s1")


def test_scores_reproduce_by_hand() -> None:
    rows = [
        row("roic", "0.01", median="0.15"),  # median 0.15 >= 0.12: 100
        row("interest_coverage", "9", median="5"),  # 5 >= 3: 60
        row("net_debt_to_ebitda", "2", median="1.0"),  # 1.0 <= 1.5: 100
        row("fcf_margin", "0.5", median="0.02"),  # 0.02 < 0.03: 20
        row("current_ratio", "1.2"),  # latest, 1.0 <= 1.2 < 1.5: 60
        row("dilution_3y", "-0.01"),  # <= 0: 100
        row("fcf_conversion", "0.9", median="0.6"),  # 0.5 <= 0.6 < 0.8: 60
        row("accruals_ratio", "0", median="-0.12"),  # |.12| > .10: 20
        row("earnings_yield", "0.04"),  # 0.035 <= .04 < .06: 60
        row("fcf_yield", None),  # missing
    ]
    r = build_rubric(
        "AAA",
        ratios_view(rows),
        statements(10, [True, True, True, False]),  # history 10 years: 100; 3/4 = .75 < .8: 20
        None,  # no case items: citations missing
        D("0.03"),  # <= .04: 100
        TODAY,
    )
    c = {x.id: x for x in r.components}
    # durability: .30x100 + .20x60 + .20x100 + .15x20 + .15x60 = 74, full coverage
    assert c["durability"].score == D("74.000000")
    # allocation: .40x100 + .35x60 + .25x20 = 66
    assert c["allocation"].score == D("66.000000")
    # valuation: known weight .7 (coverage .7): (.4x60 + .3x100) / .7 = 77.142857,
    # minus 20 x .3 = 71.142857
    assert c["valuation"].coverage == D("0.700000")
    assert c["valuation"].score == D("71.142857")
    # evidence: known .75: (.25x100 + .30x20 + .20x100) / .75 = 68, minus 20 x .25 = 63
    assert c["evidence"].raw_score == D("68.000000") and c["evidence"].score == D("63.000000")
    # overall: .35x74 + .25x66 + .20x71.142857 + .20x63 = 69.228571
    assert r.overall == D("69.228571") and not r.abstained
    assert r.calibration.status == "uncalibrated"
    # The independent recompute from the published table agrees, and catches a changed figure.
    assert recompute_problems(r) == []
    assert not any("Recomputing" in cv for cv in r.caveats)
    tampered = r.model_copy(update={"overall": D("80")})
    assert any(p.startswith("overall") for p in recompute_problems(tampered))
    edited = r.model_copy(deep=True)
    edited.components[0].indicators[0].points = D("20")
    assert any("points" in p for p in recompute_problems(edited))


def test_component_with_too_little_coverage_abstains_and_overall_follows() -> None:
    r = build_rubric("BANK", ratios_view([]), statements(10, [True]), (3, 4), None, TODAY)
    c = {x.id: x for x in r.components}
    assert c["durability"].abstained and c["durability"].score is None
    assert "missing" in (c["durability"].reason or "")
    assert r.abstained and r.overall is None  # durability never scored
    assert "Financial durability" in (r.reason or "")


def test_missing_value_is_never_scored_as_zero() -> None:
    r = build_rubric("X", ratios_view([row("roic", None)]), statements(1, []), None, None, TODAY)
    roic = next(i for c in r.components for i in c.indicators if i.id == "roic")
    assert roic.band == "missing" and roic.points is None and roic.value is None
