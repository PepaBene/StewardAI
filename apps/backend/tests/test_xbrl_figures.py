"""Figures for a 10-Q / 10-K document from its XBRL: only what the document prints."""

from typing import Any

from steward.earnings.xbrl_figures import figures_from_facts, filing_of

ACCN = "0000320193-26-000006"
DOC = """Condensed Consolidated Statements of Operations (In millions, except per-share amounts)
Net sales            $ 124,300    $ 119,575
Gross margin            58,275       54,855
Net income              36,330       33,916
Diluted earnings per share   $ 2.40    $ 2.18
"""


def _facts(value: int, accn: str = ACCN) -> dict[str, Any]:
    def row(val: float, fp: str = "Q1") -> dict[str, Any]:
        return {
            "start": "2025-09-28",
            "end": "2025-12-27",
            "val": val,
            "accn": accn,
            "fy": 2026,
            "fp": fp,
            "form": "10-Q",
            "filed": "2026-01-30",
        }

    return {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [row(value)]}},
                "NetIncomeLoss": {"units": {"USD": [row(36_330_000_000)]}},
                "EarningsPerShareDiluted": {"units": {"USD/shares": [row(2.4)]}},
            }
        }
    }


def test_filing_url_gives_company_cik_and_accession() -> None:
    url = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/aapl-20251227.htm"
    assert filing_of(url) == ("320193", ACCN)
    assert filing_of("https://example.com/release.htm") is None


def test_figures_are_built_where_the_document_prints_them() -> None:
    out = figures_from_facts(
        _facts(124_300_000_000), ACCN, DOC, {"revenue", "net_income", "eps_diluted"}
    )
    by = {f.metric: f for f in out}
    assert by["revenue"].value_as_written == "124,300"
    assert by["revenue"].scale == "millions"
    assert by["revenue"].source_snippet in DOC
    assert by["net_income"].value == 36330
    assert by["eps_diluted"].unit == "per_share" and by["eps_diluted"].value == 2.4


def test_a_figure_the_document_does_not_print_is_left_to_the_model() -> None:
    assert figures_from_facts(_facts(999_000_000_000), ACCN, DOC, {"revenue"}) == []


def test_another_filing_is_not_used() -> None:
    assert (
        figures_from_facts(_facts(124_300_000_000, "0000320193-25-000001"), ACCN, DOC, {"revenue"})
        == []
    )
