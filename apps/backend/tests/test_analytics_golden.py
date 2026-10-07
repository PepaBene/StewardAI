"""Phase 0 golden examples: expected figures come from tests/golden/analytics/cases.json,
derived by hand, not from the calculators under test."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from steward.analytics import exposure, listings, pit, returns, risk, thresholds, valuation
from steward.analytics.contract import Status, input_hash, rounded
from steward.analytics.ratios import (
    accruals_ratio,
    cash_runway_years,
    ev_to_ebit,
    fcf_yield,
    momentum_12_1,
)

CASES = json.loads((Path(__file__).parent / "golden/analytics/cases.json").read_text())
D = Decimal


def day(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def dec(text: str | None) -> Decimal | None:
    return None if text is None else D(text)


@pytest.mark.parametrize("case", CASES["price_return"], ids=lambda c: c["name"])
def test_price_return(case: dict) -> None:
    cur = case["currency"]
    if cur == "GBX":
        assert returns.to_major_unit(D(case["start"][1]), cur) == (D(case["major"][0]), "GBP")
    out = returns.price_return(
        (day(case["start"][0]), D(case["start"][1])),
        (day(case["end"][0]), D(case["end"][1])),
        currency=cur,
        splits=[returns.Split(day(d), D(r)) for d, r in case["splits"]],
    )
    assert out.value == D(case["expected"])
    assert out.status is Status.COMPLETE


def test_price_return_blocked_by_unresolved_split_and_mixed_units() -> None:
    a, b = (day("2024-01-02"), D(10)), (day("2024-02-02"), D(11))
    assert returns.price_return(a, b, currency="USD", unresolved_split=True).value is None
    assert returns.price_return(a, b, currency="GBX", end_currency="GBP").value is None


@pytest.mark.parametrize("case", CASES["total_return"], ids=lambda c: c["name"])
def test_total_return(case: dict) -> None:
    out = returns.total_return(
        [(day(d), D(p)) for d, p in case["closes"]],
        [returns.Dividend(day(d), D(a)) for d, a in case["dividends"]],
        [returns.Split(day(d), D(r)) for d, r in case["splits"]],
        distributions_complete=case["complete"],
    )
    assert out.value == dec(case["expected"])
    if not case["complete"]:
        assert out.status is Status.PARTIAL and "price return" in out.warnings[0]


@pytest.mark.parametrize("case", CASES["twr"], ids=lambda c: c["name"])
def test_twr(case: dict) -> None:
    days = [returns.DayValue(day(d), D(v), D(f)) for d, v, f in case["days"]]
    out = returns.twr(days)
    assert out.value == dec(case["expected"])
    if "naive_end_over_start" in case:
        naive = days[-1].value / days[0].value - 1
        assert naive == D(case["naive_end_over_start"]) != out.value


def test_twr_withheld_when_material_value_is_missing() -> None:
    days = [
        returns.DayValue(day("2024-01-01"), D(100)),
        returns.DayValue(day("2024-01-02"), D(101)),
    ]
    out = returns.twr(days, material_missing_weight=D("0.05"))
    assert out.value is None and out.status is Status.PARTIAL
    assert not thresholds.full_portfolio_return_allowed(D("0.05"))[0]


@pytest.mark.parametrize("case", CASES["xirr"], ids=lambda c: c["name"])
def test_xirr(case: dict) -> None:
    out = returns.xirr([(day(d), D(a)) for d, a in case["flows"]])
    assert out.value == dec(case["expected"])


@pytest.mark.parametrize("case", CASES["cagr"], ids=lambda c: c["name"])
def test_cagr(case: dict) -> None:
    out = returns.cagr(D(case["start"]), D(case["end"]), D(case["years"]))
    assert out.value == dec(case["expected"])


def test_attribution_reconciles_with_disclosed_residual() -> None:
    c = CASES["attribution"]
    comps = {k: D(v) for k, v in c["components"].items()}
    bridge = returns.attribution_bridge(D(c["start"]), D(c["end"]), D(c["net_flows"]), comps)
    assert bridge.value == D(c["expected_gain"])
    assert bridge.residual == D(c["expected_residual"])
    assert sum(bridge.components.values()) + bridge.residual == bridge.value
    partial = returns.attribution_bridge(D(1000), D(1500), D(300), comps, unattributed_value=D(150))
    assert partial.status is Status.PARTIAL and partial.coverage is not None
    assert partial.coverage.fraction == D("0.9")


def test_look_through_keeps_unknown_and_paths() -> None:
    c = CASES["look_through"]
    funds = [
        exposure.FundPosition(
            f["fund_id"],
            D(f["market_value"]),
            day(f["as_of"]),
            None if f["weights"] is None else {k: D(v) for k, v in f["weights"].items()},
            D(f.get("cash_weight", "0")),
            D(f.get("derivative_weight", "0")),
        )
        for f in c["funds"]
    ]
    lt = exposure.look_through({k: D(v) for k, v in c["direct"].items()}, funds)
    e = c["expected"]
    assert lt.total_value == D(e["total"])
    assert lt.exposures["AAA"].total_value == D(e["AAA"])
    assert lt.exposures["BBB"].total_value == D(e["BBB"])
    assert lt.cash_value == D(e["cash"]) and lt.unknown_value == D(e["unknown"])
    assert lt.unavailable_funds == e["unavailable_funds"]
    assert lt.exposures["AAA"].paths == {k: D(v) for k, v in e["AAA_paths"].items()}
    assert rounded(lt.weight("AAA"), "fraction") == rounded(D(e["AAA_weight"]), "fraction")
    buckets = (
        sum(x.total_value for x in lt.exposures.values())
        + lt.cash_value
        + lt.derivative_value
        + lt.unknown_value
    )
    assert buckets == lt.total_value
    assert lt.status is Status.PARTIAL


@pytest.mark.parametrize("case", CASES["overlap"], ids=lambda c: c["name"])
def test_overlap(case: dict) -> None:
    def weights(raw: dict | None) -> dict[str, Decimal] | None:
        return None if raw is None else {k: D(v) for k, v in raw.items()}

    a_on, b_on = (day(case["as_of"][0]), day(case["as_of"][1])) if "as_of" in case else (None, None)
    out = exposure.fund_overlap(weights(case["a"]), weights(case["b"]), as_of_a=a_on, as_of_b=b_on)
    assert out.value == dec(case["expected"])
    for word in case.get("warns", []):
        assert any(word in w for w in out.warnings)
    if "covered" in case:
        assert [sum(weights(case[k]).values()) for k in ("a", "b")] == [
            D(x) for x in case["covered"]
        ]
    if case["expected"] is None:
        assert out.status is Status.UNAVAILABLE


@pytest.mark.parametrize("case", CASES["concentration"], ids=lambda c: c["name"])
def test_concentration(case: dict) -> None:
    out = exposure.concentration(
        {k: D(v) for k, v in case["weights"].items()}, unknown_weight=D(case["unknown"])
    )
    assert out.hhi == D(case["hhi"])
    if "effective" in case:
        assert rounded(out.effective_count, "ratio") == rounded(D(case["effective"]), "ratio")
    assert (D(case["unknown"]) > 0) == any("unknown" in w for w in out.warnings)


def test_portfolio_earnings_yield_excludes_not_zeroes() -> None:
    c = CASES["earnings_yield"]
    items = [
        exposure.YieldInput(i["key"], D(i["weight"]), dec(i["ey"]), i.get("excluded"))
        for i in c["items"]
    ]
    out = exposure.portfolio_earnings_yield(items, includes_look_through=False)
    assert out.value == rounded(D(c["expected_yield"]), "ratio")
    assert exposure.pe_from_yield(D("0.05") / D("0.7")) == D(c["expected_pe"])
    assert out.coverage is not None and out.coverage.fraction == D("0.7")
    assert {e.subject for e in out.explain.exclusions} == {"C", "CASH"}  # type: ignore[union-attr]
    assert exposure.pe_from_yield(D("-0.01")) is None


def test_dcf_and_reverse_dcf() -> None:
    c = CASES["dcf"]
    inputs = valuation.DcfInputs(
        [D(x) for x in c["fcf"]],
        D(c["rate"]),
        D(c["terminal_growth"]),
        D(c["net_debt"]),
        D(c["minorities"]),
        D(c["shares"]),
    )
    assert valuation.enterprise_value(
        inputs.forecast_fcf, inputs.discount_rate, inputs.terminal_growth
    ) == pytest.approx(D(c["expected_ev"]))
    assert valuation.dcf_per_share(inputs).value == D(c["expected_per_share"])
    assert valuation.margin_of_safety(D(c["expected_per_share"]), D(c["price"])) == D(
        c["expected_mos"]
    )
    r = c["reverse"]
    out = valuation.reverse_dcf_growth(
        price=D(r["price"]),
        base_fcf=D(r["base_fcf"]),
        years=r["years"],
        discount_rate=inputs.discount_rate,
        terminal_growth=inputs.terminal_growth,
        net_debt=inputs.net_debt,
        minorities=inputs.minorities,
        diluted_shares=inputs.diluted_shares,
    )
    assert out.value is not None and abs(out.value - D(r["expected_growth"])) < D("1e-6")
    no_shares = valuation.DcfInputs(
        inputs.forecast_fcf, inputs.discount_rate, inputs.terminal_growth, D(0), D(0), None
    )
    assert valuation.dcf_per_share(no_shares).value is None
    missing_debt = valuation.DcfInputs(
        inputs.forecast_fcf, inputs.discount_rate, inputs.terminal_growth, None, D(0), D(80)
    )
    assert valuation.dcf_per_share(missing_debt).value is None


def test_risk_statistics() -> None:
    c = CASES["risk"]
    series = [D("0.01"), D("-0.01")] * 30
    exp = c["alternating_60"]
    assert abs(risk.volatility(series).value - D(exp["expected_vol"])) < D("1e-6")  # type: ignore[operator]
    assert abs(risk.downside_deviation(series).value - D(exp["expected_downside"])) < D("1e-6")  # type: ignore[operator]
    assert abs(risk.max_drawdown(series).value - D(exp["expected_drawdown"])) < D("1e-6")  # type: ignore[operator]
    t = c["tail_250"]
    tail = [D(t["big"])] * t["n_big"] + [D(t["mid"])] * t["n_mid"]
    tail += [D(t["rest"])] * (250 - len(tail))
    var, es = risk.var_es(tail)
    assert var.value == D(t["expected_var"])
    assert abs(es.value - D(t["expected_es"])) < D("1e-6")  # type: ignore[operator]
    assert risk.var_es(tail[:100])[0].value is None  # below the minimum sample
    assert risk.volatility(series[:10]).value is None


def test_beta_of_a_levered_series_is_two() -> None:
    bench = [D("0.01"), D("-0.02"), D("0.005")] * 20
    assert risk.beta([2 * b for b in bench], bench).value == D("2")
    assert risk.beta(bench[:-1], bench).value is None


def test_point_in_time_restatement_and_macro_revision() -> None:
    def obs(rows: list[dict]) -> list[pit.Observation]:
        return [
            pit.Observation(
                r["key"],
                day(r["period"]),
                D(r["value"]),
                dt.datetime.fromisoformat(r["known_at"]),
                r["revision"],
            )
            for r in rows
        ]

    for rows, expectations in (
        (CASES["pit"]["revenue"], CASES["pit"]["expectations"]),
        (CASES["pit"]["macro"], CASES["pit"]["macro_expectations"]),
    ):
        for cutoff, expected in expectations:
            known = pit.as_known_at(obs(rows), dt.datetime.fromisoformat(cutoff))
            value = next(iter(known.values())).value if known else None
            assert value == dec(expected)


@pytest.mark.parametrize("case", CASES["adr"], ids=lambda c: c["name"])
def test_adr_ratio(case: dict) -> None:
    out = listings.adr_parity_premium(
        D(case["adr_price"]),
        dec(case["ordinary_per_adr"]),
        D(case["local_price"]),
        dec(case["usd_per_local"]),
    )
    assert out.value == dec(case["expected"])


def test_peer_thresholds() -> None:
    p = CASES["peers"]
    assert thresholds.peer_median_allowed(p["comparable"])[0] is p["enough"]
    assert thresholds.peer_median_allowed(5)[0] is p["five_comparable_enough"]


def test_rounding_policy_and_input_hash() -> None:
    assert rounded(D("0.125"), "money") == D("0.12")  # banker's rounding
    assert rounded(None, "money") is None
    assert input_hash({"a": D("1.0"), "b": [day("2024-01-01")]}) == input_hash(
        {"b": [day("2024-01-01")], "a": D("1")}
    )


def test_screen_ratio_rules() -> None:
    assert fcf_yield(D(100), D(-30), D(1000)).value == D("0.07")
    assert fcf_yield(D(100), D(30), D(0)).reason.startswith("n/m")  # type: ignore[union-attr]
    assert fcf_yield(None, D(30), D(1000)).value is None
    assert ev_to_ebit(D(1000), D(-5)).value is None
    assert accruals_ratio(D(120), D(80), D(900), D(1100)).value == D("0.04")
    assert cash_runway_years(D(300), D(-100)).value == D(3)
    assert cash_runway_years(D(300), D(50)).value is None  # self-funding: no runway invented
    closes = [(dt.date(2023, 10, 31) + dt.timedelta(days=i), D(100) + i) for i in range(400)]
    mom = momentum_12_1(closes, dt.date(2024, 10, 31))
    assert mom.value is not None and mom.value > 0
    assert momentum_12_1(closes[-100:], dt.date(2024, 10, 31)).value is None
