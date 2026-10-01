"""Graph stream, offline parsers and pure logic on recorded-format fixtures (no database).

The fixtures in tests/fixtures/graph follow the public documentation of EPO OPS (exchange-document
structure), the TED search API v3, the Wikidata entity JSON and the wording of SEC 8-K items.
They are not live captures."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from steward.graph import ops
from steward.graph.deal_extract import extract_events, extract_terms
from steward.graph.deals import (
    BaseCapital,
    DealEvent,
    DealTransitionError,
    confirmed_terms,
    derive_state,
    normalize_terms,
    pro_forma,
    validate_transition,
)
from steward.graph.diffbot import parse_diffbot_date, parse_diffbot_relations
from steward.graph.keys import evidence_class, normalize_object_key
from steward.graph.passages import (
    analyze_counterparty_filing,
    analyze_issuer_filing,
    parse_period,
)
from steward.graph.patents import competitor_overlap, patent_trend
from steward.graph.ted import parse_ted_response
from steward.graph.wikidata_statements import parse_entity_statements
from steward.news.feeds import alpha_denial_state, parse_alpha_time
from steward.sources.base import AccessState
from steward.sources.company_research import (
    normalize_commodity_unit,
    parse_ops_patent_xml,
)
from steward.sources.free_market import FreeSourceError

FIXTURES = Path(__file__).parent / "fixtures" / "graph"
ALIASES = ("Example Industries AG", "Example Robotics GmbH")


def text(name: str) -> str:
    return (FIXTURES / name).read_text()


# ------------------------------------------------------------------------------- A20: OPS


def test_ops_parser_reads_the_documented_exchange_document_structure() -> None:
    """The audit's defect: the parser read attributes where OPS uses child elements and
    returned [] on a real-structure payload; the family key was a bare doc-number."""
    page = ops.parse_ops_page(text("ops_biblio_search.xml"), ALIASES)
    assert page.total == 4 and page.documents == 4
    by_id = {f.family_id: f for f in page.families}
    # The unrelated applicant's family is excluded; the A1 (EP) and B2 (US) merged into one.
    assert set(by_id) == {"25134712", "40111222"}
    fam = by_id["25134712"]
    assert fam.jurisdictions == ["EP", "US"]
    assert {(p.country, p.doc_number, p.kind) for p in fam.publications} == {
        ("EP", "1840100", "A1"),
        ("US", "8123456", "B2"),
    }
    assert fam.priority_date == dt.date(2005, 11, 15)  # earliest of the priority claims
    assert fam.application_date == dt.date(2007, 1, 5)
    assert fam.title == "Method for producing a semiconductor component"  # English title
    assert fam.cpc_codes == {"H01L21/02", "H01L29/78"}
    # "original" format names win over the upper-case EPODOC form with a [DE] tag.
    assert fam.applicants == ["Example Industries AG"]
    # Distinct cited documents across the family: US2003123456A1, EP1000001A2, US6000000A.
    assert len(fam.cited) == 3
    assert fam.first_publication_date == dt.date(2007, 10, 3)
    assert fam.grant_date == dt.date(2012, 2, 28)


def test_ops_dict_view_uses_the_ops_family_id_as_the_key_and_marks_citations_unknown() -> None:
    patents = parse_ops_patent_xml(text("ops_biblio_search.xml"), ("Example Industries AG",))
    assert {p["family_key"] for p in patents} == {"25134712", "40111222"}
    first = next(p for p in patents if p["family_key"] == "25134712")
    assert first["backward_citations"] == 3
    assert "not provided" in str(first["raw"])  # forward citations are never reported as 0
    assert parse_ops_patent_xml(text("ops_biblio_search.xml"), ("Nobody Corp",)) == []


def test_ops_parser_matches_the_epodoc_name_when_no_original_is_given() -> None:
    xml = text("ops_biblio_search.xml").replace(
        '<applicant sequence="1" data-format="original">\n'
        "                  <applicant-name>\n"
        "                    <name>Example Industries AG</name>\n"
        "                  </applicant-name>\n"
        "                  <residence>\n"
        "                    <country>DE</country>\n"
        "                  </residence>\n"
        "                </applicant>",
        "",
    )
    page = ops.parse_ops_page(xml, ("Example IND AG",))
    # "EXAMPLE IND AG [DE]" -> the country tag is stripped before matching
    assert any("25134712" == f.family_id for f in page.families)


def test_ops_parser_rejects_oversized_or_malformed_documents() -> None:
    with pytest.raises(FreeSourceError):
        ops.parse_ops_page("<not-xml", ALIASES)
    with pytest.raises(FreeSourceError):
        ops.parse_ops_page("x" * 5_000_001, ALIASES)


def test_ops_legal_status_is_derived_from_kind_codes_and_legal_events() -> None:
    page = ops.parse_ops_page(text("ops_biblio_search.xml"), ALIASES)
    fam = next(f for f in page.families if f.family_id == "25134712")
    events = ops.parse_ops_legal(text("ops_legal.xml"))["25134712"]
    assert len(events) == 3
    # A lapse in one contracting state is not the end of the family; a maintenance-fee payment
    # after grant is evidence the US right is in force.
    status, _ = ops.derive_legal_status(
        fam, [e for e in events if "MAINTENANCE" in e.description], today=dt.date(2026, 10, 1)
    )
    assert status == "active"
    lapsed, when = ops.derive_legal_status(fam, events, today=dt.date(2026, 10, 1))
    assert lapsed == "expired" and when == dt.date(2014, 6, 30)
    # Biblio data alone: B kind code -> granted (not "active": nothing proves it is in force).
    assert ops.derive_legal_status(fam, [], today=dt.date(2026, 10, 1))[0] == "granted"
    wo = next(f for f in page.families if f.family_id == "40111222")
    assert ops.derive_legal_status(wo, [], today=dt.date(2026, 10, 1)) == ("application", None)
    # The 20-year term from filing ends it regardless.
    assert ops.derive_legal_status(fam, [], today=dt.date(2030, 1, 1))[0] == "expired"


def page_xml(start: int, count: int, total: int) -> str:
    docs = "".join(
        f'<exchange-document family-id="{9000 + start + i}" country="EP" '
        f'doc-number="{100000 + start + i}" kind="A1"><bibliographic-data>'
        '<publication-reference><document-id document-id-type="docdb"><country>EP</country>'
        f"<doc-number>{100000 + start + i}</doc-number><kind>A1</kind><date>20200101</date>"
        "</document-id></publication-reference><parties><applicants>"
        '<applicant data-format="original"><applicant-name><name>Example Industries AG'
        "</name></applicant-name></applicant></applicants></parties>"
        "</bibliographic-data></exchange-document>"
        for i in range(count)
    )
    return (
        '<ops:world-patent-data xmlns="http://www.epo.org/exchange" xmlns:ops="http://ops.epo.org">'
        f'<ops:biblio-search total-result-count="{total}"><ops:search-result>'
        f"<exchange-documents>{docs}</exchange-documents></ops:search-result>"
        "</ops:biblio-search></ops:world-patent-data>"
    )


async def test_ops_search_pages_through_every_alias_group_and_stops_at_the_total() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        rng = request.url.params["Range"]
        seen.append((request.url.params["q"], rng))
        start = int(rng.split("-")[0]) - 1
        count = max(0, min(100, 230 - start))
        return httpx.Response(200, text=page_xml(start, count, 230))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await ops.search_applicants(http, "tok", ALIASES)
    assert [r for _, r in seen] == ["1-100", "101-200", "201-300"]
    assert 'pa="Example Industries AG" or pa="Example Robotics GmbH"' == seen[0][0]
    assert len(result.families) == 230 and result.pages == 3 and not result.truncated


async def test_ops_search_groups_many_aliases_and_treats_404_as_no_results() -> None:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params["q"])
        return httpx.Response(404, text="<fault><code>SERVER.EntityNotFound</code></fault>")

    names = tuple(f"Sub {i} GmbH" for i in range(7))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await ops.search_applicants(http, "tok", names)
    assert len(queries) == 2  # 5 + 2 aliases
    assert queries[0].count(" or ") == 4 and queries[1].count(" or ") == 1
    assert result.families == {} and not result.truncated


async def test_ops_search_stops_cleanly_when_the_budget_runs_out() -> None:
    from steward.sources.governor import QuotaExceeded

    calls = 0

    async def reserve() -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise QuotaExceeded("monthly budget")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page_xml(0, 100, 500))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await ops.search_applicants(http, "tok", ALIASES, reserve_request=reserve)
    assert result.truncated and "budget" in (result.stopped_reason or "")
    assert len(result.families) == 100  # what was read is kept


@pytest.mark.parametrize(
    ("status", "body", "kind", "state"),
    [
        (400, '{"error":"invalid_client"}', "invalid_credentials", AccessState.ACCESS_UNVERIFIED),
        (401, "", "invalid_credentials", AccessState.ACCESS_UNVERIFIED),
        (403, "IndividualQuotaPerHour exceeded", "quota", AccessState.QUOTA_EXHAUSTED),
        (403, "Forbidden", "forbidden", None),
        (503, "", "transient", AccessState.TEMPORARILY_FAILED),
    ],
)
async def test_epo_oauth_failures_are_typed(
    status: int, body: str, kind: str, state: AccessState | None
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ops.EpoAuthError) as err:
            await ops.fetch_token(http, "id", "secret")
    assert err.value.kind == kind and err.value.state == state


async def test_epo_oauth_network_error_is_transient_and_the_token_is_returned() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        with pytest.raises(ops.EpoAuthError) as err:
            await ops.fetch_token(http, "id", "secret")
    assert err.value.kind == "transient" and err.value.provider_charged is False

    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "abc", "expires_in": "1199"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as http:
        assert await ops.fetch_token(http, "id", "secret") == "abc"


# ----------------------------------------------------------------------------- patents trend


def test_patent_trend_counts_a_family_once_and_splits_status_and_timing() -> None:
    base = {
        "family_key": "F1",
        "priority_date": dt.date(2010, 3, 1),
        "cpc_codes": ["H01L21/02", "H01L21/02"],
        "citations_forward": 4,
        "legal_status": "granted",
        "grant_date": dt.date(2013, 3, 1),
        "expiry_date": None,
    }
    rows = [
        base,
        dict(base),  # the same family joined through a second owner row
        {
            **base,
            "family_key": "F2",
            "legal_status": "application",
            "grant_date": None,
            "citations_forward": 1,
        },
        {
            **base,
            "family_key": "F3",
            "legal_status": "expired",
            "grant_date": dt.date(2012, 3, 1),
            "expiry_date": dt.date(2025, 3, 1),
        },
    ]
    trend = patent_trend(rows, today=dt.date(2026, 10, 1))
    bucket = next(t for t in trend if t["cpc"] == "H01L21/02" and t["year"] == 2010)
    assert bucket["families"] == 3
    assert bucket["forward_citations"] == 4 + 1 + 4  # F1 once, not twice
    counts = bucket["legal_status_counts"]
    assert counts["granted"] == 1 and counts["application"] == 1 and counts["expired"] == 1
    assert bucket["median_years_to_grant"] == 2.5  # F1 3.0y and F3 2.0y
    assert bucket["expired"] >= 1  # F3 recorded expiry + the estimated 20y ends for the rest


def test_patent_competitor_overlap_uses_cpc_subclass_and_reports_missing_peer_data() -> None:
    own = [
        {"family_key": "A", "cpc_codes": ["H01L21/02", "G06N3/08"]},
        {"family_key": "B", "cpc_codes": ["A61B5/00"]},
    ]
    peers = {
        "PEER1": [{"family_key": "P", "cpc_codes": ["H01L29/78"]}],
        "PEER2": [],
    }
    result = {r["peer"]: r for r in competitor_overlap(own, peers)}
    assert result["PEER1"]["shared_subclasses"] == ["H01L"]
    assert result["PEER1"]["overlap_share"] == 0.5  # one of two own families overlaps
    assert result["PEER2"]["peer_families"] == 0 and result["PEER2"]["overlap_share"] is None


# --------------------------------------------------------------------------------- keys


def test_normalized_object_key_agrees_for_name_variants() -> None:
    assert (
        normalize_object_key("Siemens AG")
        == normalize_object_key("SIEMENS A.G.")
        == normalize_object_key("name:siemens")
        == "name:siemens"
    )
    assert normalize_object_key("The Coca-Cola Company") == "name:coca cola"
    assert normalize_object_key("5493001KJTIIGC8Y1R12") == "5493001KJTIIGC8Y1R12"
    assert normalize_object_key("wd:q42") == "wd:Q42"
    assert normalize_object_key("Alpha & Omega Holdings Inc.") == "name:alpha and omega"


def test_evidence_class_matches_whole_tokens_only() -> None:
    assert evidence_class("sec") == "regulatory"
    assert evidence_class("epo_ops") == "regulatory"
    assert evidence_class("owner_review") == "owner_review"
    assert evidence_class("wikidata") == "community"
    assert evidence_class("diffbot") == "vendor"
    assert evidence_class("secure_data_vendor") == "unknown"  # not the SEC
    assert evidence_class(None) == "unknown"


# --------------------------------------------------------------------------------- Diffbot


def test_diffbot_fields_have_explicit_direction_and_their_own_dates() -> None:
    entity = {
        "id": "E1",
        "name": "Example Industries AG",
        "acquisitions": [
            {
                "acquiree": {
                    "name": "Target Software Inc",
                    "diffbotUri": "https://diffbot.com/entity/E2",
                },
                "date": {"str": "d2021-06-15", "precision": 3},
            }
        ],
        "acquiredBy": {"name": "MegaCorp"},
        "subsidiaries": [{"name": "Example Robotics GmbH"}],
        "investors": [
            {"investor": {"name": "Big Fund"}, "startDate": {"str": "d2018", "precision": 1}}
        ],
        "competitors": [{"name": "Rival SA"}],
        "relationships": [{"type": "acquisition", "object": {"name": "Ignored"}}],
    }
    rels = {(r.provider_field, r.object_name): r for r in parse_diffbot_relations(entity)}
    assert rels[("acquisitions", "Target Software Inc")].edge_type == "acquirer"  # holding acquired
    assert rels[("acquisitions", "Target Software Inc")].direction == "outgoing"
    assert rels[("acquisitions", "Target Software Inc")].valid_from == dt.date(2021, 6, 15)
    assert rels[("acquiredBy", "MegaCorp")].edge_type == "acquired"
    assert rels[("acquiredBy", "MegaCorp")].direction == "incoming"
    assert rels[("acquiredBy", "MegaCorp")].valid_from is None  # never the fetch date
    assert rels[("subsidiaries", "Example Robotics GmbH")].edge_type == "subsidiary"
    investor = rels[("investors", "Big Fund")]
    assert (investor.edge_type, investor.direction) == ("investment", "incoming")
    assert investor.valid_from == dt.date(2018, 1, 1) and investor.detail["date_precision"] == 1
    assert rels[("competitors", "Rival SA")].direction == "symmetric"
    assert not any(name == "Ignored" for _, name in rels)  # free-text relationships not guessed
    assert parse_diffbot_date("not a date") == (None, None)


# ------------------------------------------------------------------------------------ TED


def test_ted_awards_parse_the_documented_search_response() -> None:
    awards, total = parse_ted_response(json.loads(text("ted_search_response.json")))
    assert total == 3
    assert [a.number for a in awards] == [
        "234567-2026",
        "345678-2026",
    ]  # the contract notice is skipped
    first = awards[0]
    assert first.title == "Supply of rail signalling equipment"  # English first
    assert first.buyer == "Deutsche Bahn Infrastructure AG"
    assert first.winners == ("Example Industries AG",)
    assert first.value == Decimal("15250000") and first.currency == "EUR"
    assert first.published == dt.date(2026, 3, 4)
    assert first.url.endswith("234567-2026")
    with pytest.raises(FreeSourceError):
        parse_ted_response({"error": "bad"})


# ------------------------------------------------------------------------ deal state machine


def event(
    i: int,
    state: str,
    day: int,
    status: str = "confirmed",
    terms: dict | None = None,
    known_day: int | None = None,
) -> DealEvent:
    return DealEvent(
        id=i,
        state=state,
        status=status,
        date=dt.date(2026, 1, day),
        known_at=dt.datetime(2026, 1, known_day or day, tzinfo=dt.UTC),
        terms=terms or {},
        evidence_id=i,
    )


def test_deal_transitions_are_validated() -> None:
    validate_transition(None, "closed")  # first sight may be anywhere
    validate_transition("announced", "approved")
    validate_transition("amended", "amended")
    validate_transition("closed", "closed")  # a second document corroborates the same state
    for bad in (
        ("closed", "announced"),
        ("terminated", "closed"),
        ("reversed", "announced"),
        ("announced", "reversed"),
        ("closed", "terminated"),
    ):
        with pytest.raises(DealTransitionError):
            validate_transition(*bad)
    with pytest.raises(DealTransitionError):
        validate_transition(None, "unknown")


def test_current_state_is_derived_from_dates_and_known_as_of() -> None:
    events = [event(1, "announced", 5), event(2, "approved", 12), event(3, "closed", 20)]
    assert derive_state(events).current == "closed"
    assert derive_state(events, as_of=dt.datetime(2026, 1, 13, tzinfo=dt.UTC)).current == "approved"
    # A state discovered later but dated earlier is a backfill: ordered by date, nothing rewritten.
    late_backfill = [event(1, "closed", 20), event(2, "announced", 5, known_day=25)]
    assert derive_state(late_backfill).current == "closed"
    assert [e.state for e in derive_state(late_backfill).history] == ["announced", "closed"]
    assert (
        derive_state(late_backfill, as_of=dt.datetime(2026, 1, 22, tzinfo=dt.UTC)).current
        == "closed"
    )
    # An impossible event is ignored and reported, never silently reordered.
    bad = derive_state([event(1, "closed", 5), event(2, "announced", 9)])
    assert bad.current == "closed" and bad.ignored and bad.ignored[0][0].id == 2
    # Terminal states are final.
    assert (
        derive_state(
            [event(1, "announced", 1), event(2, "terminated", 2), event(3, "approved", 3)]
        ).current
        == "terminated"
    )


def test_suggested_events_never_set_the_confirmed_state_or_terms() -> None:
    events = [
        event(1, "announced", 1, "confirmed", {"price_per_share": "10", "total_value": "1000"}),
        event(2, "amended", 2, "suggested", {"price_per_share": "99"}),
        event(3, "amended", 3, "confirmed", {"price_per_share": "12"}),
        event(4, "approved", 4, "rejected"),
        event(5, "approved", 5, "disputed"),
    ]
    assert derive_state(events).current == "amended"  # rejected and disputed never count
    assert derive_state(events, confirmed_only=True).current == "amended"
    terms, provenance = confirmed_terms(events)
    assert terms == {"price_per_share": "12", "total_value": "1000"}  # later amendment wins
    assert provenance == {"price_per_share": 3, "total_value": 1}


def test_normalize_terms_validates() -> None:
    clean = normalize_terms(
        {"price_per_share": 38.5, "cash_pct": 60, "stock_pct": 40, "currency": "USD"}
    )
    assert clean["price_per_share"] == "38.5" and clean["cash_pct"] == "60"
    for bad in (
        {"price_per_share": -1},
        {"cash_pct": 70, "stock_pct": 40},
        {"bogus": 1},
        {"total_value": "abc"},
        {"stock_pct": 120},
    ):
        with pytest.raises(ValueError):
            normalize_terms(bad)


def test_pro_forma_dilution_and_leverage_from_confirmed_terms() -> None:
    terms = normalize_terms(
        {
            "total_value": "4200000000",
            "cash_pct": "60",
            "stock_pct": "40",
            "shares_to_issue": "21000000",
            "debt_raised": "2800000000",
            "debt_assumed": "600000000",
            "target_ebitda": "300000000",
        }
    )
    base = BaseCapital(
        shares_outstanding=Decimal("200000000"),
        total_debt=Decimal("5000000000"),
        cash=Decimal("1000000000"),
        ebitda=Decimal("2000000000"),
        share_price=Decimal("80"),
    )
    out = pro_forma(terms, base)
    assert out["share_count_increase"] == Decimal("0.1050")
    assert out["new_holders_ownership"] == Decimal("0.0950")
    assert out["gross_leverage_before"] == Decimal("2.5000")
    assert out["gross_debt_after"] == Decimal("8400000000")
    assert out["gross_leverage_after"] == Decimal("3.6522")  # 8.4bn / (2.0bn + 0.3bn)
    # Net debt rises by the cash consideration (2.52bn) + assumed debt (0.6bn).
    assert out["net_debt_increase"] == Decimal("3120000000")
    assert out["net_leverage_before"] == Decimal("2.0000")
    assert out["net_leverage_after"] == Decimal("3.0957")  # (4.0bn + 3.12bn) / 2.3bn
    assert out["inputs_missing"] == []


def test_pro_forma_reports_missing_inputs_instead_of_guessing() -> None:
    out = pro_forma({"stock_value": "1000000000"}, BaseCapital())
    assert out["share_count_increase"] is None and out["gross_leverage_after"] is None
    assert "shares outstanding" in out["inputs_missing"]
    assert any("share price" in m for m in out["inputs_missing"])
    # With a price the share count is derived from the stock consideration.
    priced = pro_forma(
        {"stock_value": "1000000000"},
        BaseCapital(shares_outstanding=Decimal("100000000"), share_price=Decimal("50")),
    )
    assert priced["shares_issued"] == Decimal("20000000")


# ---------------------------------------------------------------------- 8-K item extraction


def test_extract_merger_announcement_terms_and_counterparty() -> None:
    events = extract_events(
        form="8-K",
        filed=dt.date(2026, 9, 15),
        items="1.01,9.01",
        documents=[("a.htm", text("8k_merger_announced.txt"))],
    )
    assert len(events) == 1
    ev = events[0]
    assert (ev.event_type, ev.state, ev.item) == ("deal", "announced", "1.01")
    assert ev.counterparty == "Target Software Inc"
    assert ev.terms["price_per_share"] == "38.50" and ev.terms["currency"] == "USD"
    assert ev.terms["total_value"] == "4200000000"
    assert (ev.terms["cash_pct"], ev.terms["stock_pct"]) == ("60", "40")
    assert ev.terms["shares_to_issue"] == "21000000"
    assert ev.terms["debt_raised"] == "2800000000"  # notes + term loan
    assert "Merger Agreement" in ev.quote


def test_extract_completion_credit_agreement_and_termination() -> None:
    done = extract_events(
        form="8-K",
        filed=dt.date(2027, 3, 3),
        items="2.01,9.01",
        documents=[("b.htm", text("8k_merger_completed.txt"))],
    )
    assert [(e.event_type, e.state) for e in done] == [("deal", "closed")]
    assert done[0].terms["debt_assumed"] == "600000000"
    # "all-cash and stock" is a mix, not an all-cash deal.
    assert (
        "cash_pct" not in done[0].terms and done[0].terms["consideration_type"] == "cash_and_stock"
    )

    credit = extract_events(
        form="8-K",
        filed=dt.date(2026, 11, 2),
        items="1.01,2.03",
        documents=[("c.htm", text("8k_credit_agreement.txt"))],
    )
    assert [(e.event_type, e.state) for e in credit] == [
        ("financing", "announced"),
        ("financing", "closed"),
    ]
    assert credit[0].terms["facility_size"] == "2500000000"
    assert "debt_raised" not in credit[0].terms  # a committed line is capacity, not borrowing
    assert credit[1].terms["debt_raised"] == "1000000000"

    ended = extract_events(
        form="8-K",
        filed=dt.date(2027, 1, 20),
        items="1.02",
        documents=[("d.htm", text("8k_merger_terminated.txt"))],
    )
    assert [(e.event_type, e.state) for e in ended] == [("deal", "terminated")]


def test_extract_6k_press_release_without_items_and_ignores_unlisted_items() -> None:
    release = (
        "Example plc announces completion of acquisition. Example plc today announced that it has "
        "completed the acquisition of Widget Holdings Ltd for $250 million in cash."
    )
    events = extract_events(
        form="6-K", filed=dt.date(2026, 5, 2), items="", documents=[("ex99.htm", release)]
    )
    assert [(e.state, e.counterparty) for e in events] == [("closed", "Widget Holdings Ltd")]
    assert extract_terms("nothing here") == {}
    # An item that the filing does not list is not read.
    assert (
        extract_events(
            form="8-K",
            filed=dt.date(2026, 9, 15),
            items="9.01",
            documents=[("a.htm", text("8k_merger_announced.txt"))],
        )
        == []
    )


# --------------------------------------------------------------------- passage resolution


NAMES = ["Example Industries AG", "Example Industries"]


def test_period_parsing_gives_fiscal_label_and_dates() -> None:
    assert parse_period("for the fiscal year ended December 31, 2025") == (
        "FY2025",
        dt.date(2025, 1, 1),
        dt.date(2025, 12, 31),
    )
    assert parse_period("for the three months ended June 30, 2026") == (
        "2026Q2",
        dt.date(2026, 4, 1),
        dt.date(2026, 6, 30),
    )
    assert parse_period("no period here") == (None, None, None)


def test_filer_buying_from_the_holding_makes_the_filer_the_holdings_customer() -> None:
    res = analyze_counterparty_filing(
        text("10k_filer_names_holding.txt"),
        holding_names=NAMES,
        filer_name="Acme Controls Inc",
        filer_cik="1234567",
    )
    # Boilerplate and licensing paragraphs that merely name the holding yield nothing.
    assert len(res) == 1
    r = res[0]
    assert (r.relationship, r.direction) == ("customer", "issuer_sells_to")
    assert r.counterparty_name == "Acme Controls Inc" and r.counterparty_key == "cik:0001234567"
    assert r.reported_share == Decimal("0.14")
    assert r.share_basis == "counterparty_purchases"  # 14% of the FILER's purchases
    assert (r.fiscal_period, r.period_end) == ("FY2025", dt.date(2025, 12, 31))
    assert not r.anonymous


def test_filer_selling_to_the_holding_makes_the_filer_the_holdings_supplier() -> None:
    (r,) = analyze_counterparty_filing(
        text("10k_filer_sells_to_holding.txt"),
        holding_names=NAMES,
        filer_name="Sensor Parts Inc",
        filer_cik="42",
    )
    assert (r.relationship, r.direction) == ("supplier", "issuer_buys_from")
    assert r.reported_share == Decimal("0.22") and r.share_basis == "counterparty_revenue"
    assert r.fiscal_period == "FY2026" and r.period_start == dt.date(2025, 7, 1)


def test_wording_pointing_both_ways_stays_unknown_for_the_owner() -> None:
    both = (
        "We sell modules to Example Industries AG and we also purchase wafers from Example "
        "Industries AG under separate agreements for the year ended December 31, 2025."
    )
    (r,) = analyze_counterparty_filing(
        both, holding_names=NAMES, filer_name="X Corp", filer_cik=None
    )
    assert r.direction == "unknown" and r.reported_share is None


def test_a_percentage_without_a_stated_denominator_is_dropped() -> None:
    vague = (
        "We sell to Example Industries AG, which was 22% of the total for the year ended "
        "June 30, 2026."
    )
    (r,) = analyze_counterparty_filing(
        vague, holding_names=NAMES, filer_name="Y Inc", filer_cik="9"
    )
    assert r.reported_share is None and r.share_basis is None
    assert any("denominator" in why for why in r.reasons)


def test_issuer_concentration_keeps_named_and_anonymous_apart_and_labels_the_basis() -> None:
    res = analyze_issuer_filing(text("10k_issuer_concentration.txt"), holding_names=["Example"])
    by_share = {r.reported_share: r for r in res}
    assert (
        by_share[Decimal("0.18")].anonymous and by_share[Decimal("0.18")].counterparty_name is None
    )
    assert by_share[Decimal("0.18")].share_basis == "issuer_revenue"
    named = by_share[Decimal("0.09")]
    assert not named.anonymous and named.counterparty_name == "Northwind Traders"
    assert named.share_basis == "issuer_receivables"  # not concentration of revenue
    supplier = by_share[Decimal("0.31")]
    assert (supplier.relationship, supplier.direction, supplier.share_basis) == (
        "supplier",
        "issuer_buys_from",
        "issuer_purchases",
    )
    assert all(r.fiscal_period == "FY2025" for r in res)


# ------------------------------------------------------------------------------- Wikidata


def test_wikidata_statements_carry_rank_qualifiers_and_references() -> None:
    sts = parse_entity_statements(json.loads(text("wikidata_entity.json")), "Q12345")
    by_id = {s.statement_id: s for s in sts}
    # P31 is not tracked; "somevalue" carries no claim.
    assert set(by_id) == {"Q12345$AAAA-1111", "Q12345$BBBB-2222", "Q12345$CCCC-3333"}
    parent = by_id["Q12345$AAAA-1111"]
    assert (parent.property_id, parent.value_qid, parent.rank) == ("P749", "Q777", "preferred")
    assert parent.start_time == dt.date(2019, 4, 1)
    assert parent.references == [{"P854": ["https://example.test/annual-report"]}]
    assert by_id["Q12345$BBBB-2222"].rank == "deprecated"
    assert by_id["Q12345$CCCC-3333"].value_text == "https://www.example.test"
    assert parent.content_hash != by_id["Q12345$BBBB-2222"].content_hash
    with pytest.raises(ValueError):
        parse_entity_statements({"entities": {}}, "Q1")


# ---------------------------------------------------------------- Alpha Vantage news (G17)


def test_alpha_news_time_is_the_real_timestamp_and_denials_are_typed() -> None:
    assert parse_alpha_time("20260930T143015") == dt.datetime(
        2026, 9, 30, 14, 30, 15, tzinfo=dt.UTC
    )
    assert parse_alpha_time("20260930T1430") == dt.datetime(2026, 9, 30, 14, 30, tzinfo=dt.UTC)
    assert parse_alpha_time("garbage") is None and parse_alpha_time(None) is None
    assert (
        alpha_denial_state(FreeSourceError("x", state=AccessState.PAID_ONLY))
        == AccessState.PAID_ONLY
    )
    assert alpha_denial_state(FreeSourceError("x", 429)) == AccessState.QUOTA_EXHAUSTED
    assert alpha_denial_state(FreeSourceError("x", 403)) == AccessState.ACCESS_UNVERIFIED
    assert alpha_denial_state(FreeSourceError("x", 500)) == AccessState.TEMPORARILY_FAILED


def test_commodity_cents_are_converted_not_labelled_dollars() -> None:
    assert normalize_commodity_unit("cents per pound") == ("USD per pound", "USD", Decimal(100))
    assert normalize_commodity_unit("dollars per barrel") == (
        "dollars per barrel",
        "USD",
        Decimal(1),
    )
    assert normalize_commodity_unit("index") == ("index", None, Decimal(1))
