"""The thesis-proposal judge: what it may touch, apply and revert through config versions, and
the owner's own accept as the outcome."""

import json

import asyncpg
import pytest

from steward.autopilot.judge import ApplyError
from steward.autopilot.kinds import thesis as tk
from steward.config.store import load_active, save_config
from steward.config.thesis import ThesesConfig, ThesisConfig
from steward.thesis.proposal import FieldChange, ProposedLine, ThesisProposal

OWNER = "00000000-0000-0000-0000-000000000001"


def test_kind_is_fenced() -> None:
    assert tk.KIND.promotable is False
    assert tk.KIND.revert is not None
    assert tk.KIND.actionable == frozenset({"accept"})
    assert "why_owned" not in tk.AUTOFIELDS


def proposal(base: int | None, fields: tuple[str, ...]) -> ThesisProposal:
    changes = [
        FieldChange(
            field=f,
            kind="add",
            before=[],
            after=[ProposedLine(text=f"new {f}", evidence_ids=["s1"])],
        )
        for f in fields
    ]
    return ThesisProposal(
        mode="propose_update",
        ticker="AAPL",
        base_version_id=base,
        has_thesis=True,
        changes=changes,
        unchanged=[],
        left_to_owner=[],
    )


async def seed(pool: asyncpg.Pool, fields: tuple[str, ...]) -> tuple[int, int]:
    async with pool.acquire() as conn:
        saved = await save_config(
            conn,
            "theses",
            ThesesConfig(theses=[ThesisConfig(ticker="AAPL", why_owned="services growth")]),
            created_by=None,
        )
    body = {"thesis_proposal": json.loads(proposal(saved.id, fields).model_dump_json())}
    report = await pool.fetchval(
        "insert into public.report (type, ticker, title, body, body_md)"
        " values ('research', 'AAPL', 't', $1::jsonb, '') returning id",
        json.dumps(body),
    )
    return int(report), saved.id


@pytest.mark.db
async def test_accept_applies_auto_fields_and_reverts(backend_pool: asyncpg.Pool) -> None:
    report, base = await seed(backend_pool, ("exit_conditions", "pre_mortem", "why_owned"))
    cands = await tk.KIND.load(backend_pool, 10)
    assert [c.ref for c in cands] == [f"thesis_proposal:{report}"]
    assert cands[0].evidence["fields"] == ["exit_conditions", "pre_mortem"]

    payload = await tk.KIND.apply(backend_pool, cands[0], "accept", "ok")
    assert payload is not None and payload["prior_version_id"] == base
    stored = await load_active(backend_pool, "theses", ThesesConfig)
    assert stored is not None
    thesis = stored.body.theses[0]
    assert thesis.why_owned == "services growth"  # the owner's reason is untouched
    assert thesis.exit_conditions == ["new exit_conditions"]
    assert await tk.KIND.is_open(backend_pool, [cands[0].ref]) == set()
    assert await tk.thesis_outcome(backend_pool, cands[0].ref) is None  # not the owner's call

    await tk.thesis_revert(backend_pool, payload)
    reverted = await load_active(backend_pool, "theses", ThesesConfig)
    assert reverted is not None and reverted.body.theses[0].exit_conditions == []


@pytest.mark.db
async def test_revert_is_refused_after_a_later_save(backend_pool: asyncpg.Pool) -> None:
    await seed(backend_pool, ("exit_conditions",))
    cand = (await tk.KIND.load(backend_pool, 10))[0]
    payload = await tk.KIND.apply(backend_pool, cand, "accept", "ok")
    assert payload is not None
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "theses",
            ThesesConfig(theses=[ThesisConfig(ticker="AAPL", why_owned="changed")]),
            created_by=None,
        )
    with pytest.raises(ApplyError):
        await tk.thesis_revert(backend_pool, payload)


@pytest.mark.db
async def test_only_why_owned_is_left_to_the_owner(backend_pool: asyncpg.Pool) -> None:
    await seed(backend_pool, ("why_owned",))
    assert await tk.KIND.load(backend_pool, 10) == []
