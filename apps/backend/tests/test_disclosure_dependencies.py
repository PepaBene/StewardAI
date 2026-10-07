"""P5: claim shapes, concentration with an unknown remainder, scenario bounds, audit precision."""

import datetime as dt
from decimal import Decimal as D

import pytest

from steward.disclosure.dependencies import (
    Dependency,
    Edge,
    audit_precision,
    audit_sample,
    concentration,
    problems,
    screen_passage,
    shock_scenario,
    visible,
)

UTC = dt.UTC
AS_OF = dt.datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC)
KNOWN = dt.datetime(2026, 2, 20, tzinfo=UTC)
FY = dt.date(2025, 12, 31)


def dep(i: int, shape: str = "named", **kw) -> Dependency:
    base = {
        "id": i,
        "reporter": "AAA",
        "relationship": "customer",
        "shape": shape,
        "status": "confirmed",
        "known_at": KNOWN,
        "passage": "Customer accounted for 22% of revenue.",
        "share": D("0.22"),
        "denominator": "total_revenue",
        "period_end": FY,
    }
    if shape == "named":
        base.update(counterparty_name="Acme Corp", counterparty_key="ACME")
    if shape == "group":
        base.update(counterparty_count=3)
    base.update(kw)
    return Dependency(**base)  # type: ignore[arg-type]


def test_an_anonymous_or_group_claim_never_carries_a_name() -> None:
    assert problems(dep(1)) == []
    assert problems(dep(2, "anonymous", label="Customer A")) == []
    assert problems(dep(3, "anonymous", counterparty_name="Apple")) != []
    assert problems(dep(4, "named", counterparty_name=None)) != []
    assert problems(dep(5, "group", counterparty_count=1)) != []
    assert problems(dep(6, share=D("1.4"))) != []
    assert problems(dep(7, denominator=None)) != []
    award = dep(8, relationship="procurement_award")
    assert any("contract signal" in p for p in problems(award))


def test_concentration_counts_only_confirmed_claims_and_keeps_the_unknown_remainder() -> None:
    rows = [
        dep(1, share=D("0.22")),
        dep(2, "anonymous", label="Customer A", share=D("0.12")),
        dep(3, counterparty_name="Beta", share=D("0.30"), status="suggested"),
        dep(4, counterparty_name="Gamma", share=D("0.10"), status="rejected"),
    ]
    (c,) = concentration(rows, AS_OF)
    assert c.lower_bound == D("0.34") and c.unknown_remainder == D("0.66")
    assert c.largest is not None and c.largest.label == "Acme Corp"
    assert [e.label for e in c.entries] == ["Acme Corp", "Customer A"]
    assert c.suggested_not_counted == 1


def test_different_denominators_and_periods_are_never_combined() -> None:
    rows = [
        dep(1),
        dep(2, "anonymous", label="Customer A", denominator="segment_revenue"),
        dep(3, counterparty_name="Old", period_end=dt.date(2024, 12, 31)),
    ]
    out = concentration(rows, AS_OF)
    assert len(out) == 3
    assert all(len(c.entries) == 1 for c in out)


def test_a_group_claim_raises_the_floor_without_being_added_to_single_parties() -> None:
    rows = [dep(1, share=D("0.10")), dep(2, "group", share=D("0.40"))]
    (c,) = concentration(rows, AS_OF)
    assert c.lower_bound == D("0.10") and c.reported_floor == D("0.40")
    assert c.unknown_remainder == D("0.60") and c.groups[0].count == 3


def test_claims_are_invisible_before_known_and_outside_their_validity() -> None:
    late = dep(1, known_at=dt.datetime(2026, 8, 1, tzinfo=UTC))
    ended = dep(2, valid_to=dt.date(2026, 3, 1))
    assert visible([late, ended], AS_OF) == []
    assert concentration([late, ended], AS_OF) == []


def test_a_period_with_only_suggestions_shows_nothing_confirmed() -> None:
    (c,) = concentration([dep(1, status="suggested")], AS_OF)
    assert c.entries == () and c.unknown_remainder == D(1) and c.suggested_not_counted == 1


def test_screening_flags_but_never_decides() -> None:
    assert "hypothetical" in screen_passage(
        "If we were to lose our largest customer, revenue would fall."
    )
    assert "negated" in screen_passage("No single customer accounted for more than 10% of revenue.")
    assert "historical" in screen_passage("Prior to 2019 Acme was our largest customer.")
    assert screen_passage("Acme accounted for 22% of revenue in 2025.") == []


def test_the_audit_needs_volume_deliberate_false_positives_and_precision() -> None:
    labels = {i: "true_relationship" for i in range(100)}
    planted = [100, 101, 102, 103, 104]
    labels.update({i: "hypothetical" for i in planted})
    ok = audit_precision(labels, deliberate_false_positive_ids=planted)
    assert ok.meets_bar and ok.total == 105
    assert not audit_precision(labels).meets_bar  # no deliberate false positives
    assert not audit_precision({i: "true_relationship" for i in range(20)}).meets_bar
    missed = dict(labels) | {100: "true_relationship"}
    assert not audit_precision(missed, deliberate_false_positive_ids=planted).meets_bar
    weak = {i: ("true_relationship" if i < 60 else "ambiguous") for i in range(100)}
    assert not audit_precision(weak).meets_bar
    with pytest.raises(ValueError):
        audit_precision({1: "maybe"})


def test_the_audit_sample_is_reproducible() -> None:
    ids = list(range(500))
    assert audit_sample(ids, 100) == audit_sample(ids[::-1], 100)
    assert len(set(audit_sample(ids, 100))) == 100


def edge(i: int, supplier: str, customer: str, share: str | None, **kw) -> Edge:
    return Edge(
        i,
        supplier,
        customer,
        None if share is None else D(share),
        kw.pop("status", "confirmed"),
        KNOWN,
        **kw,
    )


def test_a_scenario_gives_lower_and_upper_bounds_and_lists_qualitative_edges() -> None:
    edges = [
        edge(1, "SUP", "ORIGIN", "0.30"),
        edge(2, "SUB", "SUP", "0.50"),
        edge(3, "SUP", "OTHER", None),
        edge(4, "NOPE", "ORIGIN", "0.90", status="suggested"),
    ]
    s = shock_scenario(edges, "ORIGIN", D("0.20"), AS_OF)
    sup, sub = s.impacts
    assert (sup.entity, sup.hop) == ("SUP", 1)
    assert sup.lower == D("0.06") and sup.coverage == D("0.30")
    assert sup.upper == D("0.06") + D("0.70") * D("0.20")
    assert (sub.entity, sub.hop, sub.via) == ("SUB", 2, (1, 2))
    assert sub.lower == D("0.03")
    assert [n.edge_id for n in s.non_quantified] == [3]
    assert all(i.entity != "NOPE" for i in s.impacts)  # a suggestion carries no weight


def test_a_scenario_stops_at_the_requested_hop_and_ignores_expired_edges() -> None:
    edges = [edge(1, "A", "O", "0.5"), edge(2, "B", "A", "0.5", valid_to=dt.date(2026, 1, 1))]
    assert [i.entity for i in shock_scenario(edges, "O", D("0.1"), AS_OF).impacts] == ["A"]
    chain = [edge(1, "A", "O", "0.5"), edge(2, "B", "A", "0.5"), edge(3, "C", "B", "0.5")]
    assert [i.entity for i in shock_scenario(chain, "O", D("0.1"), AS_OF).impacts] == ["A", "B"]
    with pytest.raises(ValueError):
        shock_scenario(chain, "O", D("1.5"), AS_OF)
    with pytest.raises(ValueError):
        shock_scenario(chain, "O", D("0.1"), AS_OF, max_hops=3)
