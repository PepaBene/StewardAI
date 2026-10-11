"""N6/N7 against a real Postgres: the analysis job over ingested stories with a fake model, its
idempotence, the briefs, and the read side the pages use."""

import datetime as dt
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import asyncpg
import httpx
import pytest

from steward.budget_guard import BudgetExceeded
from steward.config import JobsConfig
from steward.config.store import save_config
from steward.jobs.runner import run_job
from steward.news import analysis_job, brief
from steward.news.analysis import latest_analysis, load_story
from steward.news.analysis_job import news_analysis_handler
from steward.news.claims import ClaimVerdicts
from steward.news.extract import StoryExtraction
from steward.news.framing import FramingComparison
from steward.news.impact import ImpactNotes
from steward.news.read_analysis import (
    NewsFeedbackIn,
    WireFilters,
    add_feedback,
    list_briefs,
    list_feedback,
    list_threads,
    story_analysis,
    theme_days,
    thread_detail,
    wire,
)
from steward.news.sentiment import RawSentiment
from steward.news.thesis_link import ThesisLinkOut, ThesisLinks
from tests.test_news_pipeline import (
    NOON,
    Clock,
    FakeChannel,
    FakeCorroborator,
    Feeds,
    first_run_feeds,
    ingest,
    make_deps,
    setup_config,
)

pytestmark = pytest.mark.db

ALL = [
    "extraction",
    "novelty",
    "impact",
    "thread",
    "framing",
    "claims",
    "market",
    "themes",
    "thesis",
]


@pytest.fixture
async def feeds() -> Feeds:
    return Feeds()


@pytest.fixture
async def http(feeds: Feeds) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(feeds.handler)) as client:
        yield client


class FakeAnalysisLLM:
    """Answers each schema from the request itself: the first item of the story, quoted."""

    def __init__(self, bad_span: bool = False, budget_after: int | None = None) -> None:
        self.calls: list[str] = []
        self.batches: list[int] = []
        self.bad_span = bad_span
        self.budget_after = budget_after

    def configured(self, model: str) -> bool:
        return True

    @staticmethod
    def first_item(request: Any) -> dict[str, Any]:
        text = request.messages[0].text
        items = json.loads(text.split("Items:\n", 1)[1])
        first: dict[str, Any] = items[0]
        return first

    async def complete_batch(self, requests: list[Any], wait_minutes: float = 60.0) -> list[Any]:
        self.batches.append(len(requests))
        return [await self.complete(r) for r in requests]

    async def complete(self, request: Any) -> SimpleNamespace:
        name = request.schema.__name__
        self.calls.append(name)
        if self.budget_after is not None and len(self.calls) > self.budget_after:
            raise BudgetExceeded("monthly AI budget of news_analysis jobs reached")
        parsed: Any
        if request.schema is StoryExtraction:
            first = self.first_item(request)
            span = "an invented sentence that is nowhere" if self.bad_span else first["text"][:50]
            parsed = StoryExtraction.model_validate(
                {
                    "event_type": "guidance",
                    "action": "changed",
                    "status": "reported",
                    "key_claims": [{"text": "A claim.", "item_id": first["item_id"], "span": span}],
                    "unknowns": ["terms"],
                }
            )
        elif request.schema is ThesisLinks:
            parsed = ThesisLinks(
                links=[
                    ThesisLinkOut(
                        kind="why_owned",
                        ref="why_owned",
                        effect="weakens",
                        reason="Touches the services case.",
                        claim_index=0,
                    )
                ]
            )
        elif request.schema is RawSentiment:
            first = self.first_item(request)
            parsed = RawSentiment.model_validate(
                {
                    "aspects": [
                        {
                            "entity": "Apple",
                            "direction": "negative",
                            "strength": 3.0,
                            "item_id": first["item_id"],
                            "span": first["text"][:50],
                        },
                        {
                            "entity": "Apple",
                            "direction": "negative",
                            "strength": 0.2,
                            "item_id": first["item_id"],
                            "span": "a sentence that is nowhere",
                        },
                    ]
                }
            )
        elif request.schema is ImpactNotes:
            parsed = ImpactNotes()
        elif request.schema is FramingComparison:
            parsed = FramingComparison(outlets=[], agreement="agree")
        elif request.schema is ClaimVerdicts:
            parsed = ClaimVerdicts()
        else:
            raise AssertionError(name)
        return SimpleNamespace(
            parsed=parsed,
            citations=[],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=400, output_tokens=80),
        )


async def prepare(
    pool: asyncpg.Pool,
    feeds: Feeds,
    http: Any,
    techniques: list[str],
    extra: dict[str, Any] | None = None,
) -> None:
    await setup_config(pool)
    async with pool.acquire() as conn:
        await save_config(
            conn,
            "jobs",
            JobsConfig.model_validate(
                {
                    "escalation": {"enabled": False},
                    "news": {
                        "analysis_techniques": techniques,
                        "analysis_per_run": 50,
                        "analysis_min_severity": 0,
                        **(extra or {}),
                    },
                }
            ),
            created_by=None,
        )
    deps = make_deps(http, Clock(NOON), FakeCorroborator(), {"telegram": FakeChannel()})
    first_run_feeds(feeds, NOON)
    assert await ingest(pool, deps) == "succeeded"
    # The ingest clock is a fixed day in the past; the analysis job looks at stories seen in the
    # last days by the real clock.
    await pool.execute("update story_cluster set last_seen = now()")


async def analyse(pool: asyncpg.Pool) -> str:
    return await run_job(pool, "news_analysis", news_analysis_handler, trigger="manual")


async def analysis_rows(pool: asyncpg.Pool) -> dict[str, int]:
    rows = await pool.fetch("select kind, count(*) n from story_analysis group by kind")
    return {r["kind"]: r["n"] for r in rows}


async def test_analysis_job_runs_every_technique_once_per_input(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepare(backend_pool, feeds, http, ALL)
    llm = FakeAnalysisLLM()
    monkeypatch.setattr(analysis_job, "make_llm", lambda: llm)
    stories = await backend_pool.fetchval("select count(*) from story_cluster")
    assert stories >= 2

    assert await analyse(backend_pool) == "succeeded"

    rows = await analysis_rows(backend_pool)
    assert rows["extraction"] == stories
    assert rows["novelty"] == stories and rows["thread"] == stories
    assert "market" not in rows  # no price series for the fixture instruments
    first = await backend_pool.fetchval("select id from story_cluster order by id limit 1")
    extraction = await latest_analysis(backend_pool, first, "extraction")
    assert extraction is not None and len(extraction["key_claims"]) == 1
    novelty = await latest_analysis(backend_pool, first, "novelty")
    assert novelty is not None and novelty["novelty"] == "new"

    # Every story is on a thread, and a thread has a timeline.
    assert (
        await backend_pool.fetchval("select count(*) from story_cluster where thread_id is null")
        == 0
    )
    threads = await list_threads(backend_pool, None, None, 50)
    assert threads and sum(t.stories for t in threads) == stories
    detail = await thread_detail(backend_pool, threads[0].id)
    assert detail is not None and detail.timeline and detail.timeline[0].diff.unknown == ["terms"]

    # A second run on unchanged stories adds nothing and calls no model for extraction.
    before = (await analysis_rows(backend_pool), len(llm.calls))
    assert await analyse(backend_pool) == "succeeded"
    after_calls = llm.calls[before[1] :]
    assert await analysis_rows(backend_pool) == before[0]
    assert "StoryExtraction" not in after_calls

    # Per-step spend is recorded under the job type, so it shows in the cost summary.
    steps = {
        r["name"]
        for r in await backend_pool.fetch(
            "select s.name from job_step s join job j on j.id = s.job_id"
            " where j.job_type = 'news_analysis'"
        )
    }
    assert {"extract", "code", "analyse"} <= steps

    # The API view assembles the latest result of each technique.
    view = await story_analysis(backend_pool, first)
    assert view is not None and view.extraction is not None and view.novelty is not None
    assert view.thread is not None and view.models["extraction"]
    assert await story_analysis(backend_pool, 999_999) is None


async def test_an_extraction_with_no_verifiable_span_is_never_saved(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepare(backend_pool, feeds, http, ["extraction", "novelty", "thread"])
    monkeypatch.setattr(analysis_job, "make_llm", lambda: FakeAnalysisLLM(bad_span=True))

    assert await analyse(backend_pool) == "succeeded"

    rows = await analysis_rows(backend_pool)
    assert "extraction" not in rows
    # The code techniques still ran without it.
    assert rows["novelty"] > 0 and rows["thread"] > 0
    detail = await backend_pool.fetchval(
        "select detail::text from job_step s join job j on j.id = s.job_id"
        " where j.job_type = 'news_analysis' and s.name = 'extract'"
    )
    assert "no verified claim" in detail


async def test_a_budget_stop_ends_the_model_techniques_but_not_the_code_ones(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepare(backend_pool, feeds, http, ["extraction", "novelty", "thread", "themes"])
    llm = FakeAnalysisLLM(budget_after=1)
    monkeypatch.setattr(analysis_job, "make_llm", lambda: llm)

    assert await analyse(backend_pool) == "succeeded"

    rows = await analysis_rows(backend_pool)
    assert rows["extraction"] == 1
    assert rows["novelty"] > 1 and rows["thread"] > 1
    assert len(llm.calls) == 2  # the second call hit the cap; no third was tried


async def test_briefs_cite_stored_items_and_work_without_a_model(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepare(backend_pool, feeds, http, ["extraction", "novelty", "thread"])
    monkeypatch.setattr(analysis_job, "make_llm", lambda: FakeAnalysisLLM())
    assert await analyse(backend_pool) == "succeeded"
    now = dt.datetime.now(dt.UTC)
    start, end = now - dt.timedelta(days=1), now + dt.timedelta(days=1)

    plain, by_model = await brief.build_brief(
        backend_pool, "morning", start, end, min_severity=0, max_lines=10, writer=None
    )
    assert not by_model and plain.generated_without_model
    lines = [line for s in plain.sections for line in s.lines]
    assert lines
    item_ids = {r["id"] for r in await backend_pool.fetch("select id from news_item")}
    assert all(line.item_ids and set(line.item_ids) <= item_ids for line in lines)

    # A model line that cites another story's item is replaced by the plain line.
    async def writer(chosen: list[brief.Candidate]) -> dict[int, brief.WrittenLine]:
        return {
            c.story.cluster_id: brief.WrittenLine(
                story_id=c.story.cluster_id, text="Written.", why="Why.", item_ids=[10**9]
            )
            for c in chosen
        }

    rejected, by_model = await brief.build_brief(
        backend_pool, "morning", start, end, min_severity=0, max_lines=10, writer=writer
    )
    assert not by_model
    assert all(not line.written_by_model for s in rejected.sections for line in s.lines)

    story = await load_story(backend_pool, lines[0].story_id)
    assert story is not None
    await backend_pool.execute(
        "insert into news_brief (kind, ticker, period_start, period_end, body)"
        " values ('morning', '', $1, $2, $3::jsonb)",
        start,
        end,
        plain.model_dump_json(),
    )
    stored = await list_briefs(backend_pool, "morning", None, 5)
    assert len(stored) == 1 and stored[0].body.sections


async def test_the_wire_feedback_and_themes_read_side(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepare(backend_pool, feeds, http, ["themes"])
    monkeypatch.setattr(analysis_job, "make_llm", lambda: FakeAnalysisLLM())
    assert await analyse(backend_pool) == "succeeded"

    page = await wire(backend_pool, WireFilters(limit=2))
    assert len(page.items) == 2 and page.next_before_id is not None
    rest = await wire(backend_pool, WireFilters(before_id=page.next_before_id, limit=50))
    assert rest.items and all(i.id < page.next_before_id for i in rest.items)
    linked = await wire(backend_pool, WireFilters(linked=True))
    unlinked = await wire(backend_pool, WireFilters(linked=False))
    assert linked.items and all(i.linked for i in linked.items)
    assert all(not i.linked for i in unlinked.items)
    found = await wire(backend_pool, WireFilters(q="Contoso"))
    assert found.items and all("contoso" in i.headline.lower() for i in found.items)
    assert (await wire(backend_pool, WireFilters(tier=1))).items

    mark = await add_feedback(
        backend_pool,
        NewsFeedbackIn(target="story", target_id="1", kind="duplicate", note="same as 2"),
    )
    assert mark.id and (await list_feedback(backend_pool, "story", "1", 10))[0].kind == "duplicate"
    assert await list_feedback(backend_pool, "item", None, 10) == []

    # Themes: the seeded watch topics tag nothing in this fixture, so there are no rows, and the
    # query still answers.
    assert await theme_days(backend_pool, 14) == []


async def test_sentiment_keeps_one_verified_aspect_per_entity_with_a_clamped_strength(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepare(backend_pool, feeds, http, ["extraction", "sentiment"])
    monkeypatch.setattr(analysis_job, "make_llm", lambda: FakeAnalysisLLM())
    alerts_before = await backend_pool.fetchval("select count(*) from alert")

    assert await analyse(backend_pool) == "succeeded"

    rows = await analysis_rows(backend_pool)
    assert rows["sentiment"] == rows["extraction"] > 0
    story = await backend_pool.fetchval("select cluster_id from story_analysis limit 1")
    payload = await latest_analysis(backend_pool, story, "sentiment")
    assert payload is not None
    # The aspect with an invented span is dropped, and 3.0 is clamped to 1.
    assert [a["entity"] for a in payload["aspects"]] == ["Apple"]
    assert payload["aspects"][0]["strength"] == 1.0 and payload["intensity"] == 1.0
    # Sentiment never touches what alerts: the analysis run created no alert.
    assert await backend_pool.fetchval("select count(*) from alert") == alerts_before


async def test_batched_extraction_sends_the_stories_as_one_batch(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepare(backend_pool, feeds, http, ["extraction"], {"batch_extraction": True})
    llm = FakeAnalysisLLM()
    monkeypatch.setattr(analysis_job, "make_llm", lambda: llm)
    stories = await backend_pool.fetchval("select count(*) from story_cluster")
    assert stories >= 2

    assert await analyse(backend_pool) == "succeeded"

    assert (await analysis_rows(backend_pool))["extraction"] == stories
    assert llm.batches == [stories]


async def test_quiet_confirmed_stories_resolve_and_others_do_not(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any
) -> None:
    await prepare(backend_pool, feeds, http, ["novelty"])
    ids = [r["id"] for r in await backend_pool.fetch("select id from story_cluster order by id")]
    assert len(ids) >= 3
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
    quiet, recent, disputed = ids[:3]
    await backend_pool.execute(
        "update story_cluster set lifecycle = 'corroborated', last_seen = $2 where id = $1",
        quiet,
        old,
    )
    await backend_pool.execute(
        "update story_cluster set lifecycle = 'corroborated', last_seen = now() where id = $1",
        recent,
    )
    await backend_pool.execute(
        "update story_cluster set lifecycle = 'disputed', last_seen = $2 where id = $1",
        disputed,
        old,
    )

    now = dt.datetime.now(dt.UTC)
    assert await analysis_job.resolve_quiet_stories(backend_pool, now, 0) == 0
    assert await analysis_job.resolve_quiet_stories(backend_pool, now, 14) == 1

    states = {
        r["id"]: r["lifecycle"]
        for r in await backend_pool.fetch("select id, lifecycle from story_cluster")
    }
    assert states[quiet] == "resolved"
    assert states[recent] == "corroborated" and states[disputed] == "disputed"
    event = await backend_pool.fetchrow(
        "select from_state from story_lifecycle_event where cluster_id = $1"
        " and to_state = 'resolved'",
        quiet,
    )
    assert event is not None and event["from_state"] == "corroborated"
