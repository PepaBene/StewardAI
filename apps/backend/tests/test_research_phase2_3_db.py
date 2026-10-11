"""Phases 2 and 3 of the AI research plan against a migrated database: a deep run plans,
iterates, records every URL it found, snapshots what it was made from and proposes thesis
changes the owner accepts field by field."""

import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.ai import Usage
from steward.config import ThesesConfig
from steward.config.store import load_active, save_config
from steward.config.thesis import ThesisConfig, ThesisKPI
from steward.jobs.runner import JobContext, run_job
from steward.research.run import ResearchBody, ResearchDeps, run_research
from steward.research.snapshot import verify_snapshot
from steward.research.web import SearchHit
from steward.research.writer import DraftSentence, SectionDraft, SectionRequest, SectionResult
from steward.thesis.proposal import review_thesis
from tests.test_phase5 import KillWorker, seed_holding

pytestmark = pytest.mark.db

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
CITED = "https://www.reuters.com/markets/apple-services"
UNCITED = "https://www.reuters.com/markets/other"
FORUM = "https://www.reddit.com/r/stocks/apple"


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


class IteratingWriter:
    """One cited sentence on the first pass (below the profile's two), two on a follow-up. The
    counter-case cites a single web page. Search returns a cited page, an uncited page and a
    forum thread."""

    def __init__(self, die_on_call: int | None = None) -> None:
        self.requests: list[SectionRequest] = []
        self.die_on_call = die_on_call

    async def write(self, request: SectionRequest) -> SectionResult:
        if self.die_on_call is not None and len(self.requests) + 1 == self.die_on_call:
            raise KillWorker()
        self.requests.append(request)
        ids = re.findall(r"\[(S\d+)\]", request.material)
        follow_up = bool(request.existing)
        if request.section == "counter_case" and not follow_up:
            sentences = [DraftSentence(text="Antitrust risk is rising.", urls=[CITED])]
        else:
            tag = "follow-up" if follow_up else "first pass"
            # No digits in the text: the code-side claim check removes a number no source has.
            words = ("alpha", "beta")[: 2 if follow_up else 1]
            sentences = [
                DraftSentence(text=f"{request.section} {tag} {w}.", source_ids=[ids[0]])
                for w in words
            ]
        usage = Usage(model="fake")
        usage.searches = 1 if request.max_searches else 0
        titles = {CITED: "Reuters: Apple services", UNCITED: "Reuters: other", FORUM: "Forum"}
        hits = [
            SearchHit(CITED, titles[CITED], "2 days"),
            SearchHit(UNCITED, titles[UNCITED]),
            SearchHit(FORUM, titles[FORUM], "1 day"),
        ]
        return SectionResult(SectionDraft(sentences=sentences), usage, titles, None, hits)


def handler(writer: IteratingWriter) -> Any:
    async def run(ctx: JobContext) -> None:
        await run_research(
            ctx,
            ResearchDeps(
                writer=writer,
                verifier=None,
                now=dt.datetime.now(dt.UTC),
                prompts={"research_writer": "WRITER SYSTEM TEXT"},
            ),
        )

    return run


async def run_deep(pool: asyncpg.Pool, writer: IteratingWriter) -> int:
    job_id = await pool.fetchval(
        "insert into job (job_type, trigger, params) values ('research', 'manual',"
        ' \'{"ticker": "AAPL_US_EQ", "profile": "deep-thesis"}\') returning id'
    )
    assert await run_job(pool, "research", handler(writer), trigger="manual", job_id=job_id) == (
        "succeeded"
    )
    return int(await pool.fetchval("select id from report where job_id = $1", job_id))


async def stored_body(pool: asyncpg.Pool, report_id: int) -> ResearchBody:
    return ResearchBody.model_validate_json(
        await pool.fetchval("select body::text from report where id = $1", report_id)
    )


async def test_a_deep_run_plans_iterates_and_records_what_it_found(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_holding(backend_pool)
    writer = IteratingWriter()
    body = await stored_body(backend_pool, await run_deep(backend_pool, writer))

    # planned before searching
    assert body.mode == "deep_thesis" and body.plan is not None
    assert body.plan.counter_evidence_intent and body.plan.iterate
    assert body.plan.planned_sections == [s.id for s in body.sections]

    # laid out by the template, not the profile: its order, its detail-only sections
    assert body.template_id == "thesis-memo" and body.default_view == "concise"
    assert [s.id for s in body.sections][:2] == ["executive_summary", "business"]
    assert next(s for s in body.sections if s.id == "catalysts").detail_only

    # the first pass left every section thin; follow-ups ran, at most three per round
    first_pass = [r for r in writer.requests if not r.existing]
    follow_ups = [r for r in writer.requests if r.existing]
    assert len(first_pass) == 9 and len(follow_ups) == 6
    assert [len(r.sections) for r in body.rounds] == [3, 3]
    assert body.stopped_because == "round limit reached"
    assert all(r.focus for r in follow_ups)
    thin_again = next(s for s in body.sections if s.id == body.rounds[0].sections[0])
    assert len(thin_again.sentences) == 3  # one from the first pass, two from the follow-up
    assert body.steps_used <= 100 and body.searches_used <= 60

    # the counter-case was searched on purpose and its single web source was picked for a
    # second look
    counter = next(r for r in first_pass if r.section == "counter_case")
    assert "Search for:" in counter.focus and "Evidence against" in counter.focus
    assert any("Corroborate" in r.focus for r in follow_ups if r.section == "counter_case")

    # every URL the search returned is kept, cited or not, with a reason when excluded
    by_url = {d.url: d for d in body.discovered_urls}
    assert by_url[CITED].status == "cited" and by_url[UNCITED].status == "found_not_cited"
    assert by_url[FORUM].status == "excluded"
    assert "forums are not enabled" in (by_url[FORUM].limitation or "")
    assert "undated" in (by_url[UNCITED].limitation or "")
    summary = body.search_summary
    assert summary is not None and summary.urls_found == 3 and summary.urls_excluded == 1
    assert summary.sources_added_by_follow_up >= 0 and summary.limitations
    web = [s for s in body.sources if s.kind == "search"]
    assert [s.role for s in web] == ["quality_news"] and web[0].retrieved_at
    # a forum page is never registered as a source while forums are off
    assert FORUM not in {s.url for s in body.sources}

    # a snapshot of what the run was made from, intact
    snap = body.snapshot
    assert snap is not None and verify_snapshot(snap)
    assert (
        snap.profile["id"] == "deep-thesis"
        and snap.template
        and snap.template["id"] == "thesis-memo"
    )
    assert snap.prompts["research_writer"].text == "WRITER SYSTEM TEXT"
    assert snap.thesis and snap.thesis["why_owned"] == "Services growth."
    assert snap.versions["theses"] is not None and snap.versions["strategy"] is not None


async def test_the_proposal_is_a_diff_and_accepting_it_saves_a_new_version(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await seed_holding(backend_pool)
    report_id = await run_deep(backend_pool, IteratingWriter())
    body = await stored_body(backend_pool, report_id)
    proposal = body.thesis_proposal
    assert proposal is not None and proposal.mode == "draft_thesis" and proposal.has_thesis
    fields = {c.field: c for c in proposal.changes}
    assert fields["why_owned"].before == ["Services growth."]
    assert fields["why_owned"].after[0].text.startswith("executive_summary")
    assert fields["why_owned"].after[0].evidence_ids
    before_version = await load_active(backend_pool, "theses", ThesesConfig)
    assert before_version is not None and proposal.base_version_id == before_version.id

    # nothing has changed yet
    assert before_version.body.theses[0].why_owned == "Services growth."
    bad = await api.post(
        f"/research/{report_id}/thesis-proposal/accept", json={"fields": ["conviction"]}
    )
    assert bad.status_code == 422

    ok = await api.post(
        f"/research/{report_id}/thesis-proposal/accept", json={"fields": ["pre_mortem"]}
    )
    assert ok.status_code == 200, ok.text
    after = await load_active(backend_pool, "theses", ThesesConfig)
    assert after is not None and after.id == ok.json()["config_version_id"] != before_version.id
    thesis = after.body.theses[0]
    assert thesis.pre_mortem and thesis.why_owned == "Services growth."  # only the accepted field
    # the earlier version is still there, so history is kept
    assert (
        await backend_pool.fetchval("select count(*) from config_version where domain = 'theses'")
        == 2
    )
    note = await backend_pool.fetchval("select note from config_version where id = $1", after.id)
    assert f"research report #{report_id}" in note

    # the report was made against the old version: a second accept is stale and refused
    stale = await api.post(
        f"/research/{report_id}/thesis-proposal/accept", json={"fields": ["why_owned"]}
    )
    assert stale.status_code == 409 and "run the research again" in stale.json()["detail"]


async def test_views_and_markdown_export_render_the_same_stored_report(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await seed_holding(backend_pool)
    report_id = await run_deep(backend_pool, IteratingWriter())
    detailed = (await api.get(f"/research/{report_id}")).json()
    concise = (await api.get(f"/research/{report_id}", params={"view": "concise"})).json()
    assert detailed["body"]["view"] == "detailed" and concise["body"]["view"] == "concise"
    assert len(concise["body"]["sections"]) < len(detailed["body"]["sections"])
    assert "Catalysts" in detailed["body_md"] and "Catalysts" not in concise["body_md"]
    kept = {s["id"] for s in concise["body"]["sections"]}
    assert kept <= {s["id"] for s in detailed["body"]["sections"]}
    export = await api.get(f"/research/{report_id}/export", params={"view": "concise"})
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/markdown")
    assert (
        export.text == concise["body_md"] and "attachment" in export.headers["content-disposition"]
    )
    assert (await api.get(f"/research/{report_id}", params={"view": "huge"})).status_code == 422


async def test_a_run_killed_during_follow_ups_resumes_without_repeating_paid_calls(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_holding(backend_pool)
    job_id = await backend_pool.fetchval(
        "insert into job (job_type, trigger, params) values ('research', 'manual',"
        ' \'{"ticker": "AAPL_US_EQ", "profile": "deep-thesis"}\') returning id'
    )
    dying = IteratingWriter(die_on_call=11)  # 9 first-pass calls, 1 follow-up, then the kill
    with pytest.raises(KillWorker):
        await run_job(backend_pool, "research", handler(dying), trigger="manual", job_id=job_id)
    assert len(dying.requests) == 10 and sum(1 for r in dying.requests if r.existing) == 1

    fresh = IteratingWriter()
    result = await run_job(
        backend_pool, "research", handler(fresh), trigger="manual", job_id=job_id
    )
    assert result == "succeeded"
    assert len(fresh.requests) == 5 and all(r.existing for r in fresh.requests)
    body = await stored_body(
        backend_pool,
        int(await backend_pool.fetchval("select id from report where job_id = $1", job_id)),
    )
    assert [len(r.sections) for r in body.rounds] == [3, 3]
    assert body.steps_used == 30  # fifteen calls, each a model call plus one search


async def test_an_update_run_can_review_a_thesis_against_stored_verdicts(
    backend_pool: asyncpg.Pool,
) -> None:
    kpi = ThesisKPI(id="services", metric="services_growth", operator=">=", threshold=10, unit="%")
    thesis = ThesisConfig(
        ticker="AAPL_US_EQ", why_owned="Services.", kpis=[kpi], exit_conditions=["Growth < 5%"]
    )
    async with backend_pool.acquire() as conn:
        stored = await save_config(conn, "theses", ThesesConfig(theses=[thesis]), created_by=None)
    await backend_pool.execute(
        "insert into public.verdict (ticker, kpi_id, metric, basis, kpi_period, status,"
        " observed_value, operator, threshold, threshold_unit, rationale, config_version_id)"
        " values ('AAPL_US_EQ', 'services', 'services_growth', 'reported', 'quarter',"
        " 'breach', 8.5, '>=', 10, '%', 'growth 8.5% is below 10%', $1)",
        stored.id,
    )
    review = await review_thesis(backend_pool, "AAPL_US_EQ", thesis, stored.id)
    [k] = review.kpis
    assert k.status == "breach" and k.observed_value == 8.5 and k.rule == ">= 10 %"
    assert review.exit_conditions == ["Growth < 5%"] and review.base_version_id == stored.id
    none = await review_thesis(backend_pool, "ZZZ", None, None)
    assert none.kpis == [] and none.notes
