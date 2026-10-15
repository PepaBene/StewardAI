"""Pre-apply `verify` and post-apply `recheck` of nine autopilot kinds: listing, peer, identifier,
deal_party, estimate_pair, disclosure_review, adjudication and insider_override (identity loads
nothing and has neither). The rules are pure helpers tested with plain values; the `db` tests run
the thin wrappers against the real tables."""

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from steward.autopilot import ledger
from steward.autopilot.judge import Candidate
from steward.autopilot.kinds import approvals as ap
from steward.autopilot.kinds import disclosure as dis
from steward.autopilot.kinds import disclosure_data as dd
from steward.disclosure import adjudication, estimate_read, review
from steward.disclosure import entities as ent
from steward.disclosure import estimates as est
from steward.disclosure.insider_analysis import record_override
from steward.reference.listings import confirm_listing
from tests.test_autopilot_approvals import NOTE as AP_NOTE
from tests.test_autopilot_approvals import OWNER, seed_link, seed_peer
from tests.test_autopilot_disclosure import (
    _adj_cand,
    _insider_line,
    _mapping_review,
    _suggestion,
)
from tests.test_autopilot_disclosure_data import LEI, make_value, suggestion
from tests.test_autopilot_disclosure_data import NOTE as DD_NOTE
from tests.test_deal_parties_db import NOW as DEAL_NOW
from tests.test_deal_parties_db import party
from tests.test_disclosure_deals_db import deal
from tests.test_disclosure_estimates_db import BEFORE_ALL, BETWEEN, snapshot, value
from tests.test_disclosure_estimates_db import setup as estimates_setup


def decided(
    kind: str,
    ref: str,
    decision: str,
    evidence: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> ledger.AutoDecision:
    """A ledger row for an applied decision."""
    return ledger.AutoDecision(
        id=1,
        kind=kind,
        subject_ref=ref,
        ticker=None,
        decision=decision,
        confidence=0.95,
        decided_by="model:test",
        summary="test",
        evidence=evidence or {},
        status="applied",
        revert_payload=payload,
        sampled=False,
        review=None,
        reviewed_by=None,
        reviewed_at=None,
        decided_at=dt.datetime.now(dt.UTC),
    )


def test_every_kind_in_scope_is_wired_and_identity_is_left_alone() -> None:
    for kind in (
        ap.listing,
        ap.peer,
        dd.identifier,
        dd.deal_party,
        dd.estimate_pair,
        dis.DISCLOSURE_REVIEW,
        dis.ADJUDICATION,
        dis.INSIDER_OVERRIDE,
    ):
        assert kind.verify is not None and kind.recheck is not None, kind.name
    assert ap.identity.verify is None and ap.identity.recheck is None


# ----------------------------------------------------------------------------------- listing


def test_listing_confirm_is_blocked_by_the_stored_derivation() -> None:
    assert ap.listing_problems("confirm", {}, "US1", "US1") == []
    assert (
        ap.listing_problems(
            "confirm",
            {"isin_has_firds_record_on_home_mic": True, "currency": {"agrees": True}},
            None,
            None,
        )
        == []
    )
    assert ap.listing_problems("confirm", {"isin_has_firds_record_on_home_mic": False}, None, None)
    assert ap.listing_problems("confirm", {"currency": {"agrees": False}}, None, None)
    assert ap.listing_problems("confirm", {}, "US0000000001", "US0000000002")
    # None is "not looked up", not a failure; a rejection is never blocked.
    assert ap.listing_problems("confirm", {"currency": {"agrees": None}}, "US1", None) == []
    assert ap.listing_problems("reject", {"currency": {"agrees": False}}, "A", "B") == []


def test_listing_state_follows_the_decision() -> None:
    assert ap.listing_state_problems("confirm", "confirmed", 5, 5) == []
    assert ap.listing_state_problems("reject", "rejected", 5, None) == []
    assert ap.listing_state_problems("confirm", "rejected", 5, 5) == ["the listing is now rejected"]
    assert ap.listing_state_problems("reject", "suggested", 5, 5)
    assert ap.listing_state_problems("confirm", "confirmed", 6, 5) == [
        "the derived listing changed after the decision"
    ]


# -------------------------------------------------------------------------------------- peer


def test_peer_confirm_is_blocked_by_the_registry() -> None:
    assert ap.base_symbol("ACME_US_EQ") == "ACME" and ap.base_symbol("acme") == "ACME"
    assert ap.peer_problems("confirm", "ACME_US_EQ", "RIVL", False, False) == []
    assert ap.peer_problems("confirm", "ACME_US_EQ", "acme", False, False) == [
        "the peer is the holding itself"
    ]
    assert ap.peer_problems("confirm", "ACME_US_EQ", "ACMEB", True, False) == [
        "the registry shows the peer and the holding are one issuer"
    ]
    assert ap.peer_problems("confirm", "ACME_US_EQ", "RIVL", False, True) == [
        "the same peer was rejected under another source"
    ]
    assert ap.peer_problems("reject", "ACME_US_EQ", "ACME", True, True) == []


def test_peer_state_follows_the_decision() -> None:
    assert ap.peer_state_problems("confirm", "confirmed", "Autopilot (model:x)") == []
    assert ap.peer_state_problems("reject", "confirmed", None) == [
        "the peer is now confirmed (by the owner)"
    ]
    assert ap.peer_state_problems("reject", "suggested", "Autopilot decision reverted") == [
        "the peer is now suggested"
    ]


# ---------------------------------------------------------------------------------- identifier


def test_identifier_formats_are_checked() -> None:
    assert dd.lei_valid(LEI) and dd.lei_valid("HWUPKR0MPOU8FGXBT394")
    assert not dd.lei_valid("5493001KJTIIGC8Y1R13")  # wrong check digits
    assert not dd.lei_valid("5493001KJTIIGC8Y1R1")  # too short
    assert dd.figi_valid("BBG000BLNNH6") and not dd.figi_valid("BBX000BLNNH6")
    assert not dd.figi_valid("BBG000BLNNH")


def test_identifier_confirm_is_blocked_by_format_status_and_conflicts() -> None:
    own = (1, None)
    assert dd.identifier_problems("confirm", "lei", LEI, {"status": "ACTIVE"}, own, []) == []
    assert dd.identifier_problems("confirm", "lei", LEI, {}, own, []) == []
    assert dd.identifier_problems("confirm", "lei", "5493001KJTIIGC8Y1R13", {}, own, [])
    assert dd.identifier_problems("confirm", "lei", LEI, {"status": "INACTIVE"}, own, [])
    elsewhere = [{"identifier": LEI, "issuer_id": 2, "security_id": None}]
    assert dd.identifier_problems("confirm", "lei", LEI, {}, own, elsewhere) == [
        "the identifier is already confirmed for another record"
    ]
    other_lei = [{"identifier": "HWUPKR0MPOU8FGXBT394", "issuer_id": 1, "security_id": None}]
    assert dd.identifier_problems("confirm", "lei", LEI, {}, own, other_lei) == [
        "the record already has a different confirmed LEI"
    ]
    assert (
        dd.identifier_problems("reject", "lei", "bad", {"status": "INACTIVE"}, own, elsewhere) == []
    )


def test_identifier_state_follows_the_decision() -> None:
    assert dd.identifier_state_problems("confirm", "confirmed", "lei", LEI, LEI) == []
    assert dd.identifier_state_problems("confirm", "confirmed", "lei", LEI, None) == []
    assert dd.identifier_state_problems("confirm", "confirmed", "lei", LEI, "X" * 20)
    assert dd.identifier_state_problems("reject", "rejected", "lei", LEI, "X" * 20) == []
    assert dd.identifier_state_problems("reject", "confirmed", "lei", LEI, None)


# ---------------------------------------------------------------------------------- deal party

DAY = dt.date(2026, 10, 5)
ACME = ent.IssuerRef(7, "Acme Corporation")


def test_party_confirm_needs_the_name_to_still_match_and_no_second_party() -> None:
    assert dd.party_problems("confirm", "ACME Corp.", DAY, ACME, [], False) == []
    alias = ent.Alias(7, "Acme Rockets", "trading", dt.date(2020, 1, 1), None)
    assert dd.party_problems("confirm", "Acme Rockets Inc", DAY, ACME, [alias], False) == []
    # The alias is dated: before it began it names nothing.
    assert dd.party_problems("confirm", "Acme Rockets", dt.date(2019, 1, 1), ACME, [alias], False)
    assert dd.party_problems("confirm", "Zeta Corp", DAY, ACME, [], False)
    assert dd.party_problems("confirm", "Acme Corp", DAY, ACME, [], True) == [
        "another party of the same deal is already matched to this issuer"
    ]
    assert dd.party_problems("confirm", "Acme Corp", DAY, None, [], False) == [
        "the issuer no longer exists"
    ]
    assert dd.party_problems("reject", "Zeta Corp", DAY, ACME, [], True) == []


def test_party_state_follows_the_decision() -> None:
    assert dd.party_state_problems("confirm", "confirmed", 7, 7) == []
    assert dd.party_state_problems("reject", "rejected", None, 7) == []
    assert dd.party_state_problems("confirm", "rejected", None, 7)
    assert dd.party_state_problems("confirm", "confirmed", 8, 7)
    assert dd.party_state_problems("reject", "rejected", 7, 7)
    assert dd.party_state_problems("reject", None, None, 7) == ["the pair is now undecided"]


# -------------------------------------------------------------------------------- estimate pair

T1 = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
T2 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)


def test_pair_must_still_read_as_it_did_when_judged() -> None:
    a, b = make_value(1, 10, T1, "100"), make_value(2, 11, T2, "110")
    same = est.revision(a, b)
    assert dd.pair_problems("correct", same, True, []) == []
    assert dd.pair_problems("incorrect", same, True, []) == []
    assert dd.pair_problems("correct", None, True, []) == [
        "the pair is no longer a revision pair on the stored lines"
    ]
    # The lines changed currency after the judge looked: now a refusal.
    moved = est.revision(a, make_value(2, 11, T2, "110", currency="EUR"))
    assert dd.pair_problems("correct", moved, True, [])
    # A refusal judged for one reason that is now another.
    unit = est.revision(a, make_value(2, 11, T2, "110", unit="percent"))
    assert dd.pair_problems("correct", unit, False, ["currency_changed"])
    assert dd.pair_problems("correct", unit, False, ["unit_changed"]) == []
    # Nothing reported, nothing to call correct (mean vs median).
    mixed = est.revision(a, replace(b, mean=None, median=Decimal("110")))
    assert mixed.comparable and mixed.statistic is None
    assert dd.pair_problems("correct", mixed, True, [])
    assert dd.pair_problems("incorrect", mixed, True, []) == []


def test_pair_label_must_still_be_on_file_and_unchallenged() -> None:
    assert dd.pair_label_problems("correct", True, None) == []
    assert dd.pair_label_problems("incorrect", False, False) == []
    assert dd.pair_label_problems("correct", None, None)
    assert dd.pair_label_problems("correct", False, None)
    assert dd.pair_label_problems("correct", True, False) == [
        "the owner labelled the pair incorrect"
    ]


# ------------------------------------------------------------------------------------- review

CLAIM = {
    "polarity": "asserted",
    "quote": "Acme supplies Borealis with parts.",
    "subject_issuer_id": 1,
    "object_issuer_id": 2,
    "object_label": None,
    "object_anonymous": False,
    "direction": "supplier_to_customer",
    "newer": False,
}


def test_claim_approval_needs_an_asserted_sourced_two_sided_current_claim() -> None:
    assert dis.claim_problems("approved", CLAIM) == []
    assert dis.claim_problems("approved", CLAIM | {"polarity": "denied"})
    assert dis.claim_problems("approved", CLAIM | {"quote": "  "})
    assert dis.claim_problems("approved", CLAIM | {"object_issuer_id": 1})
    assert dis.claim_problems("approved", CLAIM | {"object_issuer_id": None})
    assert (
        dis.claim_problems(
            "approved", CLAIM | {"object_issuer_id": None, "object_label": "Customer A"}
        )
        == []
    )
    assert dis.claim_problems("approved", CLAIM | {"newer": True})
    assert dis.claim_problems("rejected", CLAIM | {"polarity": "denied", "newer": True}) == []


def test_claim_superseded_needs_a_newer_claim() -> None:
    assert dis.claim_problems("superseded", CLAIM) == ["no newer claim replaces it"]
    assert dis.claim_problems("superseded", CLAIM | {"newer": True}) == []


OBSERVATION = {
    "value_state": "reported",
    "original_string": "$1,234.5 million",
    "quote": "Revenue was $1,234.5 million in the quarter.",
    "newer": False,
}


def test_observation_approval_needs_the_printed_value_in_the_passage() -> None:
    assert dis.printed_value_in_quote("$1,234.5 million", "revenue of 1,234.5 million")
    assert not dis.printed_value_in_quote("$9.9", "revenue of 1,234.5 million")
    assert not dis.printed_value_in_quote(None, "revenue 12")
    assert dis.observation_problems("approved", OBSERVATION) == []
    assert dis.observation_problems("approved", OBSERVATION | {"quote": "Revenue grew."})
    assert dis.observation_problems("approved", OBSERVATION | {"quote": None})
    assert dis.observation_problems("approved", OBSERVATION | {"newer": True})
    undisclosed = OBSERVATION | {"value_state": "not_disclosed", "original_string": None}
    assert dis.observation_problems("approved", undisclosed) == []
    assert dis.observation_problems("superseded", OBSERVATION) == [
        "no newer observation replaces it"
    ]
    assert dis.observation_problems("superseded", OBSERVATION | {"newer": True}) == []
    assert dis.observation_problems("rejected", OBSERVATION | {"quote": None}) == []


def test_mapping_approval_needs_a_matched_entity_with_a_related_name() -> None:
    mapping = {
        "mention": "Acme Rockets Inc.",
        "match_basis": "name",
        "issuer_id": 4,
        "security_id": None,
        "matched": "Acme Rockets Corporation",
    }
    assert dis.mapping_problems("approved", mapping) == []
    assert dis.mapping_problems("approved", mapping | {"issuer_id": None})
    assert dis.mapping_problems("approved", mapping | {"matched": "Zeta Holdings"})
    # A hard key does not depend on the words.
    assert (
        dis.mapping_problems("approved", mapping | {"matched": "Zeta", "match_basis": "cik"}) == []
    )
    assert dis.mapping_problems("rejected", mapping | {"issuer_id": None}) == []


def test_review_state_follows_the_decision() -> None:
    assert dis.review_state_problems("approved", "approved", "claim", "verified") == []
    assert dis.review_state_problems("approved", "approved", "identity_mapping", "confirmed") == []
    assert dis.review_state_problems("rejected", "rejected", "observation", "rejected") == []
    assert dis.review_state_problems("superseded", "superseded", "claim", "superseded") == []
    assert dis.review_state_problems("approved", "open", "claim", "machine_suggested") == [
        "the review is now open",
        "the subject is now machine_suggested",
    ]
    assert dis.review_state_problems("approved", "approved", "claim", None) == [
        "the subject is now gone"
    ]


# ------------------------------------------------------------------------------- adjudication


def test_direction_must_fit_the_label() -> None:
    assert dis.direction_problem("open_market_buy", "A") == []
    assert dis.direction_problem("open_market_buy", None) == []
    assert dis.direction_problem("gift", "D") == []
    assert dis.direction_problem("open_market_buy", "D")
    assert dis.direction_problem("open_market_sell", "A")
    assert dis.direction_problem("grant_award", "D") and dis.direction_problem(
        "tax_withholding", "A"
    )


def test_adjudication_label_is_read_against_the_live_item() -> None:
    live = (True, "other", "D")
    assert dis.adjudication_problems("gift", "insider_class", "other", live) == []
    assert dis.adjudication_problems("open_market_buy", "insider_class", "other", live)
    assert dis.adjudication_problems("gift", "insider_class", "gift", live)  # the class moved
    assert dis.adjudication_problems("gift", "insider_class", "other", (True, None, None)) == [
        "the sampled item no longer exists"
    ]
    # No cheap live read for this dataset, and a direction only matters for insider classes.
    unchecked = (False, None, None)
    assert dis.adjudication_problems("same_company", "ownership_match", "x", unchecked) == []
    assert dis.adjudication_problems("open_market_buy", "deal_state", "x", (False, None, "D")) == []


def test_adjudication_label_must_stay_on_file_and_unchallenged() -> None:
    assert dis.adjudication_label_problems("gift", "gift", None) == []
    assert dis.adjudication_label_problems("gift", "gift", "gift") == []
    assert dis.adjudication_label_problems("gift", None, None)
    assert dis.adjudication_label_problems("gift", "other", None)
    assert dis.adjudication_label_problems("gift", "gift", "other") == [
        "the owner labelled it other"
    ]


# ---------------------------------------------------------------------------------- insider

LINE = {
    "acquired_disposed": "D",
    "derivative": False,
    "price_per_share": 12.5,
    "footnote_text": "Shares were sold in the open market through a broker.",
    "superseded": False,
}


def test_insider_open_market_needs_direction_price_and_a_trading_footnote() -> None:
    assert dis.insider_problems("open_market_sell", LINE) == []
    assert dis.insider_problems("open_market_buy", LINE)  # a disposal
    bought = LINE | {"acquired_disposed": "A", "footnote_text": "Purchased on the open market."}
    assert dis.insider_problems("open_market_buy", bought) == []
    assert dis.insider_problems("open_market_buy", bought | {"footnote_text": "Gift to a trust."})
    assert dis.insider_problems(
        "open_market_sell", LINE | {"footnote_text": "Sold to cover taxes on vesting."}
    )
    assert dis.insider_problems("open_market_sell", LINE | {"price_per_share": None})
    assert dis.insider_problems("open_market_sell", LINE | {"price_per_share": 0})
    assert dis.insider_problems("open_market_sell", LINE | {"derivative": True})
    assert dis.insider_problems("open_market_sell", LINE | {"acquired_disposed": None}) == []


def test_insider_other_classes_only_check_direction_and_the_filing() -> None:
    gift = LINE | {"price_per_share": None, "footnote_text": "Bona fide gift."}
    assert dis.insider_problems("gift", gift) == []
    assert dis.insider_problems("other", gift | {"acquired_disposed": "A"}) == []
    assert dis.insider_problems("grant_award", gift)  # a grant is an acquisition
    assert dis.insider_problems("tax_withholding", gift | {"footnote_text": "Withheld"}) == []
    assert dis.insider_problems("gift", gift | {"superseded": True}) == [
        "an amendment replaced the filing"
    ]


def test_insider_override_must_still_be_the_latest_word() -> None:
    latest = {"id": 9, "new_class": "gift", "decided_by": "ai:autopilot"}
    assert dis.insider_state_problems("gift", latest, 9) == []
    assert dis.insider_state_problems("gift", latest, None) == []
    assert dis.insider_state_problems("gift", None, 9) == ["the override is no longer on file"]
    assert dis.insider_state_problems(
        "gift", {"id": 10, "new_class": "other", "decided_by": "owner:u1"}, 9
    ) == ["the line was overridden since by owner:u1 to other"]
    assert dis.insider_state_problems("other", latest, None)


# ============================================================================== database tests


@pytest.mark.db
async def test_listing_verify_and_recheck(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    ticker = "VRFA1_US_EQ"
    await seed_link(pool, ticker, "VRFA1")
    cand = next(c for c in await ap.listing.load(pool, 50) if c.ref == f"listing:{ticker}")
    assert ap.listing.verify is not None and ap.listing.recheck is not None
    assert await ap.listing.verify(pool, cand, "confirm") == []

    await pool.execute(
        "update public.instrument_security_link set evidence = evidence ||"
        ' \'{"isin_has_firds_record_on_home_mic": false, "currency": {"agrees": false}}\''
        " where ticker = $1",
        ticker,
    )
    found = await ap.listing.verify(pool, cand, "confirm")
    assert len(found) == 2 and "ESMA" in found[0]
    assert await ap.listing.verify(pool, cand, "reject") == []

    payload = await ap.listing.apply(pool, cand, "reject", AP_NOTE)
    row = decided("listing", cand.ref, "reject", payload=payload)
    assert await ap.listing.recheck(pool, row) == []
    await confirm_listing(pool, ticker, "confirmed", OWNER, "Checked the factsheet, it is right")
    assert await ap.listing.recheck(pool, row) == ["the listing is now confirmed"]
    gone = decided("listing", "listing:NOPE_US_EQ", "reject")
    assert await ap.listing.recheck(pool, gone) == ["the listing no longer exists"]


@pytest.mark.db
async def test_peer_verify_and_recheck(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    assert ap.peer.verify is not None and ap.peer.recheck is not None
    holding = "VRFP_US_EQ"
    own = await seed_peer(pool, holding, "VRFP")
    ok = await seed_peer(pool, holding, "RIVL")
    own_ref, ok_ref = f"peer:{own}", f"peer:{ok}"
    assert await ap.peer.verify(pool, Candidate(own_ref, holding, "", {}), "confirm") == [
        "the peer is the holding itself"
    ]
    assert await ap.peer.verify(pool, Candidate(own_ref, holding, "", {}), "reject") == []
    assert await ap.peer.verify(pool, Candidate(ok_ref, holding, "", {}), "confirm") == []

    await pool.execute(
        "insert into public.peer_set (ticker, peer_ticker, basis, status, source, detail)"
        " values ($1, 'RIVL', 'Owner note', 'rejected', 'manual', '{}'::jsonb)",
        holding,
    )
    assert await ap.peer.verify(pool, Candidate(ok_ref, holding, "", {}), "confirm") == [
        "the same peer was rejected under another source"
    ]

    cand = Candidate(own_ref, holding, "", {"id": own})
    await ap.peer.apply(pool, cand, "reject", "Autopilot (model:test): the holding itself")
    row = decided("peer", own_ref, "reject")
    assert await ap.peer.recheck(pool, row) == []
    from steward.free_sources import set_peer_status

    await set_peer_status(pool, own, "confirmed")
    assert await ap.peer.recheck(pool, row) == ["the peer is now confirmed (by the owner)"]


@pytest.mark.db
async def test_identifier_verify_and_recheck(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    assert dd.identifier.verify is not None and dd.identifier.recheck is not None
    sid, issuer = await suggestion(pool)
    cand = (await dd.identifier.load(pool, 10))[0]
    assert await dd.identifier.verify(pool, cand, "confirm") == []

    bad_sid, _ = await suggestion(pool, identifier="5493001KJTIIGC8Y1R13")
    bad = Candidate(f"identifier:{bad_sid}", None, "", {})
    assert await dd.identifier.verify(pool, bad, "confirm") == [
        "the LEI is not a well-formed identifier"
    ]
    assert await dd.identifier.verify(pool, bad, "reject") == []

    await dd.identifier.apply(pool, cand, "confirm", DD_NOTE)
    row = decided("identifier", cand.ref, "confirm")
    assert await dd.identifier.recheck(pool, row) == []
    await pool.execute("update public.issuer set lei = $2 where id = $1", issuer, "X" * 20)
    assert await dd.identifier.recheck(pool, row) == ["the registry now holds a different LEI"]
    missing = decided("identifier", "identifier:999999", "confirm")
    assert await dd.identifier.recheck(pool, missing) == ["the suggestion no longer exists"]
    assert sid


@pytest.mark.db
async def test_deal_party_verify_and_recheck(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    assert dd.deal_party.verify is not None and dd.deal_party.recheck is not None
    acme = await pool.fetchval(
        "insert into public.issuer (legal_name) values ('Acme Corporation') returning id"
    )
    d = await deal(pool, "txn:vrf1", "acquisition", "Acme buys X", DEAL_NOW)
    a = await party(pool, d, "acquirer", "ACME Corp.")
    cand = next(c for c in await dd.deal_party.load(pool, 10) if c.ref == f"deal_party:{a}:{acme}")
    assert await dd.deal_party.verify(pool, cand, "confirm") == []

    # Another party of the same deal that already is Acme: acquirer and target are not one company.
    twin = await party(pool, d, "target", "Acme Corporation")
    assert await dd.deal_parties.confirm(pool, twin, acme, "owner:u1")
    assert await dd.deal_party.verify(pool, cand, "confirm") == [
        "another party of the same deal is already matched to this issuer"
    ]
    assert await dd.deal_party.verify(pool, cand, "reject") == []

    lone = await party(pool, d, "partner", "Zeta Partners")
    renamed = Candidate(f"deal_party:{lone}:{acme}", None, "", {})
    assert "name no longer matches" in (await dd.deal_party.verify(pool, renamed, "confirm"))[0]

    # Decisions are append-only, so the recheck runs on a second deal with no twin.
    d2 = await deal(pool, "txn:vrf2", "acquisition", "Acme buys Y", DEAL_NOW)
    a2 = await party(pool, d2, "acquirer", "ACME Corp.")
    cand2 = next(
        c for c in await dd.deal_party.load(pool, 10) if c.ref == f"deal_party:{a2}:{acme}"
    )
    await dd.deal_party.apply(pool, cand2, "confirm", DD_NOTE)
    row = decided("deal_party", cand2.ref, "confirm")
    assert await dd.deal_party.recheck(pool, row) == []
    # Read as if the autopilot had rejected it: the stored pair says otherwise.
    contrary = decided("deal_party", cand2.ref, "reject")
    assert await dd.deal_party.recheck(pool, contrary) == [
        "the pair is now confirmed",
        "the party is matched to the issuer it was rejected for",
    ]


@pytest.mark.db
async def test_estimate_pair_verify_and_recheck(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    assert dd.estimate_pair.verify is not None and dd.estimate_pair.recheck is not None
    issuer, source = await estimates_setup(pool)
    s1 = await snapshot(pool, issuer, source, BEFORE_ALL, digest="vr1")
    s2 = await snapshot(pool, issuer, source, BETWEEN, digest="vr2")
    v1, v2 = await value(pool, s1, "100"), await value(pool, s2, "101")
    cand = (await dd.estimate_pair.load(pool, 10))[0]
    assert cand.ref == f"estimate_pair:{v1}:{v2}"
    assert await dd.estimate_pair.verify(pool, cand, "correct") == []

    stale = Candidate(cand.ref, None, "", cand.evidence | {"comparable": False})
    assert await dd.estimate_pair.verify(pool, stale, "correct")
    reversed_pair = Candidate(f"estimate_pair:{v2}:{v1}", None, "", {})
    assert await dd.estimate_pair.verify(pool, reversed_pair, "incorrect") == [
        "the pair is no longer a revision pair on the stored lines"
    ]

    await dd.estimate_pair.apply(pool, cand, "correct", DD_NOTE)
    row = decided("estimate_pair", cand.ref, "correct", cand.evidence)
    assert await dd.estimate_pair.recheck(pool, row) == []
    await estimate_read.record_pair_review(
        pool, earlier_value_id=v1, later_value_id=v2, correct=False, reviewer="owner:u1", note=None
    )
    assert await dd.estimate_pair.recheck(pool, row) == ["the owner labelled the pair incorrect"]


@pytest.mark.db
async def test_disclosure_review_verify_and_recheck(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    assert dis.DISCLOSURE_REVIEW.verify is not None and dis.DISCLOSURE_REVIEW.recheck is not None
    # A mapping to nothing but an anonymous label cannot be approved.
    review_id, mapping_id = await _mapping_review(pool, "DB Verify Customer A")
    anon = Candidate(f"review:{review_id}", None, "", {"review_id": review_id})
    assert await dis.DISCLOSURE_REVIEW.verify(pool, anon, "approved") == [
        "the mapping names no company or security"
    ]
    assert await dis.DISCLOSURE_REVIEW.verify(pool, anon, "rejected") == []

    issuer = await pool.fetchval(
        "insert into public.issuer (legal_name) values ('Verify Rockets Corporation') returning id"
    )
    mapping = await pool.fetchval(
        "insert into public.disclosure_entity_mapping (mention, mention_kind, issuer_id,"
        " match_basis) values ('Verify Rockets Inc.', 'issuer', $1, 'registered_name')"
        " returning id",
        issuer,
    )
    rid = await review.enqueue(pool, "identity_mapping", mapping, "entity_ambiguity", {})
    assert rid is not None
    cand = Candidate(f"review:{rid}", None, "", {"review_id": rid})
    assert await dis.DISCLOSURE_REVIEW.verify(pool, cand, "approved") == []

    await dis.review_apply(pool, cand, "approved", AP_NOTE)
    row = decided("disclosure_review", cand.ref, "approved")
    assert await dis.DISCLOSURE_REVIEW.recheck(pool, row) == []
    await pool.execute(
        "update public.disclosure_entity_mapping set status = 'rejected',"
        " rejection_reason = 'wrong' where id = $1",
        mapping,
    )
    assert await dis.DISCLOSURE_REVIEW.recheck(pool, row) == ["the subject is now rejected"]
    assert mapping_id


@pytest.mark.db
async def test_adjudication_verify_and_recheck(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    assert dis.ADJUDICATION.verify is not None and dis.ADJUDICATION.recheck is not None
    sid = await _suggestion(pool, "own:vrf1")
    cand = _adj_cand(sid)
    assert await dis.ADJUDICATION.verify(pool, cand, "same_company") == []
    gone = _adj_cand(sid + 1000)
    assert await dis.ADJUDICATION.verify(pool, gone, "same_company") == [
        "the suggestion no longer exists"
    ]

    await dis.adjudication_apply(pool, cand, "same_company", AP_NOTE)
    row = decided("adjudication", cand.ref, "same_company")
    assert await dis.ADJUDICATION.recheck(pool, row) == []
    await adjudication.record(
        pool,
        dataset="ownership_match",
        sample_key="ownership_match:dbauto:10",
        item_ref="own:vrf1",
        machine_value="same_company",
        human_value="different_company",
        by="owner:u1",
        kind="person",
    )
    assert await dis.ADJUDICATION.recheck(pool, row) == ["the owner labelled it different_company"]


@pytest.mark.db
async def test_insider_override_verify_and_recheck(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    assert dis.INSIDER_OVERRIDE.verify is not None and dis.INSIDER_OVERRIDE.recheck is not None
    acc = "0000099997-26-000001"
    await _insider_line(pool, acc, "Shares gifted to a family member.")
    ref = dis._insider_ref(acc, 0)
    cand = next(c for c in await dis.insider_load(pool, 100) if c.ref == ref)
    assert await dis.INSIDER_OVERRIDE.verify(pool, cand, "gift") == []
    found = await dis.INSIDER_OVERRIDE.verify(pool, cand, "open_market_buy")
    assert "the line states no price" in found
    assert "the footnote does not speak of a purchase or sale" in found

    payload = await dis.insider_apply(pool, cand, "gift", AP_NOTE)
    row = decided("insider_override", ref, "gift", payload=payload)
    assert await dis.INSIDER_OVERRIDE.recheck(pool, row) == []
    await record_override(pool, acc, 0, "other", "owner says it is a trust transfer", "owner:u1")
    assert await dis.INSIDER_OVERRIDE.recheck(pool, row) == [
        "the line was overridden since by owner:u1 to other"
    ]
