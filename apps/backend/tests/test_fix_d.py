"""Regression tests for the group D audit fixes (earnings validator, units, AI call errors,
reconciliation, EDGAR index parsing, document fetching, calendar dates)."""

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pydantic
import pytest

from steward.ai import Usage
from steward.config.holding_profiles import HoldingProfile
from steward.config.jobs import EarningsCalendarSettings
from steward.config.verification import VerificationConfig
from steward.documents import fetch as fetch_module
from steward.documents.fetch import FetchError, fetch_document, pdf_text
from steward.documents.text import html_to_text
from steward.earnings.calendar import _find_date, date_in_text
from steward.earnings.check import _record_failed_usage, is_sec_url
from steward.earnings.extract import ExtractionError, ExtractionRequest, LLMExtractor
from steward.earnings.locate import LLMSearchLocator, LocateError, SearchRequest
from steward.earnings.reconcile import FactToCheck, compare
from steward.earnings.schema import ExtractedFigure
from steward.earnings.units import (
    parse_written_number,
    scale_markers_present,
    scale_next_to,
    uses_decimal_comma,
)
from steward.earnings.validate import (
    FactKey,
    RejectedFact,
    ValidationContext,
    validate,
)
from steward.sources.edgar import parse_index, pick_press_release

DOC = html_to_text(
    """<html><body>
<p>ACME today announced results for its fiscal 2025 third quarter ended June 28, 2025.</p>
<p>Net loss of $171 million compared with net income of $142 million a year ago.</p>
<p>Revenue decreased 3.2% year over year.</p>
<p>Revenue was $94.0 billion.</p>
<p>Revenue - 5,000 units shipped.</p>
<p>(In millions, except per-share amounts)</p>
<table>
<tr><td>Total net sales</td><td>$</td><td>94,036</td><td>$</td><td>85,777</td></tr>
<tr><td>Net loss</td><td>\u2013171</td><td>142</td></tr>
<tr><td>Net income (loss)</td><td>1,234</td><td>(99)</td></tr>
</table>
</body></html>"""
)
UNIT_LINE = "(In millions, except per-share amounts)"


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
        "source_snippet": "Total net sales | $94,036 | $85,777",
        "unit_snippet": UNIT_LINE,
    }
    return ExtractedFigure(**{**base, **overrides})


def ctx(**overrides: Any) -> ValidationContext:
    base: dict[str, Any] = {
        "document_text": DOC,
        "report_date": dt.date(2025, 7, 31),
        "fye_month": 9,
        "reporting_currency": "USD",
        "config": VerificationConfig(),
    }
    return ValidationContext(**{**base, **overrides})


def one(figure: ExtractedFigure, context: ValidationContext | None = None) -> Any:
    [result] = validate([figure], context or ctx())
    return result


NET_LOSS = "Net loss of $171 million compared with net income of $142 million a year ago."


# 1. Signs -----------------------------------------------------------------------------


def test_a_net_loss_in_words_is_stored_negative() -> None:
    r = one(fig(metric="net_income", value_as_written="171", value=-171, source_snippet=NET_LOSS))
    assert r.status == "verified", r.reason
    assert r.value == Decimal("-171")


def test_a_positive_value_for_a_stated_loss_is_rejected() -> None:
    r = one(fig(metric="net_income", value_as_written="171", value=171, source_snippet=NET_LOSS))
    assert r.status == "rejected" and r.reason.startswith("value_reading")
    assert "negative" in r.reason


def test_the_prior_year_income_next_to_a_loss_stays_positive() -> None:
    r = one(
        fig(
            metric="net_income",
            fiscal_year=2024,
            period_end="2024-06-29",
            value_as_written="142",
            value=142,
            source_snippet=NET_LOSS,
        )
    )
    # (Alone, the prior-year figure makes the document look stale; only the sign matters.)
    assert all(c.ok for c in r.checks if c.check == "value_reading"), r.reason
    assert r.value == Decimal("142")


def growth(value: float) -> ExtractedFigure:
    return fig(
        metric="revenue_growth_yoy",
        unit="percent",
        scale="units",
        currency=None,
        unit_snippet=None,
        value_as_written="3.2",
        value=value,
        source_snippet="Revenue decreased 3.2% year over year.",
    )


def test_a_decline_in_words_is_a_negative_growth() -> None:
    ok = one(growth(-3.2))
    assert ok.status == "verified", ok.reason
    assert ok.value == Decimal("-3.2")
    bad = one(growth(3.2))
    assert bad.status == "rejected" and bad.reason.startswith("value_reading")


def test_an_attached_en_dash_is_a_minus_in_a_table() -> None:
    r = one(
        fig(
            metric="net_income",
            value_as_written="171",
            value=-171,
            source_snippet="Net loss | \u2013171 | 142",
        )
    )
    assert r.status == "verified", r.reason
    assert r.value == Decimal("-171")
    written = one(
        fig(
            metric="net_income",
            value_as_written="\u2013171",
            value=-171,
            source_snippet="Net loss | \u2013171 | 142",
        )
    )
    assert written.status == "verified" and written.value == Decimal("-171")


def test_a_signed_caption_does_not_make_a_value_negative() -> None:
    r = one(
        fig(
            metric="net_income",
            value_as_written="1,234",
            value=-1234,
            source_snippet="Net income (loss) | 1,234 | (99)",
        )
    )
    assert r.status == "rejected" and "prints it as positive" in r.reason


def test_a_spaced_hyphen_is_not_a_minus() -> None:
    r = one(
        fig(
            metric="shipments",
            unit="count",
            scale="units",
            currency=None,
            unit_snippet=None,
            value_as_written="5,000",
            value=5000,
            source_snippet="Revenue - 5,000 units shipped.",
        )
    )
    assert r.status == "verified", r.reason
    assert r.value == Decimal("5000")


# 2. Scale -----------------------------------------------------------------------------


def test_the_snippets_own_scale_beats_the_unit_line() -> None:
    wrong = one(
        fig(value_as_written="94.0", value=94.0, source_snippet="Revenue was $94.0 billion.")
    )
    assert wrong.status == "rejected" and wrong.reason.startswith("unit_scale")
    assert "billions" in wrong.reason
    right = one(
        fig(
            value_as_written="94.0",
            value=94.0,
            scale="billions",
            unit_snippet=None,
            source_snippet="Revenue was $94.0 billion.",
        )
    )
    assert right.status == "verified", right.reason


def test_short_scale_markers_need_a_number_or_currency() -> None:
    assert not scale_markers_present("Class B common stock", "billions")
    assert not scale_markers_present("Series B preferred", "billions")
    assert scale_markers_present("$1.2b", "billions")
    assert scale_markers_present("1.2bn", "billions")
    assert scale_markers_present("(in EUR m)", "millions")
    assert scale_next_to("Revenue was $94.0 billion.", "94.0") == "billions"
    assert scale_next_to("$94,036 million", "4,036") is None


# 3. Boundary-aware value_in_snippet --------------------------------------------------


def test_a_number_inside_a_longer_number_is_not_in_the_snippet() -> None:
    r = one(fig(value_as_written="4,036", value=4036))
    assert r.status == "rejected" and r.reason.startswith("value_in_snippet")


# 4/5. AI call errors and their usage ---------------------------------------------------


class _Messages:
    def __init__(self, raises: BaseException | None = None, response: Any = None) -> None:
        self.raises = raises
        self.response = response

    async def parse(self, **_: Any) -> Any:
        if self.raises is not None:
            raise self.raises
        return self.response


class _Client:
    def __init__(self, messages: _Messages) -> None:
        self.messages = messages


def _validation_error() -> pydantic.ValidationError:
    class M(pydantic.BaseModel):
        x: int

    try:
        M.model_validate_json('{"x": ')
    except pydantic.ValidationError as exc:
        return exc
    raise AssertionError


REQUEST = ExtractionRequest("AAPL", "Apple", dt.date(2025, 7, 31), 9, (2025, "Q3"), [], "USD")


async def test_unparseable_extraction_output_is_an_extraction_error() -> None:
    client: Any = _Client(_Messages(raises=_validation_error()))
    doc = fetch_module.Document("https://x.test", fetch_module.TEXT, b"text")
    with pytest.raises(ExtractionError, match="unparseable"):
        await LLMExtractor(client, "m", "low").extract(doc, "text", REQUEST)


async def test_a_truncated_extraction_carries_its_usage() -> None:
    usage_block = type("U", (), {"input_tokens": 1000, "output_tokens": 32000})()
    response = type(
        "R", (), {"usage": usage_block, "stop_reason": "max_tokens", "parsed_output": None}
    )()
    client: Any = _Client(_Messages(response=response))
    doc = fetch_module.Document("https://x.test", fetch_module.TEXT, b"text")
    with pytest.raises(ExtractionError) as info:
        await LLMExtractor(client, "m", "low").extract(doc, "text", REQUEST)
    assert info.value.usage is not None and info.value.usage.output_tokens == 32000

    class Step:
        usage: Any = None

    step = Step()
    _record_failed_usage(step, info.value.usage)
    assert step.usage is not None and step.usage.output_tokens == 32000
    empty = Step()
    _record_failed_usage(empty, Usage(model="m"))
    _record_failed_usage(empty, None)
    assert empty.usage is None


async def test_unparseable_search_output_is_a_locate_error() -> None:
    client: Any = _Client(_Messages(raises=_validation_error()))
    request = SearchRequest("AAPL", "Apple", dt.date(2025, 7, 31), (2025, "Q3"), ["x.test"], [])
    with pytest.raises(LocateError):
        await LLMSearchLocator(client, "m", 3).locate(request)


async def test_unparseable_calendar_output_is_a_note_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(*_: Any, **__: Any) -> fetch_module.Document:
        return fetch_module.Document("https://ir.test/cal", fetch_module.TEXT, b"calendar")

    monkeypatch.setattr("steward.earnings.calendar.fetch_document", fake_fetch)
    client: Any = _Client(_Messages(raises=_validation_error()))
    profile = HoldingProfile(ticker="X_US_EQ", financial_calendar_url="https://ir.test/cal")

    class Step:
        def __init__(self) -> None:
            self.usage: Any = None
            self.detail: dict[str, Any] = {}

    step = Step()
    async with httpx.AsyncClient() as http:
        found, _ = await _find_date(
            http,
            client,
            None,
            profile,
            EarningsCalendarSettings(),
            dt.date(2025, 7, 1),
            dt.date(2025, 12, 1),
            step,
        )
    assert found is None
    assert any(n.startswith("ir calendar:") for n in step.detail["notes"])


# 6. European thousands dots ------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "decimal_comma", "expected"),
    [
        ("1.234", False, "1.234"),
        ("1.234", True, "1234"),
        ("1.234.567", False, "1234567"),
        ("1.234,5", False, "1234.5"),
        ("1.2.3", False, None),
        ("94,036", False, "94036"),
    ],
)
def test_dot_thousands(written: str, decimal_comma: bool, expected: str | None) -> None:
    got = parse_written_number(written, decimal_comma=decimal_comma)
    assert (str(got) if got is not None else None) == expected


def test_decimal_comma_context() -> None:
    assert uses_decimal_comma("Umsatz 1.234 Mio. EUR, Marge 7,5 %") is True
    assert uses_decimal_comma("Revenue 1.234 billion, margin 7.5%") is False
    assert uses_decimal_comma("Revenue 1.234") is None


# 7. Reconciliation concept per period -------------------------------------------------


def test_the_xbrl_concept_is_chosen_per_period() -> None:
    def row(start: str, end: str, val: float) -> dict[str, Any]:
        return {"start": start, "end": end, "val": val, "accn": "a", "form": "10-Q",
                "filed": "2025-08-01"}  # fmt: skip

    facts = {
        "facts": {
            "us-gaap": {
                # The preferred concept only for recent periods; the old one before that.
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {"USD": [row("2025-03-30", "2025-06-28", 94036000000)]}
                },
                "Revenues": {"units": {"USD": [row("2017-04-02", "2017-07-01", 45408000000)]}},
            }
        }
    }
    old = FactToCheck(1, "revenue", "Q3", dt.date(2017, 7, 1), Decimal("45408000000"),
                      "45,408", "millions", "currency", "USD")  # fmt: skip
    new = FactToCheck(2, "revenue", "Q3", dt.date(2025, 6, 28), Decimal("94036000000"),
                      "94,036", "millions", "currency", "USD")  # fmt: skip
    assert compare(old, facts, dt.date(2025, 9, 1)).concept == "us-gaap:Revenues"
    got = compare(new, facts, dt.date(2025, 9, 1))
    assert got.outcome == "match"
    assert got.concept == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


# 8. EDGAR index with a blank description ----------------------------------------------


def test_index_parsing_survives_a_blank_description() -> None:
    markup = (
        "<table><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th>"
        "<th>Size</th></tr>"
        '<tr><td>1</td><td>8-K</td><td><a href="x">main.htm</a></td><td>8-K</td>'
        "<td>39036</td></tr>"
        '<tr><td>2</td><td></td><td><a href="y">ex991.htm</a></td><td>EX-99.1</td>'
        "<td>168258</td></tr>"
        '<tr><td>3</td><td></td><td><a href="z">img.jpg.htm</a></td><td></td>'
        "<td>100</td></tr></table>"
    )
    docs = parse_index(html_to_text(markup))
    assert ("EX-99.1", "ex991.htm") in docs
    assert ("", "img.jpg.htm") in docs
    assert pick_press_release(docs) == "ex991.htm"


# 9. Calendar numeric dates -------------------------------------------------------------


def test_numeric_dates_need_a_left_boundary() -> None:
    date = dt.date(2026, 2, 1)
    assert not date_in_text(date, "Q4 results on 11.2.2026")
    assert date_in_text(date, "Q4 results on 1.2.2026")
    assert date_in_text(date, "Q4 results on 01.02.2026")
    assert not date_in_text(date, "Q4 results on 12/01/2026")
    assert date_in_text(date, "Q4 results on 2/1/2026")
    assert not date_in_text(dt.date(2026, 2, 11), "2026-02-111")


# 11/12. Fetching ------------------------------------------------------------------------


def test_pypdf_limit_errors_are_fetch_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf.errors import LimitReachedError

    def boom(*_: Any, **__: Any) -> Any:
        raise LimitReachedError("too big")

    monkeypatch.setattr("pypdf.PdfReader", boom)
    with pytest.raises(FetchError, match="unreadable PDF"):
        pdf_text(b"%PDF-1.4")


def _resolver(table: dict[str, list[str]]) -> Any:
    async def resolve(host: str) -> list[str]:
        if host not in table:
            raise OSError("no such host")
        return table[host]

    return resolve


PUBLIC = _resolver({"ir.test": ["93.184.216.34"], "cdn.test": ["93.184.216.35"],
                    "internal.test": ["10.0.0.5"]})  # fmt: skip


async def test_redirects_are_followed_and_each_hop_is_checked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://cdn.test/doc"})
        if request.url.path == "/doc":
            return httpx.Response(200, text="hello", headers={"content-type": "text/plain"})
        if request.url.path == "/evil":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/meta"})
        if request.url.path == "/internal":
            return httpx.Response(301, headers={"location": "https://internal.test/"})
        if request.url.path == "/loop":
            return httpx.Response(302, headers={"location": "/loop"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        doc = await fetch_document(http, "https://ir.test/start", max_bytes=1000, resolve=PUBLIC)
        assert doc.data == b"hello" and doc.url == "https://ir.test/start"
        with pytest.raises(FetchError, match="non-public"):
            await fetch_document(http, "https://ir.test/evil", max_bytes=1000, resolve=PUBLIC)
        with pytest.raises(FetchError, match="non-public"):
            await fetch_document(http, "https://ir.test/internal", max_bytes=1000, resolve=PUBLIC)
        with pytest.raises(FetchError, match="non-public"):
            await fetch_document(http, "http://127.0.0.1/x", max_bytes=1000, resolve=PUBLIC)
        with pytest.raises(FetchError, match="only http"):
            await fetch_document(http, "file:///etc/passwd", max_bytes=1000, resolve=PUBLIC)
        with pytest.raises(FetchError, match="redirects more than"):
            await fetch_document(http, "https://ir.test/loop", max_bytes=1000, resolve=PUBLIC)
        with pytest.raises(FetchError, match="cannot resolve"):
            await fetch_document(http, "https://nowhere.test/", max_bytes=1000, resolve=PUBLIC)


# 13. SEC User-Agent only for SEC hosts --------------------------------------------------


def test_sec_user_agent_goes_only_to_sec_hosts() -> None:
    assert is_sec_url("https://www.sec.gov/Archives/edgar/data/1/2/x.htm")
    assert is_sec_url("https://sec.gov/x")
    assert not is_sec_url("https://evil.test/?q=sec.gov")
    assert not is_sec_url("https://notsec.gov/x")
    assert not is_sec_url("https://sec.gov.evil.test/x")


# 14. Owner-rejected values are not queued again ----------------------------------------


def test_a_value_the_owner_rejected_is_not_stored_again() -> None:
    key = FactKey("revenue", "reported", 2025, "Q3")
    rejected = {key: [RejectedFact(9, Decimal("94036000000"), "currency")]}
    r = one(fig(), ctx(owner_rejected=rejected))
    assert r.owner_rejected == 9
    other = {key: [RejectedFact(9, Decimal("94000000000"), "currency")]}
    assert one(fig(), ctx(owner_rejected=other)).owner_rejected is None


@pytest.mark.db
async def test_load_owner_rejected_reads_reviewed_rejections(backend_pool: asyncpg.Pool) -> None:
    from steward.earnings.review import review_fact
    from steward.earnings.store import load_owner_rejected
    from tests.test_phase2_followups import OWNER, TICKER, seed_fact

    held = await seed_fact(backend_pool, status="unverified")
    await seed_fact(backend_pool, status="rejected", fy=2024, period_end=dt.date(2024, 6, 29))
    await review_fact(backend_pool, held, "reject", "wrong", owner_id=OWNER)
    got = await load_owner_rejected(backend_pool, TICKER)
    # Only the owner's rejection counts; the validator's own rejection does not.
    assert list(got) == [FactKey("revenue", "reported", 2025, "Q3")]
    assert got[FactKey("revenue", "reported", 2025, "Q3")][0].id == held
