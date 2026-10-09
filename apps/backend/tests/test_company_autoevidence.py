"""Case evidence the app computes itself: hand-computed expectations, no database."""

from decimal import Decimal

from steward.company import autoevidence as ae
from steward.company.evidence import AllocationYear
from steward.company.segments import Breakdown, MemberLine, SegmentAnalysis

D = Decimal


def year(label, fcf, div, buy, shares, issued_net, fps):
    return AllocationYear(
        column=f"a:{label[2:]}-12-31",  # statement column id carries the period end
        label=label,
        fcf=fcf,
        capex=None,
        dividends=div,
        buybacks=buy,
        debt_issued=None,
        debt_repaid=None,
        net_debt_change=issued_net,
        returned_share_of_fcf=None,
        share_count=shares,
        share_change=None,
        fcf_per_share=fps,
        fcf_per_share_growth=None,
        fcf_growth=None,
    )


def test_capital_observations_are_computed_over_the_window_with_stated_inputs():
    years = [
        year("FY2022", D(100), D(20), D(30), D(1000), D(10), D("0.10")),
        year("FY2023", D(200), D(40), D(60), D(950), D(-5), D("0.21")),
        year("FY2024", D(300), D(60), D(90), D(900), None, D("0.33")),
    ]
    obs = {o.id: o for o in ae.capital_observations(years)}
    # (20+30+40+60+60+90) / (100+200+300) = 300 / 600
    assert obs["cash_returned"].value == D("0.5")
    assert obs["share_count_change"].value == D("-0.1")  # 900 / 1000 - 1
    assert obs["net_debt_issued"].value == D(5) and "2 of 3" in obs["net_debt_issued"].detail
    assert obs["net_acquisitions"].value is None  # not filed is unknown, never zero
    cagr = obs["fcf_per_share_growth"].value
    assert cagr is not None and abs(cagr - D("0.8166")) < D("0.001")  # (0.33/0.10) ** 0.5 - 1


def test_fcf_per_share_growth_uses_calendar_years_not_the_count_of_positive_years():
    years = [
        year("FY2022", D(100), None, None, None, None, D("0.10")),
        year("FY2023", D(-5), None, None, None, None, D("-0.01")),
        year("FY2024", D(300), None, None, None, None, D("0.40")),
    ]
    obs = {o.id: o for o in ae.capital_observations(years)}
    cagr = obs["fcf_per_share_growth"].value
    # Two years elapsed between FY2022 and FY2024:
    # (0.40 / 0.10) ** (1/2) - 1 = 1.0, not ** (1/1) = 3.0.
    assert cagr is not None and abs(cagr - D("1.0")) < D("0.01")


def test_missing_inputs_leave_an_empty_figure_with_its_reason_never_zero():
    obs = {
        o.id: o
        for o in ae.capital_observations([year("FY2024", None, None, None, None, None, None)])
    }
    assert all(o.value is None and o.reason for o in obs.values())
    assert {o.id for o in obs.values()} == {
        "cash_returned",
        "share_count_change",
        "net_debt_issued",
        "net_acquisitions",
        "fcf_per_share_growth",
    }


def _breakdown(kind, shares):
    return Breakdown(
        kind=kind,
        label=kind,
        periods=["FY2024"],
        latest_period="FY2024",
        members=[
            MemberLine(
                key=str(i),
                label=f"M{i}",
                role="operating",
                values={"FY2024": D(s)},
                latest_value=D(s),
                latest_share=D(s) / D(100),
                change=None,
            )
            for i, s in enumerate(shares)
        ],
        operating_total=D(100),
        eliminations=None,
        unallocated=None,
        after_eliminations=None,
        reported_total=None,
        difference=None,
        difference_note=None,
        notes=[],
    )


def test_concentration_uses_reported_shares_and_says_when_a_breakdown_is_missing():
    seg = SegmentAnalysis(
        ticker="X",
        basis="annual",
        available_bases=["annual"],
        currency="USD",
        source_note=None,
        breakdowns=[_breakdown("segment", [50, 30, 20])],
        mappings=[],
        exposures=[],
        notes=[],
        method_version="1.0.0",
    )
    customer = {
        "relationship": "customer",
        "counterparty_name": "Big Co",
        "reported_share": D("0.18"),
        "share_basis": "revenue",
        "fiscal_period": "FY2024",
        "source_url": "https://x.test/10k",
    }
    obs = {o.id: o for o in ae.concentration_observations(seg, [customer])}
    assert obs["top_segment"].value == D("0.5") and obs["top_segment"].detail.startswith("M0")
    assert obs["effective_segments"].value == D("2.6316")  # 1 / (0.25 + 0.09 + 0.04)
    assert obs["top_geography"].value is None and "no geographic" in obs["top_geography"].reason
    assert (
        obs["top_customer"].value == D("0.18")
        and obs["top_customer"].source == "https://x.test/10k"
    )
    assert ae.concentration_observations(seg, [])[-1].value is None
