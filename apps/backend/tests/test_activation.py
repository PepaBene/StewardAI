import datetime as dt

import pytest

from steward.release.activation import ActivationIn, ActivationRefused, check
from steward.release.readiness import FeatureReadiness, Readiness


def _gate(can: bool) -> Readiness:
    feature = FeatureReadiness(
        id="company", title="Company workbench", plan="M2", status="ready" if can else "partial",
        reason="r", limit=None, inputs=[], audit="passed" if can else "thin",
        audited_facts=4, can_activate=can,
    )  # fmt: skip
    return Readiness(
        as_of=dt.date(2026, 10, 8), features=[feature], zero_cost_passed=True,
        zero_cost_violations=0, ready=int(can), activatable=int(can),
    )  # fmt: skip


def test_switch_on_allowed_when_the_gate_passes() -> None:
    check(ActivationIn(feature="company", active=True), _gate(True))


def test_switch_on_refused_when_the_gate_fails() -> None:
    with pytest.raises(ActivationRefused):
        check(ActivationIn(feature="company", active=True), _gate(False))


def test_switch_off_is_always_allowed() -> None:
    check(ActivationIn(feature="company", active=False), _gate(False))


def test_unknown_feature_is_rejected() -> None:
    with pytest.raises(KeyError):
        check(ActivationIn(feature="nope", active=False), _gate(True))
