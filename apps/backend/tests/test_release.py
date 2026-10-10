"""Phase 8 (release gate, source audit ledger, pilot): hand-worked cases for the pure parts."""

import datetime as dt

import pytest
from pydantic import ValidationError

from steward.release import audit
from steward.release.audit import AuditIn, AuditRow
from steward.release.pilot import false_rate
from steward.release.readiness import FEATURES, Feature, assess

NOW = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)


def row(
    i: int,
    kind: str,
    subject: str,
    result: str,
    *,
    region: str | None = "EU",
    field: str = "revenue",
    feature: str = "company",
) -> AuditRow:
    return AuditRow(
        id=i, subject_kind=kind, subject=subject, region=region, feature=feature, field=field,  # type: ignore[arg-type]
        displayed="1", source_value="1", source_url="https://x", result=result,  # type: ignore[arg-type]
        note=None, audited_on=dt.date(2026, 10, 1), created_at=NOW,
    )  # fmt: skip


def test_match_needs_a_source_value_and_link() -> None:
    with pytest.raises(ValidationError):
        AuditIn(subject_kind="etf", subject="IWDA", feature="funds", field="ter",
                displayed="0.20%", result="match")  # fmt: skip
    ok = AuditIn(subject_kind="etf", subject="IWDA", feature="funds", field="ter",
                 displayed="0.20%", result="not_checkable")  # fmt: skip
    assert ok.source_url is None


def test_security_needs_a_region() -> None:
    with pytest.raises(ValidationError):
        AuditIn(subject_kind="security", subject="SAP", feature="company", field="revenue",
                displayed="1", source_value="1", source_url="https://x",
                result="match")  # fmt: skip


def test_newest_row_for_a_fact_stands() -> None:
    # Fact first wrong (id 1), then re-checked after a fix (id 2): only the match stands.
    rows = [row(1, "security", "SAP", "mismatch"), row(2, "security", "SAP", "match")]
    standing = audit.newest_per_fact(rows)
    assert [r.id for r in standing] == [2]


def test_summary_counts_subjects_and_regions() -> None:
    rows = [
        row(i, "security", f"S{i}", "match", region=["EU", "US", "JP"][i % 3]) for i in range(20)
    ]
    rows += [row(100 + i, "etf", f"E{i}", "match") for i in range(5)]
    rows += [row(200 + i, "corporate_action", f"C{i}", "match") for i in range(3)]
    rows += [row(300 + i, "cash_flow_window", f"W{i}", "match") for i in range(2)]
    s = audit.summarize(rows)
    assert s.complete
    assert s.regions == ["Europe", "JP", "North America"]
    assert s.facts_checked == 30


def test_region_spellings_count_once() -> None:
    assert audit.normalise_region(" eu ") == audit.normalise_region("Europe") == "Europe"
    assert audit.normalise_region("Brazil") == "Brazil"
    assert audit.normalise_region("  ") is None


def test_a_fact_typed_two_ways_is_one_fact() -> None:
    rows = [
        row(1, "security", "SAP", "mismatch", field="Revenue"),
        row(2, "security", "sap", "match", field="  revenue "),
    ]
    assert [r.id for r in audit.newest_per_fact(rows)] == [2]


def test_one_mismatch_blocks_completion_and_is_listed() -> None:
    rows = [
        row(i, "security", f"S{i}", "match", region=["EU", "US", "JP"][i % 3]) for i in range(20)
    ]
    rows += [row(100 + i, "etf", f"E{i}", "match") for i in range(5)]
    rows += [row(200 + i, "corporate_action", f"C{i}", "match") for i in range(3)]
    rows += [row(300 + i, "cash_flow_window", f"W{i}", "match") for i in range(2)]
    rows.append(row(999, "etf", "E0", "mismatch", field="ongoing charge"))
    s = audit.summarize(rows)
    assert not s.complete
    assert [m.id for m in s.open_mismatches] == [999]
    etf = next(p for p in s.targets if p.subject_kind == "etf")
    assert (etf.audited, etf.clean, etf.with_mismatch) == (5, 4, 1)


def test_not_checkable_does_not_count_as_audited() -> None:
    s = audit.summarize([row(1, "security", "SAP", "not_checkable")])
    sec = next(p for p in s.targets if p.subject_kind == "security")
    assert sec.audited == 0


def test_two_regions_are_not_enough() -> None:
    rows = [row(i, "security", f"S{i}", "match", region=["EU", "US"][i % 2]) for i in range(20)]
    rows += [row(100 + i, "etf", f"E{i}", "match") for i in range(5)]
    rows += [row(200 + i, "corporate_action", f"C{i}", "match") for i in range(3)]
    rows += [row(300 + i, "cash_flow_window", f"W{i}", "match") for i in range(2)]
    assert not audit.summarize(rows).complete


def access(state: str, tested: bool = True) -> dict[str, object]:
    return {"state": state, "tested_at": NOW if tested else None}


def passed_audit(feature: str) -> list[AuditRow]:
    return [
        row(1, "security", "SAP", "match", feature=feature, field="revenue"),
        row(2, "security", "SAP", "match", feature=feature, field="net income"),
        row(3, "security", "BMW", "match", feature=feature, field="revenue"),
    ]


def test_web_feature_choices_match_the_gate() -> None:
    import re
    from pathlib import Path

    web = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "release" / "release.ts"
    block = web.read_text(encoding="utf-8").split("FEATURE_CHOICES", 1)[1].split("];", 1)[0]
    assert re.findall(r'id: "([a-z_]+)"', block) == [f.id for f in FEATURES]


def test_one_or_two_matches_leave_the_audit_thin() -> None:
    f = Feature("x", "X", "M0", needs=("holdings",))
    one = [row(1, "security", "SAP", "match", feature="x")]
    same_subject = [
        row(1, "security", "SAP", "match", feature="x", field="a"),
        row(2, "security", "SAP", "match", feature="x", field="b"),
        row(3, "security", "SAP", "match", feature="x", field="c"),
    ]
    for rows in (one, same_subject):
        out = assess(f, {}, {"holdings": 3}, rows, True)
        assert out.audit == "thin"
        assert not out.can_activate


def test_paid_only_required_endpoint_is_unavailable_and_never_activatable() -> None:
    f = next(x for x in FEATURES if x.id == "estimates")
    out = assess(
        f, {("fmp", "analyst_estimates"): access("paid_only")}, {}, passed_audit("estimates"), True
    )
    assert out.status == "unavailable"
    assert not out.can_activate


def test_ready_needs_audit_and_zero_cost() -> None:
    f = Feature("x", "X", "M0", needs=("holdings",))
    counts = {"holdings": 12}
    assert assess(f, {}, counts, [], True).status == "ready"
    assert not assess(f, {}, counts, [], True).can_activate  # no audit yet
    # Three matched facts on two subjects pass the feature's own audit but not the whole ledger.
    thin = assess(f, {}, counts, passed_audit("x"), True)
    assert thin.audit == "passed"
    assert not thin.can_activate
    full = complete_ledger("x")
    assert audit.summarize(full).complete
    assert assess(f, {}, counts, full, True).can_activate
    assert not assess(f, {}, counts, full, False).can_activate  # zero-cost failed


def complete_ledger(feature: str) -> list[AuditRow]:
    regions = ["EU", "US", "Asia"]
    rows = [
        row(i + 1, "security", f"S{i}", "match", region=regions[i % 3], feature=feature)
        for i in range(20)
    ]
    for kind, n in (("etf", 5), ("corporate_action", 3), ("cash_flow_window", 2)):
        rows += [
            row(100 + len(rows) + i, kind, f"{kind}{i}", "match", feature=feature) for i in range(n)
        ]
    return rows


def test_audit_source_link_and_feature_are_validated() -> None:
    base = dict(subject_kind="etf", subject="IWDA", field="ter", displayed="0.2%", result="match",
                source_value="0.20%")  # fmt: skip
    for bad_url in ("x", "javascript:alert(1)", "ftp://a.b/c", "https://"):
        with pytest.raises(ValidationError):
            AuditIn(**base, feature="funds", source_url=bad_url)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AuditIn(**base, feature="nonsense", source_url="https://a.b/c")  # type: ignore[arg-type]
    ok = AuditIn(**base, feature=" Funds ", source_url="https://a.b/c")  # type: ignore[arg-type]
    assert ok.feature == "funds"


def test_an_active_feature_whose_gate_now_fails_is_flagged_for_switch_off() -> None:
    from steward.release.activation import Activation, with_gate
    from steward.release.readiness import Readiness

    f = Feature("x", "X", "M0", needs=("holdings",))
    state = assess(f, {}, {"holdings": 12}, passed_audit("x"), True)  # thin ledger: not activatable
    gate = Readiness(as_of=dt.date(2026, 10, 1), features=[state], zero_cost_passed=True,
                     zero_cost_violations=0, ready=1, activatable=0)  # fmt: skip
    on = Activation(feature="x", active=True, note=None, since=NOW)
    off = Activation(feature="x", active=False, note=None, since=NOW)
    flagged, quiet = with_gate([on], gate)[0], with_gate([off], gate)[0]
    assert flagged.needs_switch_off and flagged.gate_note
    assert not quiet.needs_switch_off


def test_mismatch_in_audit_blocks_activation() -> None:
    f = Feature("x", "X", "M0", needs=("holdings",))
    rows = [row(1, "security", "SAP", "match", feature="x"),
            row(2, "security", "BMW", "mismatch", feature="x")]  # fmt: skip
    out = assess(f, {}, {"holdings": 3}, rows, True)
    assert out.audit == "mismatch"
    assert not out.can_activate


def test_empty_store_is_unavailable() -> None:
    f = Feature("x", "X", "M0", needs=("prices",))
    assert assess(f, {}, {"prices": 0}, [], True).status == "unavailable"


def test_untested_and_temporary_states() -> None:
    f = Feature("x", "X", "M0", required=(("fred", "observations"),))
    key = ("fred", "observations")
    assert assess(f, {key: access("access_unverified")}, {}, [], True).status == "untested"
    assert assess(f, {key: access("available", tested=False)}, {}, [], True).status == "untested"
    assert assess(f, {key: access("quota_exhausted")}, {}, [], True).status == "partial"
    assert assess(f, {}, {}, [], True).status == "unavailable"


def test_unavailable_optional_input_makes_the_feature_partial() -> None:
    f = Feature("x", "X", "M0", needs=("holdings",), optional=(("ecb", "sdmx"),))
    out = assess(f, {("ecb", "sdmx"): access("not_covered")}, {"holdings": 1}, [], True)
    assert out.status == "partial"


def test_feature_ids_are_unique() -> None:
    ids = [f.id for f in FEATURES]
    assert len(ids) == len(set(ids))


def test_false_alert_rate() -> None:
    # 2 of 8 rated alerts were wrong or irrelevant -> 25%. Nothing rated -> no figure, not zero.
    assert false_rate(8, 2) == 0.25
    assert false_rate(0, 0) is None
