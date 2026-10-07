"""P7: estimate series, comparable snapshots, revisions, surprises, segment bridges, the named
estimate gap, the revision gate and the offline extractor. No database."""

import datetime as dt
from decimal import Decimal as D
from pathlib import Path

import pytest

from steward.disclosure import estimate_extract as ex
from steward.disclosure import estimates as est

UTC = dt.UTC
FIXTURES = Path(__file__).parent / "fixtures" / "disclosure"


def at(y: int, m: int, d: int, h: int = 12) -> dt.datetime:
    return dt.datetime(y, m, d, h, tzinfo=UTC)


_ids = iter(range(1, 10_000))


def val(known: dt.datetime, mean: str | None = "100", **kw: object) -> est.Value:
    n = next(_ids)
    base = dict(
        id=n,
        snapshot_id=n,
        series="published_analyst_consensus",
        published_at=known,
        known_at=known,
        metric="Revenue",
        fiscal_period="FY2026Q3",
        period_end=dt.date(2026, 6, 30),
        period_start=dt.date(2026, 4, 1),
        fiscal_calendar="fiscal year ends 30 September",
        unit="amount",
        scale=6,
        basis="GAAP",
        currency="USD",
        mean=None if mean is None else D(mean),
        analyst_count=14,
        panel_method="consensus compiled by investor relations from analysts",
    )
    base.update(kw)
    return est.Value(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------ series
def test_lanes_never_merge_series() -> None:
    values = [
        val(at(2026, 7, 1)),
        val(
            at(2026, 7, 2),
            series="company_guidance",
            mean=None,
            low=D("95"),
            high=D("105"),
            analyst_count=None,
            panel_method=None,
        ),
        val(at(2026, 7, 3), series="event_calendar_estimate"),
    ]
    lane = est.lanes(values)
    assert [len(lane[s]) for s in est.SERIES] == [1, 0, 1, 1, 0]
    assert list(lane) == list(est.SERIES)


def test_guidance_is_never_an_analyst_estimate() -> None:
    bad = val(at(2026, 7, 2), series="company_guidance", mean="100", analyst_count=5)
    assert any("management target" in p for p in est.problems(bad))
    ok = val(
        at(2026, 7, 2),
        series="company_guidance",
        mean=None,
        low=D("95"),
        high=D("105"),
        analyst_count=None,
    )
    assert est.problems(ok) == []
    # a problem line is never compared
    assert est.revision_pairs([bad, ok]) == []


def test_named_estimate_needs_analyst_and_broker() -> None:
    v = val(at(2026, 7, 2), series="named_individual_estimate", broker="Broker A")
    assert any("analyst and broker" in p for p in est.problems(v))
    full = val(
        at(2026, 7, 2), series="named_individual_estimate", broker="Broker A", analyst="P. Lee"
    )
    assert est.problems(full) == []


def test_no_named_source_is_a_gap_and_never_derived_from_consensus() -> None:
    consensus = [val(at(2026, 7, 1), mean="100", low=D("90"), high=D("110"))]
    rows, gap = est.named_estimates(consensus, source_gaps=["Acme IR (acme.ir): not covered"])
    assert rows == []
    assert gap is not None and gap.kind == "named_estimates"
    assert "never derived" in gap.detail and "Acme IR" in gap.detail


def test_lane_gaps_state_every_empty_lane() -> None:
    gaps = est.lane_gaps(est.lanes([val(at(2026, 7, 1))]))
    assert {g.kind for g in gaps} == {
        "company_guidance",
        "event_calendar_estimate",
        "owner_scenario",
        "named_estimates",
    }


# -------------------------------------------------------------------------- comparable
def test_compatible_snapshots_give_a_revision() -> None:
    a, b = val(at(2026, 6, 1), "100"), val(at(2026, 7, 1), "105", analyst_count=15)
    (p,) = est.revision_pairs([a, b])
    assert p.comparable and p.statistic == "mean"
    assert (p.change, p.change_pct, p.elapsed_days) == (D("5"), D("5"), 30)
    assert p.direction == "up" and p.analyst_count_change == 1


def test_missing_analyst_count_is_null_with_a_reason() -> None:
    a, b = val(at(2026, 6, 1), "100", analyst_count=None), val(at(2026, 7, 1), "95")
    (p,) = est.revision_pairs([a, b])
    assert p.analyst_count_change is None and "not stated" in (p.analyst_count_note or "")
    assert p.direction == "down" and p.change_pct == D("-5")


def test_percent_is_undefined_for_a_zero_estimate() -> None:
    (p,) = est.revision_pairs([val(at(2026, 6, 1), "0"), val(at(2026, 7, 1), "2")])
    assert p.change == D("2") and p.change_pct is None and p.pct_note


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"basis": "adjusted"}, "basis_changed"),
        ({"basis_definition": "now excludes restructuring"}, "basis_changed"),
        ({"currency": "EUR"}, "currency_changed"),
        ({"panel_method": "vendor consensus"}, "panel_method_changed"),
        ({"period_end": dt.date(2026, 7, 4)}, "period_redefined"),
        ({"fiscal_calendar": "fiscal year ends 31 December"}, "period_redefined"),
        ({"unit": "per_share"}, "unit_changed"),
        ({"scale": 3}, "unit_changed"),
        ({"restated_prior": True}, "restated"),
    ],
)
def test_a_break_is_a_typed_reason_and_never_a_number(
    override: dict[str, object], reason: str
) -> None:
    a, b = val(at(2026, 6, 1), "100"), val(at(2026, 7, 1), "105", **override)
    (p,) = est.revision_pairs([a, b])
    assert not p.comparable and reason in p.reasons
    assert p.change is None and p.change_pct is None and p.direction is None
    assert p.earlier_value is None and p.later_value is None


def test_fiscal_calendar_roll_is_period_redefined() -> None:
    a = val(at(2026, 6, 1), period_end=dt.date(2026, 6, 27))
    b = val(at(2026, 7, 1), period_end=dt.date(2026, 7, 4))
    assert est.break_reasons(a, b) == ("period_redefined",)


def test_company_restatement_and_segment_recast_are_restated() -> None:
    a = val(at(2026, 6, 1), segment="Industrial", segment_taxonomy="fy25 structure")
    b = val(at(2026, 7, 1), segment="Industrial", segment_taxonomy="fy26 structure")
    assert est.break_reasons(a, b) == ("restated",)


def test_panel_replacement_breaks_the_chain_at_the_right_pair() -> None:
    a, b, c = (
        val(at(2026, 5, 1), "100"),
        val(at(2026, 6, 1), "101"),
        val(at(2026, 7, 1), "90", panel_method="vendor"),
    )
    d = val(at(2026, 8, 1), "92", panel_method="vendor")
    pairs = {p.later_id: p for p in est.revision_pairs([a, b, c, d])}
    assert pairs[b.id].comparable and not pairs[c.id].comparable and pairs[d.id].comparable


def test_no_interpolation_only_consecutive_pairs() -> None:
    snaps = [val(at(2026, 5, 1)), val(at(2026, 6, 1)), val(at(2026, 7, 1))]
    pairs = est.revision_pairs(snaps)
    assert len(pairs) == 2 and all(p.elapsed_days in (30, 31) for p in pairs)


def test_a_different_broker_is_never_paired() -> None:
    kw = dict(series="named_individual_estimate", analyst="P. Lee")
    a = val(at(2026, 6, 1), broker="A", **kw)  # type: ignore[arg-type]
    b = val(at(2026, 7, 1), broker="B", **kw)  # type: ignore[arg-type]
    assert est.revision_pairs([a, b]) == []


# ---------------------------------------------------------------------- no lookahead
def test_corrected_file_replaces_the_original_only_once_known() -> None:
    original = val(at(2026, 7, 1), "100")
    corrected = val(at(2026, 7, 20), "102", supersedes_snapshot_id=original.snapshot_id)
    both = [original, corrected]
    cutoffs = [at(2026, 6, 30), at(2026, 7, 10), at(2026, 7, 25)]
    seen = [[v.id for v in est.visible(both, c)] for c in cutoffs]
    assert seen == [[], [original.id], [corrected.id]]


def test_visible_is_inclusive_and_strict_is_exclusive() -> None:
    v = val(at(2026, 7, 1))
    assert est.visible([v], at(2026, 7, 1)) == [v]
    assert est.visible([v], at(2026, 7, 1), strict=True) == []


# ------------------------------------------------------------------------------ surprise
ACTUAL = est.Actual(
    77,
    "Revenue",
    dt.date(2026, 6, 30),
    D("1262"),
    "amount",
    6,
    "USD",
    "GAAP",
    released_at=at(2026, 7, 28),
)


def test_surprise_uses_the_latest_snapshot_known_strictly_before_release() -> None:
    old, late = val(at(2026, 7, 1), "1250", scale=6), val(at(2026, 7, 20), "1255", scale=6)
    archived_later = val(at(2026, 8, 5), "1262", scale=6)
    r = est.surprise(
        ACTUAL,
        [old, late, archived_later],
        metric="Revenue",
        segment=None,
        release_at=ACTUAL.released_at,
    )
    assert r.status == "scored" and r.estimate_value_id == late.id
    assert r.surprise == D("7000000") and r.surprise_pct is not None
    assert r.formula_version == "surprise.v1" and "strictly before" in r.formula


def test_a_file_known_at_the_release_instant_is_not_before_it() -> None:
    same = val(ACTUAL.released_at, "1262")  # type: ignore[arg-type]
    r = est.surprise(ACTUAL, [same], metric="Revenue", segment=None, release_at=ACTUAL.released_at)
    assert r.status == "no_prior_estimate" and r.later_files_only
    assert "published after the release" in r.warnings[0]


def test_a_corrected_file_known_after_release_never_scores_the_earlier_surprise() -> None:
    original = val(at(2026, 7, 1), "1250")
    corrected = val(at(2026, 8, 10), "1262", supersedes_snapshot_id=original.snapshot_id)
    r = est.surprise(
        ACTUAL, [original, corrected], metric="Revenue", segment=None, release_at=ACTUAL.released_at
    )
    assert r.status == "scored" and r.estimate_value_id == original.id


def test_actual_not_loaded_is_explicit() -> None:
    r = est.surprise(None, [val(at(2026, 7, 1))], metric="Revenue", segment=None, release_at=None)
    assert r.status == "actual_not_loaded" and r.surprise is None


def test_surprise_percent_needs_a_nonzero_estimate() -> None:
    zero = val(at(2026, 7, 1), "0")
    r = est.surprise(ACTUAL, [zero], metric="Revenue", segment=None, release_at=ACTUAL.released_at)
    assert r.status == "scored" and r.surprise_pct is None and r.pct_note


def test_currency_restatement_or_basis_mismatch_is_not_scored() -> None:
    eur = val(at(2026, 7, 1), "1250", currency="EUR")
    r = est.surprise(ACTUAL, [eur], metric="Revenue", segment=None, release_at=ACTUAL.released_at)
    assert r.status == "not_comparable" and r.surprise is None
    adj = val(at(2026, 7, 1), "1250", basis="adjusted")
    r2 = est.surprise(ACTUAL, [adj], metric="Revenue", segment=None, release_at=ACTUAL.released_at)
    assert r2.status == "not_comparable" and any("basis differs" in w for w in r2.warnings)


def test_missing_analyst_count_is_a_warning_not_a_failure() -> None:
    r = est.surprise(
        ACTUAL,
        [val(at(2026, 7, 1), "1250", analyst_count=None)],
        metric="Revenue",
        segment=None,
        release_at=ACTUAL.released_at,
    )
    assert r.status == "scored" and "analyst count not stated" in r.warnings


# ------------------------------------------------------------------------------ segments
def seg(name: str, mean: str | None, **kw: object) -> est.Value:
    return val(at(2026, 7, 1), mean, segment=name, segment_taxonomy="fy26", **kw)


def actual(name: str, value: str, **kw: object) -> est.Actual:
    return est.Actual(
        next(_ids),
        "Revenue",
        dt.date(2026, 6, 30),
        D(value),
        "amount",
        6,
        "USD",
        segment=name,
        segment_taxonomy="fy26",
        **kw,
    )  # type: ignore[arg-type]


def test_segment_bridge_keeps_missing_recast_and_non_additive_lines() -> None:
    ests = [seg("Industrial", "700"), seg("Healthcare", "560"), seg("Eliminations", "-9.5")]
    acts = [
        actual("Industrial", "710"),
        actual("Healthcare", "555"),
        actual("Services", "50"),
        actual("Eliminations", "-9", non_additive=True),
    ]
    b = est.segment_bridge(ests, acts, metric="Revenue", period_end=dt.date(2026, 6, 30))
    by = {ln.segment: ln for ln in b.lines}
    assert by["Industrial"].delta == D("10000000")
    assert by["Services"].status == "estimate_missing" and by["Services"].estimate is None
    assert by["Services"].delta is None
    assert by["Eliminations"].non_additive
    assert b.additive_estimate_total == D("1260000000") and b.additive_actual_total == D(
        "1265000000"
    )
    assert any("Services" in n for n in b.notes) and b.inputs


def test_taxonomy_recast_is_flagged_and_not_scored() -> None:
    e = val(at(2026, 7, 1), "700", segment="Industrial", segment_taxonomy="fy25")
    b = est.segment_bridge(
        [e], [actual("Industrial", "710")], metric="Revenue", period_end=dt.date(2026, 6, 30)
    )
    (ln,) = b.lines
    assert ln.status == "taxonomy_break" and ln.delta is None
    assert any("recast" in f for f in ln.flags)


def test_segment_map_and_unmapped_estimates() -> None:
    e = [seg("Industrial products", "700"), seg("Corporate", "1")]
    b = est.segment_bridge(
        e,
        [actual("Industrial", "710")],
        metric="Revenue",
        period_end=dt.date(2026, 6, 30),
        segment_map={"Industrial products": "Industrial"},
    )
    assert b.lines[0].status == "scored" and b.unmapped_estimates == ("Corporate",)


# ------------------------------------------------------------------------------- the gate
def test_revision_gate_counts_distinct_reviewed_pairs() -> None:
    rows = [{"earlier_value_id": i, "later_value_id": i + 1000, "correct": True} for i in range(12)]
    g = est.revision_gate(rows)
    assert not g.met and g.reason == "12 of 20 reviewed pairs"
    rows += [{"earlier_value_id": 0, "later_value_id": 1000, "correct": True}]  # same pair again
    assert est.revision_gate(rows).reviewed == 12


def test_revision_gate_opens_only_at_20_pairs_above_the_bar() -> None:
    rows = [
        {"earlier_value_id": i, "later_value_id": i + 1000, "correct": i % 10 != 0}
        for i in range(20)
    ]
    low = est.revision_gate(rows)
    assert low.reviewed == 20 and low.rate == D("0.9") and low.met
    rows[1]["correct"] = False
    assert not est.revision_gate(rows).met
    # a later label for a pair overrides the earlier one
    rows.append({"earlier_value_id": 1, "later_value_id": 1001, "correct": True})
    assert est.revision_gate(rows).met


# -------------------------------------------------------------------------- the extractor
def extraction() -> ex.Extraction:
    return ex.extract_html((FIXTURES / "consensus_acme_2026q3.htm").read_bytes())


def test_extractor_reads_panel_calendar_and_footnotes() -> None:
    e = extraction()
    assert e.panel_size == 14 and e.published_on == dt.date(2026, 7, 15)
    assert e.fiscal_calendar == "fiscal year ends 30 September"
    assert e.panel_method and "14" not in e.panel_method
    assert any("Adjusted EPS excludes" in f for f in e.footnotes)
    assert "Consolidated estimates (USD millions)" in e.headings


def test_extractor_reads_metrics_basis_period_and_offsets() -> None:
    e = extraction()
    rev = next(
        v
        for v in e.values
        if v.metric == "Revenue" and v.fiscal_period == "FY2026Q3" and not v.segment
    )
    assert (rev.mean, rev.median, rev.high, rev.low, rev.analyst_count) == (
        D("1250.5"),
        D("1248.0"),
        D("1270.0"),
        D("1230.0"),
        14,
    )
    assert (rev.currency, rev.unit, rev.scale, rev.basis) == ("USD", "amount", 6, "GAAP")
    assert rev.period_end == dt.date(2026, 6, 30) and rev.period_start == dt.date(2026, 4, 1)
    assert rev.storable and rev.end > rev.start
    eps = next(v for v in e.values if v.metric.startswith("Adjusted EPS"))
    assert (eps.basis, eps.unit, eps.scale) == ("adjusted", "per_share", 0)
    assert eps.basis_definition and "stock compensation" in eps.basis_definition
    fy = next(v for v in e.values if v.fiscal_period == "FY2026" and v.metric == "Revenue")
    assert fy.period_end == dt.date(2026, 9, 30) and fy.period_start == dt.date(2025, 10, 1)


def test_extractor_leaves_unstated_values_none_never_zero() -> None:
    e = extraction()
    margin = next(v for v in e.values if v.metric == "Operating margin")
    assert margin.mean is None and margin.median == D("18.2") and margin.analyst_count is None
    assert margin.unit == "percent" and margin.currency is None


def test_extractor_flags_segments_and_non_additive_lines() -> None:
    e = extraction()
    segs = {v.segment: v for v in e.values if v.segment}
    assert set(segs) == {"Industrial", "Healthcare", "Eliminations"}
    assert segs["Eliminations"].non_additive and not segs["Industrial"].non_additive
    assert (
        segs["Industrial"].segment_taxonomy and "fiscal 2026" in segs["Industrial"].segment_taxonomy
    )
    assert not segs["Industrial"].restated_prior  # "have not been recast" is not a recast
    assert segs["Eliminations"].mean == D("-9.5") and segs["Eliminations"].high is None


def test_identical_bytes_are_not_a_new_version() -> None:
    data = (FIXTURES / "consensus_acme_2026q3.htm").read_bytes()
    first = ex.extract_html(data).content_hash
    assert not ex.is_new_version({first}, ex.content_hash(data))
    assert ex.is_new_version({first}, ex.content_hash(data + b" "))


def test_xlsx_rows_and_pdf_text_use_the_same_rules() -> None:
    rows = [
        ["Acme Instruments consensus"],
        ["Compiled from 14 analysts as consensus. Published 2026-07-15."],
        ["Fiscal year ends 30 September. Reported figures are IFRS."],
        ["Estimates (EUR millions)"],
        ["Metric", "Period", "Mean", "Median", "High", "Low", "Analysts"],
        ["Revenue", "FY2026", 4980, 4975, 5040, 4910, 14],
        ["Net income", "FY2026", None, None, None, None, None],
    ]
    x = ex.extract_rows(rows)
    assert x.kind == "xlsx" and x.panel_size == 14
    rev = x.values[0]
    assert (rev.currency, rev.basis, rev.mean, rev.period_end) == (
        "EUR",
        "IFRS",
        D("4980"),
        dt.date(2026, 9, 30),
    )
    assert rev.storable and not x.values[1].storable  # nothing stated is not zero
    assert ex.extract_rows(rows).content_hash == x.content_hash
    text = "\n".join(
        [
            "Acme consensus from 14 analysts as consensus. Fiscal year ends 30 September. GAAP.",
            "Estimates (USD millions)",
            "Metric   Period   Mean   Median   High   Low   Analysts",
            "Revenue   FY2026   4,980   4,975   5,040   4,910   14",
        ]
    )
    p = ex.extract_text(text)
    assert p.kind == "pdf" and p.values[0].mean == D("4980") and p.values[0].basis == "GAAP"


def test_a_line_without_a_basis_or_calendar_is_not_storable() -> None:
    rows = [
        ["Consensus from 12 analysts."],
        ["Estimates (USD millions)"],
        ["Metric", "Period", "Mean", "Median", "High", "Low"],
        ["Revenue", "FY2026", 10, 10, 11, 9],
    ]
    x = ex.extract_rows(rows)
    assert not x.values[0].storable
    assert "fiscal-year definition not stated" in x.issues
    assert any("basis" in i for i in x.values[0].issues)


def test_explicit_quarter_sentence_overrides_the_computed_calendar() -> None:
    text = "Fiscal year ends 30 September. Q3 FY2026 is the quarter ended 27 June 2026."
    assert ex.period_dates("FY2026Q3", ex.fiscal_year_end(text), ex.explicit_periods(text))[
        1
    ] == dt.date(2026, 6, 27)


def test_panel_method_ignores_the_panel_size() -> None:
    assert ex.panel_method_of("Consensus of 14 analysts") == ex.panel_method_of(
        "Consensus of 15 analysts"
    )
    assert ex.panel_method_of("Consensus of 14 analysts") != ex.panel_method_of("Vendor consensus")
