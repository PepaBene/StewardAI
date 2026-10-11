"""Event-driven agents and the inbox (agentic workflows and RAG plan, AG4 and AG5)."""

import datetime as dt
import json
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.ai import Usage
from steward.chat import catalog
from steward.config.jobs import InboxSettings
from steward.inbox import agent as agent_mod
from steward.inbox import replay, run, service, triggers
from steward.inbox.agent import (
    AgentRequest,
    AgentResult,
    InboxDraft,
    ProposedAction,
    Statement,
)
from steward.inbox.route_stats import route_stats
from steward.jobs.runner import run_job
from steward.research.read_tools import read_only_tools
from steward.research.sources import Registry
from tests.test_phase5 import seed_holding

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
NOW = dt.datetime(2026, 10, 11, 12, 0, tzinfo=dt.UTC)


def test_the_agent_can_only_read() -> None:
    assert set(agent_mod.AGENT_TOOLS) <= set(catalog.TOOLS)
    assert set(agent_mod.AGENT_TOOLS) <= read_only_tools()
    assert agent_mod.agent_tools() == agent_mod.AGENT_TOOLS
    assert "propose_job" not in agent_mod.AGENT_TOOLS
    assert {s.name for s in agent_mod.tool_specs()} == set(agent_mod.AGENT_TOOLS)


class FakeAgent:
    """Reads the owner's notes (which registers a source) and cites it, or not."""

    def __init__(self, draft: InboxDraft) -> None:
        self.draft = draft
        self.requests: list[AgentRequest] = []

    async def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        await request.caller("agent_notes", {"ticker": request.trigger.ticker})
        usage = Usage(model="fake")
        usage.input_tokens = 100
        return AgentResult(self.draft, usage, steps=2, tool_calls=["agent_notes"])


def escalation_body(headline: str = "Guidance cut below the KPI floor") -> str:
    return json.dumps(
        {
            "ticker": "AAPL_US_EQ",
            "trigger": "story",
            "cluster_id": None,
            "event_type": "guidance",
            "severity": 3,
            "headline": headline,
            "sentences": [{"text": "Apple cut its services guidance.", "source_urls": []}],
            "kpis": [{"id": "services_growth", "description": "Services growth above 10%"}],
            "exit_conditions": [],
            "none_applies": False,
            "thesis_link": "services_growth",
            "rationale": "Below the floor.",
            "sources": [],
        }
    )


async def add_escalation(pool: asyncpg.Pool, headline: str = "Guidance cut") -> int:
    return int(
        await pool.fetchval(
            "insert into public.report (type, ticker, title, body, body_md)"
            " values ('escalation', 'AAPL_US_EQ', 'Escalation', $1::jsonb, '') returning id",
            escalation_body(headline),
        )
    )


async def add_note(pool: asyncpg.Pool, text: str = "Services margin is the thesis.") -> int:
    return (await service.add_note(pool, service.NoteIn(ticker="AAPL_US_EQ", text=text))).id


def draft(
    *, statements: list[Statement] | None = None, action: ProposedAction | None = None, **kw: object
) -> InboxDraft:
    return InboxDraft(
        headline="Services guidance cut",
        statements=statements
        if statements is not None
        else [Statement(text="Cut.", source_ids=["S1"])],
        touches=["services_growth"],
        assessment="Below the KPI floor.",
        action=action,
        **kw,  # type: ignore[arg-type]
    )


SETTINGS = InboxSettings()


async def make_item(pool: asyncpg.Pool, agent: FakeAgent | None) -> tuple[triggers.Trigger, dict]:
    report = await add_escalation(pool)
    trigger = await triggers.load(pool, "escalation", report)
    assert trigger is not None
    return trigger, await run.build_item(pool, agent, trigger, SETTINGS, NOW, None)


@pytest.mark.db
async def test_the_scan_queues_one_run_per_new_trigger_and_never_twice(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_holding(backend_pool)
    report = await add_escalation(backend_pool)
    alert = await backend_pool.fetchval(
        "insert into public.alert (kind, severity, ticker, channel, message, status)"
        " values ('thesis_breach', 3, 'AAPL_US_EQ', 'email', 'Services KPI breached', 'sent')"
        " returning id"
    )
    await backend_pool.execute(  # below the severity floor: no run
        "insert into public.alert (kind, severity, ticker, channel, message, status)"
        " values ('thesis_breach', 1, 'AAPL_US_EQ', 'email', 'minor', 'sent')"
    )

    async def scan() -> None:
        status = await run_job(
            backend_pool, "inbox_scan", run.inbox_scan_handler, trigger="schedule"
        )
        assert status == "succeeded"

    await scan()
    keys = {
        r["k"]
        for r in await backend_pool.fetch(
            "select params ->> 'trigger_key' as k from public.job where job_type = 'inbox_agent'"
        )
    }
    assert keys == {f"escalation:{report}", f"alert:{alert}"}
    await scan()  # the runs are still queued: nothing new
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.job where job_type = 'inbox_agent'"
        )
        == 2
    )


@pytest.mark.db
async def test_the_agent_job_stores_one_item_with_checked_statements_and_an_action(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_holding(backend_pool)
    await add_note(backend_pool)
    fake = FakeAgent(
        draft(
            statements=[
                Statement(text="Owner says services margin is the thesis.", source_ids=["S1"]),
                Statement(text="Invented id.", source_ids=["S99"]),
                Statement(text="No citation."),
            ],
            action=ProposedAction(
                kind="research", reason="Check the guidance.", profile="deep-thesis"
            ),
        )
    )
    trigger, item = await make_item(backend_pool, fake)
    body = item["body"]
    assert [s["text"] for s in body["statements"]] == ["Owner says services margin is the thesis."]
    assert body["dropped_statements"] == 2 and body["sources"][0]["id"] == "S1"
    assert item["action"] == {
        "kind": "research",
        "params": {"ticker": "AAPL_US_EQ", "profile": "deep-thesis"},
        "reason": "Check the guidance.",
    }
    item_id = await run.store_item(backend_pool, trigger, item, None)
    assert item_id is not None
    assert await run.store_item(backend_pool, trigger, item, None) is None  # one per trigger
    stored = await service.get_item(backend_pool, item_id)
    assert stored is not None and stored.status == "new" and stored.action_status == "proposed"
    # Nothing was started by proposing it.
    assert not await backend_pool.fetchval(
        "select exists (select 1 from public.job where job_type in ('research', 'earnings_check'))"
    )


@pytest.mark.db
async def test_an_action_is_dropped_unless_it_is_allowed_and_supported(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_holding(backend_pool)
    await add_note(backend_pool)
    cases = {
        "an unknown profile": draft(
            action=ProposedAction(kind="research", reason="r", profile="nope")
        ),
        "no cited statement": draft(
            statements=[Statement(text="Uncited.")],
            action=ProposedAction(kind="earnings_check", reason="r"),
        ),
    }
    for label, d in cases.items():
        _, item = await make_item(backend_pool, FakeAgent(d))
        assert item["action"] is None, label
        assert item["body"]["action_dropped"], label
    # A company the owner neither holds nor has a thesis on.
    other = triggers.Trigger("alert", 1, "ZZZZ_US_EQ", 3, "h", "m", NOW)
    registry = Registry()
    registry.add("fact", "f", "t")
    out = await agent_mod.check_draft(
        backend_pool,
        draft(action=ProposedAction(kind="earnings_check", reason="r")),
        other,
        registry,
    )
    assert out.action is None and "not a holding" in (out.action_dropped or "")


@pytest.mark.db
async def test_nothing_to_do_and_a_missing_agent_still_leave_an_honest_item(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_holding(backend_pool)
    await add_note(backend_pool)
    _, quiet = await make_item(backend_pool, FakeAgent(draft(nothing_to_do=True)))
    assert quiet["status"] == "read"
    _, bare = await make_item(backend_pool, None)
    assert bare["status"] == "new" and bare["action"] is None
    assert "No agent assessment" in bare["body"]["assessment"]
    assert bare["body"]["trigger"]["text"].startswith("Escalation note on AAPL_US_EQ")


@pytest.mark.db
async def test_only_a_confirmation_starts_a_job_and_only_once(backend_pool: asyncpg.Pool) -> None:
    await seed_holding(backend_pool)
    await add_note(backend_pool)
    action = ProposedAction(kind="earnings_check", reason="Re-run the check.")
    trigger, item = await make_item(backend_pool, FakeAgent(draft(action=action)))
    item_id = await run.store_item(backend_pool, trigger, item, None)
    assert item_id is not None

    confirmed = await service.confirm_action(backend_pool, item_id, OWNER)
    assert confirmed.action_status == "confirmed" and confirmed.status == "actioned"
    job = await backend_pool.fetchrow(
        "select job_type, trigger, params from public.job where id = $1", confirmed.action_job_id
    )
    assert job["job_type"] == "earnings_check" and json.loads(job["params"]) == {
        "ticker": "AAPL_US_EQ"
    }
    with pytest.raises(service.ActionError) as again:
        await service.confirm_action(backend_pool, item_id, OWNER)
    assert again.value.conflict
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.job where job_type = 'earnings_check'"
        )
        == 1
    )

    # Dismissing an item with an open action declines it and starts nothing.
    trigger2, item2 = await make_item(backend_pool, FakeAgent(draft(action=action)))
    second = await run.store_item(backend_pool, trigger2, item2, None)
    assert second is not None
    dismissed = await service.set_status(backend_pool, second, "dismissed")
    assert dismissed is not None and dismissed.action_status == "declined"
    with pytest.raises(service.ActionError):
        await service.confirm_action(backend_pool, second, OWNER)
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.job where job_type = 'earnings_check'"
        )
        == 1
    )


@pytest.mark.db
async def test_replaying_past_events_writes_nothing(backend_pool: asyncpg.Pool) -> None:
    await seed_holding(backend_pool)
    await add_note(backend_pool)
    for n in range(3):
        await add_escalation(backend_pool, f"Event {n}")
    action = ProposedAction(kind="earnings_check", reason="r")
    result = await replay.replay(backend_pool, FakeAgent(draft(action=action)), limit=10)
    assert result["events"] == 3 and result["with_cited_statements"] == 3
    assert result["with_proposed_action"] == 3
    assert result["wrote_nothing"] is True
    assert await backend_pool.fetchval("select count(*) from public.inbox_item") == 0


@pytest.mark.db
async def test_notes_are_the_owners_and_the_agent_tool_reads_them(
    backend_pool: asyncpg.Pool,
) -> None:
    from steward.chat.tools import ChatTools

    general = (await service.add_note(backend_pool, service.NoteIn(text="Be terse."))).id
    mine = await add_note(backend_pool)
    other = (
        await service.add_note(backend_pool, service.NoteIn(ticker="MSFT_US_EQ", text="Other."))
    ).id
    registry = Registry()
    tools = ChatTools(backend_pool, registry, None, None)
    out = await tools.call("agent_notes", {"ticker": "AAPL_US_EQ"})
    assert [n["note"] for n in out["notes"]] == ["Be terse.", "Services margin is the thesis."]
    assert all(n["author"] == "owner" for n in out["notes"])
    assert registry.get(out["notes"][0]["source_id"]) is not None
    await service.edit_note(backend_pool, mine, service.NotePatch(active=False))
    again = await tools.call("agent_notes", {"ticker": "AAPL_US_EQ"})
    assert [n["note"] for n in again["notes"]] == ["Be terse."]
    assert await service.delete_note(backend_pool, other) and not await service.delete_note(
        backend_pool, other
    )
    assert general


@pytest.mark.db
async def test_route_stats_group_cost_and_latency_by_route(backend_pool: asyncpg.Pool) -> None:
    async def job(
        job_type: str, params: dict, cost: float | None, seconds: int, ok: bool = True
    ) -> None:
        job_id = await backend_pool.fetchval(
            "insert into public.job (job_type, trigger, status, params, started_at, finished_at)"
            " values ($1, 'manual', $2, $3::jsonb, now() - make_interval(secs => $4), now())"
            " returning id",
            job_type,
            "succeeded" if ok else "failed",
            json.dumps(params),
            seconds,
        )
        await backend_pool.execute(
            "insert into public.job_step (job_id, step_index, name, status, model, cost_usd)"
            " values ($1, 0, 's', 'succeeded', 'm', $2)",
            job_id,
            cost,
        )

    await job("research", {"profile": "deep-agentic"}, 2.0, 100)
    await job("research", {"profile": "deep-agentic"}, 4.0, 200)
    await job("research", {"profile": "deep-thesis"}, 3.0, 50, ok=False)
    await job("chat", {}, None, 5)
    stats = {s.route: s for s in await route_stats(backend_pool, 30)}
    agentic = stats["research:deep-agentic"]
    assert agentic.runs == 2 and agentic.cost_total_usd == 6.0 and agentic.cost_median_usd == 3.0
    assert stats["research:deep-thesis"].failed == 1
    assert stats["chat"].unpriced_runs == 1 and stats["chat"].cost_total_usd == 0.0


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


@pytest.mark.db
async def test_the_inbox_api_lists_confirms_and_takes_feedback(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await seed_holding(backend_pool)
    await add_note(backend_pool)
    action = ProposedAction(kind="earnings_check", reason="Re-run the check.")
    trigger, item = await make_item(backend_pool, FakeAgent(draft(action=action)))
    item_id = await run.store_item(backend_pool, trigger, item, None)

    listed = (await api.get("/inbox")).json()
    assert listed["unread"] == 1 and listed["items"][0]["id"] == item_id
    assert (await api.patch(f"/inbox/{item_id}", json={"status": "read"})).json()[
        "status"
    ] == "read"
    fb = await api.post(
        f"/inbox/{item_id}/feedback", json={"feedback": "not_useful", "note": "Already knew."}
    )
    assert fb.json()["feedback"] == "not_useful"
    ok = await api.post(f"/inbox/{item_id}/confirm")
    assert ok.status_code == 200 and ok.json()["action_status"] == "confirmed"
    assert (await api.post(f"/inbox/{item_id}/confirm")).status_code == 409
    assert (await api.post("/inbox/9999/confirm")).status_code == 404

    from steward.inbox.feedback import export_cases

    cases = await export_cases(backend_pool)
    assert [c["expected"] for c in cases] == ["avoid"] and cases[0]["owner_note"] == "Already knew."

    created = await api.post(
        "/agent-notes", json={"ticker": "AAPL_US_EQ", "text": "Watch the margin."}
    )
    assert created.status_code == 201
    note_id = created.json()["id"]
    patched = await api.patch(f"/agent-notes/{note_id}", json={"active": False})
    assert patched.json()["active"] is False
    assert (await api.delete(f"/agent-notes/{note_id}")).status_code == 204
    assert (await api.get("/ops/routes")).status_code == 200
