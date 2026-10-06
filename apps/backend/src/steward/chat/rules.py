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
from steward.earnings.read import holding_detail

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


REASONING = re.compile(
    r"\b(why|should|compare|versus|vs|better|worse|news|thesis|risk|sell|buy|hold|explain|"
    r"because|how come|what if|forecast|expect|predict|tax|exit|kpi|beat|miss|dividend)\b",
    re.I,
)
NEXT_REPORT = re.compile(
    r"\bwhen\b.*\b(report|reports|earnings|results)\b|\bnext (earnings|report)\b"
    r"|\b(earnings|report) date\b",
    re.I,
)
TOKEN = re.compile(r"[A-Za-z0-9.]+")


def asks_next_report(question: str) -> bool:
    text = question.strip()
    if not text or len(text.split()) > MAX_WORDS or REASONING.search(text):
        return False
    return bool(NEXT_REPORT.search(text))


def match_holding(question: str, instruments: list[tuple[str, str | None]]) -> str | None:
    """The one instrument the question names, by its base ticker as an upper-case word or by
    its whole name; None when it names none or more than one."""
    words = set(TOKEN.findall(question))
    lowered = question.lower()
    hits = set()
    for ticker, name in instruments:
        base = ticker.split("_")[0]
        if base and base in words:
            hits.add(ticker)
        elif name and len(name) >= 4 and name.lower() in lowered:
            hits.add(ticker)
    return next(iter(hits)) if len(hits) == 1 else None


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
        if asks_next_report(question):
            result = await self._from_report_date(question, tools)
            if result is not None:
                return result
        if self._inner is None:
            return AssistantResult(
                None, Usage(model=self._model), {}, "ANTHROPIC_API_KEY is not set"
            )
        return await self._inner.answer(history, question, tools, today)

    async def _from_report_date(self, question: str, tools: ChatTools) -> AssistantResult | None:
        rows = await tools.pool.fetch(
            "select t212_ticker, name from public.instrument where next_report_date is not null"
        )
        ticker = match_holding(question, [(r["t212_ticker"], r["name"]) for r in rows])
        if ticker is None:
            return None
        detail = await holding_detail(tools.pool, ticker)
        info = detail.instrument if detail else None
        if info is None or info.next_report_date is None:
            return None
        label = info.name or ticker.split("_")[0]
        fact = f"{label} next report date {info.next_report_date.isoformat()}"
        if info.report_date_source:
            fact += f" (source {info.report_date_source})"
        sid = tools.registry.add(
            "position",
            f"rule:report:{ticker}",
            fact,
            app_path=f"/holdings/{ticker}",
            tier=1,
            snippet=fact,
            as_of=info.next_report_date.isoformat(),
        )
        draft = ChatAnswerDraft(sentences=[AnswerSentence(text=fact + ".", source_ids=[sid])])
        return AssistantResult(draft, Usage(model=self._model), {}, None)

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
