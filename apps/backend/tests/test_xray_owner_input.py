"""Owner-entered X-Ray inputs: regional splits, the supply-chain graph, index weights and the
fund-level average, worked by hand on the small portfolio from `test_xray`."""

import dataclasses
import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from steward.company.segments import Breakdown, MemberLine, SegmentAnalysis
from steward.config.jobs import CSPX_ISIN, LookThroughSettings
from steward.xray import derive, index_weights, supply_chain
from steward.xray.fund_relative import weighted_average
from steward.xray.leaves import build_leaves
from steward.xray.owner_input import (
    IndexWeightsRequest,
    OwnerInput,
    RegionSplitRequest,
    SupplyEdgeRequest,
)
from steward.xray.service import revenue_slices
from tests.test_xray import AAPL, MSFT, NESN, cspx_snapshot, data

SOURCE = {"source_url": "https://example.com/factsheet", "as_of": dt.date(2026, 9, 30)}


def stored(kind: str, key: str, payload: dict, id_: int = 1) -> OwnerInput:  # type: ignore[type-arg]
    return OwnerInput(
        id=id_, kind=kind, key=key, payload=payload, source_url=SOURCE["source_url"],  # type: ignore[arg-type]
        as_of=SOURCE["as_of"], note=None, created_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),  # type: ignore[arg-type]
    )  # fmt: skip


def with_tickers():  # type: ignore[no-untyped-def]
    """CSPX's rows with tickers, so the graph can match them: AAPL .25, MSFT .15, NESN .25."""
    snap = cspx_snapshot()
    rows = [
        dataclasses.replace(r, ticker=t) if t else r
        for r, t in zip(snap.rows, ("AAPL", "MSFT", "NESN", None), strict=True)
    ]
    return data(snapshots={CSPX_ISIN: dataclasses.replace(snap, rows=rows)})


# ----------------------------------------------------------------------------- regions


def member(label: str, share: str) -> MemberLine:
    return MemberLine(
        key=label, label=label, role="operating", values={}, latest_value=None,
        latest_share=Decimal(share), change=None,
    )  # fmt: skip


def geography(*members: MemberLine) -> SegmentAnalysis:
    geo = Breakdown(
        kind="geographic", label="Geography", periods=["FY2025"], latest_period="FY2025",
        members=list(members), operating_total=None,
        eliminations=None, unallocated=None, after_eliminations=None, reported_total=None,
        difference=None, difference_note=None, notes=[],
    )  # fmt: skip
    return SegmentAnalysis(
        ticker="X", basis=None, available_bases=[], currency=None, source_note=None,
        breakdowns=[geo], mappings=[], exposures=[], notes=[], method_version="1",
    )  # fmt: skip


def test_a_region_you_spread_reaches_the_country_view_as_your_estimate():
    analysis = geography(member("United States", "0.55"), member("Europe", "0.30"),
                         member("Germany", "0.15"))  # fmt: skip
    regions = {"europe": {"DE": 0.5, "FR": 0.5}}
    slices = {s.value: s for s in revenue_slices(analysis, regions)["revenue_country"]}
    assert slices["US"].basis == "reported" and slices["US"].share == pytest.approx(0.55)
    assert slices["FR"].basis == "owner" and slices["FR"].share == pytest.approx(0.15)
    # Germany was named outright (0.15) and is also half of Europe (0.15): one slice, yours
    assert slices["DE"].basis == "owner" and slices["DE"].share == pytest.approx(0.30)
    assert sum(s.share for s in slices.values()) == pytest.approx(1.0)


def test_a_region_nobody_spread_is_still_left_as_no_data():
    analysis = geography(member("United States", "0.70"), member("Asia", "0.30"))
    slices = revenue_slices(analysis, {"europe": {"DE": 1.0}})["revenue_country"]
    assert {s.value for s in slices} == {"US"}


def test_a_region_split_must_name_countries_and_add_up_to_one():
    ok = RegionSplitRequest(region="Europe", countries={"Germany": 0.6, "fr": 0.4}, **SOURCE)
    assert ok.countries == {"DE": 0.6, "FR": 0.4}
    with pytest.raises(ValidationError, match="add up to 1"):
        RegionSplitRequest(region="Europe", countries={"DE": 0.6, "FR": 0.3}, **SOURCE)
    with pytest.raises(ValidationError, match="not a country"):
        RegionSplitRequest(region="Europe", countries={"Atlantis": 1.0}, **SOURCE)


def test_every_entry_needs_a_web_address_and_a_date_that_is_not_in_the_future():
    with pytest.raises(ValidationError, match="web address"):
        RegionSplitRequest(region="Europe", countries={"DE": 1.0}, source_url="my notes",
                           as_of=dt.date(2026, 1, 1))  # fmt: skip
    with pytest.raises(ValidationError, match="future"):
        RegionSplitRequest(region="Europe", countries={"DE": 1.0},
                           source_url="https://example.com", as_of=dt.date(2999, 1, 1))  # fmt: skip


# ----------------------------------------------------------------------------- supply chain


def test_the_supply_chain_places_each_company_against_what_you_hold():
    leafset = build_leaves(with_tickers())
    edges = [
        stored("supply_edge", "TSM>AAPL", {"supplier": "TSM", "customer": "AAPL",
                                          "product": "chips", "customer_share": 0.2}, 1),
        stored("supply_edge", "AAPL>MSFT", {"supplier": "AAPL", "customer": "MSFT",
                                           "product": "devices", "customer_share": None}, 2),
    ]  # fmt: skip
    graph = supply_chain.build(leafset, edges)
    nodes = {n.symbol: n for n in graph.nodes}
    assert nodes["TSM"].level == 0 and nodes["AAPL"].level == 1 and nodes["MSFT"].level == 2
    assert not nodes["TSM"].held and nodes["TSM"].weight == 0
    assert nodes["AAPL"].weight == pytest.approx(0.25) and nodes["MSFT"].weight == pytest.approx(
        0.15
    )
    # TSM is not held, so only the AAPL>MSFT edge has both ends in the portfolio
    assert [(p.supplier, p.customer) for p in graph.linked] == [("AAPL", "MSFT")]
    assert graph.linked[0].combined_weight == pytest.approx(0.40)
    assert graph.held_in_graph == pytest.approx(0.40)
    assert any("Nothing is inferred" in n for n in graph.notes)


def test_a_loop_of_customers_still_lays_out_and_an_empty_graph_says_so():
    loop = [
        stored("supply_edge", "A>B", {"supplier": "A", "customer": "B", "product": "x",
                                     "customer_share": None}, 1),
        stored("supply_edge", "B>A", {"supplier": "B", "customer": "A", "product": "y",
                                     "customer_share": None}, 2),
    ]  # fmt: skip
    graph = supply_chain.build(build_leaves(with_tickers()), loop)
    assert {n.symbol for n in graph.nodes} == {"A", "B"}
    empty = supply_chain.build(build_leaves(with_tickers()), [])
    assert empty.nodes == [] and any("No links yet" in n for n in empty.notes)


def test_a_company_cannot_supply_itself():
    with pytest.raises(ValidationError, match="supply itself"):
        SupplyEdgeRequest(supplier="aapl", customer="AAPL", product="x", **SOURCE)


# ----------------------------------------------------------------------------- index weights


def test_active_share_is_half_the_summed_weight_differences():
    leafset = build_leaves(with_tickers())
    # equity renormalised: AAPL .25, MSFT .15, NESN .25 of .65 -> .3846, .2308, .3846
    index = stored("index_weights", "Test 100", {"constituents": [
        {"name": "Apple", "isin": AAPL, "ticker": None, "weight": 0.5},
        {"name": "Microsoft", "isin": MSFT, "ticker": None, "weight": 0.3},
        {"name": "Other", "isin": None, "ticker": "OTH", "weight": 0.2},
    ]})  # fmt: skip
    result = index_weights.compare(leafset, index)
    p = {"AAPL": 0.25 / 0.65, "MSFT": 0.15 / 0.65, "NESN": 0.25 / 0.65}
    expected = 0.5 * (abs(p["AAPL"] - 0.5) + abs(p["MSFT"] - 0.3) + p["NESN"] + 0.2)
    assert result.active_share == pytest.approx(expected)
    assert result.unmatched == 1 and not result.partial
    assert result.index_weight_held == pytest.approx(0.8)
    assert result.underweights[0].name == "Other" or result.underweights[0].difference < 0
    assert any("not an official feed" in n for n in result.notes)


def test_a_partial_index_paste_is_flagged_as_approximate():
    leafset = build_leaves(with_tickers())
    index = stored("index_weights", "Top 2", {"constituents": [
        {"name": "Apple", "isin": AAPL, "ticker": None, "weight": 0.3},
        {"name": "Nestle", "isin": NESN, "ticker": None, "weight": 0.2},
    ]})  # fmt: skip
    result = index_weights.compare(leafset, index)
    assert result.partial and any("approximate" in n for n in result.notes)


def test_index_weights_cannot_add_up_to_more_than_one():
    one = {"name": "A", "weight": 0.7}
    with pytest.raises(ValidationError, match="more than 1"):
        IndexWeightsRequest(label="X", constituents=[one, {"name": "B", "weight": 0.5}], **SOURCE)


# ----------------------------------------------------------------------------- derived from funds


def test_index_weights_are_the_funds_equity_rows_renormalised_with_its_address_and_date():
    fund = LookThroughSettings().fund(CSPX_ISIN)
    assert fund is not None
    body = derive.index_request(fund, cspx_snapshot())
    # equity rows: AAPL .10, MSFT .30, NESN .50 (cash .05 left out) -> 1/9, 1/3, 5/9
    weights = {c.name: c.weight for c in body.constituents}
    assert weights == pytest.approx(
        {"Apple Inc.": 0.1 / 0.9, "Microsoft Corp": 0.3 / 0.9, "Nestle SA": 0.5 / 0.9}
    )
    assert body.constituents[0].name == "Nestle SA"  # largest first
    assert body.as_of == dt.date(2026, 10, 1) and body.label == "CSPX"
    assert body.source_url == "https://www.ishares.com/uk/individual/en/products/253743"
    assert body.note and "not the index provider's own file" in body.note


def test_a_region_is_spread_the_way_the_named_fund_spreads_its_equity():
    fund = LookThroughSettings().fund(CSPX_ISIN)
    assert fund is not None
    body = derive.region_request("Europe", fund, cspx_snapshot())
    assert body.countries == pytest.approx({"US": 0.4 / 0.9, "CH": 0.5 / 0.9})
    assert body.region == "Europe" and body.as_of == dt.date(2026, 10, 1)


def test_a_fund_without_equity_cannot_supply_either_input():
    fund = LookThroughSettings().fund(CSPX_ISIN)
    assert fund is not None
    empty = dataclasses.replace(cspx_snapshot(), rows=[])
    with pytest.raises(derive.DeriveError):
        derive.index_request(fund, empty)
    with pytest.raises(derive.DeriveError):
        derive.region_request("Europe", fund, empty)


# ----------------------------------------------------------------------------- funds


def test_the_fund_average_is_weighted_and_empty_without_weight():
    assert weighted_average([(0.5, 0.01), (0.25, -0.01)]) == pytest.approx(0.01 / 3)
    assert weighted_average([]) is None
