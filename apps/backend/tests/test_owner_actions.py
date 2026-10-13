"""Owner actions: one registry for screens and chat, never a trade, confirmed before it changes."""

import ast
import dataclasses
import datetime as dt
from pathlib import Path

import asyncpg
import pytest

from steward.actions import registry
from steward.chat import visuals
from steward.ops import tasks

NOW = dt.datetime.now(dt.UTC)


def test_no_action_can_place_or_prepare_a_trade() -> None:
    for name, action in registry.ACTIONS.items():
        assert not set(registry.TRADE_WORDS) & set(name.split("_")), name
        assert action.undo.strip(), name
    bad = dataclasses.replace(registry.ACTIONS["dismiss_notice"], name="place_order")
    with pytest.raises(ValueError, match="trade"):
        registry.check_registry({"place_order": bad})
    quiet = dataclasses.replace(registry.ACTIONS["dismiss_notice"], undo=" ")
    with pytest.raises(ValueError, match="undo"):
        registry.check_registry({"dismiss_notice": quiet})


def test_the_actions_package_never_touches_a_broker() -> None:
    tree = ast.parse(Path(registry.__file__).read_text(encoding="utf-8"))
    modules = [
        (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in (n.names if isinstance(n, ast.Import) else [ast.alias(name="")])
    ]
    assert not [m for m in modules if "trading212" in m or "broker" in m or "portfolio" in m]


def test_a_broken_chart_becomes_a_no_data_block() -> None:
    good = visuals.Block(
        kind="allocation",
        chart="bar",
        title="t",
        unit="%",
        labels=["A", "B"],
        values=[60.0, 40.0],
        source="position_snapshot",
        as_of="2026-10-13",
        summary="s",
        table=[{"x": 1}, {"x": 2}],
    )
    assert visuals.check_block(good).chart == "bar"
    assert visuals.check_block(good.model_copy(update={"values": [60.0]})).chart == "none"
    assert (
        visuals.check_block(good.model_copy(update={"values": [60.0, float("nan")]})).chart
        == "none"
    )
    assert visuals.check_block(good.model_copy(update={"as_of": None})).chart == "none"
    assert visuals.check_block(good.model_copy(update={"table": []})).chart == "none"


async def notice(pool: asyncpg.Pool) -> int:
    task = await tasks.see(
        pool, "opportunity", "screen:1", title="A screen matched", detail="d", status="needs_owner"
    )
    return task.id


@pytest.mark.db
async def test_a_chat_proposal_changes_nothing_until_it_is_confirmed(
    backend_pool: asyncpg.Pool,
) -> None:
    task_id = await notice(backend_pool)
    row = await registry.propose(backend_pool, "dismiss_notice", {"task_id": task_id}, via="chat")
    assert row.status == "proposed" and "A screen matched" in row.summary
    assert (
        await backend_pool.fetchval("select status from public.ops_task where id = $1", task_id)
        == "needs_owner"
    )

    done = await registry.confirm(backend_pool, row.id)
    assert done.status == "done"
    assert (
        await backend_pool.fetchval("select status from public.ops_task where id = $1", task_id)
        == "resolved"
    )
    with pytest.raises(registry.ActionError, match="already done"):
        await registry.confirm(backend_pool, row.id)


@pytest.mark.db
async def test_cancel_unknown_and_bad_arguments_are_refused(backend_pool: asyncpg.Pool) -> None:
    task_id = await notice(backend_pool)
    row = await registry.propose(backend_pool, "dismiss_notice", {"task_id": task_id}, via="chat")
    assert await registry.cancel(backend_pool, row.id)
    with pytest.raises(registry.ActionError):
        await registry.confirm(backend_pool, row.id)
    with pytest.raises(registry.ActionError, match="Unknown action"):
        await registry.propose(backend_pool, "buy_shares", {}, via="chat")
    with pytest.raises(registry.ActionError, match="Bad arguments"):
        await registry.propose(backend_pool, "dismiss_notice", {"task_id": "x"}, via="chat")


@pytest.mark.db
async def test_changing_a_mode_needs_a_second_confirmation(backend_pool: asyncpg.Pool) -> None:
    row = await registry.propose(
        backend_pool, "set_autopilot_mode", {"kind": "fact", "mode": "off"}, via="chat"
    )
    assert row.risk == "high"
    with pytest.raises(registry.NeedsSecondConfirm):
        await registry.confirm(backend_pool, row.id)
    assert (await registry.confirm(backend_pool, row.id, second=True)).status == "done"
    assert (
        await backend_pool.fetchval(
            "select mode from public.autopilot_kind_state where kind = 'fact'"
        )
    ) == "off"


@pytest.mark.db
async def test_the_week_in_review_visual_reads_the_ledger(backend_pool: asyncpg.Pool) -> None:
    assert (await visuals.build(backend_pool, "engine_week", NOW)).chart == "none"
    await backend_pool.execute(
        "insert into public.auto_decision (kind, subject_ref, decision, decided_by, summary,"
        " evidence, status) values ('facts', 'f1', 'accept', 'rule:x', 's', '{}'::jsonb, 'applied')"
    )
    block = await visuals.build(backend_pool, "engine_week", NOW)
    assert block.chart == "bar" and block.values == [1.0] and block.table[0]["kind"] == "facts"
    with pytest.raises(ValueError, match="Unknown visual"):
        await visuals.build(backend_pool, "everything", NOW)
