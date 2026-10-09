"""Valuation scenarios: explicit assumptions, ranges, sensitivity and the reverse DCF."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from steward.analytics.ratios import RatioValue
from steward.company.ratios import Market, build_ratios
from steward.company.statements import build_view
from steward.company.valuation import (
    Assumptions,
    ValuationRequest,
    build_valuation,
    default_scenarios,
    implied_growth_value,
)
from tests.test_company_statements import FY23, FY24, bal, cf, inc

D = Decimal
S, E = FY24


def rows() -> list[dict]:
    out = []
    for (s, e), rev in ((FY23, "900"), (FY24, "1000")):
        out += [
            inc("revenue", e, rev, s),
            inc("net_income", e, "100", s),
            inc("weighted_shares_diluted", e, "10", s),
            cf("cash_from_operations", e, "130", s),
            cf("capex", e, "30", s),
            bal("cash", e, "50"),
            bal("long_term_debt_total", e, "150"),
        ]
    return out


def market(price: str = "20") -> Market:
    today = dt.date.today()
    return Market([(today - dt.timedelta(days=1), D(price))], "USD", D("10"))


def build(request: ValuationRequest | None = None, price: str = "20"):
    annual = build_view("AAA", rows(), units=1)
    ratios = build_ratios("AAA", annual, None, market(price), sector="Industrials")
    return build_valuation(
        "AAA", annual, None, market(price), ratios, request or ValuationRequest()
    )


def test_defaults_are_the_owners_to_edit_and_ordered() -> None:
    v = build()
    values = {s.name: s.value_per_share.value for s in v.scenarios}
    assert set(values) == {"bear", "base", "bull"}
    assert values["bear"] is not None and values["bull"] is not None
    assert values["bear"] < values["base"] < values["bull"]
    assert v.range_low == values["bear"] and v.range_high == values["bull"]
    assert "no single value" in v.note


def test_margin_of_safety_only_against_the_selected_scenario() -> None:
    none_selected = build()
    assert all(s.margin_of_safety is None for s in none_selected.scenarios)
    chosen = build(ValuationRequest(selected="base"))
    base = next(s for s in chosen.scenarios if s.selected)
    assert base.margin_of_safety is not None
    assert [s.name for s in chosen.scenarios if s.margin_of_safety is not None] == ["base"]


def test_changing_an_assumption_moves_the_value_predictably() -> None:
    low = Assumptions(name="base", growth=[D("0.03")], discount_rate=D("0.10"))
    high = Assumptions(name="base", growth=[D("0.08")], discount_rate=D("0.10"))
    a = build(ValuationRequest(scenarios=[low])).scenarios[0].value_per_share.value
    b = build(ValuationRequest(scenarios=[high])).scenarios[0].value_per_share.value
    higher_rate = (
        build(ValuationRequest(scenarios=[low.model_copy(update={"discount_rate": D("0.12")})]))
        .scenarios[0]
        .value_per_share.value
    )
    assert a is not None and b is not None and higher_rate is not None
    assert b > a > higher_rate


def test_sensitivity_grid_centre_equals_the_scenario() -> None:
    v = build(ValuationRequest(selected="base"))
    centre = next(c for c in v.sensitivity if c.growth_shift == 0 and c.rate_shift == 0)
    base = next(s for s in v.scenarios if s.name == "base")
    assert len(v.sensitivity) == 25
    assert centre.value == base.value_per_share.value


def test_reverse_dcf_round_trips_to_the_price() -> None:
    v = build(ValuationRequest(selected="base"))
    g = implied_growth_value(v).value
    assert g is not None
    base = next(s for s in v.scenarios if s.name == "base")
    a = base.assumptions.model_copy(update={"growth": [g]})
    back = build(ValuationRequest(scenarios=[a])).scenarios[0].value_per_share.value
    assert back is not None and abs(back - D("20")) < D("0.05")


def test_missing_free_cash_flow_is_unavailable_not_zero() -> None:
    no_cf = [r for r in rows() if r["line_item"] not in ("cash_from_operations", "capex")]
    annual = build_view("AAA", no_cf, units=1)
    v = build_valuation("AAA", annual, None, market(), None, ValuationRequest())
    assert all(s.value_per_share.value is None for s in v.scenarios)
    assert "free cash flow" in v.scenarios[0].value_per_share.warnings[0]


def test_non_positive_start_needs_an_explicit_base() -> None:
    neg = [
        dict(r, value=D("10")) if r["line_item"] == "cash_from_operations" else r for r in rows()
    ]
    annual = build_view("AAA", neg, units=1)
    v = build_valuation("AAA", annual, None, market(), None, ValuationRequest())
    assert all(s.value_per_share.value is None for s in v.scenarios)
    a = Assumptions(name="base", growth=[D("0.05")], base_fcf=D("80"))
    ok = build_valuation("AAA", annual, None, market(), None, ValuationRequest(scenarios=[a]))
    assert ok.scenarios[0].value_per_share.value is not None


def test_growth_path_must_match_the_years() -> None:
    with pytest.raises(ValueError, match="growth has one value"):
        Assumptions(name="x", years=5, growth=[D("0.1"), D("0.1")])


def test_discount_rate_build_up_is_stated() -> None:
    v = build()
    assert "risk-free" in v.scenarios[1].discount_rate_build and v.scenarios[1].discount_rate == D(
        "0.09"
    )
    assert isinstance(implied_growth_value(v), RatioValue)
    assert default_scenarios(build_view("AAA", rows(), units=1))[1].name == "base"


def test_net_debt_is_withheld_when_cash_is_filed_without_a_debt_concept():
    from steward.company import valuation as val

    filed = {"cash": D("50"), "total_assets": D("500"), "equity": D("300")}
    value, note = val._net_debt(filed)
    assert value is None and "net debt withheld" in note  # absent debt is not zero debt
    assert val._net_debt({"cash": D("50")})[0] is None  # no balance sheet at all stays unavailable
    assert val._net_debt({**filed, "long_term_debt_total": D("20")})[0] == D("-30")


def test_dcf_per_share_is_withheld_without_net_debt_unless_the_owner_overrides():
    from steward.company import valuation as val

    a = Assumptions(name="x", growth=[D("0.05")])
    res, _ = val._dcf(a, D("100"), None, D(0), D("10"))
    assert res.value is None and any("net debt unknown" in w for w in res.warnings)
    over = a.model_copy(update={"net_debt": D("-20")})
    res, _ = val._dcf(over, D("100"), None, D(0), D("10"))
    assert res.value is not None
