"""A standing instruction for an autopilot kind, tested before it acts (autonomous engine plan, L5).

When the owner teaches the engine something about one autopilot kind ("never accept a fund
listing match without asking"), the words are not put straight into the judge's prompt: that
judge can apply decisions on its own. They become a `prompt` change that goes through the same
steps as any self-change:

1. `propose` stores the instruction, with gates fixed in code.
2. The golden set is the kind's decisions the owner settled themselves (`kind.outcome`), so the
   right answer is the owner's own. The judge model runs over up to `GOLDEN_MAX` of them twice,
   once with the current prompt and once with the instruction added.
3. Pure code compares agreement with the owner's calls. The instruction is promoted only if the
   judge agrees with the owner at least as often, escalates no more than before, and enough items
   could be tested. Otherwise it is rejected and the owner is told the numbers.
4. A promoted instruction is part of the kind's prompt until the change is rolled back from the
   engine ledger (status decides, nothing else is stored).

It costs model calls, so one change is tested per run and only while the run still has calls left.
"""

import json
from dataclasses import dataclass
from typing import Any

import asyncpg

from steward.ai import Usage
from steward.autopilot.judge import Candidate, JudgeKind, build_call, read_response
from steward.config.jobs import AutopilotSettings
from steward.engine import change
from steward.engine.change import Gates, Metrics
from steward.llm.routing import RoutingClient, parse_many
from steward.ops import tasks

KIND = "prompt_change"
TARGET_SUFFIX = ":prompt"
GOLDEN_MAX = 20
GOLDEN_MIN = 8
# Agreement with the owner may not drop; fewer than GOLDEN_MIN settled items cannot judge it.
GATES = Gates(
    min_n=GOLDEN_MIN,
    min_reviewed=GOLDEN_MIN,
    max_wrong_rate_increase=0.0,
    max_cost_increase=10.0,
    require_golden=True,
)


def target_for(kind: str) -> str:
    return f"autopilot:{kind}{TARGET_SUFFIX}"


def instruction_block(texts: list[str]) -> str:
    """The text added to the judge's system prompt. The owner's words are quoted as data from
    the owner, never as anything that can change the judge's output format."""
    if not texts:
        return ""
    lines = ["<owner_instructions>", "The following apply to this queue (tested before use):"]
    lines += [f"- {t}" for t in texts]
    lines.append("</owner_instructions>")
    return "\n".join(lines)


async def active_instructions(pool: asyncpg.Pool, kind: str) -> list[str]:
    """The promoted, not rolled back, instructions for a kind, oldest first."""
    rows = await pool.fetch(
        "select after from public.engine_change where target = $1 and status = 'promoted'"
        " order by id",
        target_for(kind),
    )
    texts: list[str] = []
    for r in rows:
        after = json.loads(r["after"]) if isinstance(r["after"], str) else r["after"]
        text = (after or {}).get("addendum")
        if isinstance(text, str) and text:
            texts.append(text)
    return texts


async def propose(pool: asyncpg.Pool, kind: str, text: str, ref: str) -> change.Change:
    return await change.propose(
        pool,
        target_for(kind),
        "prompt",
        f"Add to the {kind} judge's prompt: {text[:200]}",
        "agreement with your own calls",
        before={"addendum": None},
        after={"addendum": text, "ref": ref},
        gates=GATES,
    )


@dataclass(frozen=True)
class Golden:
    candidate: Candidate
    expected: str


async def golden_set(pool: asyncpg.Pool, kind: JudgeKind, limit: int = GOLDEN_MAX) -> list[Golden]:
    """The kind's recent items the owner settled themselves: what the judge saw, and what they
    chose. Empty for a kind with no way to read the owner's call."""
    if kind.outcome is None:
        return []
    rows = await pool.fetch(
        "select distinct on (subject_ref) subject_ref, ticker, evidence from public.auto_decision"
        " where kind = $1 and evidence ? 'prompt' order by subject_ref, decided_at desc",
        kind.name,
    )
    out: list[Golden] = []
    for r in rows:
        expected = await kind.outcome(pool, r["subject_ref"])
        if expected is None:
            continue
        evidence = json.loads(r["evidence"]) if isinstance(r["evidence"], str) else r["evidence"]
        out.append(
            Golden(
                Candidate(r["subject_ref"], r["ticker"], str(evidence["prompt"]), evidence),
                expected,
            )
        )
        if len(out) >= limit:
            break
    return out


async def _agreement(
    client: RoutingClient,
    kind: JudgeKind,
    settings: AutopilotSettings,
    items: list[Golden],
    lessons: str,
    usage: Usage,
) -> tuple[int, int]:
    """(items the judge agreed with the owner on, items it would not have settled itself)."""
    model = settings.policy(kind.name).model or settings.model
    calls = [build_call(kind, g.candidate, model, settings, lessons) for g in items]
    answers = await parse_many(client, calls)
    agreed = unsure = 0
    policy = settings.policy(kind.name)
    for g, answer in zip(items, answers, strict=True):
        settled = read_response(answer, model, usage)
        if settled is None or not settled.settles(policy.min_confidence, kind.actionable):
            unsure += 1
        elif settled.decision == g.expected:
            agreed += 1
    return agreed, unsure


def metrics(n: int, agreed: int, unsure: int) -> Metrics:
    """Disagreement with the owner is a wrong decision; an item the judge left to the owner is
    neither. `reviewed` is every settled answer, so the gates count the items that could be
    compared."""
    answered = n - unsure
    return Metrics(
        n=n,
        reviewed=answered,
        wrong=answered - agreed,
        escalated=unsure,
        verifier_failures=0,
        cost=0.0,
    )


async def settle_one(
    pool: asyncpg.Pool,
    client: RoutingClient,
    kinds: dict[str, JudgeKind],
    settings: AutopilotSettings,
    budget: dict[str, int],
    lessons_for: Any,
) -> dict[str, Any]:
    """Test the oldest waiting instruction, if the run has calls for it. `lessons_for` builds the
    kind's current prompt addition (lessons and promoted instructions)."""
    rows = await pool.fetch(
        "select " + change.COLUMNS + " from public.engine_change"  # noqa: S608
        " where status = 'shadow' and change_kind = 'prompt' and target like 'autopilot:%'"
        " order by id limit 1"
    )
    if not rows:
        return {"tested": 0}
    c = change.from_row(rows[0])
    change.ensure_changeable(c.target)
    kind_name = c.target.removeprefix("autopilot:").removesuffix(TARGET_SUFFIX)
    kind = kinds.get(kind_name)
    text = (c.after or {}).get("addendum")
    if kind is None or not isinstance(text, str):
        await pool.execute(
            "update public.engine_change set status = 'rejected', verdict = $2, decided_at = now()"
            " where id = $1 and status = 'shadow'",
            c.id,
            "the kind or the instruction no longer exists",
        )
        return {"tested": 0, "rejected": 1}
    items = await golden_set(pool, kind)
    needed = 2 * len(items)
    if len(items) < GOLDEN_MIN or budget["calls"] < needed:
        if len(items) < GOLDEN_MIN:
            await _reject(pool, c, kind_name, f"only {len(items)} items you settled yourself")
            return {"tested": 0, "rejected": 1}
        return {"tested": 0, "waiting": "not enough calls left in this run"}
    budget["calls"] -= needed
    usage = Usage(model=settings.policy(kind_name).model or settings.model)
    current = await lessons_for(kind)
    base = await _agreement(client, kind, settings, items, current, usage)
    with_it = await _agreement(
        client,
        kind,
        settings,
        items,
        "\n\n".join(b for b in (current, instruction_block([text])) if b),
        usage,
    )
    baseline, candidate = metrics(len(items), *base), metrics(len(items), *with_it)
    await change.record_results(pool, c.id, baseline, candidate)
    settled = await change.settle(pool, c.id, golden_passed=with_it[0] >= base[0])
    title = (
        f"Your instruction for the {kind_name} autopilot is now in force"
        if settled.status == "promoted"
        else f"Your instruction for the {kind_name} autopilot was not applied"
    )
    await tasks.see(
        pool,
        KIND,
        f"{c.id}:{settled.status}",
        title=title,
        detail=(
            f"Tested on {len(items)} items you settled yourself: the judge agreed with you on "
            f"{base[0]} before and {with_it[0]} with your words. {settled.verdict}. "
            + (
                "Undo it from the engine ledger if it behaves badly."
                if settled.status == "promoted"
                else "Rephrase it, or settle more items by hand so there is more to test with."
            )
        ),
        href="/engine-ledger",
        weight=0.4,
        status="needs_owner",
    )
    out: dict[str, Any] = {"tested": 1, settled.status: 1, "usage": usage}
    return out


async def _reject(pool: asyncpg.Pool, c: change.Change, kind: str, why: str) -> None:
    await pool.execute(
        "update public.engine_change set status = 'rejected', verdict = $2, decided_at = now()"
        " where id = $1 and status = 'shadow'",
        c.id,
        f"not testable: {why}",
    )
    await tasks.see(
        pool,
        KIND,
        f"{c.id}:rejected",
        title=f"Your instruction for the {kind} autopilot could not be tested",
        detail=f"There are {why}, too few to tell whether it helps, so it was not applied. "
        "Settle more of these by hand and give the instruction again.",
        href="/autopilot",
        weight=0.3,
        status="needs_owner",
    )


__all__ = [
    "KIND",
    "active_instructions",
    "golden_set",
    "instruction_block",
    "propose",
    "settle_one",
]


async def withdraw(pool: asyncpg.Pool, ref: str) -> int:
    """The owner took the instruction back: a test not yet run is dropped, a live one is rolled
    back. Returns how many changes it ended."""
    rows = await pool.fetch(
        "select " + change.COLUMNS + " from public.engine_change"  # noqa: S608
        " where change_kind = 'prompt' and status in ('shadow', 'promoted') and after->>'ref' = $1",
        ref,
    )
    ended = 0
    for rec in rows:
        c = change.from_row(rec)
        if c.status == "promoted":
            ended += int(await change.roll_back(pool, c.id, ["the instruction was withdrawn"]))
        else:
            result = await pool.execute(
                "update public.engine_change set status = 'rejected', verdict = $2,"
                " decided_at = now() where id = $1 and status = 'shadow'",
                c.id,
                "withdrawn before it was tested",
            )
            ended += int(result.endswith(" 1"))
    return ended
