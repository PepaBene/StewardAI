"""More inbox triggers (breach, verdict), the missed-events list and run button, note history
and the suggested note, and the retrieval rerank and support check."""

import datetime as dt
import json

import asyncpg
import pytest

from steward.ai import Usage
from steward.inbox import run, service, triggers
from steward.inbox.agent import AgentRequest, AgentResult, InboxDraft, Statement
from steward.llm.types import Completion, Request, normalized_usage
from steward.retrieval.eval import Relevant, supported
from steward.retrieval.rerank import Ranking, apply_order, rerank
from steward.retrieval.search import Hit
from tests.test_inbox import SETTINGS, add_escalation

NOW = dt.datetime(2026, 10, 11, 12, 0, tzinfo=dt.UTC)


async def add_breach(pool: asyncpg.Pool, key: str, severity: int = 2) -> int:
    return int(
        await pool.fetchval(
            "insert into public.portfolio_breach (key, kind, ticker, severity, detail, observed,"
            " limit_value) values ($1, 'hard_limit', 'AAPL_US_EQ', $2, 'Position is 14% of the"
            " portfolio', 14, 10) returning id",
            key,
            severity,
        )
    )


async def add_verdict(pool: asyncpg.Pool, status: str = "breach") -> int:
    version = await pool.fetchval(
        "select id from public.config_version where domain = 'theses' and active"
    ) or await pool.fetchval(
        "insert into public.config_version (domain, body, schema_version, active)"
        " values ('theses', '{}'::jsonb, 1, true) returning id"
    )
    return int(
        await pool.fetchval(
            "insert into public.verdict (ticker, kpi_id, metric, basis, kpi_period, status,"
            " observed_value, operator, threshold, threshold_unit, rationale, config_version_id)"
            " values ('AAPL_US_EQ', 'services', 'services_growth', 'reported', 'quarter', $1,"
            " 8.5, '>=', 10, '%', 'growth 8.5% is below 10%', $2) returning id",
            status,
            version,
        )
    )


@pytest.mark.db
async def test_an_open_breach_and_a_breach_verdict_are_triggers_a_cleared_or_passing_one_is_not(
    backend_pool: asyncpg.Pool,
) -> None:
    open_breach = await add_breach(backend_pool, "risk:var")
    cleared = await add_breach(backend_pool, "position:AAPL_US_EQ")
    await backend_pool.execute(
        "update public.portfolio_breach set cleared_at = now() where id = $1", cleared
    )
    low = await add_breach(backend_pool, "band:cash", severity=1)
    verdict = await add_verdict(backend_pool)
    await add_verdict(backend_pool, status="pass")
    found = {t.key for t in await triggers.pending(backend_pool, 7, 20, 2)}
    assert found == {f"breach:{open_breach}", f"verdict:{verdict}"}
    assert f"breach:{low}" not in found
    breach = await triggers.load(backend_pool, "breach", open_breach)
    assert breach is not None and "risk limit" in breach.material and breach.severity == 2
    kpi = await triggers.load(backend_pool, "verdict", verdict)
    assert kpi is not None and "services" in kpi.material and "8.5" in kpi.material


@pytest.mark.db
async def test_a_disclosure_is_dated_by_when_it_was_filed_not_when_it_was_ingested(
    backend_pool: asyncpg.Pool,
) -> None:
    async def add(key: str, known_at: dt.datetime) -> int:
        return int(
            await backend_pool.fetchval(
                "insert into public.disclosure_alert (kind, ticker, dedupe_key, headline, known_at)"
                " values ('insider_trade', 'VRT_US_EQ', $1, 'Director bought shares', $2)"
                " returning id",
                key,
                known_at,
            )
        )

    now = dt.datetime.now(dt.UTC)
    fresh = await add("fresh", now - dt.timedelta(days=1))
    await add("old", dt.datetime(2022, 3, 1, tzinfo=dt.UTC))  # ingested today, filed in 2022
    found = [t.key for t in await triggers.pending(backend_pool, 7, 20, 2)]
    assert found == [f"disclosure:{fresh}"]
    assert (
        await triggers.missed_count(backend_pool, 7, 2) == 0
    )  # the 2022 filing is past the backlog


@pytest.mark.db
async def test_a_breach_verdict_with_a_thesis_alert_is_not_a_second_trigger(
    backend_pool: asyncpg.Pool,
) -> None:
    await add_verdict(backend_pool)
    alert = await backend_pool.fetchval(
        "insert into public.alert (kind, severity, ticker, channel, message, status)"
        " values ('thesis_breach', 2, 'AAPL_US_EQ', 'telegram', 'KPI breached', 'sent')"
        " returning id"
    )
    assert [t.key for t in await triggers.pending(backend_pool, 7, 20, 2)] == [f"alert:{alert}"]


@pytest.mark.db
async def test_missed_events_are_listed_and_a_run_is_queued_once(
    backend_pool: asyncpg.Pool,
) -> None:
    old = await add_escalation(backend_pool, "Old event")
    await backend_pool.execute(
        "update public.report set created_at = now() - interval '20 days' where id = $1", old
    )
    [event] = await triggers.missed(backend_pool, 7, 2)
    assert event.key == f"escalation:{old}"
    first = await run.queue_runs(backend_pool, [event], "manual")
    again = await run.queue_runs(backend_pool, [event], "manual")
    assert first == [event.key] and again == []
    jobs = await backend_pool.fetch(
        "select trigger, params from public.job where job_type = 'inbox_agent'"
    )
    assert len(jobs) == 1 and jobs[0]["trigger"] == "manual"
    assert await triggers.missed_count(backend_pool, 7, 2) == 0  # a run is queued for it


@pytest.mark.db
async def test_note_history_survives_edit_and_delete(backend_pool: asyncpg.Pool) -> None:
    note = await service.add_note(backend_pool, service.NoteIn(text="First."))
    await service.edit_note(backend_pool, note.id, service.NotePatch(text="Second."))
    await service.edit_note(backend_pool, note.id, service.NotePatch(active=False))
    assert await service.delete_note(backend_pool, note.id)
    history = await service.note_history(backend_pool, note.id)
    assert [(h.change, h.text, h.active) for h in history] == [
        ("deleted", "Second.", False),
        ("edited", "Second.", False),
        ("edited", "Second.", True),
        ("created", "First.", True),
    ]


class NoteAgent:
    async def run(self, request: AgentRequest) -> AgentResult:
        await request.caller("agent_notes", {})
        draft = InboxDraft(
            headline="Guidance cut",
            statements=[Statement(text="Cut.", source_ids=["S1"])],
            suggested_note="Services margin is the number to watch each quarter.",
        )
        return AgentResult(draft, Usage(model="fake"), steps=1)


@pytest.mark.db
async def test_a_suggested_note_is_saved_only_when_the_owner_accepts_it(
    backend_pool: asyncpg.Pool,
) -> None:
    await service.add_note(backend_pool, service.NoteIn(text="Seed so S1 exists."))
    report = await add_escalation(backend_pool)
    trigger = await triggers.load(backend_pool, "escalation", report)
    assert trigger is not None
    built = await run.build_item(backend_pool, NoteAgent(), trigger, SETTINGS, NOW, None)  # type: ignore[arg-type]
    assert built["body"]["suggested_note"] and built["body"]["note_status"] == "suggested"
    item_id = await run.store_item(backend_pool, trigger, built, None)
    assert item_id is not None
    before = len(await service.list_notes(backend_pool))
    assert len(await service.list_notes(backend_pool)) == before  # the run itself saved nothing
    item, note = await service.decide_suggested_note(backend_pool, item_id, True)
    assert note is not None and note.ticker == "AAPL_US_EQ" and "Services margin" in note.text
    assert item.body["note_status"] == "accepted"
    assert len(await service.list_notes(backend_pool)) == before + 1
    with pytest.raises(service.ActionError):
        await service.decide_suggested_note(backend_pool, item_id, True)  # only once


@pytest.mark.db
async def test_a_confirmed_action_carries_the_item_it_came_from(
    backend_pool: asyncpg.Pool,
) -> None:
    report = await add_escalation(backend_pool)
    await backend_pool.execute(
        "insert into public.inbox_item (trigger_key, trigger_kind, trigger_ref, ticker, headline,"
        " body, proposed_action, action_status) values ($1, 'escalation', $2, 'AAPL_US_EQ', 'h',"
        " '{}'::jsonb, $3::jsonb, 'proposed')",
        f"escalation:{report}",
        report,
        json.dumps({"kind": "research", "params": {"ticker": "AAPL_US_EQ"}, "reason": "r"}),
    )
    item_id = await backend_pool.fetchval("select id from public.inbox_item")
    done = await service.confirm_action(
        backend_pool, item_id, "7f0c3d4e-0000-4000-8000-000000000001"
    )
    params = await backend_pool.fetchval(
        "select params from public.job where id = $1", done.action_job_id
    )
    assert json.loads(params)["inbox_item_id"] == item_id


# ----------------------------------------------------------------------- retrieval


def hit(kind: str, ref: str, text: str) -> Hit:
    return Hit(
        chunk_id=hash((kind, ref, text)) % 10_000,
        kind=kind,
        ref=ref,
        title="t",
        section_path=None,
        text=text,
        context=None,
        tickers=(),
        as_of=None,
        source_url=None,
        app_path=None,
        tier=None,
        score=1.0,
    )


def test_apply_order_ignores_bad_numbers_and_keeps_the_unnamed_in_place() -> None:
    hits = [hit("news", str(i), f"p{i}") for i in range(1, 5)]
    out = apply_order(hits, [3, 3, 9, 1])
    assert [h.ref for h in out] == ["3", "1", "2", "4"]


class ScriptedLLM:
    def __init__(self, parsed: Ranking | None, fail: bool = False) -> None:
        self.parsed, self.fail = parsed, fail

    async def complete(self, request: Request) -> Completion:
        from steward.llm.types import LLMError

        if self.fail:
            raise LLMError("down")
        return Completion(
            model="m",
            provider="openai",
            text="",
            parsed=self.parsed,
            stop_reason="end",
            usage=normalized_usage(input_tokens=1, output_tokens=1),
        )


async def test_rerank_reorders_and_a_failed_call_leaves_the_order() -> None:
    hits = [hit("news", str(i), f"passage {i}") for i in range(1, 4)]
    out, done = await rerank(ScriptedLLM(Ranking(order=[2, 1, 3])), "m", "q", hits)  # type: ignore[arg-type]
    assert done and [h.ref for h in out] == ["2", "1", "3"]
    same, done = await rerank(ScriptedLLM(None, fail=True), "m", "q", hits)  # type: ignore[arg-type]
    assert not done and same == hits


def test_support_needs_every_phrase_in_the_labelled_documents_top_k() -> None:
    hits = [
        hit("filing", "a", "We depend on one supplier for battery cells."),
        hit("news", "z", "Unrelated: the CEO resigned."),
        hit("filing", "a", "Revenue grew 12% in the quarter."),
    ]
    rel = [Relevant(kind="filing", ref="a")]
    assert supported(hits, rel, ["one supplier", "grew 12%"], 3) is True
    assert supported(hits, rel, ["one supplier", "grew 12%"], 1) is False  # second is past k
    assert supported(hits, rel, ["resigned"], 3) is False  # right words, wrong document
    assert supported(hits, rel, [], 3) is None
