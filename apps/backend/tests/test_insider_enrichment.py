"""Forms 3/4/5 enrichment: footnotes, derivative terms, amendments, Rule 10b5-1 flags,
economic classification, net discretionary activity, and the UK PDMR pilot adapter.

Form 4 fixtures follow the SEC ownership-document XML layout; they are not recordings of live
filings, and the PDMR fixture follows the UK MAR Article 19 notification template.
"""

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.insider_activity_routes import router as activity_router
from api.main import create_app
from steward.documents.text import html_to_text
from steward.news.regulatory import parse_investegate
from steward.ownership.insider import (
    OriginalCandidate,
    RowKey,
    match_original,
    parse_ownership_xml,
)
from steward.ownership.insider_classify import CODE_CLASS, classify, plan_flag
from steward.ownership.insider_collect import _upsert
from steward.ownership.insider_read import holding_insider_activity, summarise
from steward.sources.base import AccessState
from steward.sources.edgar import Filing
from steward.sources.pdmr_uk import (
    classify_nature,
    fetch_pdmr,
    is_pdmr_announcement,
    parse_pdmr_notification,
    pdmr_access,
)

FIXTURES = Path(__file__).parent / "fixtures"
OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
ORIGINAL = "0001222222-26-000101"
AMENDMENT = "0001222222-26-000150"


def filing(accession: str, form: str, filed: str) -> Filing:
    return Filing(
        cik="0000111111",
        form=form,
        accession=accession,
        filing_date=dt.date.fromisoformat(filed),
        report_date=None,
        primary_document="form4.xml",
        items="",
    )


def fixture(name: str) -> bytes:
    return (FIXTURES / "insider" / name).read_bytes()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_every_code_maps_to_a_documented_class() -> None:
    expected = {
        "P": "open_market_buy",
        "S": "open_market_sell",
        "M": "option_exercise",
        "X": "option_exercise",
        "O": "option_exercise",
        "C": "conversion",
        "A": "grant_award",
        "G": "gift",
        "F": "tax_withholding",
    }
    for code, cls in expected.items():
        assert classify(code, derivative=False, footnote_text=None).economic_class == cls
    for code in "DIJKLUWZEHV":
        assert CODE_CLASS[code] == "other"
        assert classify(code, derivative=False, footnote_text=None).economic_class == "other"
    assert classify("INITIAL_HOLDING", derivative=False, footnote_text=None).economic_class == (
        "holding_snapshot"
    )
    assert classify("Q", derivative=False, footnote_text=None).economic_class == "other"


def test_raw_code_alone_is_not_conviction() -> None:
    # A code P/S is only an open-market trade when nothing in the footnotes says otherwise.
    private = classify("P", derivative=False, footnote_text="Purchased in the private placement.")
    assert private.economic_class == "other" and "footnote" in private.basis
    cover = classify("S", derivative=False, footnote_text="Shares sold to cover tax withholding.")
    assert cover.economic_class == "tax_withholding"
    # Buying or selling a derivative is not a stock trade.
    assert classify("P", derivative=True, footnote_text=None).economic_class == "other"
    # J is only a gift / tax mechanic when its footnote says so.
    assert classify("J", derivative=False, footnote_text="Bona fide gift").economic_class == "gift"
    assert classify("J", derivative=False, footnote_text=None).economic_class == "other"
    plain = classify("P", derivative=False, footnote_text=None)
    assert plain.open_market and plain.basis.startswith("code P")


def test_plan_flag_is_only_what_the_form_states() -> None:
    text = "Sold under a Rule 10b5-1 trading plan adopted on March 3, 2026."
    flag = plan_flag(text, None)
    assert (flag.plan, flag.basis, flag.adopted) == (True, "footnote", dt.date(2026, 3, 3))
    # A filing-wide check box cannot say which row was planned: possible, never per-row True.
    boxed = plan_flag(None, True)
    assert boxed.basis == "form_checkbox" and boxed.plan is None and boxed.possible
    assert plan_flag(None, False).plan is False
    unknown = plan_flag("Held by the trust.", None)
    assert unknown.plan is None and unknown.basis is None
    negative = plan_flag("Not made pursuant to a 10b5-1 plan.", True)
    assert negative.plan is False and negative.basis == "footnote"
    iso = plan_flag("Rule 10b5-1 plan adopted on 2025-11-20.", None)
    assert iso.adopted == dt.date(2025, 11, 20)


@pytest.mark.parametrize(
    "text",
    [
        "The reporting person has not adopted a Rule 10b5-1 plan.",
        "Sale was not made pursuant to a Rule 10b5-1 trading plan.",
        "No Rule 10b5-1 plan covers this sale.",
        "Effected without a 10b5-1 plan.",
        "Not effected under any Rule 10b5-1 trading arrangement.",
    ],
)
def test_10b5_1_negations_are_never_true(text: str) -> None:
    flag = plan_flag(text, True)
    assert flag.plan is False and flag.basis == "footnote"


def test_10b5_1_ambiguous_or_conflicting_mentions_are_unknown() -> None:
    # A negation word the patterns cannot resolve must not read as an affirmative plan.
    assert plan_flag("The Rule 10b5-1 plan was not modified.", None).plan is None
    mixed = plan_flag(
        "Sold under a 10b5-1 plan adopted May 1, 2025 | Not under a 10b5-1 plan", None
    )
    assert mixed.plan is None


def test_plan_adoption_dates_and_code_case() -> None:
    assert plan_flag("10b5-1 plan adopted on Mar. 1, 2026.", None).adopted == dt.date(2026, 3, 1)
    assert plan_flag("10b5-1 plan adopted Sept. 4, 2025.", None).adopted == dt.date(2025, 9, 4)
    assert plan_flag("10b5-1 plan adopted on march 3rd, 2026", None).adopted == dt.date(2026, 3, 3)
    assert classify("s", derivative=False, footnote_text=None).economic_class == "open_market_sell"
    assert classify(" p ", derivative=False, footnote_text=None).economic_class == "open_market_buy"
    for phrase in (
        "Shares sold to cover taxes upon vesting.",
        "Sale to cover tax withholding on RSU vesting.",
        "Sold in order to pay taxes due on vesting.",
        "Sell-to-cover transaction.",
    ):
        got = classify("S", derivative=False, footnote_text=phrase)
        assert got.economic_class == "tax_withholding", phrase


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parser_reads_footnotes_derivative_terms_and_classes() -> None:
    parsed = parse_ownership_xml(fixture("form4_original.xml"), filing(ORIGINAL, "4", "2026-08-12"))
    assert parsed.reporting_owner_cik == "0000222222" and not parsed.is_amendment
    assert parsed.aff10b5_one is None  # the filing does not state a plan check box
    assert parsed.remarks and parsed.footnotes and set(parsed.footnotes) == {"F1", "F2", "F3"}
    by_code = {(r.transaction_code, r.derivative, r.shares): r for r in parsed.rows}
    sell = by_code[("S", False, Decimal(500))]
    assert sell.footnote_ids == ("F1",) and sell.footnote_text
    assert sell.economic_class == "open_market_sell"
    assert sell.rule_10b5_1_plan is True and sell.rule_10b5_1_basis == "footnote"
    assert sell.rule_10b5_1_adopted == dt.date(2026, 3, 3)
    assert by_code[("F", False, Decimal(300))].economic_class == "tax_withholding"
    assert by_code[("A", False, Decimal(2000))].economic_class == "grant_award"
    gift = by_code[("G", False, Decimal(100))]
    assert gift.economic_class == "gift" and gift.direct_indirect == "I"
    assert by_code[("P", False, Decimal(4000))].economic_class == "other"
    buy = by_code[("P", False, Decimal(1000))]
    assert buy.economic_class == "open_market_buy" and buy.rule_10b5_1_plan is None
    option = by_code[("M", True, Decimal(5000))]
    assert option.economic_class == "option_exercise"
    assert option.exercise_price == Decimal("5.00")
    assert option.exercisable_date == dt.date(2024, 8, 10)
    assert option.expiration_date == dt.date(2030, 8, 10)
    assert option.underlying_security_title == "Class A Common Stock"
    assert option.underlying_shares == Decimal(5000)


def test_amendment_fields_and_checkbox() -> None:
    parsed = parse_ownership_xml(
        fixture("form4_amendment.xml"), filing(AMENDMENT, "4/A", "2026-08-20")
    )
    assert parsed.is_amendment and parsed.original_submission_date == dt.date(2026, 8, 12)
    assert parsed.aff10b5_one is True
    buy = next(r for r in parsed.rows if r.shares == Decimal(1000))
    # B33: the filing-wide box is "possible", never a per-row True.
    assert buy.rule_10b5_1_plan is None and buy.rule_10b5_1_basis == "form_checkbox"


def test_zero_shares_are_kept_not_replaced() -> None:
    xml = fixture("form4_original.xml").replace(b"<value>1000</value>", b"<value>0</value>", 1)
    parsed = parse_ownership_xml(xml, filing(ORIGINAL, "4", "2026-08-12"))
    assert parsed.rows[0].shares == Decimal(0)


def test_initial_holding_is_not_a_transaction() -> None:
    xml = b"""<ownershipDocument><documentType>3</documentType>
      <issuer><issuerCik>0000111111</issuerCik></issuer>
      <reportingOwner><reportingOwnerId><rptOwnerCik>0000222222</rptOwnerCik></reportingOwnerId>
      </reportingOwner><nonDerivativeTable><nonDerivativeHolding>
      <securityTitle><value>Common</value></securityTitle>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>10</value>
      </sharesOwnedFollowingTransaction></postTransactionAmounts>
      </nonDerivativeHolding></nonDerivativeTable></ownershipDocument>"""
    row = parse_ownership_xml(xml, filing(ORIGINAL, "3", "2026-08-12")).rows[0]
    assert row.transaction_code == "INITIAL_HOLDING" and row.economic_class == "holding_snapshot"


# ---------------------------------------------------------------------------
# Amendment matching
# ---------------------------------------------------------------------------


def keys(*codes: str) -> frozenset[RowKey]:
    return frozenset((dt.date(2026, 8, 10), c, "Common") for c in codes)


def test_match_original() -> None:
    one = OriginalCandidate("A", dt.date(2026, 8, 12), keys("P"))
    assert match_original([one], keys("S"), dt.date(2026, 8, 12)) == ("A", "linked")
    assert match_original([], keys("P"), None) == (None, "original_not_found")
    same_day = [one, OriginalCandidate("B", dt.date(2026, 8, 12), keys("S"))]
    assert match_original(same_day, keys("S"), dt.date(2026, 8, 12)) == ("B", "linked")
    # Two same-day originals the amendment overlaps equally: no guess.
    assert match_original(same_day, keys("X"), dt.date(2026, 8, 12)) == (None, "ambiguous")
    assert match_original(same_day, keys("P", "S"), dt.date(2026, 8, 12)) == (None, "ambiguous")
    other_day = [one, OriginalCandidate("C", dt.date(2026, 8, 13), keys("S"))]
    assert match_original(other_day, keys("S"), dt.date(2026, 8, 13)) == ("C", "linked")


# ---------------------------------------------------------------------------
# Net discretionary activity
# ---------------------------------------------------------------------------


def row(code_class: str, shares: int, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "economic_class": code_class,
        "shares": Decimal(shares),
        "price_per_share": Decimal(10),
        "derivative": False,
        "direct_indirect": "D",
        "rule_10b5_1_plan": None,
        "superseded_by_accession": None,
        "reporting_owner_cik": "0000222222",
        "owner_name": "Doe Jane",
        "is_director": False,
        "is_officer": True,
        "is_ten_percent_owner": False,
        "officer_title": "CFO",
    }
    return base | over


def test_grants_gifts_withholding_and_exercises_are_excluded() -> None:
    rows = [
        row("open_market_buy", 1000),
        row("open_market_sell", 400),
        row("grant_award", 9000),
        row("gift", 300),
        row("tax_withholding", 250),
        row("option_exercise", 5000),
        row("conversion", 100),
        row("other", 700),
        row("holding_snapshot", 50),
        row("open_market_buy", 123, superseded_by_accession="X"),
        row(None, 77),  # stored before classification: the raw code is never trusted
    ]
    summary, flags = summarise(rows, dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert summary.bought_shares == 1000 and summary.sold_shares == 400
    assert summary.net_shares == 600 and (summary.buy_rows, summary.sell_rows) == (1, 1)
    assert flags == [True, True] + [False] * 9
    excluded = {e.reason: (e.rows, e.shares) for e in summary.exclusions}
    assert excluded["grant_award"] == (1, 9000)
    assert excluded["gift"] == (1, 300)
    assert excluded["tax_withholding"] == (1, 250)
    assert excluded["option_exercise"] == (1, 5000)
    assert excluded["conversion"] == (1, 100)
    assert excluded["other"] == (1, 700)
    assert excluded["holding_snapshot"] == (1, 50)
    assert excluded["superseded_by_amendment"] == (1, 123)
    assert excluded["not_classified_legacy_row"] == (1, 77)
    assert summary.net_value == Decimal(6000) and summary.value_complete


def test_planned_trades_direct_indirect_and_missing_prices() -> None:
    rows = [
        row("open_market_sell", 800, rule_10b5_1_plan=True),
        row("open_market_buy", 500, rule_10b5_1_plan=False),
        row("open_market_buy", 200, direct_indirect="I", price_per_share=None),
        row("open_market_sell", 100, direct_indirect=None),
    ]
    summary, _ = summarise(rows, dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert summary.pre_planned_sold_shares == 800 and summary.sold_shares == 100
    assert summary.bought_shares == 700 and summary.plan_status_unknown_rows == 2
    assert summary.by_ownership == {"direct": 500, "indirect": 200, "unknown": -100}
    assert not summary.value_complete and summary.net_value is None  # no invented prices
    assert {b.ownership for b in summary.by_insider} == {"direct", "indirect", "unknown"}
    assert summary.by_insider[0].insider_role == "officer (CFO)"


# ---------------------------------------------------------------------------
# Database: ingest, supersession, read route
# ---------------------------------------------------------------------------


async def ingest(pool: asyncpg.Pool, accession: str, form: str, filed: str, name: str) -> None:
    f = filing(accession, form, filed)
    body = fixture(name)
    await _upsert(pool, ["EXM_US_EQ"], f, f"https://example.test/{accession}.xml", body,
                  parse_ownership_xml(body, f))  # fmt: skip


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.include_router(activity_router)

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.db
async def test_activity_read_and_amendment_supersession(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await ingest(backend_pool, ORIGINAL, "4", "2026-08-12", "form4_original.xml")
    params = {"since": "2026-01-01", "until": "2026-12-31"}
    body = (await api.get("/holdings/EXM_US_EQ/insider-activity", params=params)).json()
    net = body["net_discretionary"]
    assert (Decimal(net["bought_shares"]), Decimal(net["sold_shares"])) == (1000, 0)
    assert Decimal(net["net_value"]) == 10000 and net["value_complete"]
    assert Decimal(net["pre_planned_sold_shares"]) == 500
    reasons = {e["reason"]: e for e in net["exclusions"]}
    assert set(reasons) == {
        "pre_planned_10b5_1",
        "tax_withholding",
        "grant_award",
        "gift",
        "option_exercise",
        "other",
    }
    assert reasons["option_exercise"]["rows"] == 2  # the derivative and the stock leg
    rows = body["transactions"]
    assert len(rows) == 8
    private = next(
        r for r in rows if r["shares"] == "4000.000000000000" or Decimal(r["shares"]) == 4000
    )
    # The raw code P is shown as reported, but it is `other`, not a conviction purchase.
    assert private["raw_transaction_code"] == "P" and private["economic_class"] == "other"
    assert private["counts_toward_net_discretionary"] is False
    assert "conviction" not in str(body).lower().replace("not evidence of conviction", "")
    assert {c["jurisdiction"]: c["access"] for c in body["jurisdiction_coverage"]} == {
        "US": "available",
        "GB": "access_unverified",
    }
    assert body["limitations"]
    derivative = next(r for r in rows if r["derivative"])
    assert (
        Decimal(derivative["exercise_price"]) == 5 and derivative["expiration_date"] == "2030-08-10"
    )

    # The /A restates the original: the original's rows leave the net figure.
    await ingest(backend_pool, AMENDMENT, "4/A", "2026-08-20", "form4_amendment.xml")
    links = {
        r["accession"]: r
        for r in await backend_pool.fetch(
            "select accession, amends_accession, superseded_by_accession, amendment_link_status"
            " from insider_filing"
        )
    }
    assert links[AMENDMENT]["amends_accession"] == ORIGINAL
    assert links[AMENDMENT]["amendment_link_status"] == "linked"
    assert links[ORIGINAL]["superseded_by_accession"] == AMENDMENT
    after = await holding_insider_activity(
        backend_pool, "EXM_US_EQ", since=dt.date(2026, 1, 1), until=dt.date(2026, 12, 31)
    )
    n = after.net_discretionary
    # The amendment's filing-wide check box only makes a plan *possible* (audit B33): the
    # rows stay in the discretionary figure with unknown plan status and are flagged, and the
    # original's rows are excluded rather than double counted.
    # The sale carries a footnote naming the plan (True); the purchase only the box (possible).
    assert (n.bought_shares, n.sold_shares) == (1000, 0)
    assert n.pre_planned_bought_shares == 0 and n.pre_planned_sold_shares == 500
    assert n.plan_possible_rows == 1 and n.plan_status_unknown_rows == 1
    gone = next(e for e in n.exclusions if e.reason == "superseded_by_amendment")
    assert gone.rows == 8
    sold = next(t for t in after.transactions if t.shares == 500 and t.form == "4/A")
    assert sold.price_per_share == Decimal("12.50")


@pytest.mark.db
async def test_amendment_arriving_first_and_unlinked_amendment(backend_pool: asyncpg.Pool) -> None:
    await ingest(backend_pool, AMENDMENT, "4/A", "2026-08-20", "form4_amendment.xml")
    status = await backend_pool.fetchval(
        "select amendment_link_status from insider_filing where accession = $1", AMENDMENT
    )
    assert status == "original_not_found"
    await ingest(backend_pool, ORIGINAL, "4", "2026-08-12", "form4_original.xml")
    row = await backend_pool.fetchrow(
        "select amends_accession, amendment_link_status from insider_filing where accession = $1",
        AMENDMENT,
    )
    assert (row["amends_accession"], row["amendment_link_status"]) == (ORIGINAL, "linked")


@pytest.mark.db
async def test_legacy_unclassified_rows_never_use_the_raw_code(backend_pool: asyncpg.Pool) -> None:
    await backend_pool.execute(
        "insert into insider_filing (accession, ticker, issuer_cik, form, filed, source_url)"
        " values ($1, 'EXM_US_EQ', '0000111111', '4', '2026-08-12', 'u')",
        ORIGINAL,
    )
    await backend_pool.execute(
        "insert into insider_transaction (accession, row_index, reporting_owner_cik,"
        " transaction_code, shares, transaction_date) values ($1, 0, '0000222222', 'P', 100,"
        " '2026-08-10')",
        ORIGINAL,
    )
    result = await holding_insider_activity(
        backend_pool, "EXM_US_EQ", since=dt.date(2026, 1, 1), until=dt.date(2026, 12, 31)
    )
    assert result.net_discretionary.bought_shares == 0
    assert result.transactions[0].economic_class is None
    assert result.transactions[0].exclusion_reason == "not_classified_legacy_row"


# ---------------------------------------------------------------------------
# UK PDMR pilot
# ---------------------------------------------------------------------------


def pdmr_text() -> str:
    return html_to_text((FIXTURES / "pdmr_uk" / "rns_pdmr.html").read_text(encoding="utf-8"))


def test_pdmr_items_are_selected_from_the_rns_listing() -> None:
    html = (FIXTURES / "pdmr_uk" / "rns_listing.html").read_text()
    announcements = parse_investegate(html, "https://www.investegate.co.uk/company/EXM")
    assert [is_pdmr_announcement(a.headline) for a in announcements] == [True, False]
    assert is_pdmr_announcement("Holding(s) in Company", "insider_transaction")


def test_pdmr_notification_is_parsed_and_classified() -> None:
    n = parse_pdmr_notification(pdmr_text())
    assert n is not None
    assert (n.person_name, n.position) == ("Jane Smith", "Chief Financial Officer")
    assert n.issuer_name == "Example plc" and n.lei == "213800ABCDEFGHIJKL12"
    assert not n.is_amendment and len(n.transactions) == 2
    buy, award = n.transactions
    assert buy.isin == "GB00B0000001" and buy.transaction_date == dt.date(2026, 9, 15)
    assert (buy.price, buy.currency, buy.volume) == (Decimal("4.52"), "GBP", Decimal(1000))
    assert buy.economic_class == "open_market_buy"
    assert award.economic_class == "grant_award" and award.price is None
    assert award.volume == Decimal(12500) and award.transaction_date == dt.date(2026, 9, 15)


def test_pdmr_purchase_outside_a_trading_venue_is_not_open_market() -> None:
    assert classify_nature("Purchase of shares", "Outside a trading venue")[0] == "other"
    assert classify_nature("Purchase of shares", None)[0] == "other"
    assert classify_nature("Sale of shares to cover tax", "XLON")[0] == "tax_withholding"
    assert classify_nature("Exercise of options", "XLON")[0] == "option_exercise"
    assert classify_nature("Sale of ordinary shares", "XLON")[0] == "open_market_sell"
    assert parse_pdmr_notification("Holding(s) in Company: 5.01% of voting rights") is None


async def pdmr_fetch(status: int, body: str = "") -> Any:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await fetch_pdmr(http, "https://www.investegate.co.uk/announcement/rns/x/1")


async def test_pdmr_fetch_maps_denials_to_typed_states_never_empty_success() -> None:
    page = (FIXTURES / "pdmr_uk" / "rns_pdmr.html").read_text()
    ok = await pdmr_fetch(200, page)
    assert ok.access.state is AccessState.AVAILABLE and ok.notification is not None
    shell = await pdmr_fetch(
        200, "<html><body><div id='app'></div><script>x</script></body></html>"
    )
    assert shell.access.state is AccessState.ACCESS_UNVERIFIED and shell.notification is None
    assert (await pdmr_fetch(403)).access.state is AccessState.ACCESS_UNVERIFIED
    assert (await pdmr_fetch(429)).access.state is AccessState.QUOTA_EXHAUSTED
    assert (await pdmr_fetch(503)).access.state is AccessState.TEMPORARILY_FAILED
    standing = pdmr_access()
    assert standing.state is AccessState.ACCESS_UNVERIFIED and "not demonstrated" in (
        standing.detail or ""
    )
