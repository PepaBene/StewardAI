"""A standing instruction, or a prompt addition a model drafted, for the news classifier, the
digest writer, the thesis author or the chat assistant, tested before it is used (engine plan, L5).

It is stored as a `prompt` change with the target `prompt:<area>` and starts in shadow:

1. `settle_one` runs the area's evaluator (`engine/evaluators.py`) with and without the addition
   on stored inputs, through `engine/replay.py`, and `engine.change.settle` applies the gates.
2. A promoted addition is part of that area's prompt (`overrides.lessons` reads it); a rejected
   or rolled back one never is. Nothing reaches a prompt just because it was typed.
3. Where there is too little to test on, the addition is not applied and the owner is told so;
   the owner can still apply it themselves (`owner_apply`), which the ledger records as theirs.

Instructions the owner gave before this existed have no change row and keep applying.
"""

from typing import Any, Literal

import asyncpg

from steward.ai import Usage
from steward.budget_guard import BudgetExceeded
from steward.config.jobs import AutopilotSettings
from steward.engine import change, overrides, replay
from steward.engine.evaluators import EVALUATORS
from steward.llm.routing import RoutingClient
from steward.ops import tasks

KIND = "prompt_change"
AREAS = ("news", "digest", "theses", "chat")
LABELS = {
    "news": "news items",
    "digest": "lines of earlier digests",
    "theses": "theses the engine wrote",
    "chat": "proposals the chat made",
}
Author = Literal["owner", "model"]
MAX_TEXT = overrides.STANDING_MAX


def target_for(area: str) -> str:
    return f"prompt:{area}"


async def propose(
    pool: asyncpg.Pool, area: str, text: str, ref: str | None, author: Author
) -> change.Change:
    if area not in AREAS:
        raise ValueError(f"no tested prompt for {area!r}")
    ev = EVALUATORS[area]
    who = "your instruction" if author == "owner" else "a draft the engine wrote"
    return await change.propose(
        pool,
        target_for(area),
        "prompt",
        f"Add to the {area} prompt, from {who}: {text[:200]}",
        "hard checks no worse, and the judge prefers it more often than not",
        before={"addendum": None},
        after={"addendum": text[:MAX_TEXT], "ref": ref, "author": author},
        gates=replay.gates_for(ev, 1),
    )


async def _tell(pool: asyncpg.Pool, c: change.Change, title: str, detail: str) -> None:
    await tasks.see(
        pool,
        KIND,
        f"{c.id}:{c.status}",
        title=title,
        detail=detail,
        href="/engine-ledger",
        weight=0.4,
        status="needs_owner",
    )


def _who(c: change.Change) -> str:
    return "Your instruction" if (c.after or {}).get("author") == "owner" else "A prompt addition"


async def settle_one(
    pool: asyncpg.Pool,
    client: RoutingClient,
    settings: AutopilotSettings,
    budget: dict[str, int],
) -> dict[str, Any]:
    """Test the oldest waiting addition, if this run has calls left for it."""
    rows = await pool.fetch(
        "select " + change.COLUMNS + " from public.engine_change"  # noqa: S608
        " where status = 'shadow' and change_kind = 'prompt' and target like 'prompt:%'"
        " order by id limit 1"
    )
    if not rows:
        return {"tested": 0}
    c = change.from_row(rows[0])
    change.ensure_changeable(c.target)
    area = c.target.removeprefix("prompt:")
    text = (c.after or {}).get("addendum")
    ev = EVALUATORS.get(area)
    if ev is None or not isinstance(text, str):
        await _end(pool, c, "rejected", "the area or the text no longer exists")
        return {"tested": 0, "rejected": 1}
    needed = ev.max_cases * ev.calls_per_case
    if budget["calls"] < needed:
        return {"tested": 0, "waiting": "not enough calls left in this run"}
    budget["calls"] -= needed
    label = LABELS[area]
    # The news classifier is given the owner's wishes only, the other areas also their corrections.
    current = (
        await overrides.in_force(pool, area, label)
        if area == "news"
        else await overrides.lessons(pool, area, label)
    )
    candidate = "\n\n".join(b for b in (current, overrides.render_standing([text], label)) if b)
    judge_model = settings.escalate_model or settings.model
    usage = Usage(model=judge_model)
    try:
        result = await replay.compare(
            pool, client, ev, current, candidate, text, judge_model, settings.effort, usage
        )
    except replay.Untestable as exc:
        await _end(pool, c, "rejected", f"not testable: {exc}")
        await _tell(
            pool,
            c.model_copy(update={"status": "rejected"}),
            f"{_who(c)} for {area} could not be tested",
            f"{exc}. It is not applied. If you want it anyway, apply it from the engine ledger "
            "or tell the chat to apply it.",
        )
        return {"tested": 0, "rejected": 1, "usage": usage}
    except BudgetExceeded:
        return {"tested": 0, "waiting": "the AI budget is used up", "usage": usage}
    await change.record_results(pool, c.id, result.baseline, result.candidate)
    settled = await change.settle(pool, c.id, golden_passed=result.tally.passed())
    applied = settled.status == "promoted"
    await _tell(
        pool,
        settled,
        f"{_who(c)} for {area} is now in force"
        if applied
        else f"{_who(c)} for {area} was not applied",
        f"{replay.describe_result(result)}. {settled.verdict}. "
        + (
            "Undo it from the engine ledger if it behaves badly."
            if applied
            else "Rephrase it, or apply it yourself from the engine ledger if you disagree."
        ),
    )
    return {"tested": 1, settled.status: 1, "usage": usage}


async def _end(pool: asyncpg.Pool, c: change.Change, status: str, why: str) -> None:
    await pool.execute(
        "update public.engine_change set status = $2, verdict = $3, decided_at = now()"
        " where id = $1 and status = 'shadow'",
        c.id,
        status,
        why,
    )


async def owner_apply(pool: asyncpg.Pool, change_id: int) -> bool:
    """The owner puts an untested or rejected addition to work anyway. It is recorded as theirs."""
    result = await pool.execute(
        "update public.engine_change set status = 'promoted',"
        " verdict = 'applied by the owner without a passing test', decided_at = now()"
        " where id = $1 and change_kind = 'prompt' and target like 'prompt:%'"
        " and status in ('shadow', 'rejected')",
        change_id,
    )
    return bool(result.endswith(" 1"))
