"""Defects found when the relationship sample was read by hand (2026-10-08)."""

from steward.disclosure.concentration_extract import extract
from steward.disclosure.dependencies import screen_passage
from steward.graph.ted import winner_matches


def test_one_word_winner_is_not_the_holding() -> None:
    names = ["Cisco Systems, Inc.", "Cisco"]
    assert not winner_matches("CISCO", ["Cisco Systems, Inc."])
    assert winner_matches("Cisco Systems Inc", ["Cisco Systems, Inc."])
    assert winner_matches("Cisco", names)  # written exactly as a name the holding is known by
    assert not winner_matches("CISCO SARL", ["Cisco"])


def test_residual_bucket_is_not_a_customer() -> None:
    out = extract("All Other accounted for 14% of net sales in fiscal 2025.")
    assert out.drafts == []
    table = "| | 2025 | 2024 |\n| All Other | 14% | 12% |\n| Non-U.S. customers | 40% | 41% |\n"
    assert extract(table).drafts == []


def test_no_customer_sentence_stores_nothing() -> None:
    text = "No customer accounted for 10% or more of revenue in fiscal 2025; one did in 2024."
    assert extract(text).drafts == []


def test_two_periods_in_one_sentence_is_skipped() -> None:
    text = "Customer A accounted for 28% of revenue in fiscal 2024 and 32% in fiscal 2025."
    out = extract(text)
    assert out.drafts == []
    assert out.skipped


def test_lost_support_is_hypothetical() -> None:
    assert "hypothetical" in screen_passage(
        "If we lose Microsoft Corporation's support, sales fall."
    )


def test_plain_named_share_still_extracted() -> None:
    out = extract("Apple Inc. accounted for 12% of net sales in fiscal 2025.")
    assert [(d.name, str(d.share)) for d in out.drafts] == [("Apple Inc.", "0.12")]
