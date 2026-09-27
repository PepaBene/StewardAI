"""Phase 11: evidence relationships — does a piece of evidence support, contradict, or
merely mention the claim it backs, derived from the fact's own status and checks."""

from steward.earnings.read import FactCheck, evidence_relation


def test_a_failed_check_contradicts_even_if_still_pending_review() -> None:
    checks = [FactCheck(check="restatement_bound", ok=False, detail="outside tolerance")]
    assert evidence_relation("unverified", checks) == "contradicts"


def test_a_rejected_fact_contradicts_regardless_of_its_checks() -> None:
    assert evidence_relation("rejected", []) == "contradicts"
    ok_checks = [FactCheck(check="sanity_bound", ok=True)]
    assert evidence_relation("rejected", ok_checks) == "contradicts"


def test_a_verified_fact_with_no_failed_check_supports() -> None:
    checks = [FactCheck(check="sanity_bound", ok=True), FactCheck(check="units", ok=True)]
    assert evidence_relation("verified", checks) == "supports"
    assert evidence_relation("verified", []) == "supports"


def test_an_unreviewed_or_quarantined_fact_only_mentions_the_claim() -> None:
    assert evidence_relation("unverified", []) == "mentions"
    assert evidence_relation("quarantined", []) == "mentions"
