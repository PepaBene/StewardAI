"""Chat questions answered from the read models, without a model call.

"What is my portfolio worth?" and "how much cash do I have?" have one answer in the database.
The rule answers only a short question that asks for exactly that and uses none of the words
of an open-ended one (why, should, compare, news, thesis, risk, ...); everything else goes to
the assistant. The answer is a template over the `portfolio_overview` tool, cited to a source
the rule registers with the exact figures it states, so the same figure check as for a model
answer applies and a sentence can never state a number its source lacks.
"""

import datetime as dt
import re
from typing import Any

from steward.ai import Usage
from steward.chat.assistant import AnswerSentence, AssistantResult, ChatAnswerDraft, ChatAssistant
from steward.chat.tools import ChatTools

MAX_WORDS = 12
# A question with any of these wants reasoning, comparison or a recommendation.
OPEN_ENDED = re.compile(
    r"\b(why|should|compare|versus|vs|better|worse|news|thesis|risk|sell|buy|hold|explain|"
    r"because|how come|what if|forecast|expect|predict|tax|exit|kpi|earnings|result|"
    r"report|dividend|per share|ticker)\b",
    re.I,
)
VALUE = re.compile(
    r"\b(portfolio|total|net worth|worth|everything)\b.*\b(worth|value|total|is)\b"
    r"|\bhow much (is|are|do i have|have i got)\b.*\b(portfolio|total|invested|all)\b"
    r"|\bportfolio (value|total|size)\b",
    re.I,
)
CASH = re.compile(r"\b(how much )?(cash|free cash|buying power)\b", re.I)


def classify(question: str) -> str | None:
    """ "value", "cash" or None: the one thing a short, closed question asks for."""
    text = question.strip()
    if not text or len(text.split()) > MAX_WORDS or OPEN_ENDED.search(text):
        return None
    wants_cash = bool(CASH.search(text))
    wants_value = bool(VALUE.search(text))
    if wants_cash and not wants_value:
        return "cash"
    if wants_value and not wants_cash:
        return "value"
    return None


class RuleChatAssistant:
    """Answers the closed questions from the portfolio overview and passes the rest to
    `inner` (the model assistant), or says so when there is none."""

    def __init__(self, inner: ChatAssistant | None, model: str) -> None:
        self._inner = inner
        self._model = model

    async def answer(
        self, history: list[dict[str, str]], question: str, tools: ChatTools, today: dt.date
    ) -> AssistantResult:
        kind = classify(question)
        if kind is not None:
            result = await self._from_overview(kind, tools)
            if result is not None:
                return result
        if self._inner is None:
            return AssistantResult(
                None, Usage(model=self._model), {}, "ANTHROPIC_API_KEY is not set"
            )
        return await self._inner.answer(history, question, tools, today)

    async def _from_overview(self, kind: str, tools: ChatTools) -> AssistantResult | None:
        overview: dict[str, Any] = await tools.call("portfolio_overview", {})
        as_of, total, cash, currency = (
            overview.get("as_of"),
            overview.get("total"),
            overview.get("cash"),
            overview.get("currency"),
        )
        if not (as_of and currency) or (total if kind == "value" else cash) is None:
            return None  # no snapshot yet: let the assistant say what it found
        if kind == "value":
            what, figure = "total value", total
        else:
            what, figure = "cash", cash
        # The source carries the exact words and figures the sentence uses, so the figure check
        # (every number in a sentence must be in its source) holds by construction.
        fact = f"Portfolio {what} {figure} {currency} as of {as_of}"
        sid = tools.registry.add(
            "position",
            f"rule:{kind}:{as_of}",
            fact,
            app_path="/",
            tier=1,
            snippet=fact,
            as_of=as_of,
        )
        text = f"Your portfolio {what} is {figure} {currency} as of {as_of}."
        draft = ChatAnswerDraft(sentences=[AnswerSentence(text=text, source_ids=[sid])])
        return AssistantResult(draft, Usage(model=self._model), {}, None)
