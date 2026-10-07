"""P2 gap closure, pure logic: note buckets into validators, segment metrics and cross-axis rows,
the formula table with citations, and the fact-to-evidence payload."""

import datetime as dt
from decimal import Decimal as D

from steward.disclosure.fact_evidence import build_fact_evidence, find_ix_fact
from steward.disclosure.financials import (
    SegmentFact,
    SegmentMember,
    build_segment_bridge,
    build_segment_report,
    detect_axis_overlap,
    metric_for,
)
from steward.disclosure.formulas import FORMULA_TABLE_VERSION, FormulaInput, compute_formulas
from steward.disclosure.note_store import (
    StoredNote,
    build_note_fact_rows,
    note_checks,
)
from steward.disclosure.note_tables import (
    extract_debt_maturities,
    extract_lease_maturities,
    extract_repurchases,
)
from steward.disclosure.parse import IxContext, IxFact, ParsedDocument

GEO, PROD = "StatementGeographicalAxis", "ProductOrServiceAxis"


# ------------------------------------------------------------------------------ note checks
def stored(text_rows: list[dict]) -> list[StoredNote]:
    return [
        StoredNote(
            r["note"]["schedule"],
            r["note"]["bucket"],
            r["value"],
            r["note"]["status"],
            tuple(r["note"]["reasons"]),
            r["note"]["dash"] if "dash" in r["note"] else False,
            r["note"],
            "A1",
            i + 1,
        )
        for i, r in enumerate(text_rows)
    ]


DEBT = "(in millions)\n2026 100\n2027 200\nThereafter 700\nTotal 1,000\n"
LEASE = (
    "(in millions)\n2026 40\n2027 40\nThereafter 20\nTotal lease payments 100\n"
    "Less: imputed interest (10)\nPresent value of lease liabilities 90\n"
)
REPO = (
    "Class A common stock (in millions; shares in thousands)\n"
    "| Period | Total Number of Shares Purchased | Average Price Paid per Share | Total Amount |\n"
    "| October 1 - October 31, 2025 | 2 | $ 150 | $ 0.3 |\n"
)


def test_stored_buckets_feed_all_three_validators() -> None:
    rows = build_note_fact_rows(
        debt=extract_debt_maturities(DEBT),
        lease=extract_lease_maturities(LEASE),
        repurchases=extract_repurchases(REPO),
    )
    checks = {c.name: c for c in note_checks(stored(rows))}
    assert checks["debt_maturities"].status == "pass"
    assert checks["lease_schedule"].status == "pass"
    assert checks["repurchase"].status == "pass"
    assert "October 1 - October 31, 2025" in checks["repurchase"].detail


def test_wrong_debt_total_fails_and_review_rows_block() -> None:
    bad = extract_debt_maturities(DEBT.replace("Total 1,000", "Total 1,200"))
    (c,) = note_checks(stored(build_note_fact_rows(debt=bad)))
    assert c.status == "fail"
    no_unit = extract_debt_maturities(DEBT.replace("(in millions)\n", ""))
    (c2,) = note_checks(stored(build_note_fact_rows(debt=no_unit)))
    assert c2.status == "needs_review" and "unit not stated" in c2.detail
    assert note_checks([]) == []  # no note data: the reader reports a not-run entry itself


def test_dash_bucket_stays_null_and_is_named_in_the_check() -> None:
    text = "(in millions)\n2026 100\n2027 —\nThereafter 900\nTotal 1,000\n"
    rows = build_note_fact_rows(debt=extract_debt_maturities(text))
    dash = next(r for r in rows if r["note"]["bucket"] == "year_2")
    assert dash["value"] is None and dash["value_text"] == "—"
    (c,) = note_checks(stored(rows))
    assert c.status == "pass" and "year_2" in c.detail


# ----------------------------------------------------------------------------- segment metrics
def test_segment_metrics_are_bridged_separately_with_eliminations_kept() -> None:
    facts = [
        SegmentFact("revenue", (GEO,), ("US",), D(700)),
        SegmentFact("revenue", (GEO,), ("EMEA",), D(300)),
        SegmentFact("operating_profit", (GEO,), ("US",), D(200)),
        SegmentFact("operating_profit", (GEO,), ("EMEA",), D(100)),
        SegmentFact("operating_profit", (GEO,), ("Corporate unallocated",), D(-50)),
        SegmentFact("assets", (GEO,), ("US",), D(5000)),
        SegmentFact("capex", (GEO,), ("US",), D(40)),
    ]
    report = build_segment_report(
        {"revenue": D(1000), "operating_profit": D(250), "assets": D(6000), "capex": None}, facts
    )
    by = {m.metric: m for m in report.metrics}
    assert by["revenue"].bridge.axes[0].reconciles  # type: ignore[union-attr]
    profit = by["operating_profit"].bridge.axes[0]  # type: ignore[union-attr]
    assert profit.members_sum == D(300) and profit.eliminations == D(-50) and profit.reconciles
    assert by["assets"].bridge.axes[0].unallocated == D(1000)  # type: ignore[union-attr]
    # revenue members never leak into profit, and vice versa
    assert {m.value for m in by["revenue"].bridge.axes[0].members} == {D(700), D(300)}  # type: ignore[union-attr]
    # no capex total: members shown unbridged and the gap named
    assert by["capex"].bridge is None and by["capex"].unbridged[0].members_sum == D(40)
    assert any("no consolidated capex" in n for n in by["capex"].notes)


def test_cross_axis_rows_are_their_own_breakdown_and_overlap_is_named() -> None:
    facts = [
        SegmentFact("revenue", (GEO,), ("US",), D(600)),
        SegmentFact("revenue", (GEO,), ("EMEA",), D(400)),
        SegmentFact("revenue", (PROD,), ("Widgets",), D(1000)),
        SegmentFact("revenue", (GEO, PROD), ("US", "Widgets"), D(300)),
        SegmentFact("revenue", (GEO, PROD), ("EMEA", "Widgets"), D(200)),
    ]
    (rev,) = build_segment_report({"revenue": D(1000)}, facts).metrics
    assert [(c.axes, c.members, c.value) for c in rev.cross_axis] == [
        ((GEO, PROD), ("EMEA", "Widgets"), D(200)),
        ((GEO, PROD), ("US", "Widgets"), D(300)),
    ]
    by_axis = {a.axis: a for a in rev.bridge.axes}  # type: ignore[union-attr]
    assert by_axis[GEO].members_sum == D(1000)  # cross cells are not added to the axis
    assert by_axis[PROD].members_sum == D(1000)
    assert any("overlap" in n and "never added" in n for n in rev.notes)
    assert any("cross-axis rows are a separate breakdown" in n for n in rev.notes)


def test_overlap_detection_and_legacy_bridge_note() -> None:
    assert detect_axis_overlap([GEO]) == []
    assert detect_axis_overlap([GEO, PROD])
    bridge = build_segment_bridge(
        D(100), [SegmentMember(GEO, "US", D(100)), SegmentMember(PROD, "W", D(100))]
    )
    assert any("overlap" in n for n in bridge.notes)


# --------------------------------------------------------------------------------- formulas
def fi(metric: str, value: str | None, acc: str = "A1", fid: int = 1) -> tuple[str, FormulaInput]:
    return metric, FormulaInput(metric, None if value is None else D(value), acc, fid)


def inputs(**over: str | None) -> dict[str, FormulaInput]:
    base: dict[str, str | None] = {
        "revenue": "1000",
        "gross_profit": "400",
        "operating_income": "200",
        "net_income": "150",
        "pretax_income": "200",
        "income_tax": "40",
        "operating_cash_flow": "180",
        "total_equity": "700",
        "total_debt": "400",
        "cash": "100",
        "debt_due_year_1": "50",
    }
    base.update(over)
    return dict(fi(k, v, fid=i) for i, (k, v) in enumerate(base.items(), 1) if v is not None)


def by_key(results):  # type: ignore[no-untyped-def]
    return {r.formula: r for r in results}


def test_formula_values_inputs_and_coverage() -> None:
    r = by_key(compute_formulas(inputs()))
    assert r["gross_margin"].value == D("0.4")
    assert r["operating_margin"].value == D("0.2")
    assert r["net_margin"].value == D("0.15")
    # tax rate 40/200 = 0.2; NOPAT 160; invested capital 700+400-100 = 1000
    assert r["roic"].value == D("0.16")
    assert r["debt_maturity_coverage"].value == D(2)
    assert r["cash_conversion"].value == D("1.2")
    roic = r["roic"]
    assert roic.coverage_present == roic.coverage_required == 6
    assert roic.table_version == FORMULA_TABLE_VERSION
    assert {i.metric: (i.accession, i.fact_id) for i in roic.inputs}["income_tax"][0] == "A1"


def test_missing_input_is_null_with_coverage_not_zero() -> None:
    r = by_key(compute_formulas(inputs(cash=None)))
    assert r["roic"].value is None and "cash" in (r["roic"].reason or "")
    assert (r["roic"].coverage_present, r["roic"].coverage_required) == (5, 6)
    assert r["debt_maturity_coverage"].value is None
    assert r["gross_margin"].value == D("0.4")  # unaffected formulas still compute


def test_meaningless_denominators_and_tax_rule() -> None:
    r = by_key(compute_formulas(inputs(net_income="-10", debt_due_year_1="0", revenue="0")))
    assert r["cash_conversion"].value is None and "not positive" in (
        r["cash_conversion"].reason or ""
    )
    assert r["debt_maturity_coverage"].value is None
    assert r["gross_margin"].value is None
    odd = by_key(compute_formulas(inputs(income_tax="150")))  # 75% effective rate
    assert odd["roic"].value is None and "outside" in (odd["roic"].reason or "")
    stated = by_key(
        compute_formulas(inputs(income_tax=None, pretax_income=None), stated_tax_rate=D("0.25"))
    )
    assert stated["roic"].value == D("0.15") and "stated tax rate" in stated["roic"].rule
    assert stated["roic"].coverage_required == 4


def test_new_taxonomy_metrics_map() -> None:
    assert metric_for("StockholdersEquity").key == "total_equity"  # type: ignore[union-attr]
    assert metric_for("ifrs-full:ProfitLossBeforeTax").key == "pretax_income"  # type: ignore[union-attr]


# ----------------------------------------------------------------------------- fact evidence
CTX = {
    "c1": IxContext("c1", dt.date(2025, 1, 1), dt.date(2025, 12, 31), None, ()),
    "c2": IxContext("c2", dt.date(2025, 1, 1), dt.date(2025, 12, 31), None, ((GEO, "US"),)),
}


def ix(name: str, ctx: str, value: str, start: int, end: int) -> IxFact:
    return IxFact(
        name,
        ctx,
        "usd",
        "-6",
        6,
        "",
        value,
        D(value.replace(",", "")) * 10**6,
        None,
        "f1",
        False,
        start,
        end,
    )


def row(**over: object) -> dict:
    base: dict = {
        "id": 77,
        "issuer_id": 5,
        "concept": "Revenues",
        "period_start": dt.date(2025, 1, 1),
        "period_end": dt.date(2025, 12, 31),
        "value": D(1_000_000_000),
        "value_text": None,
        "unit": "USD",
        "currency": "USD",
        "basis": "as_filed",
        "dimensions": {"members": []},
        "known_at": dt.datetime(2026, 2, 20, tzinfo=dt.UTC),
    }
    base.update(over)
    return base


def test_evidence_payload_carries_span_legacy_link_and_scale() -> None:
    doc = ParsedDocument(
        text="x" * 100,
        facts=[
            ix("us-gaap:Revenues", "c1", "1,000", 10, 18),
            ix("us-gaap:Revenues", "c2", "600", 30, 35),
        ],
        contexts=dict(CTX),
    )
    found = find_ix_fact(doc, row())
    assert found is not None and (found.start, found.end) == (10, 18)
    p = build_fact_evidence(row(), found)
    assert p.span == (10, 18) and p.gaps == ()
    assert (p.legacy_table, p.legacy_key) == ("financial_statement_fact", "77")
    assert p.evidence == {
        "method": "ixbrl",
        "table_name": "financial_statement_fact",
        "row_ref": "77",
    }
    assert p.observation["metric"] == "revenue" and p.observation["scale"] == 6
    assert (
        p.observation["original_string"] == "1,000" and p.observation["value_state"] == "reported"
    )
    assert p.observation["dimensions"] == {} and p.observation["is_instant"] is False


def test_dimension_and_value_must_match_and_ambiguity_gives_a_gap() -> None:
    doc = ParsedDocument(
        text="x" * 100,
        facts=[
            ix("us-gaap:Revenues", "c1", "1,000", 10, 18),
            ix("us-gaap:Revenues", "c2", "600", 30, 35),
        ],
        contexts=dict(CTX),
    )
    seg = row(dimensions={"members": [[GEO, "US"]]}, value=D(600_000_000))
    f = find_ix_fact(doc, seg)
    assert f is not None and f.start == 30
    assert build_fact_evidence(seg, f).observation["dimensions"] == {GEO: "US"}
    assert find_ix_fact(doc, row(value=D(5))) is None  # same concept and period, other value
    dup = ParsedDocument(
        text="x" * 100,
        facts=[
            ix("us-gaap:Revenues", "c1", "1,000", 10, 18),
            ix("us-gaap:Revenues", "c1", "1,000", 50, 58),
        ],
        contexts=dict(CTX),
    )
    assert find_ix_fact(dup, row()) is None
    gap = build_fact_evidence(row(), None)
    assert gap.span is None and gap.gaps and gap.observation["scale"] == 0


def test_unmapped_concept_and_instant_and_not_disclosed() -> None:
    p = build_fact_evidence(
        row(concept="acme:AdjustedRevenue", period_start=None, value=None, value_text=None), None
    )
    assert p.observation["metric"] == "concept:acme:AdjustedRevenue"
    assert p.observation["is_instant"] is True and p.observation["value_state"] == "not_disclosed"
