"""Regression tests for the deal, estimate and answer audit fixes (pure, no database)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from steward.disclosure import answer
from steward.disclosure import deal_extract as dx
from steward.disclosure import deal_job as dj
from steward.disclosure import estimate_extract as ex
from steward.disclosure import estimates as est
from steward.disclosure.sec_discovery import DiscoveredFiling

UTC = dt.UTC


def _filing(items: str, form: str = "8-K") -> DiscoveredFiling:
    return DiscoveredFiling(
        cik="0000000001",
        accession="0000000001-26-000001",
        form=form,
        filed_at=dt.date(2026, 10, 5),
        accepted_at=None,
        report_date=None,
        primary_document="a.htm",
        items=items,
        known_at=dt.datetime(2026, 10, 5, tzinfo=UTC),
        date_notes={},
    )


def _found(text: str = "") -> dx.Extracted:
    return dx.extract(text)


# ------------------------------------------------------------------------------------- 3.4
def test_closed_wins_over_credit_facility_termination() -> None:
    text = (
        "Item 1.02 Termination of a Material Definitive Agreement\n"
        "The Company repaid its credit facility and terminated the credit agreement.\n"
        "Item 2.01 Completion of Acquisition\nThe Company completed the acquisition on "
        "October 1, 2026."
    )
    f = _filing("1.02,2.01")
    assert dj.state_for(f, _found(text), text) == "closed"
    assert dj.effective_date_for("closed", f, _found(text), text) == dt.date(2026, 10, 1)


def test_credit_facility_1_02_is_not_a_termination() -> None:
    text = "Item 1.02 Termination\nThe Company terminated its revolving credit facility."
    assert dj.state_for(_filing("1.02"), _found(text), text) == "announced"


def test_merger_agreement_1_02_terminates_and_body_mention_does_not() -> None:
    text = "Item 1.02 Termination\nThe merger agreement was terminated on September 30, 2026."
    f = _filing("1.02")
    assert dj.state_for(f, _found(text), text) == "terminated"
    assert dj.effective_date_for("terminated", f, _found(text), text) == dt.date(2026, 9, 30)
    body = "As described under Item 1.02 of our prior report, the plan is unchanged."
    assert dj.state_for(_filing("8.01"), _found(body), body) == "announced"


# ------------------------------------------------------------------------------ 4.17 to 4.24
def test_per_share_price_is_not_also_a_cash_total() -> None:
    got = dx.extract("Acme will pay $45.00 in cash per share for each Beta share.")
    assert got.terms["price_per_share"].value == Decimal("45.00")
    assert got.terms["cash_amount"].state == "not_stated"


def test_acquisition_beats_merger_agreement_and_roles_follow_sentence() -> None:
    text = (
        "Acme Corp announced it has entered into a merger agreement to acquire Beta Inc for "
        "$10 per share."
    )
    got = dx.extract(text)
    assert got.deal_type == "acquisition"
    assert got.roles == ("acquirer", "target")
    assert dj.roles_for(got) == ("acquirer", "target")


def test_coupon_is_not_a_stake() -> None:
    assert (
        dx.extract("The Company issued 5.25% senior notes due 2030.").terms["stake_pct"].state
        == "not_stated"
    )
    assert dx.extract("The notes bear a 6% interest rate.").terms["stake_pct"].state == "not_stated"
    stake = dx.extract("Acme bought a 40% interest in Beta.").terms["stake_pct"]
    assert stake.value == Decimal("0.4")


def test_offer_sentence_price_beats_incidental_per_share() -> None:
    text = (
        "The stock closed at $30.00 per share on Friday. "
        "Acme made an offer of $42.00 per share to acquire Beta."
    )
    assert dx.extract(text).terms["price_per_share"].value == Decimal("42.00")


# --------------------------------------------------------------------------------- estimates
def test_unit_for_matches_rate_as_a_word() -> None:
    assert ex._unit_for("Corporate revenue", "")[0] == "amount"
    assert ex._unit_for("Interest rate", "")[0] == "percent"


def test_range_problems_flag_mean_outside_low_high() -> None:
    bad = {"mean": Decimal(5), "median": Decimal(2), "high": Decimal(3), "low": Decimal(1)}
    assert ex.range_problems(bad) == ["mean is outside the low-high range"]
    ok = {"mean": Decimal(2), "median": Decimal(2), "high": Decimal(3), "low": Decimal(1)}
    assert ex.range_problems(ok) == []


def test_fy_actual_is_not_scored_against_q4_estimate() -> None:
    actual = est.Actual(
        observation_id=1,
        metric="revenue",
        period_end=dt.date(2026, 12, 31),
        value=Decimal(100),
        unit="amount",
        period_start=dt.date(2026, 1, 1),
        released_at=dt.datetime(2027, 2, 1, tzinfo=UTC),
    )
    got = est.surprise(actual, [], metric="revenue", segment=None, release_at=None)
    assert got.status == "no_prior_estimate"


# ------------------------------------------------------------------------------------ 4.28
def test_numbers_grounded_sign_magnitude_and_dates() -> None:
    assert answer.numbers_grounded("revenue was 1.5 billion", ["revenue 1,500,000,000"])
    assert not answer.numbers_grounded("revenue was 1.5 million", ["revenue 1,500,000,000"])
    assert not answer.numbers_grounded("loss of 5", ["loss of (5)"])
    assert answer.numbers_grounded("loss of (5)", ["loss of (5)"])
    assert answer.numbers_grounded("on 2026-10-08", ["filed 2026-10-08"])
    assert answer.numbers_grounded("on October 8, 2026", ["filed 2026-10-08"])
    assert not answer.numbers_grounded("on 2026-10-09", ["filed 2026-10-08"])
    assert not answer.numbers_grounded("rate 12.5", ["rate 12,5"])
    assert answer.numbers_grounded("rate 12,5", ["rate 12,5"])


def test_select_ties_newest_first() -> None:
    rows = [
        {"id": 1, "label": "supply", "known_at": dt.datetime(2026, 1, 1, tzinfo=UTC)},
        {"id": 2, "label": "supply", "known_at": dt.datetime(2026, 6, 1, tzinfo=UTC)},
    ]
    assert [r["id"] for r in answer.select("supply agreement", rows)] == [2, 1]
