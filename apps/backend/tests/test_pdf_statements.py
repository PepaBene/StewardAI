"""Statements read from an issuer's own PDF report: parsing, validation, rows, link picking.

Production, 17 Oct 2026: CSG, Daiichi Sankyo and Chugai had no statement facts.
"""

import datetime as dt
from decimal import Decimal

import pytest

from steward.fundamentals.pdf_statements import (
    ALLOWED_CONCEPTS,
    Candidate,
    PdfLine,
    accession_of,
    choose_newest,
    has_filed_statements,
    parse_printed_number,
    pick_amended,
    pick_documents,
    qualifies,
    quote_page,
    statement_rows,
    to_value,
    validate_lines,
)
from steward.fundamentals.statements import FilingMeta
from steward.sources.xbrl_concepts import STATEMENT_CONCEPTS


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234.5", Decimal("1234.5")),
        ("1 234", Decimal(1234)),
        ("1 234 567", Decimal(1234567)),
        ("(1,234)", Decimal(-1234)),
        ("– 1,234", Decimal(-1234)),
        ("−1,234", Decimal(-1234)),
        ("1.234.567", Decimal(1234567)),
        ("12,5", Decimal("12.5")),
        ("1.234,56", Decimal("1234.56")),
        ("€ 4,806", Decimal(4806)),
        ("0.31", Decimal("0.31")),
    ],
)
def test_printed_numbers_parse_with_separators_signs_and_marks(text: str, expected: Decimal) -> None:
    parsed = parse_printed_number(text)
    assert parsed is not None and parsed[0] == expected


@pytest.mark.parametrize("text", ["", "-", "—", "n/m", "abc", "12x", "(,)"])
def test_a_dash_or_word_is_not_a_number(text: str) -> None:
    assert parse_printed_number(text) is None


def test_a_suffix_in_the_figure_overrides_the_table_scale() -> None:
    assert to_value("1.2bn", "millions") == (Decimal("1200000000.0"), "-8")
    assert to_value("12m", "thousands") == (Decimal(12_000_000), "-6")


def test_the_table_scale_multiplies_and_sets_the_precision() -> None:
    assert to_value("4,806.3", "millions") == (Decimal("4806300000.0"), "-5")
    assert to_value("(215)", "thousands") == (Decimal(-215000), "-3")
    # Per-share rows are never scaled.
    assert to_value("2.35", "millions", per_share=True) == (Decimal("2.35"), "2")
    assert to_value("12", "tens") is None


PAGES = [
    "Contents\nIntroduction",
    "Consolidated income statement\nIn EUR million\nRevenue 4 806 4 512\n"
    "Cost of­sales (3 170) (2 990)\nOperating profit 512 480",
    "Consolidated balance sheet\nTotal assets 9 100 8 800",
]


def test_a_quote_on_the_page_with_the_number_inside_is_accepted() -> None:
    assert quote_page("Revenue 4,806 4,512".replace(",", " "), "4 806", PAGES, 2) == 2
    # Soft hyphen, line breaks and spacing differences do not matter.
    assert quote_page("Cost of sales (3 170)", "(3 170)", PAGES, 2) == 2


def test_a_quote_is_found_on_the_page_next_to_the_claimed_one() -> None:
    assert quote_page("Total assets 9 100", "9 100", PAGES, 2) == 3


def test_a_quote_not_on_the_page_or_without_the_number_is_rejected() -> None:
    assert quote_page("Revenue 4 999", "4 999", PAGES, 2) is None
    assert quote_page("Revenue 4 806", "4 512", PAGES, 2) is None
    assert quote_page("Revenue 4 806", "4 806", PAGES, 99) is None


def _line(**changes: object) -> PdfLine:
    base: dict[str, object] = {
        "concept": "ifrs-full:Revenue",
        "statement": "income",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "value_as_printed": "4 806",
        "scale": "millions",
        "currency": "EUR",
        "shown_as_deduction": False,
        "page": 2,
        "quote": "Revenue 4 806 4 512",
    }
    return PdfLine.model_validate({**base, **changes})


def test_only_allow_listed_concepts_with_a_quote_on_the_page_are_kept() -> None:
    result = validate_lines(
        [
            _line(),
            _line(concept="ifrs-full:Inventing"),
            _line(concept="ifrs-full:Assets", quote="Total assets 1 111", value_as_printed="1 111"),
        ],
        PAGES,
    )
    assert [a.concept for a in result.accepted] == ["ifrs-full:Revenue"]
    reasons = {d.concept: d.reason for d in result.dropped}
    assert "allow-list" in reasons["ifrs-full:Inventing"]
    assert "not on the page" in reasons["ifrs-full:Assets"]
    assert result.accepted[0].value == Decimal(4806) * 1_000_000
    assert result.accepted[0].unit == "EUR"


def test_period_currency_and_value_problems_are_dropped_with_the_reason() -> None:
    result = validate_lines(
        [
            _line(period_start=None),
            _line(period_end="not a date"),
            _line(period_start="2025-12-30"),
            _line(currency="euro"),
            _line(currency="USD"),
            _line(value_as_printed="n/m"),
        ],
        PAGES,
        expected_currency="EUR",
    )
    assert result.accepted == []
    assert len(result.dropped) == 6


def test_a_cost_printed_as_a_deduction_is_stored_positive_as_ifrs_tags_it() -> None:
    result = validate_lines(
        [
            _line(
                concept="ifrs-full:CostOfSales",
                value_as_printed="(3 170)",
                quote="Cost of sales (3 170) (2 990)",
                shown_as_deduction=True,
            )
        ],
        PAGES,
    )
    assert result.accepted[0].value == Decimal(3170) * 1_000_000


def test_a_period_printed_with_two_different_values_is_dropped() -> None:
    pages = ["Revenue 4 806\nRevenue 4 700"]
    result = validate_lines(
        [
            _line(page=1, quote="Revenue 4 806"),
            _line(page=1, quote="Revenue 4 700", value_as_printed="4 700"),
        ],
        pages,
    )
    assert result.accepted == []
    assert all("conflicting" in d.reason for d in result.dropped)


def test_every_allow_listed_concept_is_one_the_statement_catalog_classifies() -> None:
    assert set(ALLOWED_CONCEPTS) <= STATEMENT_CONCEPTS


def _meta(accession: str) -> FilingMeta:
    return FilingMeta(
        ticker="CSGa_EQ",
        accession=accession,
        form="issuer PDF report",
        filing_date=dt.date(2026, 10, 17),
        filing_url="https://example.com/ar.pdf",
        source_url="https://example.com/ar.pdf",
        accepted_at=None,
        cik=None,
    )


def test_extracted_lines_become_classified_reconciled_statement_rows() -> None:
    pages = [
        "Revenue 1 000 900\nCost of sales (600) (560)\nGross profit 400 340\n"
        "Total assets 5 000 4 800\nTotal liabilities 3 000 2 900\nTotal equity 2 000 1 900"
    ]
    spec = [
        ("ifrs-full:Revenue", "income", True, "1 000", "Revenue 1 000 900"),
        ("ifrs-full:CostOfSales", "income", True, "(600)", "Cost of sales (600) (560)"),
        ("ifrs-full:GrossProfit", "income", True, "400", "Gross profit 400 340"),
        ("ifrs-full:Assets", "balance_sheet", False, "5 000", "Total assets 5 000 4 800"),
        ("ifrs-full:Liabilities", "balance_sheet", False, "3 000", "Total liabilities 3 000"),
        ("ifrs-full:Equity", "balance_sheet", False, "2 000", "Total equity 2 000 1 900"),
    ]
    lines = [
        _line(
            concept=concept,
            statement=statement,
            period_start="2025-01-01" if flow else None,
            period_end="2025-12-31",
            value_as_printed=printed,
            page=1,
            quote=quote,
            shown_as_deduction=concept.endswith("CostOfSales"),
        )
        for concept, statement, flow, printed, quote in spec
    ]
    result = validate_lines(lines, pages)
    assert result.dropped == []
    rows = statement_rows(
        result.accepted,
        _meta(accession_of("ab" * 32)),
        model="gemini-3.8-flash",
        year_end_month=None,
        known_at=dt.datetime(2026, 10, 17, tzinfo=dt.UTC),
    )
    by_concept = {r.concept: r for r in rows}
    revenue = by_concept["ifrs-full:Revenue"]
    assert (revenue.statement, revenue.line_item) == ("income", "revenue")
    assert revenue.fiscal_period == "FY2025"
    assert revenue.source == "issuer_pdf" and revenue.currency == "EUR"
    assert by_concept["ifrs-full:Assets"].statement == "balance_sheet"
    assert by_concept["ifrs-full:Assets"].period_start is None
    assert revenue.validation["extracted_by"] == "gemini-3.8-flash"
    assert revenue.validation["reviewed"] is False
    assert revenue.validation["quote"] == "Revenue 1 000 900" and revenue.validation["page"] == 1
    # The identities close (gross profit, assets = liabilities + equity), so they are published.
    assert by_concept["ifrs-full:GrossProfit"].published
    assert by_concept["ifrs-full:Assets"].published
    assert by_concept["ifrs-full:Assets"].validation["reconciled"] is True


def test_a_balance_sheet_that_does_not_close_is_not_published() -> None:
    pages = ["Total assets 5 000\nTotal liabilities 3 000\nTotal equity 1 000"]
    lines = [
        _line(
            concept=c,
            statement="balance_sheet",
            period_start=None,
            value_as_printed=p,
            page=1,
            quote=q,
        )
        for c, p, q in [
            ("ifrs-full:Assets", "5 000", "Total assets 5 000"),
            ("ifrs-full:Liabilities", "3 000", "Total liabilities 3 000"),
            ("ifrs-full:Equity", "1 000", "Total equity 1 000"),
        ]
    ]
    rows = statement_rows(
        validate_lines(lines, pages).accepted,
        _meta("pdf-x"),
        model="m",
        year_end_month=None,
        known_at=dt.datetime(2026, 10, 17, tzinfo=dt.UTC),
    )
    assert rows and not any(r.published for r in rows)


def test_the_same_pdf_bytes_always_give_the_same_accession() -> None:
    assert accession_of("a" * 64) == accession_of("a" * 64) != accession_of("b" * 64)


def test_a_report_for_the_same_period_end_amends_the_earlier_one_only() -> None:
    prior = [("pdf-1", dt.date(2025, 12, 31)), ("pdf-2", dt.date(2026, 6, 30))]
    assert pick_amended(prior, "pdf-3", dt.date(2025, 12, 31)) == "pdf-1"
    assert pick_amended(prior, "pdf-3", dt.date(2026, 12, 31)) is None
    assert pick_amended(prior, "pdf-1", dt.date(2025, 12, 31)) is None


HTML = """
<ul>
 <li>Annual Report 2025 <a href="https://cdn.example.com/files/aaa.pdf">Annual Report 2025 (PDF)</a></li>
 <li>Annual Report 2024 <a href="/files/bbb.pdf">Annual Report 2024</a></li>
 <li><a href="/files/ccc.pdf">H1 2026 results statement</a></li>
 <li><a href="/files/ddd.pdf">Q1 2026 results statement</a></li>
 <li><a href="/files/eee.pdf">H1 2026 results presentation</a></li>
 <li><a href="/files/fff.pdf">Sustainability report 2025</a></li>
 <li><a href="/about.html">Annual Report 2025 overview</a></li>
</ul>
"""


def test_the_newest_annual_and_the_newest_interim_report_are_picked_from_a_page() -> None:
    picked = pick_documents(HTML, "https://investors.example.com/reports", 2)
    assert [c.url for c in picked] == [
        "https://cdn.example.com/files/aaa.pdf",
        "https://investors.example.com/files/ccc.pdf",
    ]
    assert [c.kind for c in picked] == ["annual", "interim"]


def test_an_interim_report_older_than_the_annual_report_is_not_read() -> None:
    older = Candidate("https://x/q1.pdf", "interim", 2024, 1)
    annual = Candidate("https://x/ar.pdf", "annual", 2025, 4)
    assert choose_newest([older, annual], 2) == [annual]
    assert choose_newest([older, annual], 1) == [annual]
    assert choose_newest([], 2) == []


def test_only_held_non_fund_equities_without_sec_or_esef_facts_qualify() -> None:
    def q(**changes: object) -> bool:
        args: dict[str, object] = {
            "held": True,
            "instrument_type": "STOCK",
            "security_type": "Common Stock",
            "sources": set(),
        }
        return qualifies(**{**args, **changes})  # type: ignore[arg-type]

    assert q()
    assert q(sources={"issuer_pdf"})
    assert not q(held=False)
    assert not q(instrument_type="ETF", security_type="ETP")
    assert not q(sources={"sec_xbrl"})
    assert not q(sources={"esef", "issuer_pdf"})
    assert has_filed_statements({"sec_fsds"}) and not has_filed_statements({"issuer_pdf"})
