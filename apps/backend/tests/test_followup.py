"""Snoozing data-quality issues (M7.5) and the pilot defect log (M8.3): pure parts."""

import datetime as dt

import pytest
from pydantic import ValidationError

from steward.quality.cockpit import Check, Cockpit, QualityIssue
from steward.quality.snooze import SnoozeIn, SnoozeRow, split_snoozed, with_snoozes
from steward.release.defects import DefectRow, current

TODAY = dt.date(2026, 10, 10)
NOW = dt.datetime(2026, 10, 10, tzinfo=dt.UTC)


def issue(kind: str, subject: str, weight: float | None = 0.1) -> QualityIssue:
    return QualityIssue(kind=kind, subject=subject, detail="d", weight=weight, fix="f")  # type: ignore[arg-type]


def snooze_row(i: int, kind: str, subject: str, action: str, until: dt.date | None) -> SnoozeRow:
    return SnoozeRow(id=i, kind=kind, subject=subject, action=action, until=until, note=None)  # type: ignore[arg-type]


def test_a_snooze_hides_an_issue_until_its_date_and_then_it_returns() -> None:
    issues = [issue("stale_price", "AAA"), issue("thesis_gap", "AAA")]
    rows = [snooze_row(1, "stale_price", "AAA", "snooze", TODAY + dt.timedelta(days=5))]
    active, snoozed = split_snoozed(issues, rows, TODAY)
    assert [i.kind for i in active] == ["thesis_gap"]  # the other issue on the same holding stays
    assert [s.issue.kind for s in snoozed] == ["stale_price"]
    active, snoozed = split_snoozed(issues, rows, TODAY + dt.timedelta(days=6))
    assert len(active) == 2 and snoozed == []


def test_a_clear_row_ends_a_snooze_early() -> None:
    issues = [issue("stale_price", "AAA")]
    rows = [
        snooze_row(1, "stale_price", "AAA", "snooze", TODAY + dt.timedelta(days=30)),
        snooze_row(2, "stale_price", "AAA", "clear", None),
    ]
    assert split_snoozed(issues, rows, TODAY)[1] == []


def test_snooze_needs_a_future_date_within_the_limit() -> None:
    with pytest.raises(ValidationError):
        SnoozeIn(kind="stale_price", subject="AAA")
    with pytest.raises(ValidationError):
        SnoozeIn(kind="stale_price", subject="AAA", until=dt.date.today())
    with pytest.raises(ValidationError):
        SnoozeIn(kind="stale_price", subject="AAA", until=dt.date.today() + dt.timedelta(days=400))
    assert SnoozeIn(kind="stale_price", subject="AAA", action="clear").until is None


def test_the_cockpit_counts_and_weight_describe_only_what_is_still_open() -> None:
    issues = [issue("stale_price", "AAA", 0.2), issue("stale_price", "BBB", 0.1)]
    cockpit = Cockpit(
        as_of=TODAY, portfolio_value_base=100.0, issues=issues,
        checks=[Check(kind="stale_price", title="t", ran=True, denominator="d", issues=2)],
        not_covered=[], affected_weight=0.3,
    )  # fmt: skip
    rows = [snooze_row(1, "stale_price", "AAA", "snooze", TODAY + dt.timedelta(days=3))]
    out = with_snoozes(cockpit, rows, TODAY)
    assert out.checks[0].issues == 1
    assert out.affected_weight == pytest.approx(0.1)
    assert len(out.snoozed) == 1


def defect(
    i: int, parent: int | None, title: str, state: str, ticker: str | None = None
) -> DefectRow:
    return DefectRow(
        id=i, parent_id=parent, title=title, detail=None, ticker=ticker, feature=None,
        state=state, created_at=NOW,  # type: ignore[arg-type]
    )  # fmt: skip


def test_defects_show_their_latest_state_open_first_and_largest_weight_first() -> None:
    rows = [
        defect(1, None, "wrong FX", "open", "AAA"),
        defect(2, None, "typo", "open"),
        defect(3, None, "bad split", "open", "BBB"),
        defect(4, 1, "wrong FX", "fixed", "AAA"),
    ]
    out = current(rows, {"AAA": 0.05, "BBB": 0.2})
    assert [(d.id, d.state) for d in out] == [(3, "open"), (2, "open"), (1, "fixed")]
    assert out[0].weight == 0.2 and out[1].weight is None
