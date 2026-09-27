"""Phase 14: chat graceful degradation — an answer shape distinct from "cited" prose when
nothing in the draft ends up as a checked, sourced claim."""

from steward.chat.assistant import ChatSentence, answer_shape


def s(
    text: str,
    *,
    source_ids: list[str] | None = None,
    no_source: bool = False,
    unsupported: str | None = None,
) -> ChatSentence:
    return ChatSentence(
        text=text, source_ids=source_ids or [], no_source=no_source, unsupported=unsupported
    )


def test_an_empty_draft_is_low_evidence() -> None:
    assert answer_shape([]) == "low_evidence"


def test_every_sentence_without_a_source_is_low_evidence() -> None:
    assert answer_shape([s("I have nothing to go on.", no_source=True)]) == "low_evidence"


def test_every_sentence_unsupported_once_checked_is_low_evidence() -> None:
    sentences = [
        s("Revenue was 94,036.", source_ids=["S1"], unsupported="94,036 is not in S1"),
        s("It grew nicely.", no_source=True),
    ]
    assert answer_shape(sentences) == "low_evidence"


def test_at_least_one_checked_sourced_sentence_keeps_the_answer_cited() -> None:
    sentences = [
        s("Revenue was 94,036.", source_ids=["S1"]),
        s("Analysts expect more.", no_source=True),
    ]
    assert answer_shape(sentences) == "cited"
