"""The research regression gate: it passes on the committed baseline, and fails when a
prompt or a security rule is changed without a measured baseline."""

import re

import pytest

from steward.research import gate, untrusted, writer


def test_the_committed_baseline_passes() -> None:
    ok, lines = gate.check(gate.load_baseline())
    assert ok, "\n".join(lines)
    assert gate.load_baseline() is not None, "run: python -m steward.research.gate --write-baseline"


def test_every_suite_score_is_perfect() -> None:
    for name, (score, failed) in gate.run_suite().items():
        assert score == 1.0, f"{name}: {failed}"


def test_a_changed_prompt_fails_until_the_baseline_is_rewritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = gate.fingerprint()
    monkeypatch.setattr(writer, "SYSTEM", writer.SYSTEM + " Be bolder.")
    assert gate.fingerprint() != before
    ok, lines = gate.check(gate.load_baseline())
    assert not ok and any("changed since the baseline" in line for line in lines)


def test_a_broken_injection_rule_fails_the_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(untrusted, "INSTRUCTION", re.compile(r"(?!x)x"))
    score, failed = gate.run_suite()["injection"]
    assert score < 1.0 and failed
    ok, _ = gate.check(gate.load_baseline())
    assert not ok


def test_a_rule_that_flags_everything_fails_the_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(untrusted, "INSTRUCTION", re.compile(r"."))
    score, failed = gate.run_suite()["injection"]
    assert score < 1.0 and any("Apple reported" in f for f in failed)
