"""P9.1: malformed and awkward inputs to the disclosure adapters.

The generic contract harness (`test_adapter_contracts.py`) covers empty, renamed-schema and
truncated payloads. These tests add the cases specific to disclosure data: shifted table columns,
layouts the extractor cannot read, rate limits, authentication denial, delayed publication and
wrong envelopes.
The rule in each is the same: a value that cannot be read is absent (None), never zero, and the
reason is stated.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from steward.disclosure import estimate_extract as ex
from steward.disclosure.esef_discovery import discover_esef
from steward.disclosure.fetch import classify_failure
from steward.disclosure.jurisdiction_adapters import edinet_documents, sedarplus_documents
from steward.disclosure.sec_discovery import discover_filings
from steward.sources.free_market import FreeSourceError

FIXTURES = Path(__file__).parent / "fixtures" / "disclosure"
NOW = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.UTC)


def consensus() -> bytes:
    return (FIXTURES / "consensus_acme_2026q3.htm").read_bytes()


# ------------------------------------------------------------------------------ table layout
def test_swapped_mean_and_median_columns_follow_the_header_not_the_position() -> None:
    swapped = consensus().replace(
        b"<th>Mean</th><th>Median</th>", b"<th>Median</th><th>Mean</th>", 1
    )
    base = ex.extract_html(consensus()).values[0]
    moved = ex.extract_html(swapped).values[0]
    assert (moved.mean, moved.median) == (base.median, base.mean)


def test_a_cell_that_says_not_available_is_missing_never_zero() -> None:
    margin = [v for v in ex.extract_html(consensus()).values if v.metric == "Operating margin"]
    assert margin and margin[0].mean is None
    assert margin[0].median is not None


@pytest.mark.parametrize("raw", ["n/a", "—", "-", "", "see note", "1,2,3", "12abc", "(", "∞"])
def test_unreadable_numbers_are_none_with_a_reason_or_a_clean_none(raw: str) -> None:
    value, _reason = ex.parse_number(raw)
    assert value is None


def test_a_page_without_any_table_extracts_nothing_and_says_so() -> None:
    out = ex.extract_html(b"<html><body><p>Consensus is on the next page.</p></body></html>")
    assert out.values == ()
    assert "no estimate lines were extracted" in out.issues


def test_text_in_an_unknown_layout_extracts_nothing_instead_of_guessing() -> None:
    laid_out = "Revenue ........ 1,250.5\nEPS ........ 1.42\n"
    out = ex.extract_text(laid_out)
    assert out.values == ()
    assert out.issues


def test_the_same_bytes_have_the_same_hash_and_a_changed_byte_does_not() -> None:
    a = ex.extract_html(consensus())
    b = ex.extract_html(consensus())
    changed = ex.extract_html(consensus().replace(b"1,250.5", b"1,251.5", 1))
    assert a.content_hash == b.content_hash != changed.content_hash


# ------------------------------------------------------------------------ rate limits and denial
def test_rate_limit_keeps_the_retry_time_and_is_a_retrieval_failure() -> None:
    state, retry_at = classify_failure(429, "", "120", now=NOW)
    assert state == "retrieval_failed"
    assert retry_at is not None
    # The header is honoured, plus up to three seconds of jitter so workers do not retry together.
    assert dt.timedelta(seconds=120) <= retry_at - NOW <= dt.timedelta(seconds=123)


def test_a_rate_limit_without_a_retry_header_has_no_invented_retry_time() -> None:
    state, retry_at = classify_failure(429, "", None, now=NOW)
    assert state == "retrieval_failed"
    assert retry_at is None or retry_at > NOW


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, "", "authentication_failed"),
        (403, "", "authentication_failed"),
        (403, "this endpoint requires a paid plan", "paid_only"),
        (402, "", "paid_only"),
        (404, "", "not_covered"),
        (410, "", "not_covered"),
        (500, "", "retrieval_failed"),
        (503, "", "retrieval_failed"),
    ],
)
def test_denials_map_to_a_state_that_says_what_to_fix(
    status: int, body: str, expected: str
) -> None:
    assert classify_failure(status, body)[0] == expected


# ---------------------------------------------------------------------- delayed publication
def submissions(**row: Any) -> dict[str, Any]:
    base = {
        "accessionNumber": ["0000001234-26-000001"],
        "filingDate": ["2026-05-02"],
        "acceptanceDateTime": ["2026-05-02T20:15:09.000Z"],
        "reportDate": ["2025-12-31"],
        "form": ["10-K"],
        "primaryDocument": ["a.htm"],
        "items": [""],
    }
    for key, value in row.items():
        base[key] = [value]
    return {"cik": "1234", "filings": {"recent": base}}


def test_a_filing_is_known_when_accepted_not_when_its_period_ended() -> None:
    (found,) = discover_filings("0000001234", submissions())
    assert found.known_at.date() == dt.date(2026, 5, 2)
    assert found.known_at > dt.datetime(2025, 12, 31, tzinfo=dt.UTC)


def test_a_missing_acceptance_time_never_moves_known_at_earlier_than_the_filing_date() -> None:
    (found,) = discover_filings("0000001234", submissions(acceptanceDateTime=""))
    assert found.known_at.date() >= dt.date(2026, 5, 2)


def test_a_row_with_an_unreadable_date_is_skipped_not_guessed() -> None:
    assert discover_filings("0000001234", submissions(filingDate="not-a-date")) == []


def test_a_submissions_document_without_filings_is_empty_not_an_error() -> None:
    assert discover_filings("0000001234", {"cik": "1234"}) == []
    assert discover_filings("0000001234", {"cik": "1234", "filings": {"recent": {}}}) == []


# ---------------------------------------------------------------------------- wrong envelopes
def test_an_esef_index_that_is_not_a_list_of_entries_raises_instead_of_reading_as_empty() -> None:
    for body in ({"errors": [{"status": "500"}]}, [], "html", None, {"data": "x"}):
        with pytest.raises(ValueError):
            discover_esef(body)


def test_esef_entries_without_an_id_are_reported_as_skipped() -> None:
    out = discover_esef(
        {"data": [{"type": "filing", "attributes": {"period_end": "2025-12-31"}}, 7, None]}
    )
    assert out.discovered == []
    assert len(out.skipped) == 3


def test_edinet_authentication_and_server_errors_raise_a_typed_error() -> None:
    for body in ({"statusCode": 401}, {"metadata": {"status": "500"}}):
        with pytest.raises(FreeSourceError):
            edinet_documents(body)


def test_edinet_results_that_are_not_rows_are_skipped_with_a_reason() -> None:
    out = edinet_documents({"metadata": {"status": "200"}, "results": [None, 5, "x", {}]})
    assert out.discovered == []
    assert len(out.skipped) == 4


def test_sedarplus_rows_that_are_not_objects_do_not_crash() -> None:
    out = sedarplus_documents([None, 3, "x", {}])
    assert out.discovered == []
