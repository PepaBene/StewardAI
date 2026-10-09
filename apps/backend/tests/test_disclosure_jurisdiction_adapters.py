"""P2.6: EDINET and SEDAR+ discovery from fixtures. Field names are unverified against the live
services (SEDAR+ row shape is an assumed contract)."""

import datetime as dt

import pytest

from steward.disclosure.jurisdiction_adapters import (
    contract_summary,
    edinet_documents,
    sedarplus_documents,
    sedarplus_page_rows,
)
from steward.sources.free_market import FreeSourceError

UTC = dt.UTC

EDINET = {
    "metadata": {"title": "提出書類一覧", "status": "200", "resultset": {"count": 5}},
    "results": [
        {
            "docID": "S100ABCD",
            "edinetCode": "E00001",
            "filerName": "Example Corp",
            "formCode": "030000",
            "docTypeCode": "120",
            "docDescription": "Annual securities report",
            "submitDateTime": "2026-06-25 15:30",
            "periodEnd": "2026-03-31",
            "withdrawalStatus": "0",
        },
        {  # a large-shareholding report: the filer is the holder, the issuer is the target
            "docID": "S100EFGH",
            "edinetCode": "E99999",
            "issuerEdinetCode": "E00001",
            "filerName": "Holder Fund",
            "formCode": "010000",
            "docTypeCode": "350",
            "submitDateTime": "2026-06-26 00:15",
        },
        {
            "docID": "S100WDRN",
            "edinetCode": "E00001",
            "submitDateTime": "2026-06-25 10:00",
            "withdrawalStatus": "1",
        },
        {"docID": "S100NOTM", "edinetCode": "E00001", "submitDateTime": "yesterday"},
        {"docID": "S100OTHR", "edinetCode": "E55555", "submitDateTime": "2026-06-25 09:00"},
        {"edinetCode": "E00001", "submitDateTime": "2026-06-25 09:00"},
        "garbage",
    ],
}


def test_edinet_jst_filing_time_becomes_utc_and_known_at_is_not_earlier() -> None:
    out = edinet_documents(EDINET, {"E00001"})
    first, second = out.discovered
    assert first.doc_id == "S100ABCD" and first.form_code == "030000"
    assert first.filed_at == dt.datetime(2026, 6, 25, 6, 30, tzinfo=UTC)  # 15:30 JST
    assert first.known_at == first.filed_at and first.filed_precision == "minute"
    assert first.issuer_code == "E00001" and first.period_end == dt.date(2026, 3, 31)
    assert first.form_label == "securities report"
    assert first.url == "https://api.edinet-fsa.go.jp/api/v2/documents/S100ABCD?type=2"
    # 00:15 JST on the 26th is still the 25th in UTC.
    assert second.filed_at == dt.datetime(2026, 6, 25, 15, 15, tzinfo=UTC)
    assert second.issuer_code == "E00001" and second.filer_code == "E99999"
    assert second.form_label == "large shareholding report"


def test_edinet_skips_are_reported_with_reasons_and_other_issuers_are_filtered() -> None:
    out = edinet_documents(EDINET, {"E00001"})
    assert [(s.doc_id, s.reason) for s in out.skipped] == [
        ("S100WDRN", "withdrawn"),
        ("S100NOTM", "no_filing_time"),
        (None, "no_doc_id"),
        (None, "not_a_row"),
    ]
    assert contract_summary(out)["discovered"] == 2
    everything = edinet_documents(EDINET)  # no filter: the other issuer is kept
    assert "S100OTHR" in [d.doc_id for d in everything.discovered]


def test_edinet_observed_later_than_filing_wins_and_bad_envelopes_raise() -> None:
    seen = dt.datetime(2026, 6, 26, 1, 0, tzinfo=UTC)
    out = edinet_documents(EDINET, {"E00001"}, observed_at=seen)
    assert out.discovered[0].known_at == seen and out.discovered[0].filed_at < seen
    early = dt.datetime(2026, 1, 1, tzinfo=UTC)  # an observation before filing cannot lower it
    assert edinet_documents(EDINET, {"E00001"}, early).discovered[0].known_at > early
    assert edinet_documents({"metadata": {"status": "404"}}).discovered == []
    with pytest.raises(FreeSourceError):
        edinet_documents({"statusCode": 401})
    with pytest.raises(FreeSourceError):
        edinet_documents({"metadata": {"status": "500"}})


SEDAR = [
    {
        "document_id": "CA-0001",
        "profile_number": "000012345",
        "issuer_name": "Maple Resources Inc.",
        "document_type": "Annual information form",
        "filing_date": "2026-05-12",
        "filing_time": "16:45",
        "period_end": "2025-12-31",
        "url": "https://example.invalid/doc/CA-0001",
    },
    {"document_id": "CA-0002", "profile_number": "000012345", "filing_date": "2026-05-13"},
    {"document_id": "CA-0003", "filing_date": "not a date"},
    {"profile_number": "000012345", "filing_date": "2026-05-13"},
    None,
]


def test_sedarplus_time_is_read_in_the_most_westerly_offset_so_it_is_never_early() -> None:
    out = sedarplus_documents(SEDAR)
    timed, dated = out.discovered
    # 16:45 at UTC-8 is 00:45 UTC next day: no Canadian zone could be later than this reading.
    assert timed.filed_at == dt.datetime(2026, 5, 13, 0, 45, tzinfo=UTC)
    assert timed.known_at == timed.filed_at and timed.filed_precision == "minute"
    assert timed.issuer_code == "000012345" and timed.form_code == "Annual information form"
    assert timed.period_end == dt.date(2025, 12, 31) and timed.jurisdiction == "CA"
    # A date alone: known at the end of that day, and no minute precision is claimed.
    assert dated.filed_at is None and dated.filed_precision == "day"
    assert dated.known_at == dt.datetime(2026, 5, 14, 7, 59, 59, tzinfo=UTC)
    assert dated.period_end is None and dated.url is None


def test_sedarplus_rows_without_an_id_or_date_are_skipped_not_invented() -> None:
    out = sedarplus_documents(SEDAR)
    assert [(s.doc_id, s.reason) for s in out.skipped] == [
        ("CA-0003", "no_filing_time"),
        (None, "no_doc_id"),
        (None, "not_a_row"),
    ]
    assert contract_summary(out) == {
        "discovered": 2,
        "skipped": {"no_filing_time": 1, "no_doc_id": 1, "not_a_row": 1},
    }
    seen = dt.datetime(2026, 6, 1, tzinfo=UTC)
    assert all(d.known_at == seen for d in sedarplus_documents(SEDAR, seen).discovered)
    with pytest.raises(ValueError):
        sedarplus_documents({"rows": []})


# Rows as read from the live SEDAR+ results table on 2026-10-08 (signed-in browser page).
LIVE_SEDAR_PAGE = [
    {
        "profile": "Nano One Materials Corp. / Nano One Materials Corp (000006073)",
        "document": "News release - English.pdf",
        "submitted": "October 08 2026 at 03:21:37 Eastern Daylight Time",
        "jurisdiction": "British Columbia",
        "size": "158 KB",
    },
    {
        "profile": "Eagle Credit Card Trust / Eagle Credit Card Trust (000022832)",
        "document": "Pricing supplement (other than specified derivative) - English.pdf",
        "submitted": "October 07 2026 at 21:38:26 Eastern Daylight Time",
        "jurisdiction": "Ontario",
        "size": "118 KB",
    },
    {
        "profile": "Eagle Credit Card Trust / Eagle Credit Card Trust (000022832)",
        "document": "Pricing supplement (other than specified derivative) - English.pdf",
        "submitted": "October 07 2026 at 21:38:26 Eastern Daylight Time",
        "jurisdiction": "Ontario",
        "size": "118 KB",
    },
    {"profile": "No Number Corp", "document": "x.pdf", "submitted": "garbled", "size": "1 KB"},
]


def test_live_sedarplus_page_rows_read_the_zone_they_state() -> None:
    rows = sedarplus_page_rows(LIVE_SEDAR_PAGE)
    out = sedarplus_documents(rows)
    first = out.discovered[0]
    assert first.issuer_code == "000006073" and first.issuer_name == "Nano One Materials Corp."
    assert first.filed_at == dt.datetime(2026, 10, 8, 7, 21, 37, tzinfo=UTC)  # EDT is UTC-4
    assert first.known_at == first.filed_at and first.jurisdiction == "CA"


def test_live_sedarplus_rows_identical_in_every_field_get_distinct_derived_ids() -> None:
    rows = sedarplus_page_rows(LIVE_SEDAR_PAGE)
    assert rows[1]["document_id"] != rows[2]["document_id"]
    assert rows[1]["document_id"].endswith(":0") and rows[2]["document_id"].endswith(":1")
    assert rows[1]["id_basis"] == "derived"
    assert sedarplus_page_rows(LIVE_SEDAR_PAGE)[1]["document_id"] == rows[1]["document_id"]


def test_live_sedarplus_row_with_no_readable_time_or_profile_gets_no_id() -> None:
    rows = sedarplus_page_rows(LIVE_SEDAR_PAGE)
    assert "document_id" not in rows[3]
    out = sedarplus_documents(rows)
    assert [s.reason for s in out.skipped] == ["no_doc_id"]
    assert len(out.discovered) == 3
