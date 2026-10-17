"""The judge framework: one loop that settles a queue the way `approvals/ai.py` settles
listings and peers, for any kind of item.

A kind describes its queue (`load`), what a decision does (`apply`, `revert`), how to see the
owner's own call on an item (`outcome`) and whether an item is still open (`is_open`). The
loop does the rest: rules first, then a cheap model, then a stronger second opinion for what
the first is unsure of; a decision at or above the kind's `min_confidence` is applied (mode
`auto`) or only logged (mode `shadow`); everything else becomes an escalation for the owner.
Every decision lands in the ledger with its evidence, and an applied one can be reverted.

A kind never decides outside its `actionable` decisions: a model answering "defer" always
escalates, whatever its confidence.
"""

import datetime as dt
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.ai import Usage
from steward.autopilot import ledger
from steward.config.jobs import AutopilotKindPolicy, AutopilotSettings, effort_kwargs
from steward.llm.routing import RoutingClient, parse_many

logger = logging.getLogger("steward.autopilot")

Mode = Literal["off", "shadow", "auto"]

PROMPT_KEEP = 6000  # characters of the judge's prompt kept in the ledger, for the audit
UNDONE_MEMORY = dt.timedelta(days=365)  # how long an undone item stays out of the judge's hands


class Verdict(BaseModel):
    """What a judge returns; a kind subclasses it to narrow `decision` to its own words."""

    decision: str
    confidence: float = Field(ge=0, le=1)
    reasons: str = Field(description="Two sentences at most: what decided it.")


class ApplyError(Exception):
    """A decision could not be applied (the subject changed, a guard refused): the item is
    escalated instead and the message goes to the owner."""


@dataclass
class Candidate:
    ref: str  # e.g. "fact:123"
    ticker: str | None
    prompt: str  # the evidence block the judge sees (data, never instructions)
    evidence: dict[str, Any] = field(default_factory=dict)  # what `apply` needs, and the log


@dataclass(frozen=True)
class RuleResult:
    decision: str
    rule: str
    why: str


Load = Callable[[asyncpg.Pool, int], Awaitable[list[Candidate]]]
Apply = Callable[[asyncpg.Pool, Candidate, str, str], Awaitable[dict[str, Any] | None]]
Revert = Callable[[asyncpg.Pool, dict[str, Any]], Awaitable[None]]
Outcome = Callable[[asyncpg.Pool, str], Awaitable[str | None]]
IsOpen = Callable[[asyncpg.Pool, list[str]], Awaitable[set[str]]]
# Extra checks run just before a decision is applied: the problems found (empty: all clear).
# They see the decision the judge reached, and do their own reading of the stored records.
Verify = Callable[[asyncpg.Pool, "Candidate", str], Awaitable[list[str]]]
# The same look at a decision already applied: what no longer holds (empty: still fine).
Recheck = Callable[[asyncpg.Pool, ledger.AutoDecision], Awaitable[list[str]]]


@dataclass(frozen=True)
class JudgeKind:
    name: str
    label: str
    href: str  # the page where the owner settles one by hand
    system: str
    output_model: type[Verdict]
    actionable: frozenset[str]
    load: Load
    apply: Apply
    is_open: IsOpen
    revert: Revert | None = None
    # The owner's own decision on the subject, in the vocabulary of `actionable`; None when
    # the owner has not decided it (a decision the autopilot made does not count).
    outcome: Outcome | None = None
    rule: Callable[[Candidate], RuleResult | None] | None = None
    # What an escalation nobody settled in `ttl_days` becomes (None: it waits for the owner).
    expire_decision: str | None = None
    # No expiry decision, but the subject's standing state is already the safe one (a held fact
    # is not used; an insider line the classifier left as `other` counts for no signal): at
    # `ttl_days` the escalation closes and the subject stays as it is.
    expire_keeps: bool = False
    max_tokens: int = 1500
    # False: calibration never promotes the kind from shadow to auto; the owner sets `auto`.
    promotable: bool = True
    verify: Verify | None = None
    recheck: Recheck | None = None


@dataclass(frozen=True)
class Settled:
    decision: str
    confidence: float
    decided_by: str  # "rule:<id>" or "model:<id>"
    reasons: str

    def settles(self, min_confidence: float, actionable: frozenset[str]) -> bool:
        return self.decision in actionable and self.confidence >= min_confidence


def effective_mode(configured: Mode, override: str | None) -> Mode:
    """The mode a kind runs in: calibration's override beats the config, except that `off` in
    the config is the owner's switch and always wins."""
    if configured == "off":
        return "off"
    if override == "shadow":
        return "shadow"
    if override == "auto":
        return "auto"
    return configured


def plan_step(
    first: Settled | None, second: Settled | None, policy: AutopilotKindPolicy, kind: JudgeKind
) -> Settled | None:
    """The outcome to act on: the first opinion when it settles the item, else the second if
    that settles it, else the more confident of the two (it is escalated)."""
    for outcome in (first, second):
        if outcome is not None and outcome.settles(policy.min_confidence, kind.actionable):
            return outcome
    options = [o for o in (first, second) if o is not None]
    return max(options, key=lambda o: o.confidence, default=None)


def build_call(
    kind: JudgeKind, cand: Candidate, model: str, settings: AutopilotSettings, lessons: str = ""
) -> dict[str, Any]:
    return {
        "model": model,
        "system": f"{kind.system}\n\n{lessons}" if lessons else kind.system,
        "messages": [{"role": "user", "content": cand.prompt}],
        "output_format": kind.output_model,
        **effort_kwargs(model, settings.effort, kind.max_tokens),
    }


def read_response(response: Any, model: str, usage: Usage) -> Settled | None:
    if isinstance(response, BaseException):
        logger.warning("judge call to %s failed", model, exc_info=response)
        return None
    usage.add(response.usage)
    verdict = response.parsed_output
    if verdict is None:
        return None
    return Settled(verdict.decision, verdict.confidence, f"model:{model}", verdict.reasons)


def note_for(outcome: Settled) -> str:
    """The reason text written onto the record the decision changes."""
    return f"Autopilot ({outcome.decided_by}, {outcome.confidence:.2f}): {outcome.reasons}"


async def run_kind(
    pool: asyncpg.Pool,
    client: RoutingClient | None,
    kind: JudgeKind,
    settings: AutopilotSettings,
    mode: str,
    budget: dict[str, int],
    now: dt.datetime | None = None,
    rng: random.Random | None = None,
    lessons: str = "",
    learned_floor: float | None = None,
) -> dict[str, Any]:
    """Judge the open items of one kind. Returns counts and the usage for the job step.

    `lessons` is the kind's wrong cases for its prompt and `learned_floor` the confidence it
    learned to ask for; the floor can only raise the policy's `min_confidence`."""
    now = now or dt.datetime.now(dt.UTC)
    rng = rng or random.Random()  # noqa: S311 - sampling, not security
    policy = settings.policy(kind.name)
    if learned_floor is not None and learned_floor > policy.min_confidence:
        policy = policy.model_copy(update={"min_confidence": learned_floor})
    out: dict[str, Any] = {"mode": mode, "judged": 0, "applied": 0, "shadow": 0, "escalated": 0}
    usage = Usage(model=policy.model or settings.model)
    out["usage"] = usage
    if mode == "off":
        return out
    room = policy.daily_cap - await ledger.count_since(pool, kind.name, now - dt.timedelta(days=1))
    if room <= 0:
        out["stopped"] = "daily_cap reached"
        return out
    waiting = await ledger.decided_refs(
        pool, kind.name, ("escalated",), now - dt.timedelta(days=settings.rejudge_days)
    )
    # An item the owner undid is the owner's to settle: the engine never decides it again.
    skip = await ledger.decided_refs(pool, kind.name, ("reverted",), now - UNDONE_MEMORY)
    # An item left as it stands at expiry (see `expire_stale`) is not asked about again.
    skip |= await ledger.kept_refs(pool, kind.name)
    if mode == "shadow":
        skip |= await ledger.decided_refs(pool, kind.name, ("shadow",), now - dt.timedelta(days=90))
    # `load` returns the oldest first, so the items waiting on the owner would crowd out new ones:
    # ask for enough to get past them.
    loaded = await kind.load(pool, min(room + len(skip | waiting), 500))
    candidates = [c for c in loaded if c.ref not in skip | waiting][:room]
    # A rule costs nothing, so an item already waiting on the owner is settled as soon as a rule
    # covers it rather than after the re-judge interval.
    if kind.rule is not None:
        late = [c for c in loaded if c.ref in waiting - skip and kind.rule(c) is not None]
        candidates += late[: max(room - len(candidates), 0)]

    first_model = policy.model or settings.model
    second_model = policy.escalate_model or settings.escalate_model
    settled: dict[str, Settled | None] = {}
    pending: list[Candidate] = []
    for cand in candidates:
        ruled = kind.rule(cand) if kind.rule else None
        if ruled is not None:
            settled[cand.ref] = Settled(ruled.decision, 1.0, f"rule:{ruled.rule}", ruled.why)
        else:
            pending.append(cand)

    if pending and client is not None:
        affordable = pending[: max(budget["calls"], 0)]
        if len(affordable) < len(pending):
            out["stopped"] = "max_calls_per_run reached"
        budget["calls"] -= len(affordable)
        answers = await parse_many(
            client, [build_call(kind, c, first_model, settings, lessons) for c in affordable]
        )
        firsts = {
            c.ref: read_response(a, first_model, usage)
            for c, a in zip(affordable, answers, strict=True)
        }
        raw = {c.ref: a for c, a in zip(affordable, answers, strict=True)}

        def unsettled(ref: str) -> bool:
            first = firsts[ref]
            return first is None or not first.settles(policy.min_confidence, kind.actionable)

        retry = [
            c
            for c in affordable
            if second_model and second_model != first_model and unsettled(c.ref)
        ]
        retry = retry[: max(budget["calls"], 0)]
        budget["calls"] -= len(retry)
        seconds: dict[str, Settled | None] = {}
        if retry and second_model:
            answers2 = await parse_many(
                client, [build_call(kind, c, second_model, settings, lessons) for c in retry]
            )
            usage2 = Usage(model=second_model)
            seconds = {
                c.ref: read_response(a, second_model, usage2)
                for c, a in zip(retry, answers2, strict=True)
            }
            out["usage_second"] = usage2
            out["second_opinions"] = len(retry)
            raw |= {
                c.ref: a
                for c, a in zip(retry, answers2, strict=True)
                if not isinstance(a, BaseException)
            }
        for cand in affordable:
            if isinstance(raw[cand.ref], BaseException):
                # The call failed (not "no confident answer"): leave the item for the next run.
                out["errors"] = out.get("errors", 0) + 1
                continue
            settled[cand.ref] = plan_step(firsts[cand.ref], seconds.get(cand.ref), policy, kind)

    for cand in candidates:
        if cand.ref not in settled:
            continue
        outcome = settled[cand.ref]
        out["judged"] += 1
        await _record(pool, kind, cand, outcome, policy, mode, rng, out)
    return out


async def still_open(pool: asyncpg.Pool, kind: JudgeKind, cand: Candidate) -> bool:
    """The last look before a decision is applied: the item is still waiting and the owner has
    not decided it themselves. The judge's model calls can take minutes, in which time the owner
    may have settled the item on its page."""
    if cand.ref not in await kind.is_open(pool, [cand.ref]):
        return False
    return not (kind.outcome is not None and await kind.outcome(pool, cand.ref))


async def _record(
    pool: asyncpg.Pool,
    kind: JudgeKind,
    cand: Candidate,
    outcome: Settled | None,
    policy: AutopilotKindPolicy,
    mode: str,
    rng: random.Random,
    out: dict[str, Any],
) -> None:
    evidence = {"prompt": cand.prompt[:PROMPT_KEEP], **cand.evidence}

    async def put(
        status: ledger.Status,
        decision: str,
        confidence: float | None,
        decided_by: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        sampled: bool = False,
    ) -> None:
        await ledger.record(
            pool,
            kind=kind.name,
            subject_ref=cand.ref,
            ticker=cand.ticker,
            decision=decision,
            confidence=confidence,
            decided_by=decided_by,
            summary=summary,
            evidence=evidence,
            status=status,
            revert_payload=payload,
            sampled=sampled,
        )

    if outcome is None:
        await put("escalated", "defer", None, "model:none", "no answer from the judge")
        out["escalated"] += 1
        return
    args = (outcome.decision, outcome.confidence, outcome.decided_by)
    if not outcome.settles(policy.min_confidence, kind.actionable):
        await put("escalated", *args, outcome.reasons)
        out["escalated"] += 1
        return
    if mode == "shadow":
        await put("shadow", *args, outcome.reasons)
        out["shadow"] += 1
        return
    if not await still_open(pool, kind, cand):
        # Settled by someone else while the judge was thinking: nothing to apply, nothing to ask.
        out["settled_meanwhile"] = out.get("settled_meanwhile", 0) + 1
        return
    problems = await kind.verify(pool, cand, outcome.decision) if kind.verify else []
    if problems:
        await put(
            "escalated", *args, f"not applied (checked before applying: {'; '.join(problems)})"
        )
        out["escalated"] += 1
        out["verification_failed"] = out.get("verification_failed", 0) + 1
        return
    try:
        payload = await kind.apply(pool, cand, outcome.decision, note_for(outcome))
    except ApplyError as exc:
        await put("escalated", *args, f"not applied ({exc}): {outcome.reasons}")
        out["escalated"] += 1
        return
    await put(
        "applied",
        *args,
        outcome.reasons,
        payload=payload,
        sampled=rng.random() < policy.sample_rate,
    )
    await ledger.close_escalation(pool, kind.name, cand.ref)
    out["applied"] += 1


async def expire_stale(
    pool: asyncpg.Pool, kind: JudgeKind, policy: AutopilotKindPolicy, mode: str,
    now: dt.datetime | None = None,
) -> int:  # fmt: skip
    """Close escalations older than `ttl_days` with the kind's expiry decision (auto mode
    only). A kind with no expiry decision but whose standing state is already the safe one
    (`expire_keeps`) leaves the subject as it stands and stops asking; any other keeps them for
    the owner. Returns how many closed."""
    if mode != "auto" or (kind.expire_decision is None and not kind.expire_keeps):
        return 0
    now = now or dt.datetime.now(dt.UTC)
    closed = 0
    for row in await ledger.escalations(pool):
        if row.kind != kind.name or row.decided_at > now - dt.timedelta(days=policy.ttl_days):
            continue
        if kind.expire_decision is None:
            await ledger.close_escalation(pool, kind.name, row.subject_ref)
            await ledger.record(
                pool, kind=kind.name, subject_ref=row.subject_ref, ticker=row.ticker,
                decision="keep", confidence=None, decided_by="ttl",
                summary=f"Autopilot (ttl): no confident verdict within {policy.ttl_days} days;"
                " left as it stands, which is already the safe state",
                evidence=row.evidence, status="closed",
            )  # fmt: skip
            closed += 1
            continue
        cand = Candidate(row.subject_ref, row.ticker, "", row.evidence)
        note = f"Autopilot (ttl): no confident verdict within {policy.ttl_days} days"
        try:
            payload = await kind.apply(pool, cand, kind.expire_decision, note)
        except ApplyError:
            continue
        await ledger.record(
            pool, kind=kind.name, subject_ref=row.subject_ref, ticker=row.ticker,
            decision=kind.expire_decision, confidence=None, decided_by="ttl",
            summary=note, evidence=row.evidence, status="applied", revert_payload=payload,
        )  # fmt: skip
        await ledger.close_escalation(pool, kind.name, row.subject_ref)
        closed += 1
    return closed


async def revert(pool: asyncpg.Pool, kind: JudgeKind, row: ledger.AutoDecision) -> None:
    """Undo an applied decision and mark it wrong (calibration counts it)."""
    if kind.revert is None or row.revert_payload is None:
        raise ApplyError("this decision cannot be reverted")
    if not await ledger.set_status(pool, row.id, "reverted", only_from=("applied",)):
        raise ApplyError("the decision is no longer applied")
    try:
        await kind.revert(pool, row.revert_payload)
    except Exception:
        # Nothing was undone: the ledger must not say it was.
        await ledger.set_status(pool, row.id, "applied", only_from=("reverted",))
        raise
    await ledger.set_review(pool, row.id, "wrong", "owner")


async def accept(pool: asyncpg.Pool, kind: JudgeKind, row: ledger.AutoDecision) -> None:
    """The owner takes the app's recommendation on an escalated item: apply it."""
    if row.status != "escalated":
        raise ApplyError("the item is no longer waiting")
    if row.decision not in kind.actionable:
        raise ApplyError(f"the recommendation ({row.decision}) is not something to apply")
    cand = Candidate(row.subject_ref, row.ticker, "", row.evidence)
    payload = await kind.apply(
        pool, cand, row.decision, f"Owner accepted the autopilot: {row.summary}"
    )
    await ledger.set_status(pool, row.id, "closed", only_from=("escalated",))
    await ledger.record(
        pool, kind=kind.name, subject_ref=row.subject_ref, ticker=row.ticker,
        decision=row.decision, confidence=row.confidence, decided_by="owner-accepted",
        summary=row.summary, evidence=row.evidence, status="applied", revert_payload=payload,
    )  # fmt: skip


__all__ = [
    "ApplyError",
    "Candidate",
    "JudgeKind",
    "RuleResult",
    "Verdict",
    "accept",
    "effective_mode",
    "expire_stale",
    "plan_step",
    "revert",
    "run_kind",
]
