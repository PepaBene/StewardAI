"""The retrieval metrics and the keyword / vector / hybrid comparison."""

from typing import Any

import pytest

from steward.retrieval import eval as ev
from steward.retrieval.search import Hit, SearchResult


def test_recall_and_reciprocal_rank() -> None:
    rel = [ev.Relevant(kind="report", ref="1"), ev.Relevant(kind="filing", ref="a")]
    found = [("news", "9"), ("report", "1"), ("report", "1"), ("filing", "z")]
    scored = ev.score_case(found, rel, k=2)
    assert scored == {"recall": 0.5, "rr": 0.5}
    # Repeated passages of one document do not push other documents out of the top k.
    assert ev.score_case(found, rel, k=3)["recall"] == 0.5
    assert ev.score_case([], rel, k=5) == {"recall": 0.0, "rr": 0.0}


def test_summary_averages_and_handles_no_cases() -> None:
    assert ev.summarise([]) == {"recall": None, "mrr": None}
    assert ev.summarise([{"recall": 1.0, "rr": 1.0}, {"recall": 0.0, "rr": 0.0}]) == {
        "recall": 0.5,
        "mrr": 0.5,
    }


def hit(kind: str, ref: str) -> Hit:
    return Hit(1, kind, ref, "t", None, "x", None, (), None, None, None, None, 0.1)


async def test_evaluate_reports_each_mode_and_flags_hybrid_regressions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = {
        ("query one", "keyword"): [("report", "1")],
        ("query one", "vector"): [("report", "2")],
        ("query one", "hybrid"): [("report", "1")],
        ("query two", "keyword"): [("report", "3")],
        ("query two", "vector"): [("report", "4")],
        ("query two", "hybrid"): [("report", "4")],
    }

    async def fake_search(pool: Any, embedder: Any, query: str, **kw: Any) -> SearchResult:
        return SearchResult(kw["mode"], [hit(*d) for d in answers[(query, kw["mode"])]])

    monkeypatch.setattr(ev, "search", fake_search)
    cases = [
        ev.RetrievalCase(
            id="c1", query="query one", relevant=[ev.Relevant(kind="report", ref="1")]
        ),
        ev.RetrievalCase(
            id="c2", query="query two", relevant=[ev.Relevant(kind="report", ref="3")]
        ),
    ]
    out = await ev.evaluate(None, object(), cases, k=3)  # type: ignore[arg-type]
    assert out["modes"]["keyword"]["recall"] == 1.0
    assert out["modes"]["vector"]["recall"] == 0.0
    assert out["modes"]["hybrid"]["recall"] == 0.5
    assert out["hybrid_worse_than_keyword"] == ["c2"]
    keyword_only = await ev.evaluate(None, None, cases, k=3)
    assert list(keyword_only["modes"]) == ["keyword"]


def test_the_committed_cases_file_is_valid() -> None:
    assert ev.load_cases() is not None
