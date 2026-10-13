"""The owner-action registry (autonomous engine plan, L7).

Every change the owner makes to what the engine did is one named action here. A screen and the
chat call the same code, so validation, the audit row and the undo are identical. The chat only
ever *proposes* (a confirmation card); the owner's confirmation runs it.

Two rules are enforced when the registry is built, and tested:
- No action can place, prepare or schedule an order or call a broker. The owner trades at the
  broker; the app never does.
- Every action says how to undo it.
"""

import json
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field, model_validator

from steward.autopilot import ledger
from steward.autopilot.judge import ApplyError, accept, revert
from steward.autopilot.kinds import all_kinds
from steward.engine import overrides
from steward.ops import tasks as ops_tasks
from steward.training import labels

Risk = Literal["low", "medium", "high"]
Via = Literal["screen", "chat"]
current_via: ContextVar[Via] = ContextVar("current_via", default="screen")

# Words that mean placing or preparing a trade. An action whose name contains one is refused.
TRADE_WORDS = ("order", "trade", "buy", "sell", "broker", "execute", "submit", "place", "transfer")


class ActionError(Exception):
    """The action cannot run (unknown, bad arguments, no longer applicable)."""


class NeedsSecondConfirm(ActionError):
    """A high-risk action needs the second explicit confirmation."""


class NoticeArgs(BaseModel):
    task_id: int = Field(ge=1)


class DecisionArgs(BaseModel):
    decision_id: int = Field(ge=1)


class Why(BaseModel):
    """Why the owner is overruling the engine; both are optional, one tap or a line of text."""

    reason_code: overrides.Reason | None = None
    note: str | None = Field(default=None, max_length=1000)


class RevertArgs(DecisionArgs, Why):
    pass


class ReviewArgs(DecisionArgs, Why):
    verdict: Literal["ok", "wrong"]

    @model_validator(mode="after")
    def _why_needs_a_wrong_verdict(self) -> "ReviewArgs":
        if self.verdict == "ok":
            self.reason_code, self.note = None, None
        return self


class ModeArgs(BaseModel):
    kind: str
    mode: Literal["off", "shadow", "auto"]


@dataclass(frozen=True)
class Action:
    name: str
    title: str
    risk: Risk
    params: type[BaseModel]
    undo: str
    describe: Callable[[asyncpg.Pool, Any], Awaitable[str]]
    run: Callable[[asyncpg.Pool, Any], Awaitable[dict[str, Any]]]


async def _decision(pool: asyncpg.Pool, decision_id: int) -> ledger.AutoDecision:
    row = await ledger.get(pool, decision_id)
    if row is None:
        raise ActionError(f"No autopilot decision {decision_id}")
    return row


async def _describe_dismiss(pool: asyncpg.Pool, a: NoticeArgs) -> str:
    title = await pool.fetchval("select title from public.ops_task where id = $1", a.task_id)
    if title is None:
        raise ActionError(f"No notice {a.task_id}")
    return f"Dismiss the notice: {title}"


async def _run_dismiss(pool: asyncpg.Pool, a: NoticeArgs) -> dict[str, Any]:
    if not await ops_tasks.dismiss(pool, a.task_id):
        raise ActionError(f"Notice {a.task_id} is not open")
    return {"task_id": a.task_id}


async def _describe_revert(pool: asyncpg.Pool, a: DecisionArgs) -> str:
    row = await _decision(pool, a.decision_id)
    return f"Undo the applied {row.kind} decision: {row.summary}"


async def _run_revert(pool: asyncpg.Pool, a: RevertArgs) -> dict[str, Any]:
    row = await _decision(pool, a.decision_id)
    kind = all_kinds().get(row.kind)
    if kind is None:
        raise ActionError(f"Unknown autopilot kind {row.kind}")
    try:
        await revert(pool, kind, row)
    except ApplyError as exc:
        raise ActionError(str(exc)) from exc
    await overrides.record(
        pool,
        area="autopilot",
        subject_kind=row.kind,
        subject_ref=row.subject_ref,
        action="reverted",
        via=current_via.get(),
        reason_code=a.reason_code,
        note=a.note,
        decided_by=row.decided_by,
        decision_id=row.id,
        before={"decision": row.decision},
    )
    return {"decision_id": row.id, "kind": row.kind}


async def _describe_accept(pool: asyncpg.Pool, a: DecisionArgs) -> str:
    row = await _decision(pool, a.decision_id)
    return f"Apply the app's recommendation ({row.decision}) on {row.kind}: {row.summary}"


async def _run_accept(pool: asyncpg.Pool, a: DecisionArgs) -> dict[str, Any]:
    row = await _decision(pool, a.decision_id)
    kind = all_kinds().get(row.kind)
    if kind is None:
        raise ActionError(f"Unknown autopilot kind {row.kind}")
    try:
        await accept(pool, kind, row)
    except ApplyError as exc:
        raise ActionError(str(exc)) from exc
    await labels.record(
        pool,
        source="owner_accept",
        subject_kind=row.kind,
        subject_ref=row.subject_ref,
        label="right",
        by_owner=True,
        decision_id=row.id,
        detail={"decision": row.decision, "confidence": row.confidence},
    )
    return {"decision_id": row.id, "kind": row.kind, "decision": row.decision}


async def _describe_review(pool: asyncpg.Pool, a: ReviewArgs) -> str:
    row = await _decision(pool, a.decision_id)
    return f"Mark the {row.kind} decision as {a.verdict}: {row.summary}"


async def _run_review(pool: asyncpg.Pool, a: ReviewArgs) -> dict[str, Any]:
    row = await _decision(pool, a.decision_id)
    await ledger.set_review(pool, row.id, a.verdict, "owner")
    if a.verdict == "wrong":
        await overrides.record(
            pool,
            area="autopilot",
            subject_kind=row.kind,
            subject_ref=row.subject_ref,
            action="marked_wrong",
            via=current_via.get(),
            reason_code=a.reason_code,
            note=a.note,
            decided_by=row.decided_by,
            decision_id=row.id,
            before={"decision": row.decision},
        )
    return {"decision_id": row.id, "verdict": a.verdict}


async def _describe_mode(pool: asyncpg.Pool, a: ModeArgs) -> str:
    if a.kind not in all_kinds():
        raise ActionError(f"Unknown autopilot kind {a.kind}")
    current = (await ledger.mode_overrides(pool)).get(a.kind, "its configured mode")
    return f"Run the {a.kind} autopilot in {a.mode} (now: {current})"


async def _run_mode(pool: asyncpg.Pool, a: ModeArgs) -> dict[str, Any]:
    if a.kind not in all_kinds():
        raise ActionError(f"Unknown autopilot kind {a.kind}")
    await ledger.set_mode(pool, a.kind, a.mode, "Set by the owner")
    return {"kind": a.kind, "mode": a.mode}


ACTIONS: dict[str, Action] = {
    a.name: a
    for a in (
        Action(
            "dismiss_notice",
            "Dismiss a notice",
            "low",
            NoticeArgs,
            "A problem that is still there opens a new notice on the next watchdog run.",
            _describe_dismiss,
            _run_dismiss,
        ),
        Action(
            "revert_decision",
            "Undo an automatic decision",
            "medium",
            RevertArgs,
            "Not undoable; the decision stays marked wrong and the autopilot learns from it.",
            _describe_revert,
            _run_revert,
        ),
        Action(
            "accept_recommendation",
            "Apply a recommendation",
            "medium",
            DecisionArgs,
            "Undo it from the autopilot ledger with the revert action.",
            _describe_accept,
            _run_accept,
        ),
        Action(
            "review_decision",
            "Mark a decision right or wrong",
            "low",
            ReviewArgs,
            "Mark it again with the other verdict.",
            _describe_review,
            _run_review,
        ),
        Action(
            "set_autopilot_mode",
            "Change how an autopilot kind runs",
            "high",
            ModeArgs,
            "Set the previous mode again.",
            _describe_mode,
            _run_mode,
        ),
    )
}


def check_registry(actions: dict[str, Action] = ACTIONS) -> None:
    """Refuse a registry holding a trade-like action or one without an undo note."""
    for name, action in actions.items():
        words = set(re.split(r"[^a-z]+", f"{name} {action.title}".lower()))
        if clash := words & set(TRADE_WORDS):
            raise ValueError(f"action {name!r} looks like a trade ({sorted(clash)})")
        if not action.undo.strip():
            raise ValueError(f"action {name!r} has no undo note")


check_registry()


class ActionRow(BaseModel):
    id: int
    action: str
    args: dict[str, Any]
    summary: str
    risk: Risk
    undo: str
    status: Literal["proposed", "done", "cancelled", "failed"]
    via: Via
    reason: str | None
    result: dict[str, Any] | None


COLUMNS = "id, action, args, summary, risk, status, via, reason, result"


def _row(rec: asyncpg.Record) -> ActionRow:
    data = dict(rec)
    for key in ("args", "result"):
        value = data[key]
        data[key] = json.loads(value) if isinstance(value, str) else value
    data["undo"] = ACTIONS[data["action"]].undo
    return ActionRow.model_validate(data)


def _parse(name: str, args: dict[str, Any]) -> tuple[Action, BaseModel]:
    action = ACTIONS.get(name)
    if action is None:
        raise ActionError(f"Unknown action {name!r}")
    try:
        return action, action.params.model_validate(args)
    except ValueError as exc:
        raise ActionError(f"Bad arguments for {name}: {exc}") from exc


async def propose(
    pool: asyncpg.Pool, name: str, args: dict[str, Any], *, via: Via, reason: str | None = None
) -> ActionRow:
    """Check the arguments, say what would happen, and store a proposal. Nothing changes yet."""
    action, parsed = _parse(name, args)
    summary = await action.describe(pool, parsed)
    rec = await pool.fetchrow(
        "insert into public.owner_action (action, args, summary, risk, status, via, reason)"  # noqa: S608
        " values ($1, $2::jsonb, $3, $4, 'proposed', $5, $6) returning " + COLUMNS,
        name,
        json.dumps(parsed.model_dump()),
        summary,
        action.risk,
        via,
        reason,
    )
    return _row(rec)


async def confirm(pool: asyncpg.Pool, action_id: int, *, second: bool = False) -> ActionRow:
    """Run a proposal once. A high-risk action also needs `second=True`."""
    rec = await pool.fetchrow(
        "select " + COLUMNS + " from public.owner_action where id = $1",  # noqa: S608
        action_id,
    )
    if rec is None:
        raise ActionError(f"No proposed action {action_id}")
    row = _row(rec)
    if row.status != "proposed":
        raise ActionError(f"Action {action_id} is already {row.status}")
    if row.risk == "high" and not second:
        raise NeedsSecondConfirm("This changes how the engine runs; confirm it a second time.")
    claimed = await pool.fetchval(
        "update public.owner_action set status = 'done', decided_at = now()"
        " where id = $1 and status = 'proposed' returning id",
        action_id,
    )
    if claimed is None:
        raise ActionError(f"Action {action_id} is already being run")
    action, parsed = _parse(row.action, row.args)
    token = current_via.set(row.via)
    try:
        result = await action.run(pool, parsed)
    except ActionError as exc:
        await pool.execute(
            "update public.owner_action set status = 'failed', result = $2::jsonb where id = $1",
            action_id,
            json.dumps({"error": str(exc)}),
        )
        raise
    finally:
        current_via.reset(token)
    await pool.execute(
        "update public.owner_action set result = $2::jsonb where id = $1",
        action_id,
        json.dumps(result),
    )
    return row.model_copy(update={"status": "done", "result": result})


async def cancel(pool: asyncpg.Pool, action_id: int) -> bool:
    result = await pool.execute(
        "update public.owner_action set status = 'cancelled', decided_at = now()"
        " where id = $1 and status = 'proposed'",
        action_id,
    )
    return bool(result.endswith(" 1"))


async def execute(
    pool: asyncpg.Pool, name: str, args: dict[str, Any], *, via: Via = "screen"
) -> ActionRow:
    """Propose and confirm in one step, for a screen button that already is the confirmation.
    A high-risk action needs the explicit second confirmation, so it cannot be run this way."""
    row = await propose(pool, name, args, via=via)
    return await confirm(pool, row.id)


async def recent(pool: asyncpg.Pool, limit: int = 50) -> list[ActionRow]:
    rows = await pool.fetch(
        "select " + COLUMNS + " from public.owner_action order by created_at desc, id desc"  # noqa: S608
        " limit $1",
        limit,
    )
    return [_row(r) for r in rows]
