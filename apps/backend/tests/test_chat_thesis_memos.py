"""The chat's memo tool marks which memo stands for a ticker, so an answer does not argue from
one that a later memo replaced (FB's morning "trim" after the evening "keep")."""

import asyncio
import datetime as dt
from typing import Any

from steward.chat.tools import ChatTools, _target_line
from steward.research.sources import Registry


class FakePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return self.rows


def memo(id_: int, ticker: str, stance: str, hour: int) -> dict[str, Any]:
    return {
        "id": id_,
        "ticker": ticker,
        "mode": "held",
        "stance": stance,
        "status": "accepted",
        "executive_summary": f"{ticker} {stance}",
        "body": {"stance_reason": stance},
        "created_at": dt.datetime(2026, 10, 17, hour, tzinfo=dt.UTC),
    }


def test_only_the_newest_memo_per_ticker_is_current() -> None:
    rows = [memo(84, "FB", "keep", 18), memo(80, "PM", "keep", 17), memo(76, "FB", "trim", 17)]
    tools = ChatTools(FakePool(rows), Registry(), None, None)  # type: ignore[arg-type]
    out = asyncio.run(tools._tool_thesis_memos())
    assert [(m["ticker"], m["stance"], m["current"]) for m in out["memos"]] == [
        ("FB", "keep", True),
        ("PM", "keep", True),
        ("FB", "trim", False),
    ]


def test_the_overview_snippet_states_the_figures_an_overvaluation_answer_quotes() -> None:
    row = {
        "ticker": "CUPd_EQ",
        "price": 35.76,
        "currency": "EUR",
        "thesis_scenarios": {"bear": 15.7, "base": 31.72, "bull": 50.89},
        "price_vs_base": round(35.76 / 31.72 - 1, 4),
    }
    line = _target_line(row)
    for figure in ("35.76", "31.72", "4.04", "12.7%", "above"):
        assert figure in line
