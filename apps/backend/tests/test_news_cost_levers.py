"""Cost levers N1 and N5: batch the analysis of minor stories, reuse the analysis of a repeat."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from steward.config.jobs import NewsSettings
from steward.news import analysis_job
from steward.news.analysis_job import SEVERE, AnalysisRun


class _Pool:
    def __init__(self, source: dict[str, Any] | None) -> None:
        self.source = source
        self.asked: list[tuple[Any, ...]] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.asked.append(args)
        return self.source


def _run(news: NewsSettings, pool: _Pool) -> AnalysisRun:
    ctx = SimpleNamespace(pool=pool, job_id=1)
    return AnalysisRun(cast(Any, ctx), news, cast(Any, None), cast(Any, object()))


def _story(severity: int = 1, ticker: str | None = "AAPL") -> Any:
    return SimpleNamespace(cluster_id=20, severity=severity, ticker=ticker)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"novelty": {"novelty": "repeat", "similar_story_id": 7}, "saved": []}

    async def latest(pool: Any, cluster_id: int, kind: str) -> Any:
        return state["novelty"]

    async def exists(pool: Any, cluster_id: int, kind: str, hash_: str) -> bool:
        return False

    async def save(pool: Any, cluster_id: int, kind: str, hash_: str, *rest: Any) -> bool:
        state["saved"].append((cluster_id, kind, rest[0], rest[1]))
        return True

    monkeypatch.setattr(analysis_job, "latest_analysis", latest)
    monkeypatch.setattr(analysis_job, "analysis_exists", exists)
    monkeypatch.setattr(analysis_job, "save_analysis", save)
    return state


SOURCE = {"payload": json.dumps({"note": "same"}), "model": "gpt-6-luna"}


async def test_a_repeat_copies_the_source_analysis_when_enabled(store: dict[str, Any]) -> None:
    pool = _Pool(SOURCE)
    run = _run(NewsSettings(reuse_repeat_analysis=True), pool)
    assert await run.reuse_from_repeat(_story(), "impact") is True
    assert store["saved"] == [(20, "impact", "gpt-6-luna", {"note": "same"})]
    assert pool.asked[0] == (7, "impact", "AAPL")


async def test_the_switch_off_asks_the_database_nothing(store: dict[str, Any]) -> None:
    pool = _Pool(SOURCE)
    run = _run(NewsSettings(), pool)
    assert await run.reuse_from_repeat(_story(), "impact") is False
    assert pool.asked == [] and store["saved"] == []


async def test_a_severe_story_is_never_reused(store: dict[str, Any]) -> None:
    run = _run(NewsSettings(reuse_repeat_analysis=True), _Pool(SOURCE))
    assert await run.reuse_from_repeat(_story(severity=SEVERE), "impact") is False


async def test_only_a_repeat_with_a_source_story_is_reused(store: dict[str, Any]) -> None:
    run = _run(NewsSettings(reuse_repeat_analysis=True), _Pool(SOURCE))
    store["novelty"] = {"novelty": "new", "similar_story_id": 7}
    assert await run.reuse_from_repeat(_story(), "impact") is False
    store["novelty"] = {"novelty": "repeat", "similar_story_id": None}
    assert await run.reuse_from_repeat(_story(), "impact") is False


async def test_no_source_analysis_means_the_model_is_asked(store: dict[str, Any]) -> None:
    run = _run(NewsSettings(reuse_repeat_analysis=True), _Pool(None))
    assert await run.reuse_from_repeat(_story(), "impact") is False
    assert store["saved"] == []


async def test_analyse_batched_swaps_the_llm_only_while_the_stories_run() -> None:
    run = _run(NewsSettings(), _Pool(None))
    direct = run.llm
    seen: list[Any] = []

    async def story() -> None:
        seen.append(run.llm)

    await run.analyse_batched([story(), story()])
    assert len(seen) == 2 and all(x is not direct for x in seen)
    assert run.llm is direct


async def test_analyse_batched_restores_the_llm_after_a_failure() -> None:
    run = _run(NewsSettings(), _Pool(None))
    direct = run.llm

    async def boom() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("x")

    await run.analyse_batched([boom()])
    assert run.llm is direct
