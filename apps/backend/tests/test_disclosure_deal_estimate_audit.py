"""Deep-audit fixes for estimate extraction and scoring and for deal extraction (D44-D75)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

from steward.disclosure import deal_extract as dx
from steward.disclosure import deals as dl
from steward.disclosure import estimate_extract as ex
from steward.disclosure import estimates as est


# ----------------------------------------------------------------------------- estimates
def test_a_dollar_bracket_is_negative_and_a_footnote_marker_is_not_a_digit() -> None:
    assert ex.parse_number("$(1.20)") == (D("-1.20"), None)
    assert ex.parse_number("(1.20)") == (D("-1.20"), None)
    assert ex.parse_number("1.20 (1)") == (D("1.20"), None)
    assert ex.parse_number("1,250 [2]") == (D("1250"), None)
    assert ex.parse_number("n/a") == (None, None)


def test_non_gaap_is_adjusted_never_gaap() -> None:
    assert ex._basis("Non-GAAP EPS") == "adjusted"
    assert ex._basis("Non GAAP revenue") == "adjusted"
    assert ex._basis("GAAP revenue") == "GAAP"
    assert ex._reporting_standard("adjusted, non-GAAP figures") is None
    assert ex._reporting_standard("IFRS figures; non-GAAP adjustments") == "IFRS"


def test_a_caption_outside_the_header_still_sets_the_scale() -> None:
    assert ex._unit_for("Revenue", "Mean | High", "Figures (in millions) follow") == ("amount", 6)
    assert ex._unit_for("Revenue", "Mean | High", "") == ("amount", 0)
    assert ex._unit_for("EPS", "Mean", "in millions") == ("per_share", 0)


def test_an_annual_label_can_be_placed_by_an_explicit_sentence() -> None:
    explicit = ex.explicit_periods("FY2025 is the year ended 1 February 2026.")
    assert explicit == {"FY2025": dt.date(2026, 2, 1)}
    start, end = ex.period_dates("FY2025", None, explicit)
    assert end == dt.date(2026, 2, 1) and start == dt.date(2025, 2, 2)


def test_basis_is_compared_by_kind_and_a_mirrored_actual_matches_gaap() -> None:
    assert not est.basis_conflict("GAAP", "as_filed")
    assert not est.basis_conflict("IFRS", "restated")
    assert est.basis_conflict("adjusted", "as_filed")
    assert est.basis_conflict("GAAP", "adjusted_non_gaap")
    assert est.basis_conflict("GAAP", "IFRS")
    assert not est.basis_conflict("GAAP", None)
    assert est.basis_class("weird") is None


def test_an_actual_with_no_basis_is_not_scored() -> None:
    est_value = dl_value()
    actual = est.Actual(
        observation_id=1,
        metric="Revenue",
        period_end=dt.date(2026, 6, 30),
        value=D("1250"),
        unit="amount",
        scale=6,
        currency="USD",
        basis=None,
        released_at=dt.datetime(2026, 7, 30, tzinfo=dt.UTC),
    )
    got = est.surprise(
        actual, [est_value], metric="Revenue", segment=None, release_at=actual.released_at
    )
    assert got.status == "not_comparable" and got.surprise is None


def dl_value() -> est.Value:
    from tests.test_disclosure_estimates import at, val

    return val(at(2026, 7, 1), "1250")


def test_a_year_ending_in_february_needs_a_sentence_to_be_named() -> None:
    page = (
        "<html><body><p>Consensus published 2026-03-01 from 14 analysts. Fiscal year ends 1 "
        "February. Figures in USD millions, GAAP.</p><table><tr><th>Metric</th><th>Period</th>"
        "<th>Mean</th><th>High</th><th>Low</th></tr><tr><td>Revenue</td><td>FY2025</td>"
        "<td>1,250</td><td>1,300</td><td>1,200</td></tr></table></body></html>"
    )
    got = ex.extract_html(page.encode())
    assert got.values and not got.values[0].storable
    assert any("naming is ambiguous" in i for i in got.values[0].issues)


# --------------------------------------------------------------------------------- deals
def test_scale_words_and_non_us_dollars_are_read() -> None:
    a = dx.extract(
        "Acme Corp announced that it has agreed to acquire Borealis Systems, Inc. for $2.5B in "
        "cash."
    )
    assert a.terms["cash_amount"].value == D("2500000000") and a.currency == "USD"
    b = dx.extract(
        "Acme Corp announced that it has agreed to acquire Borealis Systems, Inc. for A$500mn in "
        "cash."
    )
    assert b.terms["cash_amount"].value == D("500000000") and b.currency == "AUD"


def test_a_cash_balance_is_not_the_consideration() -> None:
    got = dx.extract("The Company had $1.2 billion in cash at year end.")
    assert got.terms["cash_amount"].state == "not_stated"
    held = dx.extract(
        "Following the acquisition of Borealis, the Company held $1.2 billion in cash."
    )
    assert held.terms["cash_amount"].state == "not_stated"


def test_the_sentence_naming_the_parties_types_the_deal() -> None:
    text = (
        "Acme Corp announced that it has agreed to acquire Borealis Systems, Inc. for $45.00 per "
        "share. Acme also has a credit facility and a repurchase programme."
    )
    assert dx.extract(text).deal_type == "acquisition"


def test_a_minimum_condition_is_not_the_stake() -> None:
    got = dx.extract(
        "The offer is subject to a minimum condition of 51% of the outstanding shares being "
        "tendered."
    )
    assert got.terms["stake_pct"].state == "not_stated"


def tv(values: dict[str, str]) -> dl.TermVersion:
    return dl.TermVersion(
        1,
        1,
        dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        1,
        currency="USD",
        values={k: D(v) for k, v in values.items()},
    )


def test_dilution_counts_only_the_stock_part_of_the_consideration() -> None:
    ctx = dl.DealContext(acquirer_shares=D(100), target_shares=D(50))
    full = dl.dilution(tv({"exchange_ratio": "1"}), ctx).value
    half = dl.dilution(tv({"exchange_ratio": "1", "stock_pct": "0.5"}), ctx).value
    assert full == D(50) / D(150)
    assert half == D(25) / D(125)
    cash = dl.dilution(tv({"exchange_ratio": "1", "cash_pct": "0.5"}), ctx).value
    assert cash == half


def test_the_latest_terms_are_the_latest_known_not_the_highest_number() -> None:
    early = dl.TermVersion(1, 2, dt.datetime(2026, 1, 1, tzinfo=dt.UTC), 1)
    late = dl.TermVersion(2, 1, dt.datetime(2026, 2, 1, tzinfo=dt.UTC), 1)
    assert dl.latest_version([early, late], dt.datetime(2026, 3, 1, tzinfo=dt.UTC)) == late
