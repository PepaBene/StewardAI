"""P6 pure rules: taxonomy and state machine, as-of resolution, typed unknowns, impacts, extraction
and clustering. No database."""

import datetime as dt
from decimal import Decimal as D

import pytest

from steward.disclosure import deal_extract as ex
from steward.disclosure import deals as dl

UTC = dt.UTC


def at(month: int, day: int) -> dt.datetime:
    return dt.datetime(2026, month, day, 12, tzinfo=UTC)


def tr(
    i: int, state: str, eff: dt.datetime, known: dt.datetime | None = None, **kw
) -> dl.Transition:
    kw.setdefault("evidence_id", 100 + i)
    return dl.Transition(i, state, eff, known if known is not None else eff, **kw)


def tv(i: int, no: int, known: dt.datetime, values: dict[str, str], **kw) -> dl.TermVersion:
    kw.setdefault("evidence_id", 200 + i)
    kw.setdefault("currency", "USD")
    return dl.TermVersion(i, no, known, values={k: D(v) for k, v in values.items()}, **kw)


# ----------------------------------------------------------------------------------- taxonomy
def test_every_type_has_required_fields_and_reachable_states():
    assert set(dl.TAXONOMY) == {
        "acquisition", "merger", "divestiture", "ipo", "follow_on", "debt_issuance", "spin_off",
        "joint_venture", "strategic_investment", "buyback", "tender_offer", "material_agreement",
    }  # fmt: skip
    for kind, spec in dl.TAXONOMY.items():
        assert {"announced", "closed", "terminated"} <= set(spec.states), kind
        for f in (*spec.required, *spec.optional):
            assert f in dl.TERM_LABEL, (kind, f)


def test_type_limits_the_states_it_may_reach():
    assert dl.transition_problem("buyback", "announced", "contested")
    assert dl.transition_problem("debt_issuance", None, "rumored")
    assert dl.transition_problem("acquisition", "announced", "contested") is None


# ---------------------------------------------------------------------------------- state machine
def test_a_withdrawn_deal_stays_in_history_and_cannot_close_afterwards():
    ts = [
        tr(1, "announced", at(10, 5)),
        tr(2, "definitive", at(10, 20)),
        tr(3, "terminated", at(11, 1), at(11, 3)),
        tr(4, "closed", at(12, 1)),
    ]
    r = dl.resolve_state("acquisition", ts, at(12, 31))
    assert r.state == "terminated"
    assert [t.state for t in r.applied] == ["announced", "definitive", "terminated"]
    assert [(t.id, why) for t, why in r.rejected] == [
        (4, "terminated cannot move to closed (a closed or terminated deal is final)")
    ]


def test_state_at_three_cutoffs_shows_no_future_knowledge():
    ts = [
        tr(1, "announced", at(10, 5)),
        tr(2, "definitive", at(10, 20)),
        tr(3, "terminated", at(11, 1), at(11, 3)),  # effective Nov 1 but only known Nov 3
    ]
    state = lambda when: dl.resolve_state("acquisition", ts, when).state  # noqa: E731
    assert state(at(10, 1)) is None
    assert state(at(10, 10)) == "announced"
    assert state(at(11, 2)) == "definitive"  # the termination is not yet known
    assert state(at(11, 4)) == "terminated"


def test_evidence_and_known_at_are_mandatory_for_every_transition():
    no_evidence = tr(1, "announced", at(10, 5), evidence_id=None)
    no_known = dl.Transition(2, "announced", at(10, 5), None, 5)
    r = dl.resolve_state("acquisition", [no_evidence, no_known], at(12, 1))
    assert r.state is None
    assert {why for _, why in r.rejected} == {
        "evidence is mandatory for a state change",
        "a known-at date is mandatory for a state change",
    }


def test_a_rumor_must_name_its_source_and_cannot_skip_to_closed():
    assert dl.validate_history("acquisition", [tr(1, "rumored", at(9, 1))])[0][1].startswith(
        "a rumor must name"
    )
    ok = tr(1, "rumored", at(9, 1), rumor_source="Press report, 1 Sep")
    assert dl.validate_history("acquisition", [ok]) == []
    assert dl.transition_problem("acquisition", None, "closed")


def test_state_chronology_cannot_run_backwards():
    ts = [tr(1, "announced", at(10, 15)), tr(2, "definitive", at(10, 1), at(10, 16))]
    r = dl.resolve_state("acquisition", ts, at(11, 1))
    assert r.state == "announced"
    assert "effective date is before" in r.rejected[0][1]


def test_an_agreement_dated_days_before_the_announcement_still_applies():
    ts = [tr(1, "announced", at(10, 5)), tr(2, "definitive", at(10, 1), at(10, 6))]
    assert dl.resolve_state("acquisition", ts, at(11, 1)).state == "definitive"


def test_multiple_bids_a_contest_resolves_to_the_winning_terms():
    ts = [
        tr(1, "announced", at(10, 5)),
        tr(2, "contested", at(10, 12)),
        tr(3, "definitive", at(10, 25)),
    ]
    assert dl.resolve_state("acquisition", ts, at(11, 1)).state == "definitive"
    assert dl.resolve_state("acquisition", ts, at(10, 15)).state == "contested"


# ------------------------------------------------------------------------------------- terms
def test_amendments_add_versions_and_the_latest_known_wins():
    v1 = tv(1, 1, at(10, 5), {"price_per_share": "42.00"})
    v2 = tv(2, 2, at(10, 20), {"price_per_share": "44.50"}, supersedes_id=1)
    assert dl.latest_version([v1, v2], at(10, 10)) == v1
    assert dl.latest_version([v1, v2], at(10, 25)) == v2
    assert dl.latest_version([v1, v2], at(10, 1)) is None
    (change,) = dl.term_changes(v1, v2)
    assert (change.field, change.old, change.new) == ("price_per_share", "42.00", "44.50")


def test_an_absent_term_is_a_typed_unknown_never_zero():
    terms = {t.name: t for t in dl.typed_terms(tv(1, 1, at(10, 5), {"cash_amount": "100"}))}
    assert terms["cash_amount"].state == "stated"
    assert terms["stock_amount"].state == "not_stated" and terms["stock_amount"].value is None
    assert all(t.state == "not_stated" for t in dl.typed_terms(None))


def test_missing_fields_list_what_the_type_requires():
    assert dl.missing_fields("acquisition", None) == ["consideration", "currency"]
    v = tv(1, 1, at(10, 5), {"price_per_share": "44.50", "cash_amount": "100"})
    assert dl.missing_fields("acquisition", v) == ["unaffected_price", "unaffected_date"]
    assert dl.missing_fields("buyback", tv(2, 1, at(10, 5), {"authorized_amount": "1"})) == []


def test_version_problems_flag_bad_units():
    v = tv(1, 1, at(10, 5), {"stake_pct": "49", "cash_pct": "0.7", "stock_pct": "0.6"})
    assert len(dl.version_problems(v)) == 2
    assert dl.version_problems(tv(2, 1, at(10, 5), {}, evidence_id=None))


# ------------------------------------------------------------------------------------ impacts
ANNOUNCED = dt.date(2026, 10, 5)


def priced(
    unaffected_on: dt.date | None, price: str = "44.50", un: str = "33.00"
) -> dl.TermVersion:
    return tv(
        1,
        1,
        at(10, 5),
        {"price_per_share": price, "unaffected_price": un},
        unaffected_date=unaffected_on,
    )


def test_premium_uses_a_correctly_dated_unaffected_price():
    i = dl.premium(priced(dt.date(2026, 10, 2)), ANNOUNCED)
    assert i.value is not None and round(i.value, 4) == D("0.3485")
    assert i.origin == "announced" and i.flags == () and i.term_version_ids == (1,)
    assert set(i.inputs) >= {"offer_price", "unaffected_price", "unaffected_date", "currency"}
    assert i.formula_version == dl.FORMULA_VERSION


@pytest.mark.parametrize("on", [dt.date(2026, 10, 5), dt.date(2026, 10, 9)])
def test_an_unaffected_date_on_or_after_the_announcement_is_rejected(on: dt.date):
    i = dl.premium(priced(on), ANNOUNCED)
    assert i.value is None and "on or after the announcement" in (i.reason or "")


def test_a_stale_unaffected_price_is_flagged_not_hidden():
    i = dl.premium(priced(dt.date(2026, 9, 1)), ANNOUNCED)
    assert i.value is not None and i.flags == ("stale_unaffected_price",)


def test_premium_is_null_with_a_reason_for_each_missing_input():
    assert "no per-share price" in (
        dl.premium(tv(1, 1, at(10, 5), {"cash_amount": "9"}), ANNOUNCED).reason or ""
    )
    assert "no unaffected price" in (
        dl.premium(tv(1, 1, at(10, 5), {"price_per_share": "9"}), ANNOUNCED).reason or ""
    )
    assert "has no date" in (dl.premium(priced(None), ANNOUNCED).reason or "")
    no_cur = tv(
        1,
        1,
        at(10, 5),
        {"price_per_share": "9", "unaffected_price": "8"},
        unaffected_date=dt.date(2026, 10, 2),
        currency=None,
    )
    assert "currency" in (dl.premium(no_cur, ANNOUNCED).reason or "")
    assert dl.premium(None, ANNOUNCED).value is None


def test_an_owner_scenario_is_its_own_labelled_kind():
    i = dl.premium(
        priced(dt.date(2026, 10, 2)), ANNOUNCED, offer_price=D("40"), origin="owner_scenario"
    )
    assert i.origin == "owner_scenario" and i.label == "Premium in your scenario"
    assert i.value is not None and round(i.value, 4) == D("0.2121")


def test_cash_plus_stock_dilution_and_bridge_never_fill_gaps():
    v = tv(
        1,
        1,
        at(10, 5),
        {"cash_amount": "1800000000", "stock_amount": "600000000", "exchange_ratio": "0.25"},
    )
    ctx = dl.DealContext(acquirer_shares=D("1000000000"), target_shares=D("400000000"))
    d = dl.dilution(v, ctx)
    assert d.value is not None and round(d.value, 4) == D("0.0909")
    assert d.detail["new_shares"] == "100000000.00"
    b = dl.consideration_bridge(v)
    assert b.known_total == D("2400000000") and b.total_is_lower_bound
    assert b.unstated == ("debt", "earnout")
    assert [s.amount for s in b.segments if s.state == "not_stated"] == [None, None]
    assert dl.dilution(v, dl.DealContext()).value is None
    assert dl.dilution(tv(2, 1, at(10, 5), {"cash_amount": "1"}), ctx).value is None


def test_spin_off_share_distribution():
    v = tv(1, 1, at(10, 5), {"exchange_ratio": "0.5"})
    i = dl.spin_off_distribution(v, dl.DealContext(parent_shares=D("200000000")))
    assert i.value == D("100000000.0") and i.unit == "shares"
    assert dl.spin_off_distribution(v, dl.DealContext()).value is None


def test_buyback_authorization_is_not_a_purchase():
    only_auth = tv(1, 1, at(10, 5), {"authorized_amount": "10000000000"})
    assert dl.buyback_progress(only_auth).value is None
    both = tv(
        2, 2, at(12, 5), {"authorized_amount": "10000000000", "executed_amount": "2500000000"}
    )
    assert dl.buyback_progress(both).value == D("0.25")
    over = tv(3, 3, at(12, 6), {"authorized_amount": "10", "executed_amount": "12"})
    assert dl.buyback_progress(over).flags == ("executed_exceeds_authorization",)


def test_a_large_refinancing_nets_out_only_the_stated_repayment():
    ctx = dl.DealContext(enterprise_value_before=D("40000000000"), net_debt_before=D("9000000000"))
    gross = tv(1, 1, at(10, 5), {"financing_amount": "5000000000"})
    g = dl.debt_change("debt_issuance", gross, ctx)
    assert g.value == D("0.125") and g.flags == ("gross_of_refinancing",)
    net = tv(2, 2, at(10, 6), {"financing_amount": "5000000000", "refinanced_amount": "4200000000"})
    n = dl.debt_change("debt_issuance", net, ctx)
    assert n.value == D("0.02") and n.flags == () and n.detail["net_debt_after"] == "9800000000"
    assert dl.debt_change("debt_issuance", net, dl.DealContext()).value is None


def test_segment_mix_change_needs_both_sides():
    ctx = dl.DealContext(
        acquirer_segments={"cloud": D(80), "retail": D(20)}, target_segments={"retail": D(100)}
    )
    i = dl.segment_mix(ctx)
    assert i.value is not None and round(i.value, 4) == D("0.4")
    assert dl.segment_mix(dl.DealContext(acquirer_segments={"a": D(1)})).value is None


def test_projected_synergies_never_mix_into_announced_terms():
    announced = tv(1, 1, at(10, 5), {"price_per_share": "44.50"})
    proj = tv(2, 2, at(10, 6), {"expected_synergies": "150000000"}, origin="management_projection")
    assert dl.latest_version([announced, proj], at(11, 1)) == announced
    s = dl.synergy_projection([announced, proj], at(11, 1))
    assert s.origin == "management_projection" and s.value == D("150000000")
    assert dl.synergy_projection([announced], at(11, 1)).value is None


def test_recalculation_reproduces_and_an_amendment_reruns_from_its_own_version():
    v1 = priced(dt.date(2026, 10, 2), price="42.00")
    v2 = tv(
        2,
        2,
        at(10, 20),
        {"price_per_share": "44.50", "unaffected_price": "33.00"},
        unaffected_date=dt.date(2026, 10, 2),
    )
    ctx = dl.DealContext(announced_on=ANNOUNCED)
    a = dl.impacts_for("acquisition", [v1, v2], at(10, 10), ctx)
    assert a == dl.impacts_for("acquisition", [v1, v2], at(10, 10), ctx)
    b = dl.impacts_for("acquisition", [v1, v2], at(10, 25), ctx)
    prem = lambda xs: next(i for i in xs if i.kind == "premium")  # noqa: E731
    assert prem(a).term_version_ids == (1,) and prem(b).term_version_ids == (2,)
    assert prem(a).value != prem(b).value
    assert all(i.value is not None or i.reason for i in a + b)


def test_unit_scale_and_currency_survive_into_the_impact():
    ex1 = ex.extract(
        "Acme Corp announced that it has agreed to acquire Borealis Systems, Inc. for $2.5 billion "
        "in cash."
    )
    assert ex1.terms["cash_amount"].value == D("2500000000") and ex1.currency == "USD"
    ex2 = ex.extract("The acquisition consideration is€2.0bn in cash and a term loan of £400 million.")
    assert ex2.currency is None and ex2.currency_conflict


# --------------------------------------------------------------------------------- portfolio
def test_portfolio_exposure_shows_denominator_and_unknowns():
    w = {"AAA": D("0.5"), "BBB": D("0.3"), "CCC": D("0.2")}
    deals = [
        dl.DealRef("AAA", 1, "acquisition", "announced"),
        dl.DealRef("BBB", 2, "acquisition", "closed"),
    ]
    e = dl.portfolio_exposure(w, deals)
    assert e.denominator == D("1.0") and e.covered_weight == D("0.5")
    assert e.by_state == {"announced": D("0.5")} and e.none_known == ("CCC",)
    assert dl.portfolio_exposure(w, [dl.DealRef("CCC", 3, "buyback", None)]).unknown_state == (
        "CCC",
    )


# ----------------------------------------------------------------------------- corporate structure
def test_closing_dates_are_three_separate_facts():
    links = [
        dl.StructureLink(
            1,
            at(11, 1),
            7,
            legal_closing_date=dt.date(2026, 10, 30),
            successor_name="Acme Borealis",
        ),
        dl.StructureLink(2, at(11, 3), None, broker_posting_date=dt.date(2026, 11, 2)),
    ]
    v = dl.closing_view(links, at(11, 15), closed=True)
    assert v.legal_closing_date == dt.date(2026, 10, 30) and v.successor_name == "Acme Borealis"
    assert v.first_reporting_period_end is None and v.missing == ("first_reporting_period_end",)
    assert dl.closing_view(links, at(11, 2), closed=True).broker_posting_date is None
    bad = dl.StructureLink(
        3,
        at(11, 4),
        None,
        first_reporting_period_end=dt.date(2026, 9, 30),
        broker_posting_date=dt.date(2026, 10, 1),
    )
    assert len(dl.closing_view([*links, bad], at(11, 15), closed=True).problems) == 2


def test_confidence_is_low_for_a_rumor_and_medium_with_missing_terms():
    assert dl.confidence("rumored", 0, 0) == "low" and dl.confidence(None, 0, 0) == "low"
    assert (
        dl.confidence("announced", 0, 1) == "medium" and dl.confidence("definitive", 0, 0) == "high"
    )
    assert dl.confidence("definitive", 1, 0) == "low"


def test_snapshot_at_three_cutoffs_uses_only_what_was_known():
    ts = [tr(1, "announced", at(10, 5)), tr(2, "terminated", at(11, 1), at(11, 3))]
    vs = [
        tv(1, 1, at(10, 5), {"price_per_share": "42.00"}),
        tv(2, 2, at(10, 20), {"price_per_share": "44.50"}),
    ]
    ctx = dl.DealContext(announced_on=ANNOUNCED)
    snaps = [
        dl.snapshot("acquisition", ts, vs, when, ctx) for when in (at(10, 1), at(10, 25), at(11, 4))
    ]
    assert [s.state.state for s in snaps] == [None, "announced", "terminated"]
    assert [s.latest.id if s.latest else None for s in snaps] == [None, 2, 2]
    assert [len(s.history) for s in snaps] == [0, 2, 2]


# ------------------------------------------------------------------------------------ extractor
PRESS = (
    "Item 1.01 Entry into a Material Definitive Agreement. On October 5, 2026, Acme Corp announced "
    "that it has entered into a definitive agreement to acquire Borealis Systems, Inc. for $44.50 "
    "per "
    "share in cash. The transaction values Borealis at an enterprise value of approximately $2.4 "
    "billion. "
    "Acme expects annual cost synergies of approximately $150 million. The agreement provides for "
    "a "
    "termination fee of $85 million. Acme has obtained a $1.8 billion committed bridge facility to "
    "fund "
    "the transaction. The closing is subject to customary closing conditions, including regulatory "
    "approval. Borealis's unaffected share price of $33.00 was set on October 2, 2026. "
    "Item 9.01 Financial Statements and Exhibits. Acme has not received an offer from any other "
    "party."
)


def test_extractor_reads_stated_terms_with_offsets():
    r = ex.extract(PRESS)
    assert [i for i, _ in r.items] == ["1.01", "9.01"]
    assert r.deal_type == "acquisition" and r.currency == "USD"
    assert r.parties == ("Acme Corp", "Borealis Systems, Inc")
    assert r.announced_date == dt.date(2026, 10, 5) and r.unaffected_date == dt.date(2026, 10, 2)
    v = r.values()
    assert v["price_per_share"] == D("44.50") and v["termination_fee"] == D("85000000")
    assert v["financing_amount"] == D("1800000000") and v["expected_synergies"] == D("150000000")
    assert v["unaffected_price"] == D("33.00")
    for name in ("price_per_share", "termination_fee", "financing_amount"):
        t = r.terms[name]
        assert t.start is not None and t.end is not None and PRESS[t.start : t.end] == t.text
    assert r.closing_conditions.state == "stated" and "regulatory approval" in (
        r.closing_conditions.text or ""
    )


def test_a_headline_value_or_missing_term_is_a_typed_unknown():
    r = ex.extract(PRESS)
    for name in ("cash_amount", "stock_amount", "earnout_amount", "exchange_ratio", "stake_pct"):
        assert r.terms[name].state == "not_stated" and r.terms[name].value is None
    assert D("2400000000") not in r.values().values()  # the enterprise-value headline fills no term
    assert set(r.terms) == set(dl.NUMERIC_TERMS)


def test_negated_and_hypothetical_wording_is_skipped():
    r = ex.extract(
        "Acme has not received an offer of $60.00 per share. Acme may seek to acquire Borealis for "
        "$50.00 per share. There can be no assurance a deal will occur."
    )
    assert r.terms["price_per_share"].state == "not_stated"
    reasons = {s.reason for s in r.skipped}
    assert reasons == {"denial or absence of an offer or agreement", "hypothetical or risk wording"}


def test_cash_plus_stock_terms_extract_ratio_and_mix():
    r = ex.extract(
        "Holders will receive $20.00 per share in cash and 0.25 shares of Acme common stock for "
        "each "
        "share of Borealis. The consideration is 60% in cash and 40% in stock."
    )
    v = r.values()
    assert v["price_per_share"] == D("20.00") and v["exchange_ratio"] == D("0.25")
    assert (v["cash_pct"], v["stock_pct"]) == (D("0.6"), D("0.4"))


def test_documents_cluster_by_parties_and_date_and_keep_same_party_deals_apart():
    docs = [
        ex.DocText(1, dt.date(2026, 10, 5), PRESS),
        ex.DocText(
            2,
            dt.date(2026, 10, 6),
            "On October 5, 2026, Acme Corp announced today that it has agreed to acquire Borealis "
            "Systems, Inc.",
        ),
        ex.DocText(
            3,
            dt.date(2026, 10, 6),
            "On October 6, 2026, Acme Corp announced that it has agreed to acquire Cinder Retail "
            "Ltd. for $1.1 billion in cash.",
        ),
        ex.DocText(
            4,
            dt.date(2026, 10, 25),
            "On October 25, 2026, Dune Holdings announced that it has agreed to acquire Borealis "
            "Systems, Inc.",
        ),
        ex.DocText(5, dt.date(2026, 10, 7), "Quarterly update with no transaction."),
    ]
    clusters, review = ex.cluster_documents(docs)
    assert [c.document_ids for c in clusters] == [(1, 2), (3,), (4,)]
    assert len({c.key for c in clusters}) == 3
    assert review == [5]
