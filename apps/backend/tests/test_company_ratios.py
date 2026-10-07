"""The company ratio set and page: n/m instead of numbers, financials, growth, peers."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from steward.analytics.ratio_set import COMPANY_RATIOS, Period
from steward.company.ratios import (
    Market,
    build_peer_values,
    build_ratios,
    peer_stat,
)
from steward.company.statements import build_view
from tests.test_company_statements import bal, cf, inc, rows

D = Decimal
END = dt.date(2024, 12, 31)


def period(f: dict[str, str | None], **kw: object) -> Period:
    return Period(end=END, f={k: (D(v) if v is not None else None) for k, v in f.items()}, **kw)  # type: ignore[arg-type]


def test_every_ratio_has_a_formula_period_rule_and_exclusions() -> None:
    assert len(COMPANY_RATIOS) >= 50
    for r in COMPANY_RATIOS.values():
        assert r.formula and r.period_rule and r.exclusions, r.id


def test_margins_and_returns_from_figures() -> None:
    p = period(
        {
            "revenue": "1000",
            "gross_profit_resolved": "400",
            "net_income": "100",
            "total_assets": "2000",
        },
        prior={"total_assets": D("1000")},
    )
    assert COMPANY_RATIOS["gross_margin"].fn(p).value == D("0.4")
    assert COMPANY_RATIOS["net_margin"].fn(p).value == D("0.1")
    assert COMPANY_RATIOS["roa"].fn(p).value == D("100") / D("1500")


def test_not_meaningful_is_a_reason_not_a_number() -> None:
    p = period(
        {
            "revenue": "0",
            "net_income": "-10",
            "gross_profit_resolved": "0",
            "equity": "-5",
            "market_cap": None,
        }
    )
    assert COMPANY_RATIOS["net_margin"].fn(p).value is None
    assert "n/m" in (COMPANY_RATIOS["net_margin"].fn(p).reason or "")
    assert COMPANY_RATIOS["debt_to_equity"].fn(p).value is None
    assert COMPANY_RATIOS["pe"].fn(p).value is None  # no market cap: missing, never zero


def test_a_bank_gets_nm_for_gross_margin_and_leverage() -> None:
    p = period({"revenue": "1000", "gross_profit_resolved": "400"}, financial=True)
    for rid in ("gross_margin", "current_ratio", "net_debt_to_ebitda", "ev_to_ebit"):
        r = COMPANY_RATIOS[rid].fn(p)
        assert r.value is None and "banks and insurers" in (r.reason or ""), rid


def test_valuation_uses_the_market_cap_and_negative_earnings_are_nm() -> None:
    p = period(
        {"net_income": "100", "equity": "500", "cash_from_operations": "150", "capex": "-50"},
        market_cap=D("2000"),
    )
    assert COMPANY_RATIOS["pe"].fn(p).value == D("20")
    assert COMPANY_RATIOS["pb"].fn(p).value == D("4")
    assert COMPANY_RATIOS["p_fcf"].fn(p).value == D("20")
    loss = period({"net_income": "-100"}, market_cap=D("2000"))
    assert COMPANY_RATIOS["pe"].fn(loss).value is None


def test_growth_needs_positive_ends_and_forward_pe_needs_an_estimate() -> None:
    now = {"revenue": D("1210")}
    p = Period(end=END, f=now, back={1: {"revenue": D("1100")}, 3: {"revenue": D("-5")}})
    assert round(COMPANY_RATIOS["revenue_cagr_1y"].fn(p).value or D(0), 4) == D("0.1")
    assert COMPANY_RATIOS["revenue_cagr_3y"].fn(p).value is None
    assert "tested forward estimate" in (COMPANY_RATIOS["forward_pe"].fn(p).reason or "")


def test_altman_is_unavailable_with_the_reason() -> None:
    r = COMPANY_RATIOS["altman_z"].fn(period({"total_assets": "1"}))
    assert r.value is None and "retained earnings" in (r.reason or "")


def test_peer_median_is_withheld_below_five_and_percentile_is_ranked() -> None:
    few = peer_stat(D("0.2"), {f"P{i}": D("0.1") for i in range(4)}, "sector")
    assert few.median is None and few.withheld_reason and few.count == 4
    peers = {f"P{i}": D(i) / D(10) for i in range(1, 8)}
    stat = peer_stat(D("0.45"), peers, "sector")
    assert stat.median == D("0.4") and stat.percentile == D("0.571429")  # 4 of 7 at or below
    unknown = peer_stat(D("0.45"), {**peers, "X": None}, "sector")
    assert unknown.members == 8 and unknown.count == 7


def test_ratios_view_ties_to_statement_cells() -> None:
    data = [
        *rows(),
        cf("cash_from_operations", END, "200", dt.date(2024, 1, 1)),
        cf("capex", END, "50", dt.date(2024, 1, 1)),
    ]
    annual = build_view("AAA", data, units=1)
    v = build_ratios("AAA", annual, None, Market(), sector="Industrials")
    margin = next(r for g in v.groups for r in g.ratios if r.id == "net_margin")
    assert margin.latest and margin.latest.value == D("0.125")  # 150 / 1200
    assert margin.inputs and margin.inputs[0].accession and margin.inputs[0].filing_url
    assert len(margin.points) == 2
    fcf = next(r for g in v.groups for r in g.ratios if r.id == "fcf_margin")
    assert fcf.latest and fcf.latest.value == D("150") / D("1200")
    pe = next(r for g in v.groups for r in g.ratios if r.id == "pe")
    assert pe.latest and pe.latest.value is None and pe.latest.reason  # no price stored


def test_stale_peers_are_excluded_with_the_reason() -> None:
    fresh = build_view("P1", rows(), units=1)
    old_rows = [
        r
        for r in rows()
        if r["period_end"].year == 2023 or r["period_end"] == dt.date(2023, 12, 31)
    ]
    stale = build_view("P2", old_rows, units=1)
    values, excluded = build_peer_values(
        {"P1": (fresh, Market(), None, None), "P2": (stale, Market(), None, None)}, END
    )
    assert "P1" in values and "P2" not in values
    assert excluded and excluded[0].ticker == "P2" and "before the company's" in excluded[0].reason


def test_unknown_inputs_stay_unknown_when_balances_are_missing() -> None:
    annual = build_view(
        "AAA",
        [inc("revenue", END, "100", dt.date(2024, 1, 1)), bal("total_assets", END, "500")],
        units=1,
    )
    v = build_ratios("AAA", annual, None, Market(), sector="Industrials")
    roa = next(r for g in v.groups for r in g.ratios if r.id == "roa")
    assert roa.latest and roa.latest.value is None
