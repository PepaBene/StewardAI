"""P3.7: 13F amendment replacement, same-manager rename, CUSIP reuse, ADR/ordinary class, group
stakes, conflicting denominators, fund lag and non-US thresholds, on the pure ownership logic."""

import datetime as dt
from decimal import Decimal as D

from steward.disclosure.ownership import (
    LANE_TITLE,
    NATIONAL_ADAPTERS,
    CusipMapping,
    EffectiveReport,
    Filing13F,
    Holding13F,
    LaneEntry,
    build_lane,
    build_view,
    effective_positions,
    effective_reports,
    hierarchy_conflict,
    manager_name_changes,
    may_enable,
    rank_flow,
    reported_stake_delta,
    resolve_cusip,
    with_review,
)

UTC = dt.UTC
PERIOD = dt.date(2025, 12, 31)
CIK = "0000012345"


def at(y: int, m: int, d: int) -> dt.datetime:
    return dt.datetime(y, m, d, 23, 59, 59, tzinfo=UTC)


def f13(
    acc: str,
    known: dt.datetime,
    kind: str = "13F-HR",
    amend: str | None = None,
    name: str | None = None,
    cik: str = CIK,
    period: dt.date = PERIOD,
) -> Filing13F:
    return Filing13F(acc, cik, period, known.date(), known, kind, amend, name)  # type: ignore[arg-type]


ORIGINAL = f13("A1", at(2026, 2, 10))
RESTATE = f13("A2", at(2026, 3, 20), "13F-HR/A", "RESTATEMENT")
ADD = f13("A3", at(2026, 4, 5), "13F-HR/A", "NEW HOLDINGS")


def test_a_restatement_replaces_the_report_only_once_it_is_known() -> None:
    filings = [ORIGINAL, RESTATE]
    assert effective_reports(filings, at(2026, 1, 1)) == {}
    mid = effective_reports(filings, at(2026, 3, 1))[(CIK, PERIOD)]
    assert mid.accessions == ("A1",) and not mid.amended
    late = effective_reports(filings, at(2026, 6, 1))[(CIK, PERIOD)]
    assert late.accessions == ("A2",) and late.amended and late.known_at == RESTATE.known_at


def test_new_holdings_add_to_the_report_and_a_later_restatement_resets_it() -> None:
    r = effective_reports([ORIGINAL, ADD], at(2026, 6, 1))[(CIK, PERIOD)]
    assert r.accessions == ("A1", "A3")
    r2 = effective_reports(
        [ORIGINAL, ADD, f13("A4", at(2026, 5, 1), "13F-HR/A", "RESTATEMENT")], at(2026, 6, 1)
    )[(CIK, PERIOD)]
    assert r2.accessions == ("A4",)


def test_an_amendment_without_a_type_or_base_is_flagged_not_guessed() -> None:
    untyped = f13("A5", at(2026, 3, 1), "13F-HR/A", None)
    r = effective_reports([ORIGINAL, untyped], at(2026, 6, 1))[(CIK, PERIOD)]
    assert r.accessions == ("A1",) and any("type missing" in x for x in r.flags)
    orphan = effective_reports([ADD], at(2026, 6, 1))[(CIK, PERIOD)]
    assert any("base filing is not known" in x for x in orphan.flags)


def test_positions_keep_options_separate_and_missing_as_none() -> None:
    report = EffectiveReport(CIK, PERIOD, ("A1", "A3"), at(2026, 4, 5), True)
    holds = [
        Holding13F("A1", "037833100", D(100), D(1000)),
        Holding13F("A3", "037833100", D(50), D(500)),  # new holdings add to the same line
        Holding13F("A1", "037833100", D(70), D(700), put_call="Call"),
        Holding13F("A1", "594918104", None, None),
        Holding13F("A9", "999999999", D(1), D(1)),  # a filing outside the report
    ]
    lines = {(p.cusip, p.is_option): p for p in effective_positions(report, holds)}
    assert lines[("037833100", False)].shares == D(150)
    assert (
        lines[("037833100", True)].shares == D(70) and lines[("037833100", True)].put_call == "Call"
    )
    assert lines[("594918104", False)].shares is None  # unknown is not zero
    assert ("999999999", False) not in lines


def test_a_renamed_manager_stays_one_holder() -> None:
    fs = [
        f13("B1", at(2025, 5, 1), name="Old Capital LP", period=dt.date(2025, 3, 31)),
        f13("B2", at(2026, 2, 1), name="New Capital Partners"),
    ]
    assert manager_name_changes(fs) == {
        CIK: [(dt.date(2025, 3, 31), "Old Capital LP"), (PERIOD, "New Capital Partners")]
    }
    assert manager_name_changes([ORIGINAL]) == {}


def test_a_reused_cusip_is_joined_by_date_never_by_guess() -> None:
    maps = [
        CusipMapping("123456789", 1, None, dt.date(2020, 12, 31)),
        CusipMapping("123456789", 2, dt.date(2021, 6, 1), None),
    ]
    old, _ = resolve_cusip("123456789", dt.date(2019, 1, 1), maps)
    assert old is not None and old.issuer_id == 1
    new, _ = resolve_cusip("123456789", dt.date(2024, 1, 1), maps)
    assert new is not None and new.issuer_id == 2
    gap, why = resolve_cusip("123456789", dt.date(2021, 2, 1), maps)
    assert gap is None and "no issuer" in str(why)
    clash, why2 = resolve_cusip(
        "123456789", dt.date(2024, 1, 1), [*maps, CusipMapping("123456789", 3, None, None)]
    )
    assert clash is None and "more than one" in str(why2)


def test_rank_flow_describes_movement_without_intent() -> None:
    prev = {"M1": D(500), "M2": D(300), "M3": D(100)}
    cur = {"M1": D(450), "M2": D(300), "M4": D(250)}
    moves = {m.manager: m for m in rank_flow(prev, cur, top_n=3)}
    assert moves["M1"].kind == "decreased" and moves["M1"].cur_rank == 1
    assert moves["M2"].kind == "unchanged"
    assert moves["M3"].kind == "exited" and moves["M3"].cur_rank is None
    assert moves["M4"].kind == "new" and moves["M4"].prev_rank is None


def test_each_lane_sums_only_itself_and_there_is_no_combined_total() -> None:
    e13f = [
        LaneEntry("M1", D(300), None, PERIOD, dt.date(2026, 2, 10), at(2026, 2, 10)),
        LaneEntry("M2", D(200), None, PERIOD, dt.date(2026, 2, 12), at(2026, 2, 12)),
    ]
    efund = [
        LaneEntry(
            "Fund X", D(80), None, dt.date(2025, 10, 31), dt.date(2025, 12, 29), at(2025, 12, 29)
        )
    ]
    view = build_view(
        {"institutional_13f": e13f, "fund_nport": efund},
        D(1000),
        dt.date(2026, 1, 31),
        not_covered=["national_voting"],
    )
    lanes = {ln.key: ln for ln in view.lanes}
    assert lanes["institutional_13f"].total_shares == D(500) and lanes[
        "institutional_13f"
    ].percent_of_denominator == D(50)
    assert lanes["institutional_13f"].remainder_shares == D(500)
    assert lanes["fund_nport"].total_shares == D(80)
    assert (
        lanes["control_13dg"].status == "no_data"
        and lanes["national_voting"].status == "not_covered"
    )
    assert not hasattr(view, "combined_total")
    assert any("overlap; not added" in w for w in view.warnings)
    assert lanes["fund_nport"].entries[0].lag_days == 59  # fund report date to filing date


def test_a_lane_above_the_denominator_is_flagged_not_clipped() -> None:
    entries = [LaneEntry("M1", D(1200), None, PERIOD, None, at(2026, 2, 10))]
    lane = build_lane("institutional_13f", entries, D(1000))
    assert lane.percent_of_denominator == D(120) and lane.remainder_shares is None
    assert any("exceeds the denominator" in n for n in lane.notes)
    assert any(
        "No share-count denominator" in n
        for n in build_lane("institutional_13f", entries, None).notes
    )


def test_group_stake_total_is_marked_a_floor_and_missing_shares_are_not_zero() -> None:
    entries = [
        LaneEntry("Group A", D(100), D(10), PERIOD, None, at(2026, 2, 1)),
        LaneEntry("Holder B", None, D(6), PERIOD, None, at(2026, 2, 1)),
    ]
    lane = build_lane("control_13dg", entries, D(1000))
    assert lane.total_shares == D(100)
    assert any("floor" in n for n in lane.notes) and any("no share count" in n for n in lane.notes)


def test_a_stake_delta_needs_the_same_class() -> None:
    assert reported_stake_delta(D(5), "Common", D("7.5"), "common ") == (D("2.5"), None)
    assert reported_stake_delta(D(5), "Common", D(7), "Class B")[0] is None
    assert reported_stake_delta(None, "Common", D(7), "Common")[0] is None


def test_every_lane_has_a_title_and_a_basis() -> None:
    assert set(LANE_TITLE) == {"institutional_13f", "control_13dg", "fund_nport", "national_voting"}
    lane = build_lane("institutional_13f", [], None)
    assert "not a beneficial stake" in lane.basis


def test_a_jurisdiction_needs_twenty_reviewed_examples_at_98_percent() -> None:
    uk = NATIONAL_ADAPTERS["uk_tr1"]
    assert not may_enable(uk)[0]
    assert not may_enable(with_review(uk, 19, 19))[0]
    assert not may_enable(with_review(uk, 20, 19))[0]  # 95%
    assert may_enable(with_review(uk, 50, 49))[0]  # 98%
    assert (
        NATIONAL_ADAPTERS["uk_tr1"].measures == "voting_rights"
    )  # voting rights are not economic interest
    assert (
        D("3") in uk.thresholds_percent
        and D("3") not in NATIONAL_ADAPTERS["jp_edinet"].thresholds_percent
    )


def test_registry_control_and_filing_stake_that_disagree_go_to_review() -> None:
    assert hierarchy_conflict(None, True) is not None
    assert hierarchy_conflict(D(10), True) is not None
    assert hierarchy_conflict(D(60), False) is not None
    assert hierarchy_conflict(D(60), True) is None
    assert hierarchy_conflict(D(8), False) is None


def test_a_lane_that_mixes_report_dates_says_so() -> None:
    mixed = [
        LaneEntry("M1", D(10), None, dt.date(2025, 12, 31), None, at(2026, 2, 10)),
        LaneEntry("M2", D(5), None, dt.date(2025, 9, 30), None, at(2025, 11, 10)),
    ]
    notes = build_lane("institutional_13f", mixed, D(100)).notes
    assert any("2 different dates" in n for n in notes)
    same = [mixed[0], LaneEntry("M3", D(5), None, dt.date(2025, 12, 31), None, at(2026, 2, 11))]
    assert not any(
        "different dates" in n for n in build_lane("institutional_13f", same, D(100)).notes
    )
