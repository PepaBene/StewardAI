"""13F positions for holdings whose ISIN is not a US/Canadian one: a CINS embedded in an
offshore ISIN, and ADRs matched by issuer name."""

import io
import zipfile

from steward.sources.form13f import (
    MIN_NAME_MATCH_FILINGS,
    cusip_of_isin,
    issuer_key,
    parse_dataset,
)


def test_cins_from_offshore_isin() -> None:
    assert cusip_of_isin("KYG6683N1034") == "G6683N103"  # Nu Holdings
    assert cusip_of_isin("US0378331005") == "037833100"
    # National numbers that are not CUSIPs: a SEDOL, a WKN, a Japanese code.
    assert cusip_of_isin("GB0009895292") is None
    assert cusip_of_isin("DE0007164600") is None
    assert cusip_of_isin("JP3519400000") is None
    # A letter-led number whose check digit does not fit is not taken for a CINS.
    assert cusip_of_isin("KYG6683N1042") is None


def test_issuer_key_drops_legal_form_words() -> None:
    assert issuer_key("AstraZeneca") == issuer_key("ASTRAZENECA PLC") == "ASTRAZENECA"
    assert issuer_key("Taiwan Semiconductor Mfg. Co. Ltd.") == "TAIWAN SEMICONDUCTOR MFG"


def _tsv(rows: list[dict[str, str]]) -> str:
    header = list(rows[0])
    return "\n".join(["\t".join(header), *("\t".join(r[h] for h in header) for r in rows)])


def _zip(info: list[dict[str, str]], filings: int) -> io.BytesIO:
    subs = [
        {"ACCESSION_NUMBER": f"0000000001-26-{i:06d}", "FILING_DATE": "14-AUG-2026",
         "SUBMISSIONTYPE": "13F-HR", "CIK": str(i), "PERIODOFREPORT": "30-JUN-2026"}
        for i in range(filings)
    ]  # fmt: skip
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("SUBMISSION.tsv", _tsv(subs))
        z.writestr("COVERPAGE.tsv", "ACCESSION_NUMBER\tAMENDMENTTYPE\n")
        z.writestr("INFOTABLE.tsv", _tsv(info))
    buffer.seek(0)
    return buffer


def _row(i: int, sk: int, cusip: str, name: str, title: str) -> dict[str, str]:
    return {"ACCESSION_NUMBER": f"0000000001-26-{i:06d}", "INFOTABLE_SK": str(sk),
            "CUSIP": cusip, "NAMEOFISSUER": name, "TITLEOFCLASS": title, "VALUE": "100",
            "SSHPRNAMT": "10", "SSHPRNAMTTYPE": "SH"}  # fmt: skip


def test_adr_rows_are_matched_by_issuer_name() -> None:
    n = MIN_NAME_MATCH_FILINGS
    info = [_row(i, i, "046353108", "ASTRAZENECA PLC", "SPONSORED ADR") for i in range(n)]
    # A stray CUSIP fewer filers report, ordinary shares, and another issuer are not taken.
    info += [
        _row(0, 100, "046353999", "ASTRAZENECA PLC", "ADR"),
        _row(1, 101, "G0593M107", "ASTRAZENECA PLC", "ORD"),
        _row(2, 102, "88160R101", "TESLA INC", "COM"),
    ]
    data = parse_dataset(_zip(info, n), set(), None, {"ASTRAZENECA": "AZN_US_EQ"})
    assert data.name_matched == {"046353108": "AZN_US_EQ"}
    assert {p.cusip for p in data.positions} == {"046353108"}
    assert len(data.positions) == n


def test_us_listed_ordinaries_replace_the_adr_when_most_filers_report_them() -> None:
    n = MIN_NAME_MATCH_FILINGS
    info = [_row(i, i, "G0593M107", "ASTRAZENECA PLC", "ORD SHS") for i in range(n)]
    info += [
        _row(0, 100, "046353108", "ASTRAZENECA PLC", "SPONSORED ADR"),
        # A US company of the same name (a CUSIP, not a CINS, and no ADR) is never taken.
        _row(1, 101, "046353206", "ASTRAZENECA PLC", "COM"),
    ]
    data = parse_dataset(_zip(info, n), set(), None, {"ASTRAZENECA": "AZN_US_EQ"})
    assert data.name_matched == {"G0593M107": "AZN_US_EQ"}
    assert {p.cusip for p in data.positions} == {"G0593M107"}


def test_too_few_filings_do_not_make_a_name_match() -> None:
    info = [
        _row(i, i, "046353108", "ASTRAZENECA PLC", "SPONSORED ADR")
        for i in range(MIN_NAME_MATCH_FILINGS - 1)
    ]
    data = parse_dataset(_zip(info, MIN_NAME_MATCH_FILINGS), set(), None, {"ASTRAZENECA": "A"})
    assert data.name_matched == {} and data.positions == []
