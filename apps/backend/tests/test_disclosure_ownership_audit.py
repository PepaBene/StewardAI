"""Deep-audit fixes D39 to D60 (ownership, insiders, dependencies): hand-worked cases."""

import datetime as dt
from decimal import Decimal as D

from steward.disclosure import insiders as ins
from steward.disclosure import look_through as lt
from steward.disclosure.concentration_extract import extract
from steward.disclosure.dependencies import Edge, period_rank, shock_scenario
from steward.disclosure.ownership import (
    Filing13F,
    LaneEntry,
    build_lane,
    effective_reports,
    one_stake_per_position,
)
from steward.disclosure.pdmr_activity import dedupe


def at(m: int, d: int, h: int = 12) -> dt.datetime:
    return dt.datetime(2026, m, d, h, tzinfo=dt.UTC)


def f13(acc: str, kind: str, amendment: str | None, known: dt.datetime) -> Filing13F:
    return Filing13F(
        acc,
        "0000000001",
        dt.date(2026, 3, 31),
        known.date(),
        known,
        kind,
        amendment,  # type: ignore[arg-type]
    )


# D39 / D56 -------------------------------------------------------------------


def test_an_amendment_with_no_type_leaves_a_report_with_no_applied_filing() -> None:
    reports = effective_reports([f13("A", "13F-HR/A", None, at(5, 1))], at(6, 1))
    (rep,) = reports.values()
    assert rep.accessions == () and any("amendment type missing" in f for f in rep.flags)


def test_same_moment_restatement_is_applied_after_the_original() -> None:
    both = [f13("Z", "13F-HR", None, at(5, 1)), f13("A", "13F-HR/A", "RESTATEMENT", at(5, 1))]
    (rep,) = effective_reports(both, at(6, 1)).values()
    assert rep.accessions == ("A",)  # accession order alone would have let the HR win


# D41 -------------------------------------------------------------------------


def test_parent_and_subsidiary_reporting_one_stake_count_once() -> None:
    rows = [
        ("parent", D(1000), D("5.1"), dt.date(2026, 1, 5), at(1, 9)),
        ("sub", D(1000), D("5.1"), dt.date(2026, 1, 5), at(1, 10)),
        ("other", D(400), D("2.0"), dt.date(2026, 2, 1), at(2, 3)),
    ]
    assert one_stake_per_position(rows) == {1, 2}  # the later-known one of the pair, and the other


def test_old_stakes_are_flagged_not_dropped() -> None:
    old = LaneEntry("Fund", D(10), None, dt.date(2024, 1, 1), dt.date(2024, 1, 5), at(1, 5))
    lane = build_lane("control_13dg", [old], D(1000), as_of=dt.date(2026, 6, 1))
    assert len(lane.entries) == 1 and any("18 months" in n for n in lane.notes)


# D43 -------------------------------------------------------------------------


def test_segment_table_rows_are_not_customer_drafts() -> None:
    table = "Revenue by segment\n| | 2025 | 2024 |\n| Automotive | 30% | 28% |\n"
    out = extract(table)
    assert out.drafts == [] and out.skipped


def test_supplier_table_keeps_its_own_denominator_and_relationship() -> None:
    table = "Supplier concentration (% of purchases)\n| | 2025 |\n| Northwind Traders | 22% |\n"
    (draft,) = extract(table).drafts
    assert draft.relationship == "supplier" and draft.denominator == "purchases"


# D46 / D47 -------------------------------------------------------------------


def rel(shape: str | None, share: str, period: str = "FY2025", name: str = "X") -> lt.Relationship:
    return lt.Relationship(
        "AAA", "customer", name.lower(), name, False, D(share), "issuer_revenue", period,
        at(3, 1), None, "total_revenue", None, None, shape,
    )  # fmt: skip


def test_a_group_claim_is_not_the_largest_single_customer() -> None:
    assert rel("group", "0.45").single_party is False
    assert rel("named", "0.45").single_party is True
    assert rel(None, "0.45").single_party is True


def test_period_labels_and_dates_compare_as_periods() -> None:
    assert period_rank(None, "FY2025") > period_rank(dt.date(2024, 12, 31), "FY2024")
    got = lt.latest_relationships(
        [
            lt.Relationship("AAA", "customer", "x", "X", False, D("0.2"), None, "FY2024",
                            at(1, 1), None, "total_revenue", dt.date(2024, 12, 31)),
            lt.Relationship("AAA", "customer", "x", "X", False, D("0.3"), None, "FY2025",
                            at(2, 1), None, "total_revenue", None),
        ]
    )  # fmt: skip
    assert [r.fiscal_period for r in got] == ["FY2025"]


# D54 -------------------------------------------------------------------------


def tx(acc: str, known: dt.datetime, shares: str, price: str) -> ins.InsiderTx:
    return ins.InsiderTx(
        accession=acc, row_index=0, insider="jo", name="Jo", role="officer",
        tx_date=dt.date(2026, 2, 2), filed=known.date(), known_at=known,
        economic_class="open_market_buy", derivative=False, shares=D(shares), price=D(price),
    )  # fmt: skip


def test_a_corrected_notice_replaces_the_original_instead_of_adding_to_it() -> None:
    original, correction = (
        tx("pdmr:1", at(2, 3), "1000", "5.00"),
        tx("pdmr:2", at(2, 9), "900", "5.10"),
    )
    assert [t.accession for t in dedupe([original, correction], {"pdmr:2"})] == ["pdmr:2"]
    # without the amendment flag two different trades both stay
    assert len(dedupe([original, correction])) == 2


# D57 -------------------------------------------------------------------------


def test_position_change_uses_the_class_in_force() -> None:
    t = ins.InsiderTx(
        accession="a", row_index=0, insider="jo", name="Jo", role="officer",
        tx_date=dt.date(2026, 2, 2), filed=dt.date(2026, 2, 3), known_at=at(2, 3),
        economic_class="open_market_buy", derivative=False, shares=D(100), price=D(10),
        shares_after=D(900),
    )  # fmt: skip
    assert ins.position_change(t) == D(100) / D(800) * 100  # a buy: held 800 before
    assert ins.position_change(t, "open_market_sell") == -D(100) / D(1000) * 100


# D60 -------------------------------------------------------------------------


def test_shock_counts_a_pair_once_when_reported_and_owner_weights_both_exist() -> None:
    edges = [
        Edge(1, "S", "C", D("0.4"), "confirmed", at(1, 1)),
        Edge(2, "S", "C", D("0.3"), "confirmed", at(1, 2), owner_assigned=True),
    ]
    out = shock_scenario(edges, "C", D("0.5"), at(3, 1))
    hit = {i.entity: i for i in out.impacts}["S"]
    assert hit.lower == D("0.4") * D("0.5")  # the reported 40 %, not 40 % + 30 %
