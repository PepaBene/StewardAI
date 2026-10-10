"""Gap closure for P4/P5: splits, entity resolution, two-sided checks, change detection, portfolio
exposure, structured extraction and the adjudication gates. Pure logic only (no database)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from steward.disclosure import adjudication as adj
from steward.disclosure import dependencies as dep
from steward.disclosure import entities as ent
from steward.disclosure import insiders as ins
from steward.disclosure.concentration_extract import extract
from steward.graph.ted import parse_ted_response

UTC = dt.UTC
D = Decimal


def at(y: int, m: int, d: int) -> dt.datetime:
    return dt.datetime(y, m, d, tzinfo=UTC)


# ------------------------------------------------------------------------------------ splits
def tx(shares: str, price: str, day: dt.date, after: str | None = None) -> ins.InsiderTx:
    return ins.InsiderTx(
        accession="0000000000-26-000001",
        row_index=0,
        insider="0000000001",
        name="A. Person",
        role="officer",
        tx_date=day,
        filed=day,
        known_at=dt.datetime.combine(day, dt.time(23, 59), tzinfo=UTC),
        economic_class="open_market_buy",
        shares=D(shares),
        price=D(price),
        shares_after=None if after is None else D(after),
    )


def test_a_split_after_the_trade_restates_shares_price_and_holdings_but_not_value() -> None:
    row = tx("100", "400", dt.date(2026, 1, 5), after="1000")
    [out] = ins.restate_for_splits([row], [(dt.date(2026, 3, 1), D(4))], dt.date(2026, 6, 1))
    assert (out.shares, out.price, out.shares_after) == (D(400), D(100), D(4000))
    assert out.shares * out.price == row.shares * row.price


def test_a_split_not_yet_effective_at_the_replay_date_is_not_applied() -> None:
    row = tx("100", "400", dt.date(2026, 1, 5))
    [out] = ins.restate_for_splits([row], [(dt.date(2026, 3, 1), D(4))], dt.date(2026, 2, 1))
    assert out.shares == D(100)


def test_a_split_before_the_trade_does_not_touch_it() -> None:
    row = tx("100", "400", dt.date(2026, 5, 5))
    [out] = ins.restate_for_splits([row], [(dt.date(2026, 3, 1), D(4))], dt.date(2026, 6, 1))
    assert out.shares == D(100)


# ------------------------------------------------------------------------------------ entities
ISSUERS = [
    ent.IssuerRef(1, "Northwind Traders, Inc.", cik="0000012345", ticker="NWT"),
    ent.IssuerRef(2, "Contoso Ltd", lei="5493001KJTIIGC8Y1R12"),
    ent.IssuerRef(3, "Fabrikam Corporation"),
    ent.IssuerRef(4, "Fabrikam Co"),
]
ALIASES = [
    ent.Alias(2, "Tailspin Toys", "trading", dt.date(2018, 1, 1), dt.date(2022, 12, 31)),
    ent.Alias(1, "Tailspin Toys", "former", dt.date(2023, 1, 1), None),
]


def test_legal_form_and_punctuation_do_not_change_a_name() -> None:
    assert ent.norm("Northwind Traders, Inc.") == ent.norm("NORTHWIND TRADERS INC")


def test_an_identifier_match_is_automatic() -> None:
    r = ent.resolve("whatever", ISSUERS, ALIASES, dt.date(2026, 1, 1), cik="12345")
    assert (r.status, r.basis, r.needs_review) == ("matched", "cik", False)
    r = ent.resolve(None, ISSUERS, ALIASES, dt.date(2026, 1, 1), lei="5493001kjtiigc8y1r12")
    assert (r.status, r.issuer_ids, r.basis) == ("matched", (2,), "lei")


def test_a_name_only_match_needs_a_reviewer() -> None:
    r = ent.resolve("Northwind Traders", ISSUERS, ALIASES, dt.date(2026, 1, 1))
    assert (r.status, r.needs_review, r.basis) == ("candidate", True, "registered_name")


def test_two_issuers_with_one_name_are_ambiguous_not_picked() -> None:
    r = ent.resolve("Fabrikam", ISSUERS, ALIASES, dt.date(2026, 1, 1))
    assert r.status == "ambiguous" and r.issuer_ids == (3, 4) and r.needs_review


def test_an_alias_applies_only_on_the_dates_it_was_valid() -> None:
    old = ent.resolve("Tailspin Toys", ISSUERS, ALIASES, dt.date(2020, 6, 1))
    new = ent.resolve("Tailspin Toys", ISSUERS, ALIASES, dt.date(2024, 6, 1))
    assert old.issuer_ids == (2,) and new.issuer_ids == (1,)


def test_an_anonymous_label_is_never_resolved() -> None:
    r = ent.resolve("Customer A", ISSUERS, ALIASES, dt.date(2026, 1, 1))
    assert r.status == "anonymous" and not r.issuer_ids


def test_the_scenario_key_prefers_the_held_ticker() -> None:
    assert ent.key_for(ISSUERS[0]) == "NWT"
    assert ent.key_for(ISSUERS[1]) == "lei:5493001KJTIIGC8Y1R12"
    assert ent.key_for(ISSUERS[2]) == "issuer:3"


# ------------------------------------------------------------------------- dependencies
def claim(
    i: int,
    reporter: str,
    relationship: str,
    name: str | None = "Counterparty",
    share: str | None = None,
    status: str = "confirmed",
    **kw: object,
) -> dep.Dependency:
    return dep.Dependency(
        id=i,
        reporter=reporter,
        relationship=relationship,  # type: ignore[arg-type]
        shape="named",
        status=status,  # type: ignore[arg-type]
        known_at=kw.pop("known_at", at(2026, 1, 10)),  # type: ignore[arg-type]
        passage="It accounted for a share of revenue.",
        counterparty_name=name,
        share=None if share is None else D(share),
        denominator="total_revenue" if share else None,
        fiscal_period=kw.pop("fiscal_period", "FY2025"),  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


def test_opposite_direction_claims_from_the_two_sides_conflict() -> None:
    a = claim(1, "AAA", "customer", "BBB")  # AAA sells to BBB
    b = claim(2, "BBB", "customer", "AAA")  # BBB sells to AAA
    found = dep.reconcile([a, b], {1: "BBB", 2: "AAA"})
    assert [(c.kind, c.a_id, c.b_id) for c in found] == [("opposite_direction", 1, 2)]


def test_a_customer_claim_and_the_matching_supplier_claim_agree() -> None:
    a = claim(1, "AAA", "customer", "BBB")  # AAA sells to BBB
    b = claim(2, "BBB", "supplier", "AAA")  # BBB buys from AAA
    assert dep.reconcile([a, b], {1: "BBB", 2: "AAA"}) == []


def test_claims_that_never_overlap_in_time_are_not_a_conflict() -> None:
    a = claim(1, "AAA", "customer", "BBB", valid_to=dt.date(2022, 12, 31))
    b = claim(2, "BBB", "customer", "AAA", valid_from=dt.date(2024, 1, 1))
    assert dep.reconcile([a, b], {1: "BBB", 2: "AAA"}) == []


def test_a_claim_without_an_entity_key_is_skipped_not_guessed() -> None:
    a = claim(1, "AAA", "customer", "BBB")
    b = claim(2, "BBB", "customer", "AAA")
    assert dep.reconcile([a, b], {2: "AAA"}) == []


def test_a_relationship_confirmed_after_the_start_is_new_and_a_closed_one_is_ended() -> None:
    since, now = at(2026, 1, 1), at(2026, 6, 1)
    unconfirmed = claim(1, "AAA", "customer", "Big Co", share="0.2", status="suggested")
    confirmed = claim(1, "AAA", "customer", "Big Co", share="0.2")
    closing = claim(
        2,
        "AAA",
        "customer",
        "Old Co",
        share="0.15",
        valid_to=dt.date(2026, 3, 1),
        known_at=at(2025, 11, 1),
    )
    out = dep.changes([unconfirmed, closing], [confirmed, closing], since, now, min_share=D("0.1"))
    assert sorted((c.kind, c.dep.id) for c in out) == [("ended", 2), ("new", 1)]


def test_a_small_relationship_change_is_not_material() -> None:
    since, now = at(2026, 1, 1), at(2026, 6, 1)
    small = claim(1, "AAA", "customer", "Tiny Co", share="0.02")
    assert dep.changes([], [small], since, now, min_share=D("0.1")) == []


def test_owner_assigned_weight_is_flagged_on_every_impact_that_uses_it() -> None:
    edges = [
        dep.Edge(1, "SUP", "HOLD", D("0.30"), "confirmed", at(2026, 1, 1)),
        dep.Edge(-7, "HOLD", "CUST", D("0.50"), "confirmed", at(2026, 1, 1), owner_assigned=True),
    ]
    s = dep.shock_scenario(edges, "CUST", D("0.2"), at(2026, 6, 1))
    by_entity = {i.entity: i for i in s.impacts}
    assert by_entity["HOLD"].uses_owner_assigned is True
    assert by_entity["SUP"].uses_owner_assigned is True  # second hop passes through that weight
    assert by_entity["HOLD"].lower == D("0.10")


def test_a_scenario_without_owner_weights_is_not_flagged() -> None:
    edges = [dep.Edge(1, "SUP", "CUST", D("0.3"), "confirmed", at(2026, 1, 1))]
    [impact] = dep.shock_scenario(edges, "CUST", D("0.2"), at(2026, 6, 1)).impacts
    assert impact.uses_owner_assigned is False


def test_portfolio_exposure_weights_confirmed_numeric_customer_shares_only() -> None:
    weights = {"AAA": D("0.6"), "BBB": D("0.3"), "CCC": D("0.1")}
    deps = [
        claim(1, "AAA", "customer", "Big Co", share="0.20"),
        claim(2, "AAA", "customer", "Other Co", share="0.10"),
        claim(3, "BBB", "customer", "Big Co", share="0.40", status="suggested"),  # not counted
    ]
    keys = {1: "BIG", 2: "OTHER", 3: "BIG"}
    out = dep.portfolio_exposure(weights, deps, keys, at(2026, 6, 1))
    assert out.by_counterparty == {"BIG": D("0.12"), "OTHER": D("0.06")}
    assert out.covered_weight == D("0.6")
    assert out.uncovered == ("BBB", "CCC")  # unknown, not zero


def test_portfolio_exposure_uses_only_each_holdings_latest_period() -> None:
    weights = {"AAA": D("1")}
    deps = [
        claim(1, "AAA", "customer", "Big Co", share="0.50", fiscal_period="FY2024"),
        claim(2, "AAA", "customer", "Big Co", share="0.20", fiscal_period="FY2025"),
    ]
    out = dep.portfolio_exposure(weights, deps, {1: "BIG", 2: "BIG"}, at(2026, 6, 1))
    assert out.by_counterparty == {"BIG": D("0.20")}


def test_portfolio_exposure_leaves_out_a_claim_with_no_entity_key() -> None:
    out = dep.portfolio_exposure(
        {"AAA": D("1")}, [claim(1, "AAA", "customer", "Big Co", share="0.2")], {}, at(2026, 6, 1)
    )
    assert out.flows == () and out.uncovered == ("AAA",)


# --------------------------------------------------------------------------- extraction
NOTE = """Concentrations. Customer A accounted for 23% of total revenue in fiscal 2025.
Two customers accounted for 45% of revenue in fiscal 2024.
Apple Inc. accounted for 12% of net sales in fiscal 2025 under an exclusive supply agreement.
No single customer accounted for more than 10% of revenue in fiscal 2023.
If we were to lose Customer B, which accounted for 30% of revenue, our results could suffer."""


def test_sentences_become_named_anonymous_and_group_drafts_with_offsets() -> None:
    out = extract(NOTE)
    by = {(d.shape, d.name or d.label): d for d in out.drafts}
    a = by[("anonymous", "Customer A")]
    assert (a.share, a.denominator, a.fiscal_period) == (D("0.23"), "total_revenue", "FY2025")
    assert NOTE[a.start : a.end].strip().startswith("Customer A accounted")
    g = by[("group", "Two customers")]
    assert (g.count, g.share, g.fiscal_period) == (2, D("0.45"), "FY2024")
    named = by[("named", "Apple Inc.")]
    assert named.share == D("0.12") and "exclusivity" in named.terms


def test_negated_and_hypothetical_sentences_are_skipped_with_a_reason() -> None:
    out = extract(NOTE)
    reasons = {s.reason for s in out.skipped}
    assert any("negated" in r for r in reasons)
    assert any("hypothetical" in r for r in reasons)
    assert all(d.passage and "No single customer" not in d.passage for d in out.drafts)
    assert all("lose Customer B" not in d.passage for d in out.drafts)


def test_a_table_gives_one_draft_per_year_column_and_ignores_totals() -> None:
    table = (
        "Customer concentration (% of revenue)\n"
        "| | 2025 | 2024 |\n"
        "| Customer A | 23% | 19% |\n"
        "| Northwind Traders | 12% | 9% |\n"
        "| Other | 65% | 72% |\n"
    )
    out = extract(table)
    got = sorted((d.name or d.label, d.fiscal_period, d.share) for d in out.drafts)
    assert got == [
        ("Customer A", "FY2024", D("0.19")),
        ("Customer A", "FY2025", D("0.23")),
        ("Northwind Traders", "FY2024", D("0.09")),
        ("Northwind Traders", "FY2025", D("0.12")),
    ]


def test_a_receivables_share_is_not_a_revenue_share() -> None:
    out = extract("Customer A accounted for 31% of accounts receivable in fiscal 2025.")
    assert out.drafts[0].denominator == "receivables"


def test_an_out_of_range_percentage_is_not_a_claim() -> None:
    assert extract("Customer A accounted for 140% of revenue in fiscal 2025.").drafts == []


# ------------------------------------------------------------------------------------- TED
def test_ted_lot_cpv_and_modification_status_are_read() -> None:
    body = {
        "notices": [
            {
                "publication-number": "123-2026",
                "notice-type": "can-modif",
                "winner-name": {"ENG": ["Northwind Traders"]},
                "buyer-name": {"ENG": ["Ministry"]},
                "total-value": "250000",
                "total-value-cur": ["eur"],
                "classification-cpv": ["30213000", "30213000", "72000000"],
                "identifier-lot": ["LOT-0001", "LOT-0002"],
                "publication-date": "2026-03-01+01:00",
            }
        ],
        "totalNoticeCount": 1,
    }
    [award], _ = parse_ted_response(body)
    assert award.cpv_codes == ("30213000", "72000000")
    assert award.lot_ids == ("LOT-0001", "LOT-0002")
    assert award.status == "modified"


# -------------------------------------------------------------------------- adjudication
def test_the_same_seed_gives_the_same_sample() -> None:
    ids = [str(i) for i in range(500)]
    assert adj.pick(ids, 20, "s") == adj.pick(ids, 20, "s")
    assert adj.pick(ids, 20, "s") != adj.pick(ids, 20, "t")


def test_planted_false_positives_look_like_claims_and_are_mixed_in_by_hash() -> None:
    plant = adj.planted_items("p5.1")
    assert len(plant) == 7 and all(p.machine_value == "true_relationship" for p in plant)
    real = [adj.SampleItem(f"claim:{i}", "x", "suggested", ()) for i in range(20)]
    mixed = adj.shuffle_in(real, plant, "p5.1")
    assert [m.item_ref for m in mixed] == [m.item_ref for m in adj.shuffle_in(real, plant, "p5.1")]
    assert mixed[: len(plant)] != plant  # not placed first


def label(ref: str, human: str, machine: str = "true_relationship") -> dict[str, object]:
    planted = ref.startswith("planted:")
    return {
        "item_ref": ref,
        "human_value": human,
        "correct": adj.is_correct("relationship_claim", machine, human),
        "planted_false_positive": planted,
    }


def sample_rows(real_true: int, real_false: int, planted_caught: int, planted_missed: int) -> list:
    rows = [label(f"claim:{i}", "true_relationship") for i in range(real_true)]
    rows += [label(f"claim:f{i}", "negated") for i in range(real_false)]
    rows += [label(f"planted:c{i}", "negated") for i in range(planted_caught)]
    rows += [label(f"planted:m{i}", "true_relationship") for i in range(planted_missed)]
    return rows


def test_relationship_gate_met_with_enough_claims_precision_and_caught_plants() -> None:
    g = adj.measure_labels("relationship_claim", sample_rows(97, 3, 7, 0))
    assert g.met and g.rate == D("0.97") and g.planted == 7 and g.planted_caught == 7


def test_relationship_gate_fails_when_a_planted_false_positive_is_accepted() -> None:
    g = adj.measure_labels("relationship_claim", sample_rows(100, 0, 6, 1))
    assert not g.met and "planted" in g.reason


def test_relationship_gate_fails_with_too_few_claims_or_too_few_plants() -> None:
    assert not adj.measure_labels("relationship_claim", sample_rows(60, 0, 7, 0)).met
    assert not adj.measure_labels("relationship_claim", sample_rows(100, 0, 3, 0)).met


def test_relationship_gate_fails_below_the_precision_bar() -> None:
    g = adj.measure_labels("relationship_claim", sample_rows(90, 10, 7, 0))
    assert not g.met and "below" in g.reason


def test_planted_items_do_not_inflate_precision() -> None:
    g = adj.measure_labels("relationship_claim", sample_rows(95, 5, 7, 0))
    assert g.rate == D("0.95")  # 95 of 100 real claims; the 7 planted items are not counted


def test_insider_gate_met_at_exactly_the_bar_and_not_below() -> None:
    at_bar = [
        {
            "item_ref": f"a:{i}",
            "correct": i != 0,
            "planted_false_positive": False,
            "human_value": "x",
        }
        for i in range(50)
    ]
    assert adj.measure_labels("insider_class", at_bar).met is True
    below = [{**r, "correct": r["item_ref"] not in {"a:0", "a:1"}} for r in at_bar]
    assert adj.measure_labels("insider_class", below).met is False
    assert adj.measure_labels("insider_class", at_bar[:49]).met is False


def test_the_latest_label_for_an_item_wins() -> None:
    rows = [
        {"item_ref": "a:1", "correct": False, "planted_false_positive": False, "human_value": "x"},
        {"item_ref": "a:1", "correct": True, "planted_false_positive": False, "human_value": "y"},
    ]
    assert adj.measure_labels("document_sample", rows).correct == 1


def test_a_label_must_belong_to_the_dataset() -> None:
    assert adj.valid_label("insider_class", "open_market_buy")
    assert not adj.valid_label("insider_class", "true_relationship")
    assert adj.valid_label("relationship_claim", "hypothetical")
    assert not adj.valid_label("relationship_claim", "open_market_buy")


@pytest.mark.parametrize("dataset", ["bogus"])
def test_unknown_dataset_has_no_gate(dataset: str) -> None:
    assert dataset not in adj.GATES
