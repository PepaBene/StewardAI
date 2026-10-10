"""Deep audit section 6 (risk, decisions, journal, release): D19, M1-M14, pure parts."""

import datetime as dt
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from steward.decision import covariance
from steward.decision import pretrade as pt
from steward.decision.service import PretradeRequest
from steward.decision.shocks import ShockSet
from steward.decision.whatif import CaseError, WhatIfCase, pro_rata_purchase
from steward.events.feed import Story


def test_pro_rata_purchase_of_a_held_ticker_nets_to_one_leg_and_keeps_the_total() -> None:
    values = {"A": 600.0, "B": 400.0}
    after, legs, factor = pro_rata_purchase("A", 100.0, values)
    assert factor == pytest.approx(0.9)
    assert sum(after.values()) == pytest.approx(sum(values.values()))
    assert after["A"] == pytest.approx(600 * 0.9 + 100)
    # A: trimmed by 60 and bought 100 = net buy 40; B: trimmed by 40. One leg per ticker.
    assert [(x.side, x.ticker, round(x.value_base, 6)) for x in legs] == [
        ("buy", "A", 40.0),
        ("sell", "B", 40.0),
    ]


def test_pro_rata_purchase_of_a_new_ticker_spends_once() -> None:
    after, legs, _ = pro_rata_purchase("NEW", 100.0, {"A": 600.0, "B": 400.0})
    assert after["NEW"] == pytest.approx(100.0)
    assert sum(after.values()) == pytest.approx(1000.0)
    assert sum(x.value_base for x in legs if x.side == "buy") == pytest.approx(100.0)
    with pytest.raises(CaseError):
        pro_rata_purchase("NEW", 1000.0, {"A": 600.0, "B": 400.0})


def test_what_if_split_shares_must_be_positive_and_add_to_at_most_one() -> None:
    for bad in ({"A": 2, "B": -1}, {"A": 0.7, "B": 0.5}, {"A": 0}):
        with pytest.raises(ValidationError):
            WhatIfCase(kind="contribution", amount=100, split=bad)
    ok = WhatIfCase(kind="contribution", amount=100, split={"A": 0.6, "B": 0.4})
    assert ok.split == {"A": 0.6, "B": 0.4}


def test_shock_moves_are_bounded_and_keys_normalised() -> None:
    with pytest.raises(ValidationError):
        ShockSet(fx={"USD": -1.5})
    with pytest.raises(ValidationError):
        ShockSet(sector={"Tech": 5})
    with pytest.raises(ValidationError):
        ShockSet(fx={"dollar": -0.1})
    shocks = ShockSet(fx={" usd ": -0.1}, country={"us": -0.2}, sector={" Technology ": -0.3})
    assert shocks.fx == {"USD": -0.1}
    assert shocks.country == {"US": -0.2}
    assert shocks.sector == {"Technology": -0.3}


def test_duplicate_legs_on_one_side_of_a_ticker_are_merged() -> None:
    req = PretradeRequest(
        trades=[
            pt.TradeLeg(side="sell", ticker="A", value_base=100),
            pt.TradeLeg(side="sell", ticker="A", value_base=50),
            pt.TradeLeg(side="buy", ticker="B", value_base=150),
        ]
    )
    assert [(t.side, t.ticker, t.value_base) for t in req.trades] == [
        ("sell", "A", 150),
        ("buy", "B", 150),
    ]


def _lot(gain: float, passed: bool = False) -> SimpleNamespace:
    return SimpleNamespace(gain=gain, passed=passed)


def test_switch_tax_is_carried_from_account_into_base_currency() -> None:
    # Account currency is USD, base is EUR at 0.5 EUR per USD: a 200 USD gain is 100 EUR.
    sell = pt.TradeLeg(side="sell", ticker="A", value_base=500)
    impact = SimpleNamespace(gross_proceeds=1000.0, lots=[_lot(200.0), _lot(900.0, passed=True)])
    ctx = SimpleNamespace(
        exemption_limit=1000.0,
        year_proceeds_so_far=5000.0,
        sale_within_test_exemption_ccy={"A": 1000.0},
        sale_base_per_account={"A": 0.5},
        rules=SimpleNamespace(short_term_tax_rate=0.3),
    )
    assert pt._tax_estimate(sell, impact, ctx) == pytest.approx(200 * 0.5 * 0.3)  # type: ignore[arg-type]


def _model(*symbols: str) -> covariance.CovModel:
    n = len(symbols)
    matrix = tuple(tuple(0.04 if i == j else 0.0 for j in range(n)) for i in range(n))
    return covariance.CovModel(symbols, matrix, 250, ())


def _risk_ctx(weights: dict[str, float], model_symbols: tuple[str, ...], unmodelled: list[str]):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        cov=_model(*model_symbols),
        weights_before=weights,
        weights_after=weights,
        unmodelled=unmodelled,
        rules=SimpleNamespace(risk_contribution_cap=0.5),
    )


def test_risk_budget_is_not_judged_when_the_model_covers_little_of_the_portfolio() -> None:
    weights = {"A": 0.1, "B": 0.1, "C": 0.8}
    leg = pt.TradeLeg(side="buy", ticker="A", value_base=10)
    out = pt._risk_budget(leg, _risk_ctx(weights, ("A", "B"), ["C"]))  # type: ignore[arg-type]
    assert out.status == "unavailable"
    assert "C" in out.detail
    full = pt._risk_budget(leg, _risk_ctx({"A": 0.5, "B": 0.5}, ("A", "B"), []))  # type: ignore[arg-type]
    assert full.status in ("pass", "flag")


def test_event_key_is_the_cluster_not_the_representative_story() -> None:
    def story(i: int, cluster: str | None) -> Story:
        return Story(
            id=i,
            headline="h",
            url="u",
            outlet=None,
            published_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
            event_type="m_and_a", ticker="X", tier=1, rumour=False, cluster_id=cluster,
        )  # fmt: skip

    assert story(1, "c1").event_key == story(2, "c1").event_key == "c1"
    assert story(7, None).event_key == "7"
