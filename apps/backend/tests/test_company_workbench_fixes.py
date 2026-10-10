"""Company workbench audit fixes that need no database: peer tickers, citation URLs, segment
lines and the compare() subject period."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from steward.company.evidence import Citation, segment_lines
from steward.company.service import PeerGroupIn, clean_peers, match_universe

D = Decimal
KNOWN = dt.datetime(2025, 3, 1, tzinfo=dt.UTC)


def test_peer_tickers_keep_their_case_and_drop_the_company_itself() -> None:
    peers = clean_peers([" VUSAl_EQ", "AAA", "aaa", "MSFT_US_EQ", "MSFT_US_EQ", " "], "AAA")
    assert peers == ["MSFT_US_EQ", "VUSAl_EQ"]  # Trading212 tickers are case-sensitive


def test_stored_peers_resolve_to_the_universe_ticker_case_insensitively() -> None:
    universe = ["VUSAl_EQ", "MSFT_US_EQ", "ABCa_EQ", "ABCA_EQ"]
    # exact match first, then a unique case-insensitive match; ambiguous is dropped with a note
    out, notes = match_universe(["VUSAL_EQ", "MSFT_US_EQ", "ABCa_EQ", "abca_eq", "ZZZ"], universe)
    assert out == ["VUSAl_EQ", "MSFT_US_EQ", "ABCa_EQ", "ZZZ"]
    assert [n.ticker for n in notes] == ["abca_eq"] and "ambiguous" in notes[0].reason


@pytest.mark.parametrize("bad", ["", "a b", "x;drop", "../etc", "A" * 33])
def test_peer_group_rejects_malformed_tickers(bad: str) -> None:
    with pytest.raises(ValidationError):
        PeerGroupIn(name="g", peers=[bad])
    assert PeerGroupIn(name="g", peers=[" VUSAl_EQ "]).peers == ["VUSAl_EQ"]


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,hi", "ftp://example.com/a", "https://", "//x.y/z"],
)
def test_citation_urls_must_be_http_or_https(url: str) -> None:
    with pytest.raises(ValidationError):
        Citation(url=url)


def test_citation_accepts_a_web_address() -> None:
    assert Citation(url=" https://www.sec.gov/a ").url == "https://www.sec.gov/a"


def seg(
    axis: str, member: str, value: str, start: dt.date, end: dt.date, *, day: int = 1, id_: int = 1
):
    return {
        "dimensions": {"members": {axis: member}},
        "period_start": start,
        "period_end": end,
        "value": D(value),
        "known_at": KNOWN + dt.timedelta(days=day),
        "id": id_,
    }


def test_segment_lines_use_one_axis_annual_facts_and_the_latest_restatement() -> None:
    fy, end = dt.date(2024, 1, 1), dt.date(2024, 12, 31)
    rows = [
        seg("us-gaap:StatementBusinessSegmentsAxis", "x:Cloud", "600", fy, end, id_=1),
        seg("us-gaap:StatementBusinessSegmentsAxis", "x:Cloud", "620", fy, end, day=30, id_=2),
        seg("us-gaap:StatementBusinessSegmentsAxis", "x:Devices", "380", fy, end, id_=3),
        # The same revenue by geography, a year-to-date copy and a cross-cut must not be added in.
        seg("srt:StatementGeographicalAxis", "x:US", "1000", fy, end, id_=4),
        seg(
            "us-gaap:StatementBusinessSegmentsAxis",
            "x:Cloud",
            "300",
            fy,
            dt.date(2024, 6, 30),
            id_=5,
        ),
        {**seg("a", "b", "50", fy, end, id_=6), "dimensions": {"members": {"a": "b", "c": "d"}}},
    ]
    lines = segment_lines(rows)
    assert [(ln.label, ln.value) for ln in lines] == [("Cloud", D(620)), ("Devices", D(380))]
    assert lines[0].share == D("0.62") and lines[0].period_end == end


def test_segment_lines_empty_without_annual_single_axis_facts() -> None:
    assert segment_lines([]) == []
    ytd = seg("a:Axis", "x:M", "5", dt.date(2024, 1, 1), dt.date(2024, 6, 30))
    assert segment_lines([ytd]) == []
