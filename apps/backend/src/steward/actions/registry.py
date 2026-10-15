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
from steward.config import CONFIG_MODELS
from steward.config.store import RollbackError, load_active
from steward.config.store import rollback as config_rollback
from steward.config.verification import VerificationConfig
from steward.engine import change as engine_change
from steward.engine import config_change, instructions, overrides, prompt_change
from steward.news.read import resolve_story, set_story_closed
from steward.ops import tasks as ops_tasks
from steward.settings import get_settings
from steward.training import labels
from steward.verify.feedback import FeedbackError, FeedbackIn, record_feedback

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


class SnoozeArgs(BaseModel):
    task_id: int = Field(ge=1)
    days: int = Field(default=7, ge=1, le=ops_tasks.SNOOZE_MAX_DAYS)


class FactMarkArgs(BaseModel):
    fact_id: int = Field(ge=1)
    rating: Literal["wrong", "irrelevant", "useful"]
    note: str | None = Field(default=None, max_length=500)


class StoryResolveArgs(BaseModel):
    story_id: int = Field(ge=1)
    resolution: Literal["confirmed", "false"]
    note: str | None = Field(default=None, max_length=500)


class StoryCloseArgs(BaseModel):
    story_id: int = Field(ge=1)
    closed: bool = True


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


async def _describe_snooze(pool: asyncpg.Pool, a: SnoozeArgs) -> str:
    title = await pool.fetchval("select title from public.ops_task where id = $1", a.task_id)
    if title is None:
        raise ActionError(f"No notice {a.task_id}")
    return f"Snooze the notice for {a.days} days: {title}"


async def _run_snooze(pool: asyncpg.Pool, a: SnoozeArgs) -> dict[str, Any]:
    if not await ops_tasks.snooze(pool, a.task_id, a.days):
        raise ActionError(f"Notice {a.task_id} is not open")
    return {"task_id": a.task_id, "days": a.days}


async def _describe_fact_mark(pool: asyncpg.Pool, a: FactMarkArgs) -> str:
    row = await pool.fetchrow("select ticker, metric from public.fact where id = $1", a.fact_id)
    if row is None:
        raise ActionError(f"No fact {a.fact_id}")
    what = {
        "wrong": "wrong (it is set aside and its holding's verdicts are judged again)",
        "irrelevant": "not relevant",
        "useful": "useful",
    }[a.rating]
    return f"Mark the {row['metric']} fact of {row['ticker'] or 'the portfolio'} as {what}"


async def _run_fact_mark(pool: asyncpg.Pool, a: FactMarkArgs) -> dict[str, Any]:
    stored = await load_active(pool, "verification", VerificationConfig)
    try:
        out = await record_feedback(
            pool,
            FeedbackIn(target_kind="fact", target_id=a.fact_id, rating=a.rating, note=a.note),
            stored.body if stored else VerificationConfig(),
            None,
        )
    except FeedbackError as exc:
        raise ActionError(str(exc)) from exc
    if a.rating == "wrong":
        await overrides.record(
            pool,
            area="facts",
            subject_kind="fact",
            subject_ref=str(a.fact_id),
            action="marked_wrong",
            via=current_via.get(),
            note=a.note,
            decided_by="engine",
        )
    return {"fact_id": a.fact_id, "rating": a.rating, "effects": out.effects}


async def _describe_story_resolve(pool: asyncpg.Pool, a: StoryResolveArgs) -> str:
    headline = await pool.fetchval(
        "select headline from public.story_cluster where id = $1", a.story_id
    )
    if headline is None:
        raise ActionError(f"No story {a.story_id}")
    verdict = "happened" if a.resolution == "confirmed" else "did not happen"
    return f"Settle the disputed story as {verdict}: {headline}"


async def _run_story_resolve(pool: asyncpg.Pool, a: StoryResolveArgs) -> dict[str, Any]:
    if not await resolve_story(pool, a.story_id, a.resolution, a.note):
        raise ActionError(f"Story {a.story_id} is not a disputed story")
    return {"story_id": a.story_id, "resolution": a.resolution}


async def _describe_story_close(pool: asyncpg.Pool, a: StoryCloseArgs) -> str:
    headline = await pool.fetchval(
        "select headline from public.story_cluster where id = $1", a.story_id
    )
    if headline is None:
        raise ActionError(f"No story {a.story_id}")
    return f"{'Close' if a.closed else 'Reopen'} the story: {headline}"


async def _run_story_close(pool: asyncpg.Pool, a: StoryCloseArgs) -> dict[str, Any]:
    if not await set_story_closed(pool, a.story_id, a.closed):
        raise ActionError(f"No story {a.story_id}")
    return {"story_id": a.story_id, "closed": a.closed}


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


class ConfigRollbackArgs(BaseModel):
    domain: str
    version_id: int = Field(ge=1)


class ChangeArgs(BaseModel):
    change_id: int = Field(ge=1)


async def _describe_config_rollback(pool: asyncpg.Pool, a: ConfigRollbackArgs) -> str:
    if a.domain not in CONFIG_MODELS:
        raise ActionError(f"Unknown configuration {a.domain}")
    if not await pool.fetchval(
        "select exists (select 1 from public.config_version where id = $1 and domain = $2)",
        a.version_id,
        a.domain,
    ):
        raise ActionError(f"No {a.domain} version {a.version_id}")
    return f"Put the {a.domain} settings back to version #{a.version_id}"


async def _run_config_rollback(pool: asyncpg.Pool, a: ConfigRollbackArgs) -> dict[str, Any]:
    owner = get_settings().owner_user_id
    previous = await overrides.active_version(pool, a.domain)
    try:
        async with pool.acquire() as conn:
            saved = await config_rollback(conn, a.domain, a.version_id, created_by=owner)
    except RollbackError as exc:
        raise ActionError(str(exc)) from exc
    if saved is None:
        raise ActionError(f"No {a.domain} version {a.version_id}")
    await overrides.record_config_edit(
        pool,
        a.domain,
        previous,
        saved.body.model_dump(mode="json"),
        action="reverted",
        via=current_via.get(),
    )
    return {"domain": a.domain, "restored": a.version_id, "new_version": saved.id}


async def _describe_roll_back_change(pool: asyncpg.Pool, a: ChangeArgs) -> str:
    row = await pool.fetchrow(
        "select hypothesis, status from public.engine_change where id = $1", a.change_id
    )
    if row is None:
        raise ActionError(f"No engine change {a.change_id}")
    if row["status"] != "promoted":
        raise ActionError(f"Engine change {a.change_id} is {row['status']}, not live")
    return f"Undo the engine's own change: {row['hypothesis']}"


async def _run_roll_back_change(pool: asyncpg.Pool, a: ChangeArgs) -> dict[str, Any]:
    row = await pool.fetchrow(
        "select " + engine_change.COLUMNS + " from public.engine_change where id = $1",  # noqa: S608
        a.change_id,
    )
    reasons = ["rolled back by the owner"]
    # A change to a setting also puts the old value back (unless it was edited since).
    if row is not None and str(row["target"]).startswith(config_change.PREFIX):
        live = await config_change.undo(pool, engine_change.from_row(row), reasons)
    else:
        live = await engine_change.roll_back(pool, a.change_id, reasons)
    if not live:
        raise ActionError(f"Engine change {a.change_id} is not live")
    return {"change_id": a.change_id}


class TeachArgs(BaseModel):
    # Every area is tested before it applies: `autopilot:<kind>` against your own earlier calls,
    # the others by re-running the area on stored inputs (`engine/instructions.py`).
    area: str = Field(pattern=r"^(theses|chat|digest|news|autopilot:[a-z_]{2,40})$")
    text: str = Field(min_length=5, max_length=overrides.STANDING_MAX)

    @model_validator(mode="after")
    def _known_kind(self) -> "TeachArgs":
        if self.area.startswith("autopilot:") and self.area.removeprefix("autopilot:") not in (
            all_kinds()
        ):
            raise ValueError(f"No autopilot queue called {self.area.removeprefix('autopilot:')!r}")
        return self


class RetractArgs(BaseModel):
    ref: str = Field(pattern=r"^teaching:[0-9a-f]{12}$")


async def _describe_teach(pool: asyncpg.Pool, a: TeachArgs) -> str:
    if a.area.startswith("autopilot:"):
        return (
            f"Teach the {a.area.removeprefix('autopilot:')} autopilot: “{a.text}”. It is tested "
            "on items you settled yourself first and applies only if it agrees with you at "
            "least as often as now"
        )
    return (
        f"Remember for {a.area}: “{a.text}”. It is tested on stored {a.area} work first and "
        "applies only if the checks hold and it follows your words better than it loses"
    )


async def _describe_apply_change(pool: asyncpg.Pool, a: ChangeArgs) -> str:
    row = await pool.fetchrow(
        "select hypothesis, status from public.engine_change where id = $1"
        " and change_kind = 'prompt' and target like 'prompt:%'",
        a.change_id,
    )
    if row is None:
        raise ActionError(f"No prompt addition {a.change_id}")
    if row["status"] not in ("shadow", "rejected"):
        raise ActionError(f"Prompt addition {a.change_id} is {row['status']}")
    return f"Apply without a passing test: {row['hypothesis']}"


async def _run_apply_change(pool: asyncpg.Pool, a: ChangeArgs) -> dict[str, Any]:
    if not await instructions.owner_apply(pool, a.change_id):
        raise ActionError(f"Prompt addition {a.change_id} cannot be applied")
    return {"change_id": a.change_id}


async def _run_teach(pool: asyncpg.Pool, a: TeachArgs) -> dict[str, Any]:
    ref = await overrides.teach(pool, a.area, a.text, current_via.get())
    if a.area.startswith("autopilot:"):
        await prompt_change.propose(pool, a.area.removeprefix("autopilot:"), a.text, ref)
    else:
        await instructions.propose(pool, a.area, a.text, ref, "owner")
    return {"ref": ref, "area": a.area}


async def _describe_retract(pool: asyncpg.Pool, a: RetractArgs) -> str:
    found = [
        s
        for s in await overrides.standing_instructions(pool, include_waiting=True)
        if s.ref == a.ref
    ]
    if not found:
        raise ActionError(f"No standing instruction {a.ref}")
    return f"Forget the standing instruction for {found[0].area}: “{found[0].text}”"


async def _run_retract(pool: asyncpg.Pool, a: RetractArgs) -> dict[str, Any]:
    if not await overrides.retract(pool, a.ref, current_via.get()):
        raise ActionError(f"No standing instruction {a.ref}")
    await prompt_change.withdraw(pool, a.ref)
    return {"ref": a.ref}


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
            "snooze_notice",
            "Snooze a notice",
            "low",
            SnoozeArgs,
            "It comes back by itself after the days you chose; nothing is lost.",
            _describe_snooze,
            _run_snooze,
        ),
        Action(
            "mark_fact",
            "Mark a fact wrong, irrelevant or useful",
            "medium",
            FactMarkArgs,
            "Mark it useful again; a fact set aside as wrong is restored from the Review page.",
            _describe_fact_mark,
            _run_fact_mark,
        ),
        Action(
            "resolve_story",
            "Settle a disputed news story",
            "medium",
            StoryResolveArgs,
            "Settle it again with the other answer.",
            _describe_story_resolve,
            _run_story_resolve,
        ),
        Action(
            "close_story",
            "Close or reopen a news story",
            "low",
            StoryCloseArgs,
            "Run it again with the opposite choice.",
            _describe_story_close,
            _run_story_close,
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
            "rollback_config",
            "Put a setting back to an earlier version",
            "medium",
            ConfigRollbackArgs,
            "Roll back again to the version that was active before.",
            _describe_config_rollback,
            _run_config_rollback,
        ),
        Action(
            "roll_back_change",
            "Undo a change the engine made to itself",
            "medium",
            ChangeArgs,
            "The change stays recorded as rolled back; the engine proposes it again only "
            "after new evidence.",
            _describe_roll_back_change,
            _run_roll_back_change,
        ),
        Action(
            "apply_change",
            "Apply a prompt addition that was not tested or did not pass",
            "medium",
            ChangeArgs,
            "Undo it with the roll_back_change action.",
            _describe_apply_change,
            _run_apply_change,
        ),
        Action(
            "teach",
            "Give the engine a standing instruction",
            "low",
            TeachArgs,
            "Forget it with the retract_teaching action.",
            _describe_teach,
            _run_teach,
        ),
        Action(
            "retract_teaching",
            "Forget a standing instruction",
            "low",
            RetractArgs,
            "Give the same instruction again with the teach action.",
            _describe_retract,
            _run_retract,
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


async def cancel(
    pool: asyncpg.Pool, action_id: int, reason_code: overrides.Reason | None = None
) -> bool:
    """Turn a proposal down. A proposal the engine or the chat made and the owner declined is an
    override: the chat learns from what the owner keeps declining."""
    rec = await pool.fetchrow(
        "update public.owner_action set status = 'cancelled', decided_at = now()"
        " where id = $1 and status = 'proposed' returning action, via, summary",
        action_id,
    )
    if rec is None:
        return False
    await overrides.record(
        pool,
        area="chat" if rec["via"] == "chat" else "screen_actions",
        subject_kind="owner_action",
        subject_ref=f"{rec['action']}:{action_id}",
        action="rejected",
        via=rec["via"],
        reason_code=reason_code,
        decided_by="chat" if rec["via"] == "chat" else "engine",
        before={"summary": rec["summary"]},
    )
    return True


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
