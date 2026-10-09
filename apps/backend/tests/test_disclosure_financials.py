"""P2.8: restatement, amendment, changed segment taxonomy, negative cash flow, unit multiplier,
fiscal-year change and a missing note, all against the pure statement logic."""

import datetime as dt
from decimal import Decimal as D

from steward.disclosure.financials import (
    ADAPTER_CONTRACTS,
    AdapterContract,
    FiledFact,
    FiledSegments,
    Repurchase,
    SegmentMember,
    build_segment_bridge,
    check_customer_share,
    check_debt_maturities,
    check_free_cash_flow,
    check_gross_profit,
    check_lease_schedule,
    check_repurchase,
    detect_recast,
    fiscal_year_changes,
    global_coverage_label,
    metric_by_key,
    metric_for,
    period_kind,
    release_gate,
    scale_value,
    statement_history,
)

UTC = dt.UTC


def at(y: int, m: int, d: int) -> dt.datetime:
    return dt.datetime(y, m, d, 23, 59, 59, tzinfo=UTC)


def test_taxonomy_maps_concepts_and_leaves_extensions_unmapped() -> None:
    assert metric_for("us-gaap:Revenues") is not None
    assert metric_for("RevenueFromContractWithCustomerExcludingAssessedTax").key == "revenue"  # type: ignore[union-attr]
    assert metric_for("acme:AdjustedRevenue") is None  # an extension is never guessed
    assert metric_by_key("cash").instant is True  # type: ignore[union-attr]
    assert metric_by_key("revenue").instant is False  # type: ignore[union-attr]


def test_period_kinds_keep_year_to_date_out_of_annual() -> None:
    assert period_kind(None, dt.date(2025, 12, 31)) == "instant"
    assert period_kind(dt.date(2025, 1, 1), dt.date(2025, 12, 31)) == "annual"
    assert period_kind(dt.date(2025, 1, 1), dt.date(2025, 3, 31)) == "quarter"
    assert period_kind(dt.date(2025, 1, 1), dt.date(2025, 9, 30)) == "nine_months"
    assert period_kind(dt.date(2025, 1, 1), dt.date(2025, 6, 30)) == "half"
    assert (
        period_kind(dt.date(2025, 1, 1), dt.date(2025, 7, 31)) == "transition"
    )  # a short year after a year-end change


def test_fiscal_year_change_is_detected() -> None:
    ends = [
        dt.date(2023, 12, 31),
        dt.date(2024, 12, 31),
        dt.date(2025, 6, 30),
        dt.date(2026, 6, 30),
    ]
    assert fiscal_year_changes(ends) == [(dt.date(2024, 12, 31), dt.date(2025, 6, 30))]
    assert fiscal_year_changes(ends[:2]) == []


def test_unit_multiplier_is_applied_once() -> None:
    assert scale_value("950", 6) == D(950_000_000)
    assert scale_value("12.4", 3) == D(12_400)
    assert scale_value(0, 6) == D(0)


def test_gross_profit_arithmetic_and_rounding_tolerance() -> None:
    assert check_gross_profit(D(1000), D(600), D(400)).usable
    # filers round to millions: a 0.2% difference passes at the default tolerance
    assert check_gross_profit(D(1_000_000_000), D(600_000_000), D(400_800_000)).usable
    bad = check_gross_profit(D(1000), D(600), D(450))
    assert bad.status == "fail" and not bad.usable
    assert (
        check_gross_profit(D(1000), None, D(400)).status == "needs_review"
    )  # missing piece is not a pass


def test_negative_cash_flow_and_capex_sign() -> None:
    assert check_free_cash_flow(
        D(-50), D(20), D(-70)
    ).usable  # negative operating cash flow is legitimate
    flipped = check_free_cash_flow(D(500), D(-100), D(600))
    assert flipped.status == "needs_review" and "sign" in flipped.detail


def test_debt_maturities_reconcile_to_total_net_of_discount() -> None:
    buckets = {"2027": D(100), "2028": D(200), "thereafter": D(700)}
    assert check_debt_maturities(buckets, D(1000)).usable
    assert check_debt_maturities(buckets, D(990), unamortized_discount=D(10)).usable
    assert check_debt_maturities(buckets, D(800)).status == "fail"
    assert check_debt_maturities({}, D(800)).status == "needs_review"  # missing note
    assert check_debt_maturities({"2027": D(-5)}, D(0)).status == "needs_review"


def test_lease_schedule_needs_imputed_interest() -> None:
    sched = {"2027": D(60), "2028": D(60), "thereafter": D(80)}
    assert check_lease_schedule(sched, D(40), D(160)).usable
    assert check_lease_schedule(sched, None, D(160)).status == "needs_review"
    assert check_lease_schedule(sched, D(40), D(120)).status == "fail"


def test_repurchase_needs_period_and_class() -> None:
    good = Repurchase(D(500), D(10), dt.date(2025, 1, 1), dt.date(2025, 12, 31), "Class A")
    assert check_repurchase(good).usable
    assert (
        check_repurchase(Repurchase(D(500), D(10), None, None, "Class A")).status == "needs_review"
    )
    assert (
        check_repurchase(
            Repurchase(D(500), D(10), dt.date(2025, 1, 1), dt.date(2025, 12, 31), None)
        ).status
        == "needs_review"
    )


def test_customer_share_needs_denominator_and_period() -> None:
    assert check_customer_share(D(23), "total revenue", "FY2025").usable
    assert check_customer_share(D(23), None, "FY2025").status == "needs_review"
    assert check_customer_share(D(230), "total revenue", "FY2025").status == "needs_review"


def test_release_gate_blocks_on_anything_not_passed() -> None:
    ok, blockers = release_gate(
        [check_gross_profit(D(10), D(6), D(4)), check_gross_profit(D(10), None, D(4))]
    )
    assert not ok and len(blockers) == 1
    assert release_gate([check_gross_profit(D(10), D(6), D(4))]) == (True, [])


def test_segment_bridge_never_adds_axes_and_shows_the_remainder() -> None:
    members = [
        SegmentMember("geography", "US", D(600)),
        SegmentMember("geography", "EMEA", D(300)),
        SegmentMember("geography", "Other", D(50)),
        SegmentMember("product", "Widgets", D(700)),
        SegmentMember("product", "Services", D(250)),
        SegmentMember("product", "Eliminations", D(-10), eliminations=True),
        SegmentMember("product", "Services plus widgets", D(950), subtotal=True),
    ]
    bridge = build_segment_bridge(D(950), members)
    axes = {a.axis: a for a in bridge.axes}
    assert axes["geography"].members_sum == D(950) and axes["geography"].reconciles
    # product members 950, eliminations -10: off by +10, and it says so; subtotal not counted
    assert axes["product"].members_sum == D(950) and axes["product"].eliminations == D(-10)
    assert axes["product"].unallocated == D(10)
    assert any("not added together" in n for n in bridge.notes)
    assert any("subtotal members shown but not counted" in n for n in bridge.notes)


def test_segment_bridge_reports_a_gap_instead_of_hiding_it() -> None:
    bridge = build_segment_bridge(
        D(1000), [SegmentMember("segment", "A", D(400)), SegmentMember("segment", "B", D(300))]
    )
    (axis,) = bridge.axes
    assert not axis.reconciles and axis.unallocated == D(300)
    assert any("short of the reported total" in n for n in bridge.notes)


def test_changed_segment_taxonomy_is_flagged_not_rewritten() -> None:
    f1 = FiledSegments(at(2026, 2, 20), {"Widgets": D(900), "Services": D(100)})
    f2 = FiledSegments(at(2027, 2, 20), {"Widgets": D(900), "Services": D(100)})
    assert detect_recast([f1, f2]) == []
    redefined = FiledSegments(at(2027, 2, 20), {"Hardware": D(700), "Software": D(300)})
    assert "redefined" in detect_recast([f1, redefined])[0]
    recast = FiledSegments(at(2027, 2, 20), {"Widgets": D(850), "Services": D(150)})
    assert "recast" in detect_recast([f1, recast])[0]


def fact(
    value: str | None,
    known: dt.datetime,
    acc: str,
    amends: str | None = None,
    *,
    end: dt.date = dt.date(2025, 12, 31),
) -> FiledFact:
    return FiledFact(
        "revenue", dt.date(2025, 1, 1), end, None if value is None else D(value), known, acc, amends
    )


def test_history_shows_no_restatement_before_it_was_known() -> None:
    facts = [fact("1000", at(2026, 2, 20), "A1"), fact("950", at(2026, 5, 2), "A2", amends="A1")]
    before = statement_history(facts, at(2026, 1, 1))
    assert before == []
    between = statement_history(facts, at(2026, 3, 15))
    assert [(p.value, p.restated, p.original_value) for p in between] == [(D(1000), False, None)]
    after = statement_history(facts, at(2026, 6, 1))
    assert [(p.value, p.restated, p.original_value, p.accession) for p in after] == [
        (D(950), True, D(1000), "A2")
    ]


def test_history_keeps_year_to_date_out_of_the_annual_series() -> None:
    ytd = FiledFact(
        "revenue", dt.date(2025, 1, 1), dt.date(2025, 9, 30), D(700), at(2025, 11, 1), "Q3"
    )
    annual = fact("1000", at(2026, 2, 20), "A1")
    assert [p.accession for p in statement_history([ytd, annual], at(2026, 6, 1))] == ["A1"]
    assert [
        p.kind
        for p in statement_history([ytd, annual], at(2026, 6, 1), kinds=("nine_months", "annual"))
    ] == ["nine_months", "annual"]


def test_a_restated_value_equal_to_the_original_is_not_called_a_restatement() -> None:
    facts = [fact("1000", at(2026, 2, 20), "A1"), fact("1000", at(2026, 5, 2), "A2", amends="A1")]
    (p,) = statement_history(facts, at(2026, 6, 1))
    assert not p.restated and p.accession == "A2"


def test_a_filing_that_leaves_the_value_null_is_kept_as_null_not_zero() -> None:
    (p,) = statement_history([fact(None, at(2026, 2, 20), "A1")], at(2026, 6, 1))
    assert p.value is None


def test_no_global_label_until_coverage_is_measured() -> None:
    contracts = list(ADAPTER_CONTRACTS.values())
    assert all(c.coverage_measured is None for c in contracts)
    assert "not yet measured" in global_coverage_label(contracts)
    from dataclasses import replace

    measured = [replace(c, coverage_measured=0.8) for c in contracts]
    assert global_coverage_label(measured) == "Global fundamentals"
    partial = [replace(contracts[0], coverage_measured=0.9), *contracts[1:]]
    assert global_coverage_label(partial) == "Fundamentals for US"


def test_adapter_contracts_state_how_each_source_authenticates() -> None:
    assert ADAPTER_CONTRACTS["edinet"].authentication == "api_key"
    assert ADAPTER_CONTRACTS["sedarplus"].authentication == "session"
    assert isinstance(ADAPTER_CONTRACTS["sec"], AdapterContract)
