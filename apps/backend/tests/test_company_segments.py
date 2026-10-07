"""Plan M2.12: segment analysis, eliminations, changed definitions and the owner's mapping."""

import datetime as dt
from decimal import Decimal

from steward.company.segments import MapLine, build_analysis, exposures, newest_mappings
from steward.fundamentals.read import (
    HoldingSegments,
    SegmentHistoryPoint,
    SegmentPeriod,
    SegmentRow,
)

D = Decimal


def row(label: str, value: str) -> SegmentRow:
    return SegmentRow(label=label, member=None, value=D(value), share=None)


def period(year: int) -> SegmentPeriod:
    return SegmentPeriod(
        start=dt.date(year, 1, 1),
        end=dt.date(year, 12, 31),
        months=12,
        basis="annual",
        label=f"FY{year}",
    )


def segments(total: str | None = "1160") -> HoldingSegments:
    history = [
        SegmentHistoryPoint(
            period=period(2024),
            rows=[
                row("Cloud", "600"),
                row("Devices", "400"),
                row("Intersegment eliminations", "-50"),
            ],
        ),
        SegmentHistoryPoint(
            period=period(2025),
            rows=[
                row("Cloud", "800"),
                row("Devices", "350"),
                row("Services", "100"),
                row("Intersegment eliminations", "-60"),
                row("Corporate and unallocated", "-30"),
            ],
        ),
    ]
    return HoldingSegments(
        ticker="AAA",
        basis="annual",
        available_bases=["annual"],
        period=period(2025),
        currency="USD",
        geographic=[],
        segment=[],
        product=[],
        total_revenue=None if total is None else D(total),
        history={"segment": history},
        source=None,
        as_of=None,
    )


def map_line(i: int, member: str, value: str, share: str, context: str = "country") -> MapLine:
    return MapLine(
        id=i,
        breakdown="segment",
        member_label=member,
        context=context,
        value=value,
        share=D(share),
        note=None,
        created_at=dt.datetime(2026, 10, 7, tzinfo=dt.UTC),
    )


def test_eliminations_are_separated_and_tie_to_the_reported_total() -> None:
    bd = build_analysis("AAA", segments(), []).breakdowns[0]
    members = {m.label: m for m in bd.members}
    assert members["Intersegment eliminations"].role == "elimination"
    assert members["Corporate and unallocated"].role == "unallocated"
    # operating 800 + 350 + 100 = 1250; eliminations -60; unallocated -30; after = 1160
    assert (bd.operating_total, bd.eliminations, bd.unallocated) == (D(1250), D(-60), D(-30))
    assert bd.after_eliminations == D(1160) and bd.difference == D(0) and bd.difference_note is None
    assert members["Cloud"].latest_share == D("0.64000000")  # 800 / 1250
    assert members["Cloud"].change == D("0.33333333")  # 800 / 600 - 1
    assert members["Devices"].change == D("-0.12500000")  # 350 / 400 - 1


def test_new_member_is_flagged_not_shown_as_growth_from_zero() -> None:
    members = {m.label: m for m in build_analysis("AAA", segments(), []).breakdowns[0].members}
    assert members["Services"].change is None
    assert "New in FY2025" in (members["Services"].definition_note or "")


def test_difference_to_the_filing_total_is_stated() -> None:
    bd = build_analysis("AAA", segments(total="1300"), []).breakdowns[0]
    assert bd.difference == D(140) and "differ from the filing" in (bd.difference_note or "")
    assert "not stored" in (
        build_analysis("AAA", segments(total=None), []).breakdowns[0].difference_note or ""
    )


def test_mapped_exposure_keeps_unmapped_and_unallocated_visible() -> None:
    maps = [
        map_line(1, "Cloud", "US", "0.5"),
        map_line(2, "Cloud", "IE", "0.5"),
        map_line(3, "Devices", "CN", "1"),
    ]
    bd = build_analysis("AAA", segments(), maps)
    e = bd.exposures[0]
    lines = {x.value: x for x in e.lines}
    # US 800 x .5 = 400, IE 400, CN 350; mapped 1150 of 1250; Services (100) stays unmapped
    assert (lines["US"].revenue, lines["IE"].revenue, lines["CN"].revenue) == (
        D(400),
        D(400),
        D(350),
    )
    assert lines["US"].share == D("0.32000000")
    assert (e.mapped, e.unmapped, e.unallocated) == (D(1150), D(100), D(-30))
    assert "Estimate" in e.label


def test_shares_above_one_are_scaled_down_with_a_warning() -> None:
    maps = [map_line(1, "Devices", "CN", "0.8"), map_line(2, "Devices", "VN", "0.6")]
    e = exposures(build_analysis("AAA", segments(), []).breakdowns, maps)[0]
    lines = {x.value: x.revenue for x in e.lines}
    # 350 x .8 / 1.4 = 200 and 350 x .6 / 1.4 = 150: never more than the segment's revenue
    assert lines == {"CN": D(200), "VN": D(150)} and any("scaled down" in w for w in e.warnings)


def test_newest_mapping_wins_and_zero_share_removes_it() -> None:
    rows = [
        {
            "id": 1,
            "breakdown": "segment",
            "member_label": "Cloud",
            "context": "country",
            "value": "US",
            "share": D("1"),
            "note": None,
            "created_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        },
        {
            "id": 2,
            "breakdown": "segment",
            "member_label": "Cloud",
            "context": "country",
            "value": "US",
            "share": D("0"),
            "note": None,
            "created_at": dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
        },
        {
            "id": 3,
            "breakdown": "segment",
            "member_label": "Cloud",
            "context": "commodity",
            "value": "silicon",
            "share": D("0.2"),
            "note": None,
            "created_at": dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
        },
    ]
    kept = newest_mappings(rows)
    assert [(m.context, m.value) for m in kept] == [("commodity", "silicon")]
