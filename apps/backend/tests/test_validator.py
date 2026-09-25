"""The validator: each check has at least one case it correctly rejects (Phase 2 AC)."""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from steward.config.verification import VerificationConfig
from steward.documents.text import html_to_text
from steward.earnings.schema import ExtractedFigure
from steward.earnings.validate import FactKey, KnownFact, ValidationContext, validate

DOC = html_to_text(
    """<html><body>
<p>ACME reports third quarter results</p>
<p>ACME today announced financial results for its fiscal 2025 third quarter ended
June 28, 2025. Revenue was $94.0 billion, up 10 percent year over year.
Gross margin was 46.5 percent.</p>
<p>CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS (Unaudited)</p>
<p>(In millions, except per-share amounts)</p>
<table>
<tr><td></td><td>Three Months Ended</td></tr>
<tr><td></td><td>June 28, 2025</td><td>June 29, 2024</td></tr>
<tr><td>Total net sales</td><td>$</td><td>94,036</td><td>$</td><td>85,777</td></tr>
<tr><td>Gross profit</td><td>43,718</td><td>39,678</td></tr>
<tr><td>Other income/(expense), net</td><td>(171</td><td>)</td><td>142</td></tr>
<tr><td>Diluted</td><td>$</td><td>1.57</td><td>$</td><td>1.40</td></tr>
<tr><td>Restructuring</td><td>&mdash;</td><td>6,328</td></tr>
</table>
<p>Figures in thousands of units are shipments.</p>
</body></html>"""
)
UNIT_LINE = "(In millions, except per-share amounts)"
SALES_ROW = "Total net sales | $94,036 | $85,777"
REPORT_DATE = dt.date(2025, 7, 31)


def fig(**overrides: Any) -> ExtractedFigure:
    base: dict[str, Any] = {
        "metric": "revenue",
        "basis": "reported",
        "fiscal_year": 2025,
        "fiscal_period": "Q3",
        "period_end": "2025-06-28",
        "value_as_written": "94,036",
        "value": 94036,
        "scale": "millions",
        "unit": "currency",
        "currency": "USD",
        "source_snippet": SALES_ROW,
        "unit_snippet": UNIT_LINE,
    }
    return ExtractedFigure(**{**base, **overrides})


def ctx(**overrides: Any) -> ValidationContext:
    base: dict[str, Any] = {
        "document_text": DOC,
        "report_date": REPORT_DATE,
        "fye_month": 9,
        "reporting_currency": "USD",
        "config": VerificationConfig(),
    }
    return ValidationContext(**{**base, **overrides})


def one(figure: ExtractedFigure, context: ValidationContext | None = None) -> Any:
    [result] = validate([figure], context or ctx())
    return result


def test_converter_merges_symbol_cells() -> None:
    assert SALES_ROW in DOC
    assert "Other income/(expense), net | (171) | 142" in DOC


def test_a_correct_figure_is_verified_with_the_value_read_in_code() -> None:
    r = one(fig())
    assert r.status == "verified", r.reason
    assert r.value == Decimal("94036") and r.value_units == Decimal("94036000000")
    assert r.period_end == dt.date(2025, 6, 28)
    assert {c.check for c in r.checks} >= {
        "snippet_containment",
        "value_in_snippet",
        "value_reading",
        "unit_scale",
        "currency",
        "period_alignment",
    }


def test_snippet_containment_rejects_a_quote_not_in_the_document() -> None:
    r = one(fig(source_snippet="Total net sales | $95,036 | $85,777", value_as_written="95,036"))
    assert r.status == "rejected" and r.reason.startswith("snippet_containment")


def test_snippet_containment_rejects_an_invented_unit_line() -> None:
    r = one(fig(unit_snippet="(In billions)"))
    assert r.status == "rejected" and "unit line" in r.reason


def test_value_in_snippet_rejects_a_number_that_is_not_in_the_snippet() -> None:
    r = one(fig(value_as_written="94,037", value=94037))
    assert r.status == "rejected" and r.reason.startswith("value_in_snippet")


def test_value_reading_rejects_a_model_number_that_differs_from_the_print() -> None:
    r = one(fig(value=94.036))
    assert r.status == "rejected" and r.reason.startswith("value_reading")


def test_value_reading_takes_the_sign_from_parentheses() -> None:
    r = one(
        fig(
            metric="other_income",
            value_as_written="171",
            value=-171,
            source_snippet="Other income/(expense), net | (171) | 142",
        )
    )
    assert r.status == "verified" and r.value == Decimal("-171")


def test_value_reading_rejects_an_invented_minus() -> None:
    r = one(
        fig(
            metric="gross_profit",
            value=-43718,
            value_as_written="43,718",
            source_snippet="Gross profit | 43,718 | 39,678",
        )
    )
    assert r.status == "rejected" and "prints it as positive" in r.reason


def test_a_nil_dash_is_not_a_minus_sign() -> None:
    r = one(
        fig(
            metric="restructuring",
            value_as_written="6,328",
            value=6328,
            source_snippet="Restructuring | — | 6,328",
        )
    )
    assert r.status == "verified" and r.value == Decimal("6328")


def test_unit_scale_rejects_a_scale_the_document_does_not_state() -> None:
    r = one(fig(scale="billions", value=94036))
    assert r.status == "rejected" and r.reason.startswith("unit_scale")


def test_unit_scale_rejects_a_scale_with_no_unit_line() -> None:
    r = one(fig(unit_snippet=None))
    assert r.status == "rejected" and r.reason.startswith("unit_scale")


def test_unit_scale_rejects_a_percentage_without_a_percent_sign_and_scaled_eps() -> None:
    pct = one(
        fig(
            metric="gross_margin",
            unit="percent",
            scale="units",
            currency=None,
            value_as_written="43,718",
            value=43718,
            unit_snippet=None,
            source_snippet="Gross profit | 43,718 | 39,678",
        )
    )
    assert pct.status == "rejected" and pct.reason.startswith("unit_scale")
    eps = one(
        fig(
            metric="eps_diluted",
            unit="per_share",
            scale="millions",
            value_as_written="1.57",
            value=1.57,
            source_snippet="Diluted | $1.57 | $1.40",
        )
    )
    assert eps.status == "rejected" and eps.reason.startswith("unit_scale")


def test_currency_rejects_a_currency_absent_from_the_document() -> None:
    r = one(fig(currency="JPY"), ctx(reporting_currency=None))
    assert r.status == "rejected" and r.reason.startswith("currency")


def test_currency_holds_a_currency_other_than_the_profiles() -> None:
    r = one(fig(), ctx(reporting_currency="EUR"))
    assert r.status == "unverified" and r.reason.startswith("currency")


def test_period_alignment_rejects_a_fiscal_label_that_maps_to_another_month() -> None:
    # With a December fiscal year end, "Q3" ends in September, not June.
    r = one(fig(), ctx(fye_month=12))
    assert r.status == "rejected" and "fiscal_year_end_month" in r.reason


def test_period_alignment_rejects_a_stale_document() -> None:
    r = one(fig(), ctx(report_date=dt.date(2026, 7, 31)))
    assert r.status == "rejected" and "not the release for this report" in r.reason


def test_consistency_rejects_two_values_for_one_figure() -> None:
    results = validate(
        [fig(), fig(value_as_written="85,777", value=85777)],
        ctx(),
    )
    assert {r.status for r in results} == {"rejected"}
    assert all(r.reason.startswith("consistency") for r in results)


def test_recomputation_rejects_a_stated_ratio_the_raw_figures_contradict() -> None:
    growth = fig(
        metric="revenue_growth_yoy",
        unit="percent",
        scale="units",
        currency=None,
        unit_snippet=None,
        value_as_written="10",
        value=10,
        source_snippet="Revenue was $94.0 billion, up 10 percent year over year.",
    )
    prior = fig(fiscal_year=2024, period_end="2024-06-29", value_as_written="85,777", value=85777)
    ok = {r.key.metric: r for r in validate([fig(), prior, growth], ctx())}
    assert ok["revenue_growth_yoy"].status == "verified"  # 9.63 % rounds to "10 percent"

    wrong_prior = fig(
        fiscal_year=2024, period_end="2024-06-29", value_as_written="94,036", value=94036
    )
    bad = {r.key.metric: r for r in validate([fig(), wrong_prior, growth], ctx())}
    assert bad["revenue_growth_yoy"].status == "rejected"
    assert bad["revenue_growth_yoy"].reason.startswith("recomputation")


def test_sanity_bound_holds_a_quarter_that_jumps_against_the_previous_one() -> None:
    known = {
        FactKey("revenue", "reported", 2025, "Q2"): KnownFact(
            7, Decimal("50000000000"), "currency", "USD", "verified"
        )
    }
    r = one(fig(), ctx(known=known))
    assert r.status == "unverified" and r.reason.startswith("sanity_bound")

    relaxed = VerificationConfig(sanity_overrides={"revenue": None})
    assert one(fig(), ctx(known=known, config=relaxed)).status == "verified"


def test_restatement_is_held_and_linked_never_overwritten() -> None:
    key = FactKey("revenue", "reported", 2025, "Q3")
    restated = one(
        fig(), ctx(known={key: KnownFact(3, Decimal("94000000000"), "currency", "USD", "verified")})
    )
    assert restated.status == "unverified" and restated.restates == 3
    assert restated.reason.startswith("restatement")

    same = one(
        fig(), ctx(known={key: KnownFact(3, Decimal("94036000000"), "currency", "USD", "verified")})
    )
    assert same.duplicate_of == 3 and same.status == "verified"


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("94,036", "94036"),
        ("(171)", "-171"),
        ("1.234,5", "1234.5"),
        ("7,52", "7.52"),
        ("\u22120.4", "-0.4"),
        ("1 234", "1234"),
        ("n/a", None),
    ],
)
def test_parse_written_number(written: str, expected: str | None) -> None:
    from steward.earnings.units import parse_written_number

    got = parse_written_number(written)
    assert (str(got) if got is not None else None) == expected
