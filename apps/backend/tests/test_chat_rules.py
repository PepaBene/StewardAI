"""Closed chat questions answered from the read models, without a model call."""

import asyncio
import datetime as dt
from typing import Any

import pytest

from steward.chat.assistant import AssistantResult, finish_answer, mark_unsupported_figures
from steward.chat.rules import RuleChatAssistant, classify
from steward.research.sources import Registry

TODAY = dt.date(2026, 10, 6)


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        ("What is my portfolio worth?", "value"),
        ("portfolio value", "value"),
        ("How much cash do I have?", "cash"),
        ("cash", "cash"),
        ("Why is my portfolio down?", None),
        ("Should I sell if my portfolio is worth less than my cash?", None),
        ("What is my portfolio worth and how much cash do I have?", None),
        ("How is Apple doing?", None),
        ("", None),
    ],
)
def test_only_short_closed_questions_are_classified(question: str, kind: str | None) -> None:
    assert classify(question) == kind


class _Tools:
    def __init__(self, overview: dict[str, Any]) -> None:
        self.registry = Registry()
        self._overview = overview
        self.used: list[str] = []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.used.append(name)
        return self._overview


class _Inner:
    def __init__(self) -> None:
        self.asked = 0

    async def answer(self, *args: Any) -> AssistantResult:
        from steward.ai import Usage

        self.asked += 1
        return AssistantResult(None, Usage(model="m"), {}, "inner")


OVERVIEW = {"as_of": "2026-10-05", "total": 1234567.89, "cash": 4321.5, "currency": "CZK"}


def test_value_question_is_answered_with_a_source_that_holds_every_figure() -> None:
    tools = _Tools(OVERVIEW)
    inner = _Inner()
    result = asyncio.run(
        RuleChatAssistant(inner, "gpt-6.1-sol").answer(  # type: ignore[arg-type]
            [],
            "What is my portfolio worth?",
            tools,
            TODAY,  # type: ignore[arg-type]
        )
    )
    assert inner.asked == 0 and result.draft is not None
    sentences = finish_answer(result.draft, tools.registry, {}, lambda _h: None)
    asyncio.run(mark_unsupported_figures(sentences, tools.registry))
    assert "1234567.89 CZK" in sentences[0].text
    assert not sentences[0].no_source and sentences[0].unsupported is None


def test_other_questions_go_to_the_assistant() -> None:
    inner = _Inner()
    result = asyncio.run(
        RuleChatAssistant(inner, "m").answer(  # type: ignore[arg-type]
            [],
            "Why did Apple fall?",
            _Tools(OVERVIEW),
            TODAY,  # type: ignore[arg-type]
        )
    )
    assert inner.asked == 1 and result.note == "inner"


def test_no_snapshot_falls_back_to_the_assistant() -> None:
    inner = _Inner()
    asyncio.run(
        RuleChatAssistant(inner, "m").answer(  # type: ignore[arg-type]
            [],
            "portfolio value",
            _Tools({"note": "no portfolio snapshot yet"}),
            TODAY,  # type: ignore[arg-type]
        )
    )
    assert inner.asked == 1


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("When does AAPL report next?", True),
        ("next earnings date for SAP", True),
        ("Why did AAPL beat when it reported earnings?", False),
        ("Should I buy before AAPL reports? When is the report", False),
        ("What is my portfolio worth?", False),
    ],
)
def test_next_report_question_is_closed_and_narrow(question: str, expected: bool) -> None:
    from steward.chat.rules import asks_next_report

    assert asks_next_report(question) is expected


def test_holding_is_matched_exactly_one_way() -> None:
    from steward.chat.rules import match_holding

    held = [("AAPL_US_EQ", "Apple Inc"), ("SAPd_EQ", "SAP SE"), ("SAP_US_EQ", "SAP ADR")]
    assert match_holding("When does AAPL report?", held) == "AAPL_US_EQ"
    assert match_holding("when does apple inc report", held) == "AAPL_US_EQ"
    assert match_holding("when does aapl report", held) is None  # ticker must be upper case
    assert match_holding("When does SAP report?", held) == "SAP_US_EQ"
    assert match_holding("When does AAPL or SAP report?", held) is None


@pytest.mark.parametrize(
    ("text", "clean", "ids"),
    [
        ("The cash is 15,009 CZK. citeS1", "The cash is 15,009 CZK.", ["S1"]),
        ("It is at risk [S19, S20, S6].", "It is at risk.", ["S19", "S20", "S6"]),
        ("Revenue fell 【S3】 by a third (S4).", "Revenue fell by a third.", ["S3", "S4"]),
        ("ASML raised its outlook citeturn0search1.", "ASML raised its outlook.", []),
        ("Section [S1a] stays.", "Section [S1a] stays.", []),
    ],
)
def test_inline_citation_markers_move_into_the_sources(
    text: str, clean: str, ids: list[str]
) -> None:
    from steward.chat.assistant import split_inline_citations

    assert split_inline_citations(text) == (clean, ids)
