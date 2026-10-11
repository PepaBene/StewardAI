"""The agentic research engine: lens workers, their limits and the run that uses them."""

import asyncio
import datetime as dt
import re
from typing import Any

import asyncpg
import pytest

from steward.ai import Usage
from steward.chat import catalog
from steward.config.research import ALL_LENSES, ResearchProfilesConfig
from steward.jobs.runner import JobContext, run_job
from steward.research import agents
from steward.research.agents import (
    Finding,
    LensDraft,
    WorkerRequest,
    WorkerResult,
    findings_text,
    keep_findings,
    lens_tools,
    run_agents,
)
from steward.research.read_tools import read_only_tools
from steward.research.run import ResearchBody, ResearchDeps, run_research
from steward.research.sources import Registry
from steward.research.writer import DraftSentence, SectionDraft, SectionRequest, SectionResult
from tests.test_phase5 import seed_holding


def test_every_lens_tool_exists_and_only_reads() -> None:
    assert set(ALL_LENSES) == set(agents.LENS_TOOLS) == set(agents.BRIEFS)
    for lens, names in agents.LENS_TOOLS.items():
        assert set(names) <= set(catalog.TOOLS), f"{lens} names a tool the catalog lacks"
        assert set(names) <= read_only_tools(), f"{lens} names a tool that writes"
        assert lens_tools(lens) == names
    assert "propose_job" not in {t for names in agents.LENS_TOOLS.values() for t in names}
    for lens in ALL_LENSES:
        assert {s.name for s in agents.tool_specs(lens)} == set(agents.LENS_TOOLS[lens])


def make_request(registry: Registry, tier_of: Any = lambda host: None) -> WorkerRequest:
    async def caller(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    return WorkerRequest(
        lens="news_web",
        name="Acme",
        ticker="ACME_US_EQ",
        today="2026-10-11",
        model="m",
        effort="low",
        max_steps=4,
        max_searches=2,
        domains=("reuters.com",),
        caller=caller,
        registry=registry,
        tier_of=tier_of,
    )


def test_only_findings_with_a_checkable_citation_are_kept() -> None:
    reg = Registry()
    sid = reg.add("fact", "f1", "Revenue", snippet="Revenue grew")
    draft = LensDraft(
        findings=[
            Finding(text="Revenue grew.", source_ids=[sid]),
            Finding(text="Invented id.", source_ids=["S99"]),
            Finding(text="No citation."),
            Finding(text="Page not searched.", urls=["https://www.reuters.com/a"]),
            Finding(text="Off-list outlet.", urls=["https://blog.example.com/a"]),
            Finding(text="Searched page.", urls=["https://www.reuters.com/b"]),
        ]
    )
    request = make_request(reg, lambda host: 2 if host == "reuters.com" else None)
    titles = {
        "https://www.reuters.com/b": "Reuters b",
        "https://blog.example.com/a": "Blog",
    }
    kept, dropped = keep_findings("news_web", draft, request, titles)
    assert [k.text for k in kept] == ["Revenue grew.", "Searched page."]
    assert dropped == 4
    searched = reg.get(kept[1].source_ids[0])
    assert searched is not None and searched.kind == "search" and searched.tier == 2


class FakeWorker:
    def __init__(self, fail: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.fail = fail
        self.running = 0
        self.peak = 0
        self.order: list[str] = []
        self.searches: dict[str, int] = {}

    async def run(self, request: WorkerRequest) -> WorkerResult:
        self.order.append(request.lens)
        self.searches[request.lens] = request.max_searches
        self.running += 1
        self.peak = max(self.peak, self.running)
        await asyncio.sleep(0.01)
        self.running -= 1
        if request.lens in self.fail:
            raise RuntimeError("boom")
        sid = request.registry.add("fact", f"f-{request.lens}", request.lens, snippet="x")
        usage = Usage(model="m")
        usage.input_tokens = 100
        return WorkerResult(
            LensDraft(findings=[Finding(text=f"{request.lens} finding.", source_ids=[sid])]),
            usage,
            steps=2,
            tool_calls=["fundamentals"],
        )


async def drive(worker: FakeWorker, parallel: int = 2, total_searches: int = 10) -> Any:
    reg = Registry()
    request = make_request(reg)
    return await run_agents(
        worker,
        lenses=list(ALL_LENSES),
        name="Acme",
        ticker="ACME_US_EQ",
        today="2026-10-11",
        model="m",
        effort="low",
        max_steps=4,
        worker_searches=8,
        total_searches=total_searches,
        parallel=parallel,
        domains=("reuters.com",),
        caller=request.caller,
        registry=reg,
        tier_of=lambda host: None,
    )


async def test_workers_respect_the_parallel_cap_and_the_first_runs_alone() -> None:
    worker = FakeWorker()
    out = await drive(worker, parallel=2)
    assert worker.peak == 2
    assert worker.order[0] == ALL_LENSES[0] and len(out.records) == len(ALL_LENSES)
    assert [r.lens for r in out.records] == list(ALL_LENSES)
    assert all(r.outcome == "ok" and r.findings == 1 for r in out.records)
    assert out.sources_added == len(ALL_LENSES)
    one = await drive(FakeWorker(), parallel=1)
    assert one.sources_added == len(ALL_LENSES)
    serial = FakeWorker()
    await drive(serial, parallel=1)
    assert serial.peak == 1


async def test_searches_are_shared_out_of_the_total_and_only_to_web_lenses() -> None:
    worker = FakeWorker()
    await drive(worker, total_searches=10)
    web = {lens: n for lens, n in worker.searches.items() if n}
    assert set(web) == agents.WEB_LENSES and sum(web.values()) <= 10
    assert web["news_web"] == 8 and web["counter_case"] == 2  # the total runs out
    none = FakeWorker()
    await drive(none, total_searches=0)
    assert not any(none.searches.values())


async def test_a_failing_worker_is_recorded_and_the_others_still_count() -> None:
    out = await drive(FakeWorker(fail={"financials"}))
    by_lens = {r.lens: r for r in out.records}
    assert by_lens["financials"].outcome == "failed" and "boom" in (
        by_lens["financials"].note or ""
    )
    assert len(out.findings) == len(ALL_LENSES) - 1
    assert any("financials" in n for n in out.notices)


def test_findings_are_rendered_with_their_ids_and_gaps() -> None:
    reg = Registry()
    sid = reg.add("fact", "f", "t")
    run = agents.AgentRun(
        findings=[agents.KeptFinding(lens="financials", text="Margin 12%.", source_ids=[sid])],
        gaps={"counter_case": ["no litigation found"]},
    )
    text = findings_text(run)
    assert "financials:" in text and f"Margin 12%. [{sid}]" in text
    assert "gap (counter_case): no litigation found" in text
    assert findings_text(agents.AgentRun()) == ""


# ------------------------------------------------------------------------------- the run


class CapturingWriter:
    def __init__(self) -> None:
        self.requests: list[SectionRequest] = []

    async def write(self, request: SectionRequest) -> SectionResult:
        self.requests.append(request)
        ids = re.findall(r"\[(S\d+)\]", request.material)
        draft = SectionDraft(
            sentences=[DraftSentence(text=f"{request.section} alpha.", source_ids=[ids[0]])]
        )
        usage = Usage(model="fake")
        usage.searches = 1 if request.max_searches else 0
        return SectionResult(draft, usage, {}, None, [])


def handler(writer: CapturingWriter, worker: FakeWorker | None) -> Any:
    async def run(ctx: JobContext) -> None:
        await run_research(
            ctx,
            ResearchDeps(
                writer=writer,
                verifier=None,
                now=dt.datetime.now(dt.UTC),
                prompts={"research_writer": "W"},
                worker=worker,
            ),
        )

    return run


async def run_profile(
    pool: asyncpg.Pool, profile: str, writer: CapturingWriter, worker: FakeWorker | None
) -> ResearchBody:
    job_id = await pool.fetchval(
        "insert into job (job_type, trigger, params) values ('research', 'manual', $1::jsonb)"
        " returning id",
        f'{{"ticker": "AAPL_US_EQ", "profile": "{profile}"}}',
    )
    status = await run_job(
        pool, "research", handler(writer, worker), trigger="manual", job_id=job_id
    )
    assert status == "succeeded"
    return ResearchBody.model_validate_json(
        await pool.fetchval("select body::text from report where job_id = $1", job_id)
    )


@pytest.mark.db
async def test_an_agentic_run_feeds_worker_findings_to_the_writer_within_the_budget(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_holding(backend_pool)
    writer, worker = CapturingWriter(), FakeWorker()
    body = await run_profile(backend_pool, "deep-agentic", writer, worker)

    profile = ResearchProfilesConfig().get("deep-agentic")
    assert profile is not None and body.engine == "agentic"
    assert [a.lens for a in body.agents] == list(ALL_LENSES)
    first_pass = [r for r in writer.requests if not r.existing]
    assert first_pass
    # The writer reads what the workers found, and does not search in the first pass.
    assert all("Findings from the research workers" in r.material for r in first_pass)
    assert all("financials finding." in r.material for r in first_pass)
    assert all(r.max_searches == 0 for r in first_pass)
    # The caps of the profile hold.
    assert body.steps_used <= profile.max_steps and body.searches_used <= profile.max_searches
    assert body.trace is not None
    assert {c.step for c in body.trace.model_calls} >= {f"worker:{lens}" for lens in ALL_LENSES}
    assert any(s.title == "financials" for s in body.sources)


def fake_body(engine: str, tiers: list[int], supported: int, unsupported: int, cost: float) -> Any:
    from types import SimpleNamespace as NS

    section = NS(sentences=[NS(source_ids=["S1"])] * supported)
    return NS(
        engine=engine,
        profile_id="p",
        sources=[NS(tier=t, kind="fact") for t in tiers],
        sections=[section],
        claims=NS(supported=supported, unsupported=unsupported),
        dropped_sentences=0,
        steps_used=10,
        searches_used=3,
        trace=NS(model_calls=[NS(cost_usd=cost)]),
        agents=[],
    )


def test_the_comparison_reports_evidence_gain_and_the_cost_limit() -> None:
    from steward.research import compare

    out = compare.compare(
        fake_body("fixed", [1, 3], 5, 1, 1.0),
        fake_body("agentic", [1, 2, 2], 8, 0, 2.5),
        max_cost_usd=3.0,
    )
    assert out["difference"]["sources_tier_1_2"] == 2
    assert out["agentic_found_more_checkable_evidence"] and out["agentic_within_cost_limit"]
    over = compare.compare(
        fake_body("fixed", [1], 5, 0, 1.0), fake_body("agentic", [1], 5, 0, 9.0), max_cost_usd=3.0
    )
    assert not over["agentic_found_more_checkable_evidence"]
    assert not over["agentic_within_cost_limit"]


@pytest.mark.db
async def test_the_fixed_profile_never_starts_workers_and_a_missing_worker_falls_back(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_holding(backend_pool)
    worker = FakeWorker()
    fixed = await run_profile(backend_pool, "deep-thesis", CapturingWriter(), worker)
    assert fixed.engine == "fixed" and fixed.agents == [] and worker.order == []
    # An agentic profile without a worker (no provider key) runs as the fixed pipeline.
    writer = CapturingWriter()
    body = await run_profile(backend_pool, "deep-agentic", writer, None)
    assert body.engine == "agentic" and body.agents == []
    assert any(r.max_searches for r in writer.requests if not r.existing)
