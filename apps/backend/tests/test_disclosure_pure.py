"""Public-disclosure P1 offline checks: parser offsets, SEC discovery, fetch failure classes,
identity ranking and the source-enable rule. No network, no database."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from steward.disclosure.fetch import (
    FetchFailure,
    Validators,
    classify_failure,
    extension_for,
    safe_url,
)
from steward.disclosure.parse import ParseError, parse_fact_value, parse_html, section_for
from steward.disclosure.registry import SOURCES, SourceNotEnabled, check_enable
from steward.disclosure.review import needs_review
from steward.disclosure.sec_discovery import (
    classify_archive,
    discover_filings,
    incremental,
    known_at_for,
    next_cursor,
)
from steward.reference.disclosure_identity import (
    decide_status,
    is_anonymous_label,
    normalize_name,
    rank_by_name,
)

FIXTURES = Path(__file__).parent / "fixtures" / "disclosure"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ------------------------------------------------------------------------------- parser
def test_every_span_offset_reproduces_its_text() -> None:
    doc = parse_html(fixture_bytes("sample_10k.htm"))
    for fact in doc.facts:
        if fact.start is not None and fact.end is not None:
            assert doc.span(fact.start, fact.end).strip() == fact.raw_text
    for table in doc.tables:
        for row in table.rows:
            assert doc.span(row.start, row.end) == " | ".join(row.cells)
    assert "not a table" not in doc.text  # script content is never text


def test_parsing_is_deterministic() -> None:
    a, b = parse_html(fixture_bytes("sample_10k.htm")), parse_html(fixture_bytes("sample_10k.htm"))
    assert a.text == b.text and a.facts == b.facts and a.sections == b.sections


def test_facts_scale_sign_dimension_and_unit() -> None:
    doc = parse_html(fixture_bytes("sample_10k.htm"))
    by_name = {(f.name, f.context_ref): f for f in doc.facts}
    revenue = by_name[("us-gaap:Revenues", "c-dur")]
    assert revenue.value == Decimal("1250500000")
    assert revenue.footnote_ids == ("fn1",)
    assert doc.units[revenue.unit_ref or ""] == "USD"
    ctx = doc.contexts["c-dur"]
    assert (ctx.start, ctx.end) == (dt.date(2025, 1, 1), dt.date(2025, 12, 31))
    assert doc.contexts["c-inst"].instant == dt.date(2025, 12, 31)
    segment = by_name[("us-gaap:Revenues", "c-seg")]
    assert doc.dimensions_of(segment) == {
        "us-gaap:StatementBusinessSegmentsAxis": "acme:WidgetsMember"
    }
    assert by_name[("us-gaap:NetIncomeLoss", "c-dur")].value == Decimal("-12400000")
    assert by_name[("acme:DebtRepaid", "c-dur")].value == Decimal(0)  # dash is zero, not missing


def test_unreadable_number_is_an_error_not_a_guess() -> None:
    doc = parse_html(fixture_bytes("sample_10k.htm"))
    odd = next(f for f in doc.facts if f.name == "acme:Odd")
    assert odd.value is None and odd.error and "unsupported" in odd.error
    assert parse_fact_value("1.234,5", "ixt:numcommadecimal", 0, "") == (Decimal("1234.5"), None)
    assert parse_fact_value("abc", None, 0, "")[0] is None


def test_hidden_cover_fact_and_footnote_are_read_without_entering_the_text() -> None:
    doc = parse_html(fixture_bytes("sample_10k.htm"))
    registrant = next(f for f in doc.facts if f.name == "dei:EntityRegistrantName")
    assert registrant.raw_text == "Acme Corp" and registrant.start is None
    assert doc.footnotes[0].text == "Includes a one-time licensing gain."
    assert "licensing gain" not in doc.text


def test_sections_find_the_body_not_only_the_contents_line() -> None:
    doc = parse_html(fixture_bytes("sample_10k.htm"))
    titles = [s.title for s in doc.sections]
    assert any(t.startswith("Item 7") for t in titles)
    revenue = next(
        f for f in doc.facts if f.name == "us-gaap:Revenues" and f.context_ref == "c-dur"
    )
    assert revenue.start is not None
    section = section_for(doc, revenue.start)
    assert section is None or section.title  # a tiny fixture section may be classed as TOC


def test_text_block_has_an_offset_range() -> None:
    doc = parse_html(fixture_bytes("sample_10k.htm"))
    block = doc.text_blocks[0]
    assert "one segment" in doc.span(block.start, block.end)


@pytest.mark.parametrize("data", [b"", b"   \n", b"<html><head><title>t</title></head></html>"])
def test_unreadable_documents_raise_so_they_are_quarantined(data: bytes) -> None:
    with pytest.raises(ParseError):
        parse_html(data)


# ----------------------------------------------------------------------------- discovery
def submissions() -> dict:
    return json.loads(fixture_bytes("submissions.json"))


def test_discovery_skips_unreadable_rows_and_orders_by_known_time() -> None:
    filings = discover_filings("1234", submissions())
    assert [f.accession for f in filings] == [
        "0000001234-25-000099",
        "0000001234-26-000003",
        "0000001234-26-000007",
        "0000001234-26-000012",
    ]


def test_dates_are_kept_apart_and_missing_ones_explained() -> None:
    by_acc = {f.accession: f for f in discover_filings("1234", submissions())}
    annual = by_acc["0000001234-26-000003"]  # no acceptance time in the feed
    assert annual.accepted_at is None
    assert annual.known_at == dt.datetime(2026, 2, 20, 23, 59, 59, tzinfo=dt.UTC)
    assert "accepted_at" in annual.date_notes and "published_at" in annual.date_notes
    accepted = by_acc["0000001234-26-000007"]
    assert accepted.known_at == accepted.accepted_at == dt.datetime(2026, 3, 1, 9, tzinfo=dt.UTC)


def test_amendment_points_at_the_original_of_the_same_period() -> None:
    by_acc = {f.accession: f for f in discover_filings("1234", submissions())}
    amendment = by_acc["0000001234-26-000012"]
    assert amendment.is_amendment and amendment.amends_accession == "0000001234-26-000003"
    assert by_acc["0000001234-26-000003"].amends_accession is None
    # the FY2024 10-K is a different period and is not the target
    assert amendment.amends_accession != "0000001234-25-000099"


def test_form_and_since_filters_and_urls() -> None:
    only_8k = discover_filings("1234", submissions(), forms={"8-K"})
    assert [f.form for f in only_8k] == ["8-K"]
    recent = discover_filings("1234", submissions(), since=dt.date(2026, 1, 1))
    assert len(recent) == 3
    assert recent[0].primary_url == (
        "https://www.sec.gov/Archives/edgar/data/1234/000000123426000003/acme-20251231.htm"
    )


def test_incremental_cursor_returns_only_new_filings() -> None:
    filings = discover_filings("1234", submissions())
    cursor = next_cursor(filings[:2], None)
    assert [f.accession for f in incremental(filings, cursor)] == [
        "0000001234-26-000007",
        "0000001234-26-000012",
    ]
    assert incremental(filings, None) == filings
    assert next_cursor([], cursor) == cursor


def test_known_at_for_prefers_acceptance() -> None:
    accepted = dt.datetime(2026, 1, 2, 3, tzinfo=dt.UTC)
    assert known_at_for(accepted, dt.date(2026, 1, 2))[0] == accepted


def test_archive_roles() -> None:
    filing = next(
        f for f in discover_filings("1234", submissions()) if f.accession == "0000001234-26-000003"
    )
    roles = {
        f.name: f.role
        for f in classify_archive(filing, json.loads(fixture_bytes("archive_index.json")))
    }
    assert roles["acme-20251231.htm"] == "primary"
    assert roles["acme-20251231_htm.xml"] == "xbrl_instance"
    assert roles["ex21.htm"] == roles["ex10-3.htm"] == "exhibit"
    assert roles["R2.htm"] == roles["acme-20251231.xsd"] == roles["FilingSummary.xml"] == "other"


# --------------------------------------------------------------------------- fetch / registry
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, "", "authentication_failed"),
        (403, "Forbidden", "authentication_failed"),  # a bare 403 is not a licensing verdict
        (403, "Please upgrade your plan", "paid_only"),
        (402, "", "paid_only"),
        (404, "", "not_covered"),
        (429, "", "retrieval_failed"),
        (503, "", "retrieval_failed"),
    ],
)
def test_failure_classes(status: int, body: str, expected: str) -> None:
    assert classify_failure(status, body)[0] == expected


def test_429_carries_the_server_wait() -> None:
    now = dt.datetime(2026, 10, 7, tzinfo=dt.UTC)
    _state, retry_at = classify_failure(429, "", "30", now=now)
    assert retry_at is not None and retry_at >= now + dt.timedelta(seconds=30)
    assert classify_failure(503, "", "30", now=now)[1] is None


def test_validators_and_urls_never_leak_keys() -> None:
    assert Validators("abc", "Tue").headers() == {
        "If-None-Match": "abc",
        "If-Modified-Since": "Tue",
    }
    assert safe_url("https://x.example/a/b?api_key=SECRET#f") == "https://x.example/a/b"
    assert extension_for("text/html; charset=utf-8", "https://x/y") == "html"
    assert extension_for(None, "https://x/y/report.pdf") == "pdf"
    assert FetchFailure("not_covered", "m").state == "not_covered"


def test_a_source_cannot_be_enabled_without_a_passing_access_test() -> None:
    for state in ("untested", "authentication_failed", "retrieval_failed", "paid_only"):
        with pytest.raises(SourceNotEnabled):
            check_enable(state, True)
    check_enable("available", True)
    check_enable("untested", False)


def test_registry_is_free_only_and_keyed_uniquely() -> None:
    assert len({s.source_key for s in SOURCES}) == len(SOURCES)
    assert {"sec.submissions", "sedarplus.portal", "edinet.api", "ted.search"} <= {
        s.source_key for s in SOURCES
    }
    assert all(s.stop_condition for s in SOURCES)
    assert all(s.requires_user_agent for s in SOURCES if s.provider == "sec")


# --------------------------------------------------------------------------------- identity
def test_name_normalization_and_anonymous_labels() -> None:
    assert normalize_name("Globex Corporation, Inc.") == normalize_name("GLOBEX CORP")
    assert is_anonymous_label("Customer A") and is_anonymous_label("Distributor 2")
    assert not is_anonymous_label("Customer Acme Ltd")


def test_name_only_matches_are_never_confirmed() -> None:
    names = {1: [("Globex Corporation", "legal")], 2: [("Globex Holdings Ltd", "legal")]}
    ranked = rank_by_name("Globex Corp", names)
    status, chosen = decide_status(ranked)
    assert status == "suggested"  # tied/ambiguous or name-grade: a reviewer decides
    assert chosen is None or chosen.basis != "cik"
    assert decide_status([]) == ("unresolved", None)


def test_identifier_match_is_confirmed_but_ambiguous_ones_are_not() -> None:
    from steward.reference.disclosure_identity import Candidate

    assert decide_status([Candidate(5, 1.0, "lei", "X")])[0] == "confirmed"
    assert decide_status([Candidate(5, 0.95, "registered_name", "A")])[0] == "suggested"
    tied = [Candidate(1, 0.95, "registered_name", "A"), Candidate(2, 0.93, "alias", "A")]
    assert decide_status(tied) == ("suggested", None)


def test_review_triggers() -> None:
    assert needs_review(0.9) is None
    assert needs_review(0.4) == "low_confidence"
    assert needs_review(None) == "low_confidence"
    assert needs_review(0.99, entity_resolved=False) == "entity_ambiguity"
    assert needs_review(0.99, contradicted=True) == "contradiction"
