"""P3 gaps: the common quarter, 13F summary totals and the omitted-holdings flag, 13D/G exhibits."""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from decimal import Decimal as D

from steward.disclosure.ownership import EffectiveReport, choose_common_quarter
from steward.sources.form13f import parse_dataset
from steward.sources.schedule13 import parse_exhibits

UTC = dt.UTC
Q1, Q2 = dt.date(2026, 3, 31), dt.date(2026, 6, 30)


def report(cik: str, period: dt.date) -> EffectiveReport:
    return EffectiveReport(
        cik, period, (f"{cik}-{period}",), dt.datetime(2026, 8, 1, tzinfo=UTC), False
    )


def reports(*pairs: tuple[str, dt.date]) -> dict[tuple[str, dt.date], EffectiveReport]:
    return {(c, p): report(c, p) for c, p in pairs}


def test_the_newest_quarter_is_used_once_enough_managers_have_filed_it() -> None:
    r = reports(*[(c, Q2) for c in "ABCD"], ("E", Q1), *[(c, Q1) for c in "ABCD"])
    choice = choose_common_quarter(r)
    assert choice.period == Q2 and choice.not_yet_filed == ("E",) and not choice.fell_back
    assert choice.share_filed == D("0.8")


def test_a_partly_filed_newest_quarter_is_skipped_for_the_one_most_have_filed() -> None:
    r = reports(("A", Q2), *[(c, Q1) for c in "ABCDE"])
    choice = choose_common_quarter(r)
    assert choice.period == Q1 and choice.not_yet_filed == () and not choice.fell_back


def test_when_no_quarter_reaches_the_bar_the_best_covered_one_is_shown_and_flagged() -> None:
    r = reports(("A", Q2), ("A", Q1), ("B", Q1), ("C", Q1 - dt.timedelta(days=200)))
    choice = choose_common_quarter(r)
    assert choice.period == Q1 and choice.fell_back and choice.not_yet_filed == ("C",)


def test_a_manager_is_named_not_carried_forward_from_an_older_quarter() -> None:
    r = reports(*[(c, Q2) for c in "ABCDEFGHI"], ("J", Q1))
    choice = choose_common_quarter(r)
    assert choice.period == Q2 and "J" in choice.not_yet_filed and "J" not in choice.filed


def test_no_reports_means_no_quarter() -> None:
    assert choose_common_quarter({}).period is None


# ------------------------------------------------------------------------------ 13F summary
def tsv(rows: list[dict[str, str]]) -> str:
    header = list(rows[0])
    return "\n".join(["\t".join(header), *("\t".join(r[h] for h in header) for r in rows)])


def archive(*, summary: bool, confidential: str = "N") -> io.BytesIO:
    subs = [
        {
            "ACCESSION_NUMBER": "0000000001-26-000001",
            "FILING_DATE": "14-AUG-2026",
            "SUBMISSIONTYPE": "13F-HR",
            "CIK": "1",
            "PERIODOFREPORT": "30-JUN-2026",
        }
    ]
    cover = [
        {
            "ACCESSION_NUMBER": "0000000001-26-000001",
            "AMENDMENTTYPE": "",
            "FILINGMANAGER_NAME": "Mgr LP",
            "ISCONFIDENTIALOMITTED": confidential,
        }
    ]
    info = [
        {
            "ACCESSION_NUMBER": "0000000001-26-000001",
            "INFOTABLE_SK": "1",
            "CUSIP": "037833100",
            "NAMEOFISSUER": "APPLE INC",
            "TITLEOFCLASS": "COM",
            "VALUE": "100",
            "SSHPRNAMT": "10",
            "SSHPRNAMTTYPE": "SH",
        }
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv(subs))
        z.writestr("COVERPAGE.tsv", tsv(cover))
        z.writestr("INFOTABLE.tsv", tsv(info))
        if summary:
            z.writestr(
                "SUMMARYPAGE.tsv",
                tsv(
                    [
                        {
                            "ACCESSION_NUMBER": "0000000001-26-000001",
                            "TABLEENTRYTOTAL": "1,204",
                            "TABLEVALUETOTAL": "98,000,000",
                        }
                    ]
                ),
            )
    buf.seek(0)
    return buf


def test_the_summary_page_gives_the_reported_line_count_and_total() -> None:
    [f] = parse_dataset(archive(summary=True), {"037833100"}).filings
    assert f.reported_line_count == 1204 and f.reported_total_value_usd == D("98000000")
    assert f.omitted_holdings_notice is False


def test_a_confidential_omission_flag_is_read_from_the_cover() -> None:
    [f] = parse_dataset(archive(summary=True, confidential="Y"), {"037833100"}).filings
    assert f.omitted_holdings_notice is True


def test_without_a_summary_page_the_totals_are_unknown_not_zero() -> None:
    [f] = parse_dataset(archive(summary=False), {"037833100"}).filings
    assert f.reported_line_count is None and f.reported_total_value_usd is None


def test_a_blank_confidential_flag_is_unknown_not_no() -> None:
    [f] = parse_dataset(archive(summary=True, confidential=""), {"037833100"}).filings
    assert f.omitted_holdings_notice is None


# --------------------------------------------------------------------------------- exhibits
def test_item_7_text_gives_one_row_per_exhibit() -> None:
    text = (
        "Exhibit 99.1 - Joint Filing Agreement. Exhibit 99.2: Stock Purchase Agreement, "
        f"dated May 1, 2026; Exhibit 10.3 {chr(0x2013)} Voting Agreement"
    )
    assert parse_exhibits(text) == (
        ("Exhibit 99.1", "Joint Filing Agreement"),
        ("Exhibit 99.2", "Stock Purchase Agreement, dated May 1, 2026"),
        ("Exhibit 10.3", "Voting Agreement"),
    )


def test_text_without_an_exhibit_label_gives_no_rows_and_a_bare_label_has_no_description() -> None:
    assert parse_exhibits("None.") == ()
    assert parse_exhibits(None) == ()
    assert parse_exhibits("Exhibit 99.1") == (("Exhibit 99.1", None),)
