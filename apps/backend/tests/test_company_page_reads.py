"""Pure parts of the UI-redesign read endpoints: company summary helpers, market context maths,
fair-value assembly, drawdown and waterfall curves, and the allocation Lorenz curve and tree."""

import datetime as dt
from decimal import Decimal

import pytest

from steward.company.fair_values import build_fair_values
from steward.company.market_context import (
    build_context,
    compare_sentence,
    weekly_std,
    window_return,
)
from steward.company.quote import quote_from
from steward.company.summary import short_symbol
from steward.performance.curves import (
    drawdown_points,
    drawdown_response,
    waterfall_response,
)
from steward.performance.reports import (
    AttributionResponse,
    BridgeOut,
    Figure,
    PerformanceExplain,
)
from steward.portfolio.read import PortfolioHistory
from steward.reference.read import PricePoint
from steward.xray.allocation import build, lorenz_curve
from steward.xray.leaves import Leaf, LeafSet

D = Decimal
TODAY = dt.date(2026, 10, 1)


def _daily(start: dt.date, closes: list[float]) -> list[tuple[dt.date, float]]:
    return [(start + dt.timedelta(days=i), c) for i, c in enumerate(closes)]


def _weekly(n: int, step: float, end: dt.date = TODAY) -> list[tuple[dt.date, float]]:
    out = []
    price = 100.0
    for i in range(n):
        d = end - dt.timedelta(days=7 * (n - 1 - i))
        out.append((d, price))
        price *= 1 + step * (1 if i % 2 else -1)
    return out


# ---------------------------------------------------------------- summary / quote


def test_short_symbol_strips_trading212_suffixes() -> None:
    assert short_symbol("AAPL_US_EQ") == "AAPL"
    assert short_symbol("VOLVb_EQ") == "VOLV B"
    assert short_symbol("SAPd_EQ") == "SAP"
    assert short_symbol("plain") == "plain"


def test_quote_day_change_and_missing_data() -> None:
    q = quote_from([(dt.date(2026, 9, 29), D("100")), (dt.date(2026, 9, 30), D("102"))], "USD")
    assert (q.last, q.day_change, q.day_change_pct, q.reason) == (D("102"), D("2"), D("0.02"), None)
    pence = quote_from([(dt.date(2026, 9, 30), D("250"))], "GBp")
    assert pence.last == D("2.5") and pence.currency == "GBP" and pence.day_change is None
    assert pence.reason and "day change" in pence.reason
    none = quote_from([], "USD")
    assert none.last is None and none.reason == "no stored daily prices"
    assert quote_from([(dt.date(2026, 9, 30), D("1"))], None).reason


# ---------------------------------------------------------------- market context


def test_window_return_needs_enough_history_and_fresh_price() -> None:
    series = _daily(dt.date(2026, 9, 1), [100, 101, 102, 103, 104, 105, 106, 110])
    r, why = window_return(series, 7, dt.date(2026, 9, 9))
    assert why is None and r == pytest.approx(110 / 100 - 1)
    r, why = window_return(series, 365, dt.date(2026, 9, 9))
    assert r is None and why and "short of 365" in why
    r, why = window_return(series, 7, dt.date(2026, 12, 1))
    assert r is None and why and "last price is from" in why
    assert window_return([], 7)[0] is None


def test_weekly_std_needs_26_weekly_moves() -> None:
    std, why = weekly_std(_weekly(40, 0.02), TODAY)
    assert why is None and std is not None and 0.02 < std < 0.03
    std, why = weekly_std(_weekly(10, 0.02), TODAY)
    assert std is None and why and "needs 26" in why


def test_compare_sentence_is_deterministic() -> None:
    assert compare_sentence("Company", 0.042, "market", "past year") == (
        "Outperformed the market by 4.2% over the past year"
    )
    assert compare_sentence("Company", -0.1, "market", "past year") == (
        "Underperformed the market by 10.0% over the past year"
    )
    assert compare_sentence("Company", 0.00001, "market", "past year") == (
        "Company matched the market over the past year"
    )
    assert compare_sentence("Company", None, "market", "past year") is None


def test_context_with_missing_history_is_null_with_reasons() -> None:
    ctx = build_context("X", [], {}, None, TODAY)
    assert all(r.company is None and r.market is None for r in ctx.returns)
    assert ctx.return_sentence is None and ctx.volatility.company is None
    assert any("no market series" in r for r in ctx.reasons)
    assert any("industry 1y return" in r for r in ctx.reasons)


def test_context_builds_returns_volatility_and_sentence() -> None:
    own = _weekly(60, 0.03)
    market = _weekly(60, 0.01)
    peers = {"A": _weekly(60, 0.02), "B": _weekly(60, 0.02)}
    ctx = build_context("X", own, peers, market, TODAY, industry_label="Software")
    year = next(r for r in ctx.returns if r.window == "1y")
    assert year.company is not None and year.market is not None and year.industry is not None
    assert year.vs_market == pytest.approx(year.company - year.market)
    assert ctx.return_sentence and ctx.return_sentence.endswith("over the past year")
    v = ctx.volatility
    assert v.company and v.market and v.industry and v.company > v.market
    assert v.company_annualised == pytest.approx(v.company * 52**0.5)
    assert v.sentence and "more volatile than the market" in v.sentence
    assert ctx.industry_members == 2


# ---------------------------------------------------------------- fair values


def _point(basis: str, value: str, ccy: str = "USD") -> dict[str, object]:
    return {"basis": basis, "value": D(value), "currency": ccy, "as_of": dt.date(2026, 9, 30),
            "source": "valuation_tab", "method_version": "v1"}  # fmt: skip


def test_fair_values_gathers_every_source_and_picks_the_default() -> None:
    quote = quote_from([(dt.date(2026, 9, 30), D("100"))], "USD")
    consensus = {"as_of": dt.date(2026, 9, 28), "source": "finnhub", "analyst_count": 20,
                 "target_mean": D("130"), "target_median": D("128"), "target_high": D("160"),
                 "target_low": D("90"), "target_currency": "USD"}  # fmt: skip
    out = build_fair_values(
        "X", quote,
        [_point("dcf_default", "110"), _point("thesis_target", "120"), _point("consensus_target", "130")],
        consensus, lambda v, c: v,
    )  # fmt: skip
    by = {i.source: i for i in out.values}
    assert set(by) == {"dcf_default", "thesis_target", "analyst_mean", "analyst_low", "analyst_high"}
    assert out.default_source == "thesis_target" and by["thesis_target"].is_default
    assert by["analyst_mean"].upside == D("0.3") and by["analyst_low"].value == D("90")
    assert "Median 128" in by["analyst_mean"].basis_note
    assert out.price == D("100") and out.price_currency == "USD"


def test_fair_values_reports_missing_pieces_and_fx() -> None:
    quote = quote_from([(dt.date(2026, 9, 30), D("100"))], "EUR")
    out = build_fair_values("X", quote, [_point("dcf_base", "110", "USD")], None, lambda v, c: None)
    assert out.values[0].value is None and out.values[0].currency == "USD"
    assert any("no FX rate from USD to EUR" in r for r in out.reasons)
    assert any("no analyst targets" in r for r in out.reasons)
    assert out.default_source is None
    empty = build_fair_values("X", quote, [], None, lambda v, c: v)
    assert empty.values == [] and any("no saved scenario" in r for r in empty.reasons)


# ---------------------------------------------------------------- drawdown / waterfall


def test_drawdown_max_dates_and_recovery() -> None:
    s = _daily(dt.date(2026, 1, 1), [100, 110, 99, 88, 100, 111, 105])
    pts, worst, peak, trough, recovery = drawdown_points(s)
    assert worst == pytest.approx(-0.2)
    assert (peak, trough, recovery) == (dt.date(2026, 1, 2), dt.date(2026, 1, 4), dt.date(2026, 1, 6))
    assert pts[1].drawdown == 0 and pts[-1].drawdown == pytest.approx(105 / 111 - 1, abs=1e-6)
    flat = drawdown_points(_daily(dt.date(2026, 1, 1), [100, 101, 102]))
    assert flat[1] == 0.0 and flat[2:] == (None, None, None)
    assert drawdown_points([])[1] is None


def test_drawdown_response_without_history_says_why() -> None:
    hist = PortfolioHistory(as_of=None, base_currency=None, points=[], benchmark_symbol=None,
                            benchmark=[], gap="no portfolio snapshot yet", values_currency=None,
                            values=[])  # fmt: skip
    out = drawdown_response(hist, 365)
    assert out.points == [] and out.max_drawdown is None and out.reasons == ["no portfolio snapshot yet"]
    hist.points = [PricePoint(date=dt.date(2026, 1, 1), close=100), PricePoint(date=dt.date(2026, 1, 2), close=90)]
    ok = drawdown_response(hist, 365)
    assert ok.max_drawdown == pytest.approx(-0.1) and ok.recovery_date is None
    assert ok.current_drawdown == pytest.approx(-0.1)


def _attribution(bridge: BridgeOut | None, gap: str | None = None) -> AttributionResponse:
    return AttributionResponse(
        status="complete" if bridge else "unavailable", start=dt.date(2025, 10, 1), end=TODAY,
        base_currency="EUR", days=365, gap=gap, bridge=bridge, holdings=[], groups={},
        explain=PerformanceExplain(formula_version="v", formula=["f"]),
    )  # fmt: skip


def test_waterfall_cuts_the_attribution_bridge() -> None:
    bridge = BridgeOut(
        gain=Figure(value=150.0, unit="EUR", status="complete", method_version="v"),
        start_value=1000.0, end_value=1650.0, net_external_flows=500.0, net_trading=0.0,
        components={"price": 120.0, "income": 20.0, "fx": 25.0, "fees": -10.0},
        residual=-5.0, unattributed_tickers=[],
    )  # fmt: skip
    out = waterfall_response(_attribution(bridge))
    v = {s.key: s.value for s in out.steps}
    assert out.status == "complete" and out.reasons == []
    assert v["costs"] == -10.0 and v["net_deposits"] == 500.0 and v["unattributed"] == -5.0
    deltas = sum(s.value or 0 for s in out.steps if s.kind == "delta")
    assert v["start"] + deltas == pytest.approx(v["end"])


def test_waterfall_without_a_bridge_is_all_null_with_the_reason() -> None:
    out = waterfall_response(_attribution(None, "fewer than two daily snapshots are stored"))
    assert out.status == "unavailable" and all(s.value is None for s in out.steps)
    assert out.reasons == ["fewer than two daily snapshots are stored"]
    partial = BridgeOut(
        gain=Figure(value=1.0, unit="EUR", status="partial", method_version="v"),
        start_value=1.0, end_value=2.0, net_external_flows=0.0, net_trading=0.0,
        components={"price": 1.0}, residual=0.0, unattributed_tickers=["ZZZ"],
    )  # fmt: skip
    p = waterfall_response(_attribution(partial))
    assert p.status == "partial" and next(s for s in p.steps if s.key == "fx").value is None
    assert any("ZZZ" in r for r in p.reasons)


# ---------------------------------------------------------------- allocation


def test_lorenz_hhi_effective_n_and_gini() -> None:
    equal = lorenz_curve([1.0, 1.0, 1.0, 1.0])
    assert equal.hhi == pytest.approx(0.25) and equal.effective_n == pytest.approx(4)
    assert equal.gini == pytest.approx(0.0, abs=1e-9)
    assert (equal.points[0].weight_share, equal.points[-1].weight_share) == (0.0, 1.0)
    skew = lorenz_curve([97.0, 1.0, 1.0, 1.0])
    assert skew.top_weight_share == pytest.approx(0.97) and skew.gini and skew.gini > 0.6
    assert [p.holdings_share for p in skew.points] == [0.0, 0.25, 0.5, 0.75, 1.0]
    empty = lorenz_curve([])
    assert empty.points == [] and empty.hhi is None and empty.reason


def _leaf(key: str, weight: float, sector: str | None, industry: str | None) -> Leaf:
    return Leaf(channel_kind="direct", channel_key="direct", channel_label="Direct", weight=weight,
                asset_class="equity", issuer_key=key, issuer_label=key.upper(), sector=sector,
                industry=industry)  # fmt: skip


def test_allocation_tree_groups_sector_industry_holding() -> None:
    leaves = [
        _leaf("a", 0.3, "Tech", "Software"), _leaf("b", 0.2, "Tech", "Software"),
        _leaf("c", 0.1, "Tech", "Chips"), _leaf("d", 0.2, "Health", "Pharma"),
        _leaf("e", 0.1, None, None),
        Leaf(channel_kind="cash", channel_key="cash", channel_label="Cash", weight=0.1,
             asset_class="cash", special="CASH"),
    ]  # fmt: skip
    out = build("2026-10-01", LeafSet(leaves, {}, 1.0, 0.0, "EUR"), [])
    assert out.equity_weight == pytest.approx(0.9)
    tech = out.tree[0]
    assert tech.label == "Tech" and tech.weight == pytest.approx(0.6)
    assert [c.label for c in tech.children] == ["Software", "Chips"]
    assert [h.label for h in tech.children[0].children] == ["A", "B"]
    assert tech.children[0].children[0].share_of_equity == pytest.approx(0.3 / 0.9)
    assert out.tree[-1].unclassified and out.lorenz.holdings == 5
    assert sum(n.weight for n in out.tree) == pytest.approx(out.equity_weight)
    none = build("2026-10-01", LeafSet([], {}, 0.0, 0.0, None), [])
    assert none.tree == [] and none.reasons and none.lorenz.hhi is None
