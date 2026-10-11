"""The live benchmark runner with a fake model client and fake tools: no network, no database.

Fake sentences carry no digits (the code-side claim check removes a sentence with a number no
source contains).
"""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from steward.chat import benchmark, data_tools
from steward.chat import benchmark_runner as br
from steward.chat import research_tools as rt
from steward.chat.assistant import AnswerSentence, ChatAnswerDraft, LLMChatAssistant
from steward.config.jobs import ChatSettings
from steward.llm.types import Completion, ToolCall
from steward.research.sources import Registry

TODAY = dt.date(2026, 10, 10)


def usage() -> Any:
    return SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        server_tool_use=SimpleNamespace(web_search_requests=0, web_fetch_requests=0),
    )


class FakeLLM:
    """Asks for the given tools once, then answers with the given draft."""

    def __init__(self, tools: list[str], draft: ChatAnswerDraft | None) -> None:
        self.tools = tools
        self.draft = draft
        self.calls = 0

    async def complete(self, request: Any) -> Completion:
        self.calls += 1
        if self.calls == 1 and self.tools:
            return Completion(
                model="fake",
                provider="openai",
                text="",
                parsed=None,
                stop_reason="tool_use",
                usage=usage(),
                tool_calls=[ToolCall(f"c{i}", t, {}) for i, t in enumerate(self.tools)],
            )
        return Completion(
            model="fake",
            provider="openai",
            text="",
            parsed=self.draft,
            stop_reason="end",
            usage=usage(),
        )


class FakeTools:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.registry = Registry()
        self.trace: list[dict[str, Any]] = []
        self.failing = failing or set()

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        ok = name not in self.failing
        self.trace.append({"tool": name, "args": args, "ok": ok, "ms": 1})
        if not ok:
            return {"error": "boom"}
        sid = self.registry.add("observation", name, name, snippet="the portfolio is large")
        return {"source_id": sid}


def assistant(llm: FakeLLM) -> LLMChatAssistant:
    return LLMChatAssistant(llm, ChatSettings(model="gpt-fake"), ())  # type: ignore[arg-type]


CITED = ChatAnswerDraft(
    sentences=[AnswerSentence(text="The portfolio is large.", source_ids=["S1"])]
)
UNCITED = ChatAnswerDraft(sentences=[AnswerSentence(text="The portfolio is large.")])


def quick_case() -> benchmark.Case:
    return next(c for c in benchmark.load_cases() if c.id == "quick-fact-01")


async def test_a_good_run_passes_every_code_scored_stage() -> None:
    result = await br.run_case(
        quick_case(),
        assistant(FakeLLM(["portfolio_overview"], CITED)),
        lambda: FakeTools(),
        today=TODAY,
        usd_per_mtok=(1.0, 2.0),
    )
    assert result.passed and result.failure_stage is None
    assert result.tools_called == ["portfolio_overview"]
    assert "portfolio" in result.datasets_touched
    assert result.cited_sentences == 1 and result.score.tool_recall == 1.0
    assert result.input_tokens == 200 and result.output_tokens == 40
    assert result.cost_usd == pytest.approx((200 * 1.0 + 40 * 2.0) / 1e6)
    assert result.stages["strategy"] == "unreviewed"


async def test_missing_tool_is_a_retrieval_failure_and_no_citation_is_a_citation_failure() -> None:
    missed = await br.run_case(
        quick_case(), assistant(FakeLLM([], CITED)), lambda: FakeTools(), today=TODAY
    )
    assert missed.failure_stage == "retrieval" and not missed.passed
    uncited = await br.run_case(
        quick_case(),
        assistant(FakeLLM(["portfolio_overview"], UNCITED)),
        lambda: FakeTools(),
        today=TODAY,
    )
    assert uncited.stages["retrieval"] == "pass" and uncited.failure_stage == "citation"


async def test_unsupported_figure_is_an_analysis_failure() -> None:
    draft = ChatAnswerDraft(
        sentences=[AnswerSentence(text="The portfolio holds 999 positions.", source_ids=["S1"])]
    )
    result = await br.run_case(
        quick_case(),
        assistant(FakeLLM(["portfolio_overview"], draft)),
        lambda: FakeTools(),
        today=TODAY,
    )
    assert result.unsupported_sentences == 1 and result.stages["analysis"] == "fail"


async def test_a_crashing_client_is_recorded_not_raised() -> None:
    class Boom:
        async def answer(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("down")

    result = await br.run_case(quick_case(), Boom(), lambda: FakeTools(), today=TODAY)  # type: ignore[arg-type]
    assert result.error and "down" in result.error and result.stages["rendering"] == "fail"


async def test_run_is_stored_and_aggregated(tmp_path: Path) -> None:
    cases = [quick_case()]
    run = await br.run_cases(
        cases,
        assistant(FakeLLM(["portfolio_overview"], CITED)),
        lambda: FakeTools(),
        model="fake",
        today=TODAY,
        run_id="r1",
    )
    agg = run.aggregate
    assert agg["cases"] == 1 and agg["passed"] == 1 and agg["tool_group_recall"] == 1.0
    assert agg["citation_presence"] == 1.0 and agg["unsupported_figure_rate"] == 0.0
    path = br.save_run(run, tmp_path)
    assert path == tmp_path / "r1.json"
    assert br.load_run("r1", tmp_path).model_dump() == run.model_dump()
    assert br.load_run(path).run_id == "r1"


def result_for(case_id: str, **kw: Any) -> br.CaseResult:
    base = {
        "case": case_id,
        "category": "quick_fact",
        "question": "q",
        "tools_called": [],
        "datasets_touched": [],
        "score": benchmark.Score(
            case=case_id,
            tool_recall=kw.pop("recall", 1.0),
            missed_groups=[],
            failed_calls=[],
            unexpected_actions=[],
            failure=None,
        ),
        "stages": {},
        "failure_stage": kw.pop("failure_stage", None),
        "passed": kw.pop("passed", True),
        **kw,
    }
    return br.CaseResult.model_validate(base)


def run_of(run_id: str, results: list[br.CaseResult]) -> br.RunRecord:
    return br.RunRecord(
        run_id=run_id,
        created_at="2026-10-10T00:00:00+00:00",
        git_sha=None,
        model="m",
        cases=results,
        aggregate=br.aggregate(results),
    )


def test_compare_lists_regressions_and_deltas() -> None:
    a = run_of("a", [result_for("x"), result_for("y"), result_for("z", seconds=1.0)])
    b = run_of(
        "b",
        [
            result_for("x", passed=False, failure_stage="retrieval", recall=0.5),
            result_for("y"),
            result_for("w"),
        ],
    )
    diff = br.compare_runs(a, b)
    assert diff["cases_only_in_a"] == ["z"] and diff["cases_only_in_b"] == ["w"]
    assert any(r.startswith("x: passed before") for r in diff["regressions"])
    assert any("aggregate tool_group_recall" in r for r in diff["regressions"])
    row = next(r for r in diff["per_case"] if r["case"] == "x")
    assert row["recall"] == -0.5 and row["passed"] == [True, False]
    assert diff["aggregate"]["tool_group_recall"]["delta"] < 0
    assert br.compare_runs(a, a)["regressions"] == []


def test_review_scores_are_stored_and_merged(tmp_path: Path) -> None:
    run = run_of("r2", [result_for("x"), result_for("y")])
    br.save_run(run, tmp_path)
    br.record_review("r2", "x", {"analysis": 2, "citation": 0, "strategy": None}, "ok", tmp_path)
    merged = br.record_review("r2", "y", {"analysis": 1, "rendering": 2}, "", tmp_path)
    manual = merged.aggregate["manual"]
    assert manual["reviewed_cases"] == 2
    assert manual["stages"]["analysis"] == {"mean": 1.5, "scored": 2}
    assert manual["stages"]["citation"] == {"mean": 0.0, "scored": 1}
    assert manual["stages"]["strategy"] == {"mean": None, "scored": 0}
    assert manual["broken"] == ["x:citation"]
    assert br.load_review("r2", tmp_path).cases["x"].notes == "ok"
    assert br.load_run("r2", tmp_path).aggregate["manual"] == manual  # stored in the run file
    with pytest.raises(ValueError):
        br.record_review("r2", "nope", {"analysis": 1}, "", tmp_path)


def test_review_rejects_scores_outside_zero_to_two() -> None:
    with pytest.raises(ValueError):
        br.CaseReview(analysis=3)


def test_real_failures_become_cases_loaded_with_the_built_in_ones(tmp_path: Path) -> None:
    path = tmp_path / "extra.json"
    case = br.append_case(
        "Why did my Apple position fall last week?",
        [["holding"], ["price_history", "latest_quote"]],
        ["a cause not in any source"],
        path=path,
    )
    assert case.id == "real-01" and "portfolio" in case.datasets
    second = br.append_case("What is the dollar rate today?", [["fx_rates"]], ["a rate"], path=path)
    assert second.id == "real-02"
    everything = br.load_all_cases(path)
    assert len(everything) == len(benchmark.load_cases()) + 2
    assert json.loads(path.read_text(encoding="utf-8"))["cases"][0]["id"] == "real-01"
    with pytest.raises(ValueError):
        br.append_case("A question long enough", [["no_such_tool"]], ["x"], path=path)
    with pytest.raises(ValueError):
        br.append_case("A question long enough", [["holding"]], ["x"], path=path, case_id="real-01")
    assert len(br.load_extra(path)) == 2  # the failed adds wrote nothing


def test_cli_compare_and_add_case(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    br.save_run(run_of("a", [result_for("x")]), tmp_path)
    br.save_run(run_of("b", [result_for("x", passed=False, failure_stage="citation")]), tmp_path)
    assert br.main(["compare", "a", "b", "--out", str(tmp_path)]) == 0
    assert "x: passed before" in capsys.readouterr().out
    extra = tmp_path / "e.json"
    assert (
        br.main(
            [
                "add-case",
                "A long enough question",
                "--group",
                "holding,peers",
                "--trap",
                "t",
                "--file",
                str(extra),
            ]
        )
        == 0
    )
    assert br.load_extra(extra)[0].tool_groups == [["holding", "peers"]]


# ------------------------------------------------------------------ data tools (fakes)


class FakePool:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    async def fetch(self, *a: Any, **k: Any) -> list[dict[str, Any]]:
        return self.rows

    async def fetchrow(self, *a: Any, **k: Any) -> Any:
        return None


async def test_fx_rates_validates_and_tells_missing_from_present() -> None:
    reg = Registry()
    assert "error" in await data_tools.fx_rates(FakePool(), reg, "dollar", 30, 5)  # type: ignore[arg-type]
    missing = await data_tools.fx_rates(FakePool(), reg, "usd", 30, 5)  # type: ignore[arg-type]
    assert missing["status"] == "missing" and "rates" not in missing
    old = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=30)
    row = {
        "source": "ecb",
        "rate_date": old,
        "currency": "USD",
        "home_currency": "EUR",
        "rate": Decimal("1.1"),
        "published_amount": Decimal(1),
    }
    out = await data_tools.fx_rates(FakePool([row]), reg, "usd", 30, 5)  # type: ignore[arg-type]
    assert out["latest"]["status"] == "stale" and out["latest"]["value"] == pytest.approx(1.1)
    assert out["source_id"] in reg.ids() and out["page"]["truncated"] is False


async def test_uncovered_ticker_is_an_error_not_an_empty_figure() -> None:
    for call in (
        data_tools.latest_quote(FakePool(), Registry(), "NOPE"),  # type: ignore[arg-type]
        data_tools.listing_liquidity(FakePool(), Registry(), "NOPE"),  # type: ignore[arg-type]
        data_tools.corporate_actions(FakePool(), Registry(), "NOPE", 30, 5),  # type: ignore[arg-type]
    ):
        assert "not in the covered universe" in (await call)["error"]


async def test_statement_and_segment_inputs_are_checked() -> None:
    assert "error" in await data_tools.statement_lines(
        FakePool(),  # type: ignore[arg-type]
        Registry(),
        "T",
        "weird",
        None,
        5,
    )
    assert "error" in await data_tools.segment_revenue(FakePool(), Registry(), "T", "weird", 5)  # type: ignore[arg-type]
    none = await data_tools.statement_lines(FakePool(), Registry(), "T", None, None, 5)  # type: ignore[arg-type]
    assert none["status"] == "missing"


def test_relative_performance_uses_common_days_only_and_is_bounded() -> None:
    d0 = dt.date(2026, 1, 1)
    own = [(d0 + dt.timedelta(days=i), Decimal(100 + i)) for i in range(200)]
    bench = [(d0 + dt.timedelta(days=i), Decimal(100)) for i in range(50, 300)]
    rel = rt.relative_performance(own, bench)
    assert rel is not None and rel["common_days"] == 150
    assert rel["own_return"] == pytest.approx(149 / 150)
    assert rel["benchmark_return"] == 0 and rel["excess_return"] == rel["own_return"]
    assert len(rel["series"]) <= rt.MAX_RELATIVE_POINTS + 1
    assert rel["series"][-1][0] == rel["to"].isoformat()
    assert rt.relative_performance(own[:1], bench) is None
    assert rt.relative_performance(own[:10], bench) is None  # no common days
