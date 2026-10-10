"""P3.5: PSC bands and the GLEIF parent against a reported stake: agree, conflict (kept) or not
comparable, with the band as a band."""

import datetime as dt
from decimal import Decimal as D

from steward.disclosure.control_corroboration import (
    Basis,
    GleifParent,
    PercentBand,
    PscRecord,
    ReportedStake,
    compare_gleif,
    compare_psc,
    corroborate,
    normalise_name,
    parse_nature,
)

UTC = dt.UTC
NOW = dt.datetime(2026, 6, 1, tzinfo=UTC)
LEI = "5493001KJTIIGC8Y1R12"
PARENT_LEI = "529900T8BM49AURSDO55"


def psc(
    name: str = "Alpha Holdings Ltd",
    natures: tuple[str, ...] = ("ownership-of-shares-25-to-50-percent",),
    known_at: dt.datetime = dt.datetime(2026, 3, 1, tzinfo=UTC),
    ceased_on: dt.date | None = None,
) -> PscRecord:
    return PscRecord("01234567", name, natures, dt.date(2025, 5, 1), ceased_on, known_at)


def stake(
    percent: str | None = "30", basis: Basis | None = "shares", name: str = "ALPHA HOLDINGS LIMITED"
) -> ReportedStake:
    value = None if percent is None else D(percent)
    return ReportedStake(name, None, value, basis, dt.date(2026, 1, 15))


def test_nature_strings_become_bands_and_other_natures_have_none() -> None:
    assert parse_nature("ownership-of-shares-50-to-75-percent") == (
        "shares",
        PercentBand(D(50), D(75)),
    )
    assert parse_nature("voting-rights-75-to-100-percent") == ("voting", PercentBand(D(75), D(100)))
    assert parse_nature("right-to-appoint-and-remove-directors") is None
    assert parse_nature("ownership-of-shares-60-to-40-percent") is None


def test_band_edges_follow_more_than_low_up_to_high() -> None:
    band = PercentBand(D(25), D(50))
    assert not band.contains(D(25)) and band.contains(D("25.01")) and band.contains(D(50))
    assert not band.contains(D("50.01"))


def test_percent_inside_the_band_agrees_and_carries_the_band_not_a_point() -> None:
    out = compare_psc(stake("30"), [psc()], NOW)
    assert out.verdict == "agree" and out.needs_review is False
    assert out.band == PercentBand(D(25), D(50)) and out.band.label() == "25-50%"
    assert out.reported_percent == D(30) and out.control_as_of == dt.date(2025, 5, 1)


def test_percent_outside_the_band_is_a_kept_conflict() -> None:
    out = compare_psc(stake("62"), [psc()], NOW)
    assert out.verdict == "conflict" and out.needs_review is True
    assert out.band == PercentBand(D(25), D(50)) and "kept for review" in out.reason


def test_basis_must_match_so_a_voting_band_is_not_read_as_shares() -> None:
    only_voting = psc(natures=("voting-rights-50-to-75-percent",))
    out = compare_psc(stake("30", "shares"), [only_voting], NOW)
    assert out.verdict == "not_comparable" and out.band is None
    assert compare_psc(stake("60", "voting"), [only_voting], NOW).verdict == "agree"


def test_not_comparable_reasons_never_become_a_verdict() -> None:
    assert "no percent" in compare_psc(stake(None), [psc()], NOW).reason
    assert "shares or voting" in compare_psc(stake("30", None), [psc()], NOW).reason
    assert "no PSC record" in compare_psc(stake(name="Other Co"), [psc()], NOW).reason
    ended = compare_psc(stake(), [psc(ceased_on=dt.date(2025, 12, 1))], NOW)
    assert ended.verdict == "not_comparable" and "ended" in ended.reason
    none = compare_psc(stake(), [psc(natures=("significant-influence-or-control",))], NOW)
    assert none.verdict == "not_comparable"
    two = psc(
        natures=("ownership-of-shares-25-to-50-percent", "ownership-of-shares-50-to-75-percent")
    )
    ambiguous = compare_psc(stake(), [two], NOW)
    assert ambiguous.verdict == "not_comparable" and ambiguous.band is None


def test_a_psc_record_first_known_after_the_cutoff_is_ignored() -> None:
    late = psc(known_at=dt.datetime(2026, 9, 1, tzinfo=UTC))
    assert compare_psc(stake(), [late], NOW).verdict == "not_comparable"


def test_name_normalisation_drops_company_forms_only() -> None:
    assert normalise_name("Alpha Holdings Ltd.") == normalise_name("ALPHA HOLDINGS LIMITED")
    assert normalise_name("Alpha & Co plc") != normalise_name("Beta & Co plc")


def parent(lei: str | None = PARENT_LEI, exception: str | None = None) -> GleifParent:
    return GleifParent(
        LEI,
        lei,
        "Alpha Holdings Ltd" if lei else None,
        "IS_DIRECTLY_CONSOLIDATED_BY" if lei else None,
        exception,
        dt.datetime(2026, 2, 1, tzinfo=UTC),
    )


def holder(percent: str | None, lei: str | None, name: str = "Alpha Holdings Ltd") -> ReportedStake:
    return ReportedStake(name, lei, None if percent is None else D(percent), "shares", None)


def test_gleif_parent_with_a_majority_stake_agrees_without_a_band() -> None:
    out = compare_gleif(holder("62", PARENT_LEI), parent(), NOW)
    assert out.verdict == "agree" and out.band is None


def test_gleif_parent_with_a_minority_stake_is_a_conflict() -> None:
    out = compare_gleif(holder("30", PARENT_LEI), parent(), NOW)
    assert out.verdict == "conflict" and out.needs_review


def test_majority_holder_that_is_not_the_gleif_parent_is_a_conflict() -> None:
    out = compare_gleif(holder("70", "5299000000000000AB12", "Beta Fund"), parent(), NOW)
    assert out.verdict == "conflict"


def test_gleif_not_comparable_cases() -> None:
    assert compare_gleif(holder("62", PARENT_LEI), None, NOW).verdict == "not_comparable"
    exc = compare_gleif(holder("62", PARENT_LEI), parent(None, "NATURAL_PERSONS"), NOW)
    assert exc.verdict == "not_comparable" and "NATURAL_PERSONS" in exc.reason
    assert compare_gleif(holder(None, PARENT_LEI), parent(), NOW).verdict == "not_comparable"
    minority = compare_gleif(holder("10", "5299000000000000AB12", "Beta"), parent(), NOW)
    assert minority.verdict == "not_comparable"
    future = GleifParent(LEI, PARENT_LEI, "X", None, None, dt.datetime(2027, 1, 1, tzinfo=UTC))
    assert compare_gleif(holder("62", PARENT_LEI), future, NOW).verdict == "not_comparable"


def test_name_only_parent_match_says_so() -> None:
    out = compare_gleif(holder("62", None), parent(), NOW)
    assert out.verdict == "agree" and "name only" in out.reason


def test_corroborate_keeps_two_separate_verdicts() -> None:
    both = corroborate(stake("62"), [psc()], parent(), NOW)
    assert [c.source for c in both] == ["psc", "gleif"]
    assert [c.verdict for c in both] == ["conflict", "agree"]  # a name-matched parent, majority
    mixed = corroborate(holder("30", PARENT_LEI), [psc()], parent(), NOW)
    assert [c.verdict for c in mixed] == ["agree", "conflict"]  # not merged into one
