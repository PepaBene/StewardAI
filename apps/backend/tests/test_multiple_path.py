"""Earnings-multiple scenario method (insight plan I6): golden hand calculation, the chain,
reverse solves and the round trip of saved assumptions."""

import datetime as dt
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from steward.company import multiple_path as mp


def _a(**kw: object) -> mp.MultipleAssumptions:
    base: dict[str, object] = {
        "name": "base",
        "years": 3,
        "revenue_growth": [D("0.10")],
        "net_margin": [D("0.10")],
        "exit_pe": D(20),
        "share_change": [D(0)],
        "discount_rate": D("0.10"),
    }
    base.update(kw)
    return mp.MultipleAssumptions(**base)


def test_golden_hand_calculation() -> None:
    # 100 revenue growing 10 % for 3 years = 133.1; 10 % margin = 13.31 earnings; 10 shares = 1.331
    # EPS; times 20 = 26.62 exit price; over 1.1^3 = 20.00 today.
    a = _a()
    rows = mp.project(a, D(100), D(10))
    assert rows[-1].revenue == D("133.100")
    assert rows[-1].eps == D("1.3310")
    assert mp.value_of(a, D(100), D(10)) == pytest.approx(D(20))


def test_buybacks_raise_eps_and_value() -> None:
    flat = mp.value_of(_a(), D(100), D(10))
    buyback = mp.value_of(_a(share_change=[D("-0.02")]), D(100), D(10))
    assert buyback > flat
    assert buyback == pytest.approx(D(20) / (D("0.98") ** 3), rel=D("1e-9"))


def test_per_year_paths_and_rate_build() -> None:
    a = _a(
        revenue_growth=[D("0.2"), D("0.1"), D(0)],
        net_margin=[D("0.1"), D("0.1"), D("0.2")],
        discount_rate=None,
    )
    rows = mp.project(a, D(100), D(1))
    assert [r.revenue for r in rows] == [D("120.0"), D("132.00"), D("132.00")]
    assert rows[-1].earnings == D("26.4000")
    assert a.rate() == D("0.04") + D("0.05")
    assert "risk-free" in a.rate_build()


def test_lengths_and_bounds_are_validated() -> None:
    with pytest.raises(ValidationError):
        _a(revenue_growth=[D("0.1"), D("0.1")])
    with pytest.raises(ValidationError):
        _a(net_margin=[D("1.2")])
    with pytest.raises(ValidationError):
        _a(years=2)


def test_chain_prints_each_step() -> None:
    a = _a()
    rows = mp.project(a, D(100), D(10))
    steps = mp.chain(a, D(100), "fiscal year", D(10), rows, D(25), "USD")
    labels = [s.label for s in steps]
    assert labels[0] == "Starting revenue"
    assert "Revenue year 3" in labels
    assert labels[-2] == "Value per share today"
    assert steps[-2].value == D("20.00")
    assert labels[-1] == "Upside to today's price"
    assert steps[-1].value == pytest.approx(D("-0.2"))


def test_reverse_solves_return_the_price() -> None:
    a = _a()
    pe, reason = mp.solve_exit_pe(a, D(100), D(10), D(30))
    assert reason is None and pe == pytest.approx(D(30))
    g, reason = mp.solve_growth(a, D(100), D(10), D(20))
    assert reason is None and g == pytest.approx(D("0.10"), abs=D("1e-9"))
    check = mp.value_of(a.model_copy(update={"revenue_growth": [g]}), D(100), D(10))
    assert check == pytest.approx(D(20))


def test_reverse_solve_states_why_when_impossible() -> None:
    a = _a(net_margin=[D("-0.1")])
    pe, reason = mp.solve_exit_pe(a, D(100), D(10), D(30))
    assert pe is None and reason and "not positive" in reason
    g, reason = mp.solve_growth(_a(), D(100), D(10), D(10_000))
    assert g is None and reason and "doubling" in reason


def test_round_trip_of_saved_payload() -> None:
    req = mp.MultipleRequest(scenarios=[_a(), _a(name="bull", exit_pe=D(25))], selected="base")
    payload = mp.saved_payload(req)
    back = mp.MultipleRequest(
        scenarios=[mp.MultipleAssumptions(**s) for s in payload["scenarios"]],  # type: ignore[attr-defined]
        selected=payload["selected"],
    )
    assert back == req
    assert mp.request_hash(back, "h") == mp.request_hash(req, "h")
    assert mp.request_hash(req, "h") != mp.request_hash(req, "other statements")


def test_drift_flags_age() -> None:
    saved = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    out = mp.drift(
        [_a()], "base", saved, None, stale_after_days=90, band=D("0.05"), today=dt.date(2026, 6, 1)
    )
    assert out.stale and not out.drifting
    assert out.age_days == 151
    fresh = mp.drift(
        [_a()], "base", saved, None, stale_after_days=90, band=D("0.05"), today=dt.date(2026, 1, 15)
    )
    assert not fresh.stale and fresh.reasons == []
