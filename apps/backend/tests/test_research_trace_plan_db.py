"""The research trace and the owner-editable plan against a migrated database: a run keeps a
per-call trace the purge and the retention sweep remove; a plan draft is created, edited,
approved and started, and the run uses exactly that plan."""

import datetime as dt

import asyncpg
import httpx
import pytest

from steward.jobs.runner import run_job
from steward.research.retention import purge_old_traces
from tests.conftest import MigratedDatabase
from tests.test_phase2_followups import needs_pgmq
from tests.test_phase5 import seed_holding
from tests.test_research_phase2_3_db import (
    IteratingWriter,
    api,  # noqa: F401  (fixture)
    handler,
    run_deep,
    stored_body,
)

pytestmark = pytest.mark.db


async def test_a_run_keeps_a_trace_that_the_purge_and_the_sweep_remove(
    backend_pool: asyncpg.Pool,
    api: httpx.AsyncClient,  # noqa: F811
) -> None:
    await seed_holding(backend_pool)
    report_id = await run_deep(backend_pool, IteratingWriter())
    body = await stored_body(backend_pool, report_id)
    trace = body.trace
    assert trace is not None and trace.model_calls
    assert trace.plan_source == "code"
    assert {c.step for c in trace.model_calls} >= {
        f"section:{s.id}" for s in body.sections if s.written_by == "model"
    }
    assert all(c.outcome == "ok" for c in trace.model_calls)
    assert trace.web and trace.web[0].urls_found == 3
    assert trace.retrieval and all(r.sources_offered >= r.sources_cited for r in trace.retrieval)
    assert trace.validation and all(not v.schema_failure for v in trace.validation)
    # metadata only: nothing from a source snippet or a model answer is in the trace
    dumped = trace.model_dump_json()
    assert all(s.text not in dumped for sec in body.sections for s in sec.sentences)

    # the retention sweep drops the trace from the stored body
    counts = await purge_old_traces(backend_pool, 1, dt.datetime.now(dt.UTC) + dt.timedelta(days=5))
    assert counts["report_traces"] == 1
    swept = await stored_body(backend_pool, report_id)
    assert swept.trace is None and swept.trace_purged_at
    assert swept.sections == body.sections

    # the owner's purge removes it too
    second = await stored_body(backend_pool, await run_deep(backend_pool, IteratingWriter()))
    assert second.trace is not None
    rid = await backend_pool.fetchval("select max(id) from report where type = 'research'")
    assert (await api.post(f"/research/{rid}/trace/purge")).status_code == 200
    assert (await stored_body(backend_pool, rid)).trace is None


async def test_a_plan_is_drafted_edited_approved_and_started(
    backend_pool: asyncpg.Pool,
    api: httpx.AsyncClient,  # noqa: F811
    migrated_db: MigratedDatabase,
) -> None:
    await seed_holding(backend_pool)
    made = await api.post(
        "/research/plans",
        json={"ticker": "AAPL_US_EQ", "profile": "deep-thesis", "planner": "code"},
    )
    assert made.status_code == 201
    draft = made.json()
    assert draft["status"] == "draft" and draft["plan"]["authored_by"] == "code"
    plan_id = draft["id"]

    # a draft cannot start before it is approved
    assert (await api.post(f"/research/plans/{plan_id}/start")).status_code == 409

    # an edit that names a site the profile did not allow is refused, with every problem listed
    questions = [
        {
            "id": q["id"],
            "section": q["section"],
            "question": q["question"],
            "source_groups": q["source_groups"],
            "web_intent": q["web_intent"],
        }
        for q in draft["plan"]["questions"]
    ]
    bad = [dict(questions[0], question="Check what evil.example says about it"), *questions[1:]]
    refused = await api.patch(f"/research/plans/{plan_id}", json={"questions": bad})
    assert refused.status_code == 422
    assert "evil.example" in str(refused.json())

    # reorder, remove one, reword one, approve
    dropped = questions[-1]
    kept = [q for q in questions if q is not dropped]
    kept.reverse()
    kept[0] = dict(kept[0], question="What is the single strongest piece of evidence here?")
    edited = await api.patch(
        f"/research/plans/{plan_id}", json={"questions": kept, "approve": True}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "approved" and edited.json()["edited"]

    needs_pgmq(migrated_db)  # starting a run enqueues a job
    started = await api.post(f"/research/plans/{plan_id}/start")
    assert started.status_code == 202
    job_id = started.json()["id"]
    assert (await api.get(f"/research/plans/{plan_id}")).json()["status"] == "started"
    assert (
        await api.patch(f"/research/plans/{plan_id}", json={"approve": True})
    ).status_code == 409

    writer = IteratingWriter()
    assert (
        await run_job(backend_pool, "research", handler(writer), trigger="manual", job_id=job_id)
        == "succeeded"
    )
    report_id = int(await backend_pool.fetchval("select id from report where job_id = $1", job_id))
    plan = (await stored_body(backend_pool, report_id)).plan
    assert plan is not None and plan.edited_by_owner and plan.draft_id == plan_id
    assert [q.id for q in plan.questions] == [q["id"] for q in kept]
    assert dropped["id"] not in [q.id for q in plan.questions]
    assert plan.questions[0].question == kept[0]["question"]
