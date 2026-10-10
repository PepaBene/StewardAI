"""P2.4 table extraction: buckets with offsets, units never guessed, dash is null, ambiguity is
sent to review."""

import datetime as dt
from decimal import Decimal as D

from steward.disclosure.note_tables import (
    detect_unit,
    extract_debt_maturities,
    extract_lease_maturities,
    extract_repurchases,
    parse_period,
)

DEBT = """Note 8. Debt
The following table summarizes scheduled maturities of our debt (in millions):
Year ending December 31, 2026 $ 500
Year ending December 31, 2027 $ 750
Year ending December 31, 2028 —
Year ending December 31, 2029 $ 1,000
Year ending December 31, 2030 $ 250
Thereafter 2,000
Total principal 4,500
Less: unamortized discount (45)
Total debt 4,455
The weighted-average interest rate was 4.5%.
"""

DEBT_PIPES = """(in thousands)
| Year | Amount |
| 2026 | $ 100 |
| 2027 | $ 200 |
| Thereafter | $ 300 |
| Total | $ 600 |
"""


def test_debt_buckets_scale_and_keep_offsets() -> None:
    s = extract_debt_maturities(DEBT)
    assert s.status == "extracted", s.reasons
    assert s.unit.multiplier == D(10) ** 6
    a = s.amounts()
    assert a["year_1"] == D(500_000_000) and a["year_4"] == D(1_000_000_000)
    assert a["thereafter"] == D(2_000_000_000)
    assert s.row("total").value == D(4455) * 10**6  # type: ignore[union-attr]
    # the discount keeps its parentheses sign; a subtotal is not the reported total
    assert s.row("discount").value == D(-45) * 10**6  # type: ignore[union-attr]
    assert s.row("subtotal") is not None
    for r in s.rows:
        assert DEBT[r.start : r.end].strip().startswith(r.label.split()[0])


def test_dash_bucket_is_null_not_zero() -> None:
    s = extract_debt_maturities(DEBT)
    year3 = s.row("year_3")
    assert year3 is not None and year3.dash and year3.value is None
    assert "year_3" not in s.amounts()
    assert year3.status == "extracted" and any("not zero" in r for r in year3.reasons)


def test_pipe_table_and_thousands() -> None:
    s = extract_debt_maturities(DEBT_PIPES)
    assert s.unit.multiplier == D(1000)
    assert s.amounts() == {"year_1": D(100_000), "year_2": D(200_000), "thereafter": D(300_000)}
    assert s.row("total").value == D(600_000)  # type: ignore[union-attr]
    assert s.status == "extracted"


def test_no_unit_statement_is_review_and_nothing_is_scaled() -> None:
    text = DEBT_PIPES.replace("(in thousands)\n", "")
    s = extract_debt_maturities(text)
    assert s.status == "review" and any("unit not stated" in r for r in s.reasons)
    assert all(r.value is None for r in s.rows) and s.row("total").raw_value == D(600)  # type: ignore[union-attr]
    assert all(r.status == "review" for r in s.rows)


def test_conflicting_units_are_ambiguous() -> None:
    u = detect_unit("(in millions) ... later (in thousands)")
    assert u.ambiguous and u.multiplier is None
    assert detect_unit("shares in thousands; amounts in millions").multiplier == D(10) ** 6
    assert detect_unit("shares in thousands").multiplier is None


def test_missing_total_and_unlabeled_bucket_go_to_review() -> None:
    text = "(in millions)\n2026 $ 100\nSenior notes due 2030 $ 200\n2027 $ 300\n"
    s = extract_debt_maturities(text)
    assert s.status == "review" and "total missing" in s.reasons
    unlabeled = [r for r in s.rows if r.key is None]
    assert len(unlabeled) == 1 and any("unlabeled" in x for x in unlabeled[0].reasons)


def test_nonconsecutive_years_and_two_columns_are_not_guessed() -> None:
    s = extract_debt_maturities("(in millions)\n2026 100\n2028 200\nTotal 300\n")
    assert s.row("year_1") is None
    assert [r.key for r in s.rows if r.label.startswith("20")] == [None, None]
    two = extract_debt_maturities("(in millions)\n2026 100 50\nTotal 150 50\n")
    assert two.row("year_1").status == "review"  # type: ignore[union-attr]
    assert two.row("year_1").value is None  # type: ignore[union-attr]


def test_parentheses_and_unbalanced_parenthesis() -> None:
    s = extract_debt_maturities("(in millions)\n2026 (100\nTotal (100)\n")
    assert s.row("year_1").status == "review"  # type: ignore[union-attr]
    assert s.row("total").value == D(-100) * 10**6  # type: ignore[union-attr]


LEASE = """Maturities of lease liabilities as of December 31, 2025 (in thousands):
2026 $ 1,200
2027 1,100
2028 1,000
2029 900
2030 800
Thereafter 2,000
Total lease payments 7,000
Less: imputed interest (1,000)
Present value of lease liabilities $ 6,000
"""


def test_lease_schedule_has_interest_and_present_value() -> None:
    s = extract_lease_maturities(LEASE)
    assert s.status == "extracted", s.reasons
    assert s.row("total").value == D(7_000_000)  # type: ignore[union-attr]
    assert s.row("imputed_interest").value == D(-1_000_000)  # type: ignore[union-attr]
    assert s.row("present_value").value == D(6_000_000)  # type: ignore[union-attr]
    assert len(s.amounts()) == 6
    assert LEASE[s.row("present_value").start : s.row("present_value").end].startswith("Present")  # type: ignore[union-attr]


def test_lease_without_imputed_interest_is_review() -> None:
    text = LEASE.replace("Less: imputed interest (1,000)\n", "")
    s = extract_lease_maturities(text)
    assert s.status == "review" and "imputed interest missing" in s.reasons


REPO = """Issuer purchases of Class A common stock (in millions, except per share data)
| Period | Total Number of Shares Purchased | Average Price Paid per Share | Total Amount |
| October 1 - October 31, 2025 | 2 | $ 150.50 | $ 301 |
| November 1 - November 30, 2025 | — | — | — |
| Total | 2 | $ 150.50 | $ 301 |
"""


def test_repurchase_rows_need_a_header_and_state_scale() -> None:
    t = extract_repurchases(REPO)
    assert t.share_class and "Class A" in t.share_class
    oct_row = t.rows[0]
    assert oct_row.period_start == dt.date(2025, 10, 1) and oct_row.period_end == dt.date(
        2025, 10, 31
    )
    assert oct_row.average_price == D("150.50") and oct_row.amount == D(301_000_000)
    # shares in millions are not stated separately, so the count is left null for review
    assert oct_row.shares is None and oct_row.status == "review"
    assert any("share count scale" in r for r in oct_row.reasons)
    nov = t.rows[1]
    assert nov.amount is None and nov.average_price is None  # dash is null, not zero
    assert t.rows[2].is_total and t.rows[2].period_end is None


def test_repurchase_shares_scaled_when_stated() -> None:
    text = REPO.replace(
        "(in millions, except per share data)", "(in millions; shares in thousands)"
    )
    t = extract_repurchases(text)
    assert t.rows[0].shares == D(2000) and t.rows[0].status == "extracted", t.rows[0].reasons


def test_repurchase_without_header_or_class_is_review() -> None:
    text = "(in millions)\nOctober 1 - October 31, 2025 2 150.50 301\n"
    t = extract_repurchases(text)
    assert t.rows[0].status == "review"
    assert any("column roles" in r for r in t.rows[0].reasons)
    assert t.rows[0].shares is None and t.rows[0].amount is None
    assert extract_repurchases("nothing here").status == "review"


def test_parse_period_variants() -> None:
    assert parse_period("October 2025") == (dt.date(2025, 10, 1), dt.date(2025, 10, 31))
    assert parse_period("Year ended December 31, 2025") == (None, dt.date(2025, 12, 31))
    assert parse_period("February 30, 2025") == (None, None)
    assert parse_period("Q4 2025") == (None, None)
