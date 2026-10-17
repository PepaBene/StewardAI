"""Phase 14: chat graceful degradation — an answer shape distinct from "cited" prose when
nothing in the draft ends up as a checked, sourced claim.

This is now a genuine prompt/response-shape change, not just a post-hoc reclassification:
the model itself can set ChatAnswerDraft.insufficient_evidence instead of writing an
unsupported-sounding paragraph, per the system prompt in steward.chat.assistant.SYSTEM. The
post-hoc check (every sentence unsupported or without a source) remains as a backstop for
when the model doesn't self-report."""

from steward.chat.assistant import (
    AnswerSentence,
    ChatAnswerDraft,
    ChatSentence,
    answer_shape,
    draft_note,
)


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


def test_a_partial_answer_keeps_its_sourced_sentences_beside_the_models_note() -> None:
    # The prompt asks for the sourced part plus a note on the rest: the note alone does not
    # discard sentences that checked out.
    sentences = [s("Revenue was 94,036.", source_ids=["S1"])]
    reason = "I could not find a Q3 figure for this ticker."
    assert answer_shape(sentences, reason) == "cited"
    assert answer_shape([], "Nothing on record for this holding.") == "low_evidence"
    unchecked = [s("Revenue was 94,036.", source_ids=["S1"], unsupported="not in S1")]
    assert answer_shape(unchecked, reason) == "low_evidence"


def test_no_admission_falls_back_to_the_checked_draft() -> None:
    sentences = [s("Revenue was 94,036.", source_ids=["S1"])]
    assert answer_shape(sentences, None) == "cited"


def test_draft_note_is_the_models_own_reason_when_it_gives_one() -> None:
    draft = ChatAnswerDraft(sentences=[], insufficient_evidence="No filing found for this period.")
    assert draft_note(draft) == "No filing found for this period."


def test_draft_note_is_none_for_an_ordinary_answer() -> None:
    sentence = AnswerSentence(text="Revenue was 94,036.", source_ids=["S1"])
    assert draft_note(ChatAnswerDraft(sentences=[sentence])) is None


def test_draft_note_reports_no_parseable_answer_when_there_is_no_draft() -> None:
    assert draft_note(None) == "no parseable answer"
