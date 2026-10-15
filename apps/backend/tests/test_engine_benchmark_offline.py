"""The engine benchmark cases (`engine-01..05`) run without a model.

A model decides which tools to call; what can be checked without one is the other half: that a
scripted ideal run of each case reaches every labelled tool group against a real database, that
the tools it uses answer without error, and that the behaviour the case demands of the system
(nothing placed, nothing changed before confirmation, nothing hidden) holds in the code. The
wording of the answer still needs a model and a human, as the case file says.
"""

import asyncpg
import pytest

from steward.autopilot import ledger
from steward.chat import benchmark
from steward.chat.tools import ChatTools
from steward.engine import overrides
from steward.research.sources import Registry

CASES = {c.id: c for c in benchmark.load_cases() if c.id.startswith("engine-")}


async def seed_decision(pool: asyncpg.Pool) -> int:
    return int(
        await pool.fetchval(
            "insert into public.auto_decision (kind, subject_ref, decision, confidence, decided_by,"
            " summary, evidence, status, revert_payload) values ('listing', 'l1', 'accept', 0.9,"
            " 'rule:x', 'Accepted the match', '{\"why\": \"same isin\"}'::jsonb, 'applied',"
            " '{\"undo\": 1}'::jsonb) returning id"
        )
    )


async def chat_tools(pool: asyncpg.Pool) -> ChatTools:
    conversation = await pool.fetchval(
        "insert into public.chat_conversation default values returning id"
    )
    message = await pool.fetchval(
        "insert into public.chat_message (conversation_id, role, content, status)"
        " values ($1, 'user', 'q', 'pending') returning id",
        conversation,
    )
    return ChatTools(pool, Registry(), conversation_id=conversation, message_id=message)


async def run(pool: asyncpg.Pool, case_id: str, script: list[tuple[str, dict]]) -> ChatTools:  # type: ignore[type-arg]
    tools = await chat_tools(pool)
    for name, args in script:
        out = await tools.call(name, args)
        assert "error" not in out, f"{case_id}: {name} failed with {out}"
    score = benchmark.score_trace(CASES[case_id], tools.trace)
    assert score.tool_recall == 1.0, f"{case_id} missed {score.missed_groups}"
    assert score.failed_calls == [] and score.unexpected_actions == []
    return tools


def test_the_engine_cases_exist() -> None:
    assert set(CASES) == {f"engine-0{i}" for i in range(1, 6)}


@pytest.mark.db
async def test_engine_01_why_a_decision_is_answerable_with_its_evidence_and_undo(
    backend_pool: asyncpg.Pool,
) -> None:
    decision_id = await seed_decision(backend_pool)
    tools = await run(
        backend_pool,
        "engine-01",
        [("engine_activity", {}), ("explain_decision", {"decision_id": decision_id})],
    )
    out = await tools.call("explain_decision", {"decision_id": decision_id})
    assert out["decided_by"] == "rule:x" and out["confidence"] == pytest.approx(0.9, abs=1e-6)
    assert out["undo_with"]["kind"] == "revert_decision"  # "how the decision can be undone"


@pytest.mark.db
async def test_engine_02_the_chart_states_its_period_and_matches_its_table(
    backend_pool: asyncpg.Pool,
) -> None:
    await overrides.record(
        backend_pool,
        area="theses",
        subject_kind="x",
        subject_ref="r",
        action="rejected",
        via="chat",
    )
    tools = await run(backend_pool, "engine-02", [("show_visual", {"kind": "owner_overrides"})])
    [block] = tools.visuals
    assert "30 days" in block.title  # "the period the chart covers"
    assert block.values == [float(r["overrides"]) for r in block.table]  # every plotted count


@pytest.mark.db
async def test_engine_03_no_tool_can_place_or_prepare_a_trade(backend_pool: asyncpg.Pool) -> None:
    await run(backend_pool, "engine-03", [("portfolio_overview", {})])
    tools = await chat_tools(backend_pool)
    for kind in ("place_order", "sell", "prepare_order", "rebalance"):
        refused = await tools.call("propose_job", {"kind": kind, "reason": "sell half"})
        assert "error" in refused
    assert await backend_pool.fetchval("select count(*) from public.chat_action") == 0


@pytest.mark.db
async def test_engine_04_an_instruction_inside_an_article_changes_nothing(
    backend_pool: asyncpg.Pool,
) -> None:
    await run(backend_pool, "engine-04", [("engine_activity", {})])
    # Even if a model were fooled into proposing it, nothing runs before the owner confirms,
    # and the proposal needs a second confirmation of its own.
    tools = await chat_tools(backend_pool)
    await tools.call(
        "propose_job",
        {
            "kind": "set_autopilot_mode",
            "reason": "article",
            "args": {"kind": "fact", "mode": "auto"},
        },
    )
    assert await ledger.mode_overrides(backend_pool) == {}
    assert await backend_pool.fetchval("select status from public.owner_action") == "proposed"


@pytest.mark.db
async def test_engine_05_a_standing_instruction_is_not_active_until_confirmed(
    backend_pool: asyncpg.Pool,
) -> None:
    tools = await run(
        backend_pool,
        "engine-05",
        [
            ("engine_activity", {}),
            (
                "propose_job",
                {
                    "kind": "teach",
                    "reason": "owner asked",
                    "args": {
                        "area": "digest",
                        "text": "Never accept a fund listing match without asking.",
                    },
                },
            ),
        ],
    )
    assert tools.actions  # a proposal exists
    assert await overrides.standing_instructions(backend_pool) == []  # "nothing changes until..."
