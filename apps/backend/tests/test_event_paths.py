"""M7.4 event paths: hand-worked cases on a small chain A -> B -> C (A supplies B, B supplies C)."""

import datetime as dt

import pytest

from steward.events.paths import HIGH_WEIGHT, event_paths, severity_of
from steward.xray.supply_chain import SupplyChain, SupplyEdge, SupplyNode

TODAY = dt.date(2026, 10, 1)


def edge(
    a: str, b: str, share: float | None = 0.3, as_of: dt.date = dt.date(2026, 3, 1)
) -> SupplyEdge:
    return SupplyEdge(
        id=None, supplier=a, customer=b, product="chips", customer_share=share,
        source_url="https://example.com/f", as_of=as_of, origin="filing",
    )  # fmt: skip


def chain(edges: list[SupplyEdge], weights: dict[str, float]) -> SupplyChain:
    nodes = [
        SupplyNode(symbol=s, name=None, weight=w, held=w > 0, level=0) for s, w in weights.items()
    ]
    return SupplyChain(nodes=nodes, edges=edges, linked=[], held_in_graph=0.0, notes=[])


def test_a_supplier_event_reaches_the_customer_it_feeds_with_its_weight() -> None:
    c = chain([edge("A", "B")], {"A": 0.05, "B": 0.10})
    out = event_paths(c, "a", "downstream", "confirmed", {"B": ["revenue_growth > 5 %"]}, TODAY)
    assert [(p.end, p.weight) for p in out.paths] == [("B", 0.10)]
    assert out.paths[0].indicators == ["revenue_growth > 5 %"]
    assert out.own_weight == 0.05
    assert out.reached_weight == 0.10


def test_the_end_weight_is_split_into_direct_and_through_funds() -> None:
    c = chain([edge("A", "B")], {"A": 0.0, "B": 0.10})
    c.nodes[1].direct_weight = 0.04
    (path,) = event_paths(c, "A", "downstream", "confirmed", None, TODAY).paths
    assert (path.weight, path.direct_weight) == (0.10, 0.04)
    assert path.fund_weight == pytest.approx(0.06)


def test_path_confidence_never_exceeds_its_weakest_link() -> None:
    # A -> B is high (stated share, recent); B -> C has no share, so the two-step path is medium.
    c = chain([edge("A", "B"), edge("B", "C", share=None)], {"A": 0.0, "B": 0.05, "C": 0.05})
    out = event_paths(c, "A", "downstream", "confirmed", None, TODAY)
    by_end = {p.end: p for p in out.paths}
    assert by_end["B"].confidence == "high"
    assert by_end["C"].confidence == "medium"
    assert by_end["C"].severity == "medium"  # not high, despite a confirmed event and 5% weight


def test_an_old_link_is_low_and_caps_severity() -> None:
    c = chain([edge("A", "B", as_of=dt.date(2020, 1, 1))], {"B": 0.5})
    out = event_paths(c, "A", "downstream", "confirmed", None, TODAY)
    assert out.paths[0].confidence == "low"
    assert out.paths[0].severity == "low"


def test_high_severity_needs_a_confirmed_event_high_links_and_enough_weight() -> None:
    assert severity_of("high", "confirmed", HIGH_WEIGHT) == "high"
    assert severity_of("high", "reported", 0.5) == "medium"
    assert severity_of("high", "rumoured", 0.5) == "low"
    assert severity_of("high", "confirmed", HIGH_WEIGHT / 2) == "medium"
    assert severity_of("medium", "confirmed", 0.5) == "medium"
    assert severity_of("high", "confirmed", 0.0) == "none"


def test_a_customer_loss_flows_upstream_and_shows_the_stated_share_for_one_link() -> None:
    c = chain(
        [edge("A", "B", share=0.4), edge("Z", "A", share=0.2)], {"A": 0.03, "B": 0.0, "Z": 0.04}
    )
    out = event_paths(c, "B", "upstream", "confirmed", None, TODAY)
    by_end = {p.end: p for p in out.paths}
    assert by_end["A"].revenue_share == 0.4
    assert by_end["Z"].revenue_share is None  # two steps: no compounding of shares


def test_links_to_companies_not_held_are_not_listed_and_say_so() -> None:
    c = chain([edge("A", "B")], {"A": 0.1, "B": 0.0})
    out = event_paths(c, "A", "downstream", "confirmed", None, TODAY)
    assert out.paths == []
    assert "does not hold" in out.notes[0]


def test_an_unknown_company_has_no_path_and_a_note() -> None:
    out = event_paths(chain([], {"A": 0.1}), "Q", "downstream", "reported", None, TODAY)
    assert out.paths == []
    assert "no links" in out.notes[0]


def test_a_cycle_does_not_loop_and_depth_is_bounded() -> None:
    c = chain([edge("A", "B"), edge("B", "A"), edge("B", "C"), edge("C", "D"), edge("D", "E")],
              {s: 0.1 for s in "ABCDE"})  # fmt: skip
    out = event_paths(c, "A", "downstream", "confirmed", None, TODAY)
    assert {p.end for p in out.paths} == {"B", "C", "D"}  # E would be four steps away
    assert all(len(p.edges) <= 3 for p in out.paths)
