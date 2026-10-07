"""Issuer sync of fund facts and NAV: the readers and the write rules, no network or database."""

import datetime as dt
from decimal import Decimal

import pytest

from steward.fundresearch import issuer_sync as sync

D = Decimal
TODAY = dt.date(2026, 10, 7)
NASDAQ = sync.IssuerSource("IE00B53SZB19", "ishares", "253741")
BRENT = sync.IssuerSource("JE00B78CGV99", "wisdomtree")


def dp(value, as_of=None):
    return {"formattedValue": value, "value": value, "formattedAsOfDate": as_of}


def body(component, containers):
    return {
        "componentsByNameMap": {
            component: {
                "containersByNameMap": {
                    name: {"dataPointsByNameMap": points} for name, points in containers.items()
                }
            }
        }
    }


def key_facts(**over):
    points = {
        "isin": dp("IE00B53SZB19"),
        "fundMethodologyTypeCode": dp("Replicated"),
        "productStructure": dp("Physical"),
        "inceptionDate": dp("26/Jan/2010"),
        "domicile": dp("Ireland"),
        "ucitsCompliantFlag": dp("Yes"),
        "indexSeriesName": dp("NASDAQ 100 Index"),
        "seriesBaseCurrencyCode": dp("USD"),
    }
    points.update(over)
    return body("keyFundFacts", {"default": points})


def header(amount="1,793.99", as_of="06/Oct/2026"):
    return body("fundHeader", {"fundNav": {"navAmount": dp(amount, as_of)}})


def test_ishares_reader_maps_the_issuer_fields_to_the_fact_keys_and_reads_the_nav():
    r = sync.parse_ishares(key_facts(), header(), NASDAQ, TODAY)
    assert {k: c.value for k, c in r.facts.items()} == {
        "replication_method": "physical_full",
        "inception_date": "2010-01-26",
        "domicile": "Ireland",
        "ucits": "yes",
        "tracking_index": "NASDAQ 100 Index",
    }
    assert r.nav == sync.NavReading(dt.date(2026, 10, 6), D("1793.99"), "USD")
    assert r.skipped == {}


def test_a_swap_fund_is_synthetic_and_an_unrecognised_method_is_skipped_not_guessed():
    swap = key_facts(fundMethodologyTypeCode=dp("Swap"), productStructure=dp("Synthetic"))
    assert (
        sync.parse_ishares(swap, header(), NASDAQ, TODAY).facts["replication_method"].value
        == "synthetic"
    )
    odd = key_facts(fundMethodologyTypeCode=dp("Mystery"), productStructure=dp("Hybrid"))
    r = sync.parse_ishares(odd, header(), NASDAQ, TODAY)
    assert (
        "replication_method" not in r.facts and "not recognised" in r.skipped["replication_method"]
    )


def test_a_missing_or_future_nav_is_left_out_with_its_reason():
    r = sync.parse_ishares(key_facts(), header(amount="-", as_of=None), NASDAQ, TODAY)
    assert r.nav is None and "no usable published NAV" in r.skipped["nav"]
    future = sync.parse_ishares(key_facts(), header(as_of="09/Oct/2026"), NASDAQ, TODAY)
    assert future.nav is None


def test_a_response_for_another_fund_is_refused():
    with pytest.raises(sync.IssuerReadError, match="expected IE00B53SZB19"):
        sync.parse_ishares(key_facts(isin=dp("IE00BDFL4P12")), header(), NASDAQ, TODAY)


WISDOMTREE_TEXT = """Investment Objective
WisdomTree Brent Crude Oil is a fully collateralised ETC.
Document Date: 31/08/2026
BRNT
Product Information
Asset Class Commodities
ISIN JE00B78CGV99
Base Currency USD
Inception Date  21/02/2012
Currency Hedged No
Management Fee 0.49%
Domicile Jersey
Legal Form / Structure Debt security / ETC
Replication Method Synthetic - fully funded
collateralised swap
UCITS Eligible Yes
UCITS Compliant  No
Index Information
Index
Bloomberg Commodity Brent
Crude Subindex 4W Total
Return
Index Provider Bloomberg
"""


def test_wisdomtree_factsheet_reader_records_an_etc_as_not_ucits_and_has_no_nav():
    r = sync.parse_wisdomtree(WISDOMTREE_TEXT, BRENT, TODAY)
    assert {k: c.value for k, c in r.facts.items()} == {
        "replication_method": "synthetic",
        "inception_date": "2012-02-21",
        "domicile": "Jersey",
        "ucits": "no",
        "tracking_index": "Bloomberg Commodity Brent Crude Subindex 4W Total Return",
    }
    assert r.as_of == dt.date(2026, 8, 31) and r.nav is None and "no NAV" in r.skipped["nav"]
    with pytest.raises(sync.IssuerReadError):
        sync.parse_wisdomtree(WISDOMTREE_TEXT.replace("JE00B78CGV99", "JE00000000X0"), BRENT, TODAY)


def test_dates_in_the_issuers_formats():
    assert sync.parse_issuer_date("30/Sept/2024") == dt.date(2024, 9, 30)
    assert sync.parse_issuer_date("21/02/2012") == dt.date(2012, 2, 21)
    assert sync.parse_issuer_date("31/Feb/2024") is None and sync.parse_issuer_date("-") is None


def test_facts_are_written_only_when_new_or_changed_and_never_over_the_owners_source():
    ish = "https://www.ishares.com/uk/individual/en/products/253741"
    cands = {
        "domicile": sync.Candidate("Ireland", "p"),
        "ucits": sync.Candidate("no", "p"),
        "tracking_index": sync.Candidate("NASDAQ 100 Index", "p"),
        "inception_date": sync.Candidate("2010-01-26", "p"),
    }
    recorded = {
        "domicile": sync.RecordedFact("ireland", "https://www.ishares.com/ch/x"),  # same value
        "ucits": sync.RecordedFact(
            "yes", "https://www.blackrock.com/uk/y"
        ),  # issuer family: update
        "tracking_index": sync.RecordedFact("My own index note", "https://example.org/mine"),
    }
    plan = sync.plan_fact_writes(cands, recorded, ish)
    assert set(plan.writes) == {"ucits", "inception_date"}
    assert plan.unchanged == ["domicile"]
    assert (
        set(plan.conflicts) == {"tracking_index"}
        and "example.org" in plan.conflicts["tracking_index"]
    )


def test_nav_rules():
    nav = sync.NavReading(dt.date(2026, 10, 6), D("10.66"), "USD")
    ish = "https://www.ishares.com/uk/individual/en/products/287254"
    assert sync.plan_nav_write(None, [], ish) == "none"
    assert sync.plan_nav_write(nav, [], ish) == "write"
    assert sync.plan_nav_write(nav, [(D("10.66"), "USD", ish)], ish) == "unchanged"
    assert (
        sync.plan_nav_write(nav, [(D("10.60"), "USD", "https://www.ishares.com/ch/z")], ish)
        == "write"
    )
    assert (
        sync.plan_nav_write(nav, [(D("10.60"), "USD", "https://example.org/mine")], ish)
        == "conflict"
    )
