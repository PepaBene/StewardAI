"""EDGAR adapter, Finnhub calendar, IR-calendar date checks and document fetching, against
recorded responses (no network)."""

import datetime as dt
import json
from collections.abc import Callable

import httpx
import pytest

from steward.documents.fetch import HTML, PDF, TEXT, FetchError, fetch_document, sniff_type
from steward.documents.text import contains, normalize_for_match
from steward.earnings.calendar import date_in_text
from steward.sources.edgar import EdgarClient, EdgarError, parse_index, pick_press_release
from steward.sources.finnhub import next_earnings_date

UA = "StewardAI test test@example.com"

SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["10-Q", "8-K", "8-K", "6-K"],
            "accessionNumber": ["a-1", "0000320193-25-000071", "0000320193-25-000060", "x-9"],
            "filingDate": ["2025-08-01", "2025-07-31", "2025-06-01", "2024-01-01"],
            "reportDate": ["2025-06-28", "2025-07-31", "2025-06-01", ""],
            "primaryDocument": ["q.htm", "aapl-20250731.htm", "other.htm", "f.htm"],
            "items": ["", "2.02,9.01", "5.07", ""],
        }
    }
}
INDEX_HTML = "\n".join(
    [
        "<table><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>",
        '<tr><td>1</td><td>8-K</td><td><a href="x">aapl-20250731.htm</a> iXBRL</td>',
        "<td>8-K</td><td>39036</td></tr>",
        '<tr><td>2</td><td>EX-99.1</td><td><a href="y">a8-kex991q3202506282025.htm</a></td>',
        "<td>EX-99.1</td><td>168258</td></tr>",
        '<tr><td>3</td><td>XBRL SCHEMA</td><td><a href="z">aapl-20250731.xsd</a></td>',
        "<td>EX-101.SCH</td><td>3864</td></tr></table>",
    ]
)


def edgar(handler: Callable[[httpx.Request], httpx.Response]) -> EdgarClient:
    return EdgarClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), UA)


async def test_finds_the_results_8k_and_its_press_release() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.headers["User-Agent"] == UA
        if request.url.host == "data.sec.gov":
            return httpx.Response(200, json=SUBMISSIONS)
        if request.url.path.endswith("-index.html"):
            return httpx.Response(200, text=INDEX_HTML)
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL"}})
        return httpx.Response(404)

    client = edgar(handler)
    assert await client.cik_for_ticker("aapl") == "0000320193"
    found = await client.find_earnings_release("320193", dt.date(2025, 7, 31))
    assert found is not None
    filing, url = found
    assert filing.form == "8-K" and filing.items == "2.02,9.01"
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019325000071/"
        "a8-kex991q3202506282025.htm"
    )
    # The 5.07 8-K and the out-of-window filings were never opened.
    assert sum("-index.html" in u for u in seen) == 1
    assert await client.find_earnings_release("320193", dt.date(2025, 3, 1)) is None


def test_edgar_requires_a_contact_address() -> None:
    with pytest.raises(EdgarError):
        EdgarClient(httpx.AsyncClient(), "StewardAI")


def test_index_parsing_prefers_exhibit_99_1_html() -> None:
    from steward.documents.text import html_to_text

    docs = parse_index(html_to_text(INDEX_HTML))
    assert ("EX-99.1", "a8-kex991q3202506282025.htm") in docs
    assert (
        pick_press_release([("EX-99.2", "b.htm"), ("EX-99.1", "a.pdf"), ("EX-99.1", "a.htm")])
        == "a.htm"
    )
    assert pick_press_release([("8-K", "x.htm")]) is None


async def test_finnhub_key_goes_in_a_header_and_the_earliest_upcoming_date_wins() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "token" not in str(request.url)
        assert request.headers["X-Finnhub-Token"] == "secret"
        rows = [
            {"date": "2025-10-30", "symbol": "AAPL"},
            {"date": "2025-10-01", "symbol": "AAPL"},
            {"date": "2025-09-01", "symbol": "AAPL"},
            {"date": "2025-09-20", "symbol": "MSFT"},
        ]
        return httpx.Response(200, json={"earningsCalendar": rows})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    got = await next_earnings_date(
        http, "secret", "AAPL", dt.date(2025, 9, 15), dt.date(2026, 1, 1)
    )
    assert got is not None and got.date == dt.date(2025, 10, 1)
    assert "secret" not in got.source


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Q3 2025 results | 16 October 2025", True),
        ("Thursday, October 16, 2025", True),
        ("16.10.2025 Interim report", True),
        ("2025-10-16 results", True),
        ("17 October 2025", False),
        ("16 November 2025", False),
    ],
)
def test_calendar_dates_must_be_visible_in_the_quote(text: str, ok: bool) -> None:
    assert date_in_text(dt.date(2025, 10, 16), text) is ok


async def test_fetch_caps_size_and_sniffs_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * 5000)
        if request.url.path == "/pdf":
            return httpx.Response(
                200, content=b"%PDF-1.7 ...", headers={"content-type": "application/octet-stream"}
            )
        if request.url.path == "/gone":
            return httpx.Response(404)
        return httpx.Response(
            200,
            content=b"<html><body><p>Hello</p></body></html>",
            headers={"content-type": "text/html"},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    doc = await fetch_document(http, "https://x.test/page", max_bytes=1000)
    assert doc.content_type == HTML and doc.text() == "Hello" and len(doc.sha256) == 64
    assert (await fetch_document(http, "https://x.test/pdf", max_bytes=1000)).content_type == PDF
    with pytest.raises(FetchError, match="larger than"):
        await fetch_document(http, "https://x.test/big", max_bytes=1000)
    with pytest.raises(FetchError, match="HTTP 404"):
        await fetch_document(http, "https://x.test/gone", max_bytes=1000)
    assert sniff_type(b"plain words", "text/plain", "https://x.test/a.txt") == TEXT


def test_containment_ignores_whitespace_quotes_dashes_and_table_bars() -> None:
    doc = "Apple\u2019s revenue \u2014 up 10%\nTotal net sales | 94,036 | 85,777"
    assert contains(doc, "Apple's revenue - up  10%")
    assert contains(doc, "Total net sales 94,036")
    assert not contains(doc, "Total net sales 94,037")
    assert normalize_for_match("A |  B") == "a b"


def test_golden_cases_hold_real_documents() -> None:
    from steward.earnings.golden import load_cases

    cases = load_cases()
    assert len(cases) >= 20
    assert {c.data["ticker"] for c in cases} >= {"AAPL_US_EQ", "MSFT_US_EQ", "SAP_US_EQ"}
    assert all(json.dumps(c.data["figures"]) for c in cases)
