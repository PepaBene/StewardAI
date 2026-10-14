"""An owner instruction for an autopilot kind is tested on items the owner settled themselves
before it reaches the judge's prompt: promoted only if the judge agrees with the owner at least as
often, otherwise rejected with the numbers."""

import datetime as dt
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from steward.actions import registry
from steward.autopilot import lessons
from steward.autopilot.judge import JudgeKind, Verdict
from steward.config.jobs import AutopilotKindPolicy, AutopilotSettings
from steward.engine import change, prompt_change

NOW = dt.datetime.now(dt.UTC)
OWNER_SAYS = "defer"


class ToyVerdict(Verdict):
    decision: str


async def _none(*_: Any) -> Any:
    return None


def toy_kind(owner: dict[str, str]) -> JudgeKind:
    async def outcome(pool: Any, ref: str) -> str | None:
        return owner.get(ref)

    return JudgeKind(
        name="listing",
        label="Toy",
        href="/x",
        system="Judge the item.",
        output_model=ToyVerdict,
        actionable=frozenset({"accept", "reject"}),
        load=_none,
        apply=_none,
        is_open=_none,
        outcome=outcome,
    )


def settings() -> AutopilotSettings:
    return AutopilotSettings(
        model="gpt-small",
        default=AutopilotKindPolicy(mode="auto", min_confidence=0.8, sample_rate=0.0),
    )


class Client:
    """Answers `accept` unless the instruction is in the system prompt and `obeys` is set."""

    def __init__(self, obeys: bool) -> None:
        self.obeys = obeys
        self.messages = SimpleNamespace(parse=self._parse)
        self.llm = SimpleNamespace(configured=lambda model: True)

    async def _parse(self, **kw: Any) -> Any:
        followed = self.obeys and "<owner_instructions>" in kw["system"]
        decision = "reject" if followed else "accept"
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            parsed_output=ToyVerdict(decision=decision, confidence=0.95, reasons="r"),
        )


async def seed(pool: asyncpg.Pool, n: int) -> dict[str, str]:
    """`n` decided items; the owner settled them all as `reject`."""
    owner: dict[str, str] = {}
    for i in range(n):
        ref = f"listing:{i}"
        owner[ref] = "reject"
        await pool.execute(
            "insert into public.auto_decision (kind, subject_ref, decision, confidence, decided_by,"
            " summary, evidence, status) values ('listing', $1, 'accept', 0.9, 'model:x', 's',"
            " jsonb_build_object('prompt', 'item ' || $1::text), 'applied')",
            ref,
        )
    return owner


async def lessons_none(kind: JudgeKind) -> str:
    return ""


async def run_once(pool: asyncpg.Pool, owner: dict[str, str], client: Client) -> dict[str, Any]:
    kind = toy_kind(owner)
    return await prompt_change.settle_one(
        pool,
        client,
        {"listing": kind},
        settings(),
        {"calls": 100},
        lessons_none,  # type: ignore[arg-type]
    )


@pytest.mark.db
async def test_an_instruction_the_judge_follows_toward_the_owners_calls_is_promoted(
    backend_pool: asyncpg.Pool,
) -> None:
    owner = await seed(backend_pool, 10)
    await prompt_change.propose(backend_pool, "listing", "Reject fund listings.", "teaching:a1")
    out = await run_once(backend_pool, owner, Client(obeys=True))
    assert out["promoted"] == 1
    assert await prompt_change.active_instructions(backend_pool, "listing") == [
        "Reject fund listings."
    ]


@pytest.mark.db
async def test_an_instruction_that_moves_the_judge_away_from_the_owners_calls_is_rejected(
    backend_pool: asyncpg.Pool,
) -> None:
    owner = {k: "accept" for k in await seed(backend_pool, 10)}  # the owner accepted them all
    await prompt_change.propose(backend_pool, "listing", "Reject fund listings.", "teaching:a2")
    out = await run_once(backend_pool, owner, Client(obeys=True))  # the judge now rejects them
    assert out["rejected"] == 1
    assert await prompt_change.active_instructions(backend_pool, "listing") == []
    verdict = await backend_pool.fetchval("select verdict from public.engine_change")
    assert "more wrong decisions" in verdict or "golden" in verdict


@pytest.mark.db
async def test_with_too_few_settled_items_nothing_is_applied_and_the_owner_is_told(
    backend_pool: asyncpg.Pool,
) -> None:
    owner = await seed(backend_pool, 3)
    await prompt_change.propose(backend_pool, "listing", "Reject fund listings.", "teaching:a3")
    out = await run_once(backend_pool, owner, Client(obeys=True))
    assert out["rejected"] == 1
    assert await prompt_change.active_instructions(backend_pool, "listing") == []
    assert await backend_pool.fetchval(
        "select count(*) from public.ops_task where kind = $1", prompt_change.KIND
    )


@pytest.mark.db
async def test_a_promoted_instruction_reaches_the_prompt_until_it_is_withdrawn(
    backend_pool: asyncpg.Pool,
) -> None:
    owner = await seed(backend_pool, 10)
    await prompt_change.propose(backend_pool, "listing", "Reject fund listings.", "teaching:a4")
    await run_once(backend_pool, owner, Client(obeys=True))
    text = await lessons.lessons_for(backend_pool, toy_kind(owner), NOW)
    assert "Reject fund listings." in text and "<owner_instructions>" in text
    assert await prompt_change.withdraw(backend_pool, "teaching:a4") == 1
    assert await lessons.lessons_for(backend_pool, toy_kind(owner), NOW) == ""


@pytest.mark.db
async def test_teaching_a_kind_proposes_a_change_and_an_unknown_kind_is_refused(
    backend_pool: asyncpg.Pool,
) -> None:
    row = await registry.propose(
        backend_pool,
        "teach",
        {"area": "autopilot:listing", "text": "Never accept a fund listing."},
        via="chat",
    )
    assert "tested" in row.summary
    await registry.confirm(backend_pool, row.id)
    [c] = await backend_pool.fetch("select target, status from public.engine_change")
    assert (c["target"], c["status"]) == ("autopilot:listing:prompt", "shadow")
    with pytest.raises(registry.ActionError):
        await registry.propose(
            backend_pool,
            "teach",
            {"area": "autopilot:nonsense", "text": "Do something."},
            via="chat",
        )
    assert change.ensure_changeable(prompt_change.target_for("listing")) is None
