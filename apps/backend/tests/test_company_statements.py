"""The statements view built from synthetic stored facts: periods, restatements, derived
quarters, 'other' residuals and the balance check."""

# ruff: noqa: RUF001

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from itertools import count
from typing import Any

from steward.company.statements import build_view

D = Decimal
_ids = count(1)
KNOWN = dt.datetime(2025, 3, 1, tzinfo=dt.UTC)


def fact(
    item: str,
    concept: str,
    statement: str,
    end: dt.date,
    value: str,
    start: dt.date | None = None,
    *,
    known: dt.datetime = KNOWN,
    accession: str = "0000000000-25-000001",
) -> dict[str, Any]:
    return {
        "id": next(_ids),
        "concept": concept,
        "label": item,
        "statement": statement,
        "line_item": item,
        "period_start": start,
        "period_end": end,
        "fiscal_period": None,
        "value": D(value),
        "unit": "USD",
        "currency": "USD",
        "dimensions": {},
        "known_at": known,
        "accession": accession,
        "form": "10-K",
        "filing_url": f"https://sec.example/{accession}",
        "source_url": f"https://sec.example/{accession}",
        "validation": {"reconciled": True},
    }


def inc(item: str, end: dt.date, value: str, start: dt.date, **kw: Any) -> dict[str, Any]:
    return fact(item, f"us-gaap:{item}", "income", end, value, start, **kw)


def bal(item: str, end: dt.date, value: str, **kw: Any) -> dict[str, Any]:
    return fact(item, f"us-gaap:{item}", "balance_sheet", end, value, None, **kw)


def cf(item: str, end: dt.date, value: str, start: dt.date, **kw: Any) -> dict[str, Any]:
    return fact(item, f"us-gaap:{item}", "cash_flow", end, value, start, **kw)


FY23 = (dt.date(2023, 1, 1), dt.date(2023, 12, 31))
FY24 = (dt.date(2024, 1, 1), dt.date(2024, 12, 31))


def rows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for (s, e), rev, ni in ((FY23, "1000", "100"), (FY24, "1200", "150")):
        out += [
            inc("revenue", e, rev, s),
            inc("cost_of_revenue", e, str(int(rev) * 6 // 10), s),
            inc("operating_income", e, str(int(rev) // 5), s),
            inc("net_income", e, ni, s),
        ]
    out += [
        bal("total_assets", FY24[1], "5000"),
        bal("current_assets", FY24[1], "2000"),
        bal("goodwill", FY24[1], "800"),
        bal("total_liabilities_and_equity", FY24[1], "5000"),
        bal("total_assets", FY23[1], "4500"),
        bal("total_liabilities_and_equity", FY23[1], "4500"),
    ]
    return out


def line(view: Any, statement: str, key: str) -> Any:
    table = next(t for t in view.statements if t.statement == statement)
    return next(ln for ln in table.lines if ln.key == key)


def test_annual_columns_cells_link_to_the_filing() -> None:
    v = build_view("AAA", rows(), units=1)
    assert [c.label for c in v.columns] == ["FY Dec 2023", "FY Dec 2024"]
    cell = line(v, "income", "revenue").cells[v.columns[1].id]
    assert cell.value == D("1200")
    assert cell.accession == "0000000000-25-000001"
    assert cell.filing_url and cell.concept == "us-gaap:revenue"


def test_gross_profit_is_derived_and_says_how() -> None:
    v = build_view("AAA", rows(), units=1)
    cell = line(v, "income", "gross_profit").cells[v.columns[1].id]
    assert cell.value == D("480")
    assert cell.derived == "revenue − cost of revenue"


def test_balance_sheet_balances_and_other_is_the_residual() -> None:
    v = build_view("AAA", rows(), units=1)
    checks = [c for c in v.checks if c.name == "balance_sheet_balances"]
    assert checks and all(c.passed for c in checks)
    other = line(v, "balance_sheet", "other_noncurrent_assets").cells[v.columns[1].id]
    assert other.value == D("2200")  # 5000 - 2000 current - 800 goodwill
    totals = [c for c in v.checks if c.name == "total_assets_equals_lines_plus_other"]
    assert totals and all(c.passed for c in totals)


def test_an_unbalanced_period_fails_the_check() -> None:
    data = rows()
    for r in data:
        if r["line_item"] == "total_liabilities_and_equity" and r["period_end"] == FY24[1]:
            r["value"] = D("4700")
    v = build_view("AAA", data, units=1)
    failed = [c for c in v.checks if c.passed is False]
    assert any(c.name == "balance_sheet_balances" for c in failed)
    assert any("integrity check" in w for w in v.warnings)


def test_restated_value_keeps_the_original_and_the_view_can_flip() -> None:
    data = rows()
    data.append(
        inc(
            "revenue",
            FY23[1],
            "1050",
            FY23[0],
            known=KNOWN + dt.timedelta(days=400),
            accession="0000000000-26-000009",
        )
    )
    restated = build_view("AAA", data, units=1)
    cell = line(restated, "income", "revenue").cells[restated.columns[0].id]
    assert (cell.value, cell.restated, cell.original_value) == (D("1050"), True, D("1000"))
    original = build_view("AAA", data, units=1, view="original")
    assert line(original, "income", "revenue").cells[original.columns[0].id].value == D("1000")


def test_quarters_from_year_to_date_and_a_derived_q4_and_ttm() -> None:
    s = dt.date(2024, 1, 1)
    data = [
        cf("cash_from_operations", dt.date(2024, 3, 31), "30", s),
        cf("cash_from_operations", dt.date(2024, 6, 30), "70", s),
        cf("cash_from_operations", dt.date(2024, 9, 30), "120", s),
        cf("cash_from_operations", dt.date(2024, 12, 31), "200", s),
    ]
    v = build_view("AAA", data, frequency="quarterly", units=1)
    cells = line(v, "cash_flow", "cash_from_operations").cells
    by_label = {c.label: cells[c.id] for c in v.columns}
    assert by_label["Mar 2024"].value == D("30") and by_label["Mar 2024"].derived is None
    assert by_label["Jun 2024"].value == D("40") and "YTD" in (by_label["Jun 2024"].derived or "")
    assert by_label["Dec 2024"].value == D("80") and (
        by_label["Dec 2024"].derived or ""
    ).startswith("FY")
    ttm = next(c for c in v.columns if c.kind == "ttm")
    assert cells[ttm.id].value == D("200")


def test_missing_quarter_is_named() -> None:
    data = [
        inc("revenue", dt.date(2024, 3, 31), "10", dt.date(2024, 1, 1)),
        inc("revenue", dt.date(2024, 12, 31), "12", dt.date(2024, 10, 1)),
    ]
    v = build_view("AAA", data, frequency="quarterly", units=1)
    assert v.missing_periods and "missing between 2024-03-31 and 2024-12-31" in v.missing_periods[0]


def test_common_size_and_unknown_is_not_zero() -> None:
    v = build_view("AAA", rows(), units=1, transform="common_size")
    cell = line(v, "income", "net_income").cells[v.columns[1].id]
    assert cell.unit == "fraction" and cell.value == D("0.125")
    # No cash-flow facts were filed: no cash-flow lines, never a row of zeros.
    cash_flow = next(t for t in v.statements if t.statement == "cash_flow")
    assert cash_flow.lines == []


def test_reported_view_keeps_the_issuers_concepts() -> None:
    v = build_view("AAA", rows(), basis="reported", units=1)
    income = next(t for t in v.statements if t.statement == "income")
    assert {ln.key for ln in income.lines} >= {"us-gaap:revenue", "us-gaap:net_income"}
    assert v.checks == []


def test_dimensions_stored_as_axis_member_pairs_are_read_as_dimensional():
    from steward.company.statements import _members

    pairs = {"members": [["srt:StatementGeographicalAxis", "country:US"], ["a:Axis", "a:Member"]]}
    assert _members(pairs) == {"srt:StatementGeographicalAxis": "country:US", "a:Axis": "a:Member"}
    assert _members({"members": {"a:Axis": "a:Member"}}) == {"a:Axis": "a:Member"}
    assert _members({"members": []}) == {}
