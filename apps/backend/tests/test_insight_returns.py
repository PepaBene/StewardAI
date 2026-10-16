# ruff: noqa: E501
"""Insight plan I11: realised/unrealised split, per-holding positions and diversification.
Expected figures are worked out by hand in the comments."""

import datetime as dt
from decimal import Decimal

from steward.performance.attribution import AccountDay, PositionDay, Trade, attribute
from steward.performance.ledger import Kind, LedgerEntry, Split
from steward.performance.lots import RealizedGain
from steward.performance.positions import PositionValue, build_positions
from steward.xray.diversification import (
    INDUSTRY_UNKNOWN,
    geography,
    sankey,
    verdict,
)
from steward.xray.leaves import Leaf, LeafSet
from steward.xray.metrics import XrayConcentration, XrayItem
from steward.xray.views import XrayBucket, XrayView

D = Decimal
D0, D1 = dt.date(2026, 1, 5), dt.date(2026, 1, 6)


def test_price_effect_splits_into_realised_and_unrealised() -> None:
    # 10 units at 100; 4 sold at 110 during the day; 6 left, closing at 105.
    # Price effect = 6*105 - 10*100 + 4*110 = 70: realised 4*(110-100) = 40, unrealised 6*5 = 30.
    report = attribute(
        [AccountDay(D0, D(1000)), AccountDay(D1, D(1070))],
        [PositionDay(D0, "A", D(10), D(100), D(1), D(1000)), PositionDay(D1, "A", D(6), D(105), D(1), D(630))],
        [Trade(D1, "A", D(-4), D(110))],
        [],
        [],
    )  # fmt: skip
    assert report is not None
    h = report.holdings[0]
    assert (h.price, h.realised, h.unrealised) == (D(70), D(40), D(30))
    assert (report.realised, report.unrealised) == (D(40), D(30))


def test_units_bought_and_sold_in_one_interval_use_the_buy_price() -> None:
    # Bought 5 at 50 and sold 5 at 56 between snapshots: realised 5*(56-50) = 30, nothing open.
    report = attribute(
        [AccountDay(D0, D(0)), AccountDay(D1, D(30))],
        [],
        [Trade(D1, "B", D(5), D(50)), Trade(D1, "B", D(-5), D(56))],
        [],
        [],
        fx_fallback=lambda _t, _d: D(1),
    )
    assert report is not None
    assert report.holdings[0].realised == D(30)
    assert report.holdings[0].unrealised == D(0)


def entry(i: int, day: str, kind: Kind, qty: str, price: str | None, cash: str, ticker: str = "A") -> LedgerEntry:  # fmt: skip
    return LedgerEntry(
        id=str(i), occurred_at=dt.datetime.fromisoformat(day + "T12:00:00+00:00"), kind=kind,
        ticker=ticker, quantity=D(qty), price=D(price) if price else None, gross=abs(D(cash)),
        fees=D(0), cash_effect=D(cash), currency="EUR",
    )  # fmt: skip


def test_closed_position_lists_its_rows_with_a_split_and_an_annual_rate() -> None:
    ledger = [
        entry(
            1, "2024-01-02", Kind.BUY, "20", "50", "-1000"
        ),  # 10 bought at 100 before a 2:1 split
        entry(2, "2024-07-01", Kind.DIVIDEND, "0", None, "20"),
        entry(3, "2025-01-02", Kind.SELL, "-20", "60", "1200"),
    ]
    gains = [RealizedGain("s3", "A", dt.date(2025, 1, 2), D(20), D(1200), D(1200), D(0), D(1000), D(200), 366, ("l1",))]  # fmt: skip
    (p,) = build_positions(
        ledger, [Split("A", dt.date(2024, 3, 1), D(2))], gains, {}, dt.date(2026, 1, 1)
    )
    assert p.status == "closed"
    assert [t.kind for t in p.transactions] == ["buy", "split", "dividend", "sell"]
    assert p.transactions[1].running_quantity == D(20)
    assert (p.invested, p.proceeds, p.dividends, p.realised_gain) == (
        D(1000),
        D(1200),
        D(20),
        D(200),
    )
    assert p.total_gain == D(220)
    # -1000 on 2024-01-02, +20 on 2024-07-01, +1200 on 2025-01-02 (366 days): about 22 % a year.
    assert p.annualised_return is not None
    assert abs(p.annualised_return - D("0.2196")) < D("0.002")


def test_open_position_adds_todays_value_and_short_holds_get_no_annual_rate() -> None:
    ledger = [entry(1, "2025-12-20", Kind.BUY, "10", "10", "-100", "B")]
    now = {"B": PositionValue(D(10), D(110), D(100), dt.date(2026, 1, 1))}
    (p,) = build_positions(ledger, [], [], now, dt.date(2026, 1, 1))
    assert (p.status, p.unrealised_gain, p.total_gain) == ("open", D(10), D(10))
    assert p.annualised_return is None
    assert p.annualised_reason == "held 12 days; an annual rate needs 30"


def test_sold_out_position_with_buys_before_the_ledger_is_closed() -> None:
    # Only the sale is stored and the broker no longer holds it: closed, with the gap stated.
    ledger = [entry(1, "2025-06-02", Kind.SELL, "-5", "60", "300", "C")]
    (p,) = build_positions(ledger, [], [], {}, dt.date(2026, 1, 1))
    assert p.status == "closed" and p.quantity == D(0)
    assert any("before the first stored fill" in w for w in p.warnings)


def leaf(w: float, sector: str | None, industry: str | None, name: str, special: str | None = None) -> Leaf:  # fmt: skip
    return Leaf(channel_kind="direct", channel_key="direct", channel_label="Direct", weight=w,
                asset_class="cash" if special else "equity", special=special, issuer_key=name,
                issuer_label=name, name=name, sector=sector, industry=industry)  # fmt: skip


def test_sankey_flows_add_up_and_name_the_residual() -> None:
    leaves = [
        leaf(0.30, "Information Technology", "Semiconductors", "ASML"),
        leaf(0.20, "Information Technology", "Software", "SAP"),
        leaf(0.25, "Health Care", None, "Fund row"),
        leaf(0.25, None, None, "CASH", special="__cash__"),
    ]
    nodes, links, residual = sankey(LeafSet(leaves, {}, 1.0, 0.0, "EUR"))
    sectors = {n.label: n.weight for n in nodes if n.column == 0}
    assert sectors == {"Information Technology": 0.5, "Health Care": 0.25}
    assert any(n.label == INDUSTRY_UNKNOWN and n.kind == "unclassified" for n in nodes)
    # Every sector's outflow equals its weight; the residual holds the cash.
    out = {s: sum(lk.weight for lk in links if lk.source == f"s:{s}") for s in sectors}
    assert out == sectors
    assert [(r.weight, r.kind) for r in residual] == [(0.25, "residual")]


def test_geography_states_its_coverage_and_map_rule() -> None:
    def bucket(key: str, w: float, kind: str = "classified") -> XrayBucket:
        return XrayBucket(key=key, label=key, kind=kind, weight=w, value_base=0, direct_weight=w,
                          fund_weight=0, contributors=[], basis={"reported": w})  # fmt: skip

    view = XrayView(dimension="revenue_country", label="", group="", description="",
                    buckets=[bucket("US", 0.3), bucket("DE", 0.1), bucket("__unclassified__", 0.4, "unclassified")],
                    total_weight=1, reconciles=True, classified_weight=0.4, equity_weight=0.8,
                    bases={}, warnings=[])  # fmt: skip
    g = geography(view)
    # Known 0.4 of 0.8 equity: 50 %.
    assert g.coverage == 0.5
    assert g.coverage_text.startswith("Revenue geography known for 50% of the equity")
    assert [r.country for r in g.rows] == ["US", "DE"]
    assert abs(g.rows[0].share_of_known - 0.75) < 1e-9
    assert g.map_allowed is False


def test_concentration_verdict_sentence() -> None:
    items = [XrayItem(key=k, label=k, weight=w, share_of_known=w) for k, w in (("ASML", 0.2), ("SAP", 0.1), ("NOVO", 0.05), ("X", 0.05))]  # fmt: skip
    level = XrayConcentration(level="issuer", label="Issuer", basis="issuer", items=4, known_weight=0.4,
                              unknown_weight=0.0, unclassified_weight=0.0, top_items=items,
                              largest=items[0], top10_weight=0.4, top10_of_known=1.0, hhi=0.15625,
                              effective_n=6.4, warnings=[])  # fmt: skip
    v = verdict(level)
    # Top three: 0.2 + 0.1 + 0.05 = 35 %; effective holdings 6.4 < 10 is concentrated.
    assert v.band == "concentrated"
    assert v.sentence == (
        "Your three largest issuers are 35% of the portfolio and it is as concentrated as 6 equal "
        "holdings; the largest is ASML at 20%."
    )
    assert verdict(None).band == "unknown"
