"""The autopilot framework: policy, mode, the judge loop (rules, ladder, shadow, apply, cap,
escalation, expiry, revert, accept) and calibration. The ledger is replaced by an in-memory
one and the model by a scripted client, so none of it needs a database or a network."""

import datetime as dt
import random
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Literal

import pytest

from steward.autopilot import calibration, judge
from steward.autopilot.judge import (
    ApplyError,
    Candidate,
    JudgeKind,
    RuleResult,
    Settled,
    Verdict,
    effective_mode,
    plan_step,
)
from steward.config.jobs import AutopilotKindPolicy, AutopilotSettings, JobsConfig

NOW = dt.datetime(2026, 11, 9, 12, 0, tzinfo=dt.UTC)


class ToyVerdict(Verdict):
    decision: Literal["accept", "reject", "defer"]


# --------------------------------------------------------------------- an in-memory ledger


@dataclass
class Row:
    id: int
    kind: str
    subject_ref: str
    ticker: str | None
    decision: str
    confidence: float | None
    decided_by: str
    summary: str
    evidence: dict[str, Any]
    status: str
    revert_payload: dict[str, Any] | None = None
    sampled: bool = False
    review: str | None = None
    reviewed_by: str | None = None
    reviewed_at: dt.datetime | None = None
    decided_at: dt.datetime = NOW


@dataclass
class FakeLedger:
    rows: list[Row] = field(default_factory=list)

    async def record(self, pool: Any, **kw: Any) -> int:
        if kw["status"] == "escalated":
            for row in self.rows:
                if (
                    row.kind == kw["kind"]
                    and row.subject_ref == kw["subject_ref"]
                    and row.status == "escalated"
                ):
                    row.decision, row.confidence = kw["decision"], kw["confidence"]
                    row.summary, row.decided_by = kw["summary"], kw["decided_by"]
                    return row.id
        row = Row(
            id=len(self.rows) + 1,
            kind=kw["kind"],
            subject_ref=kw["subject_ref"],
            ticker=kw["ticker"],
            decision=kw["decision"],
            confidence=kw["confidence"],
            decided_by=kw["decided_by"],
            summary=kw["summary"],
            evidence=kw["evidence"],
            status=kw["status"],
            revert_payload=kw.get("revert_payload"),
            sampled=kw.get("sampled", False),
            decided_at=NOW,
        )
        self.rows.append(row)
        return row.id

    async def count_since(self, pool: Any, kind: str, since: dt.datetime) -> int:
        return sum(
            1
            for r in self.rows
            if r.kind == kind and r.status in ("applied", "shadow") and r.decided_at >= since
        )

    async def decided_refs(
        self, pool: Any, kind: str, statuses: tuple[str, ...], since: dt.datetime
    ) -> set[str]:
        return {
            r.subject_ref
            for r in self.rows
            if r.kind == kind and r.status in statuses and r.decided_at >= since
        }

    async def close_escalation(self, pool: Any, kind: str, ref: str) -> None:
        for r in self.rows:
            if r.kind == kind and r.subject_ref == ref and r.status == "escalated":
                r.status = "closed"

    async def escalations(self, pool: Any, limit: int = 500) -> list[Row]:
        return [r for r in self.rows if r.status == "escalated"]

    async def set_status(
        self, pool: Any, decision_id: int, status: str, *, only_from: tuple[str, ...] = ()
    ) -> bool:
        row = self.rows[decision_id - 1]
        if only_from and row.status not in only_from:
            return False
        row.status = status
        return True

    async def set_review(self, pool: Any, decision_id: int, review: str, by: str) -> bool:
        self.rows[decision_id - 1].review = review
        self.rows[decision_id - 1].reviewed_by = by
        return True


@pytest.fixture
def fake_ledger(monkeypatch: pytest.MonkeyPatch) -> FakeLedger:
    fake = FakeLedger()
    namespace = SimpleNamespace(
        record=fake.record,
        count_since=fake.count_since,
        decided_refs=fake.decided_refs,
        close_escalation=fake.close_escalation,
        escalations=fake.escalations,
        set_status=fake.set_status,
        set_review=fake.set_review,
        Status=str,
    )
    monkeypatch.setattr(judge, "ledger", namespace)
    return fake


# ------------------------------------------------------------------------- scripted model


class ScriptedClient:
    """Answers each call by model and the evidence's `ref` line: {(model, ref): (dec, conf)}."""

    def __init__(self, script: dict[tuple[str, str], tuple[str, float]]) -> None:
        self.script = script
        self.calls: list[tuple[str, str]] = []
        self.messages = SimpleNamespace(parse=self._parse)
        self.llm = SimpleNamespace(configured=lambda model: True)

    async def _parse(self, **kw: Any) -> Any:
        ref = kw["messages"][0]["content"].strip()
        self.calls.append((kw["model"], ref))
        decision, confidence = self.script[(kw["model"], ref)]
        usage = SimpleNamespace(input_tokens=10, output_tokens=5)
        return SimpleNamespace(
            usage=usage,
            parsed_output=ToyVerdict(decision=decision, confidence=confidence, reasons="because"),
        )


@dataclass
class Store:
    """The 'queue' behind the toy kind: ref -> state."""

    open: dict[str, str | None] = field(default_factory=dict)  # ref -> decision made
    applied: list[tuple[str, str, str]] = field(default_factory=list)
    reverted: list[dict[str, Any]] = field(default_factory=list)
    fail_on: set[str] = field(default_factory=set)
    owner_calls: dict[str, str] = field(default_factory=dict)


def toy_kind(store: Store, **overrides: Any) -> JudgeKind:
    async def load(pool: Any, limit: int) -> list[Candidate]:
        return [
            Candidate(ref, "TST", ref, {"ref": ref}) for ref, d in store.open.items() if d is None
        ][:limit]

    async def apply(pool: Any, cand: Candidate, decision: str, note: str) -> dict[str, Any] | None:
        if cand.ref in store.fail_on:
            raise ApplyError("the item changed")
        store.open[cand.ref] = decision
        store.applied.append((cand.ref, decision, note))
        return {"ref": cand.ref}

    async def revert(pool: Any, payload: dict[str, Any]) -> None:
        store.open[payload["ref"]] = None
        store.reverted.append(payload)

    async def is_open(pool: Any, refs: list[str]) -> set[str]:
        return {r for r in refs if store.open.get(r) is None}

    async def outcome(pool: Any, ref: str) -> str | None:
        return store.owner_calls.get(ref)

    def rule(cand: Candidate) -> RuleResult | None:
        if cand.ref == "sure":
            return RuleResult("accept", "always", "the rule is certain")
        return None

    fields: dict[str, Any] = {
        "name": "toy",
        "label": "Toy",
        "href": "/toy",
        "system": "judge it",
        "output_model": ToyVerdict,
        "actionable": frozenset({"accept", "reject"}),
        "load": load,
        "apply": apply,
        "revert": revert,
        "is_open": is_open,
        "outcome": outcome,
        "rule": rule,
        "expire_decision": "reject",
    }
    fields.update(overrides)
    return JudgeKind(**fields)


def settings(**policy: Any) -> AutopilotSettings:
    return AutopilotSettings(
        model="gpt-small",
        escalate_model="gpt-big",
        default=AutopilotKindPolicy(mode="auto", min_confidence=0.9, sample_rate=0.0, **policy),
    )


async def run(
    store: Store,
    client: ScriptedClient | None,
    cfg: AutopilotSettings,
    mode: str = "auto",
    calls: int = 100,
) -> dict[str, Any]:
    return await judge.run_kind(
        None,
        client,  # type: ignore[arg-type]
        toy_kind(store),
        cfg,
        mode,
        {"calls": calls},
        now=NOW,
        rng=random.Random(1),  # noqa: S311
    )


# ------------------------------------------------------------------ pure pieces


def test_defaults_start_in_shadow_and_are_valid() -> None:
    cfg = JobsConfig().autopilot_settings
    assert cfg.default.mode == "shadow"
    assert cfg.policy("anything").min_confidence >= 0.9
    assert {"autopilot", "autopilot_calibration"} <= set(JobsConfig().schedules())


def test_demote_bar_cannot_exceed_promote_bar() -> None:
    with pytest.raises(ValueError, match="demote_precision"):
        AutopilotSettings(promote_precision=0.9, demote_precision=0.95)


def test_per_kind_policy_overrides_the_default() -> None:
    cfg = AutopilotSettings(kinds={"fact": AutopilotKindPolicy(mode="auto", min_confidence=0.97)})
    assert cfg.policy("fact").mode == "auto"
    assert cfg.policy("edge").mode == "shadow"


def test_off_in_config_always_wins_over_calibration() -> None:
    assert effective_mode("off", "auto") == "off"
    assert effective_mode("shadow", "auto") == "auto"
    assert effective_mode("auto", "shadow") == "shadow"
    assert effective_mode("auto", None) == "auto"
    assert effective_mode("shadow", "garbage") == "shadow"


def test_plan_step_prefers_the_opinion_that_settles_the_item() -> None:
    kind = toy_kind(Store())
    policy = AutopilotKindPolicy(min_confidence=0.9)
    sure = Settled("accept", 0.95, "model:a", "x")
    unsure = Settled("accept", 0.6, "model:b", "y")
    assert plan_step(sure, unsure, policy, kind) is sure
    assert plan_step(unsure, sure, policy, kind) is sure
    # a defer is never settled, however confident
    defer = Settled("defer", 0.99, "model:a", "z")
    assert plan_step(defer, None, policy, kind) is defer
    assert not defer.settles(0.9, kind.actionable)
    # nothing settles: the more confident opinion is kept for the escalation
    kept = plan_step(unsure, Settled("reject", 0.7, "model:b", "w"), policy, kind)
    assert kept is not None and kept.confidence == 0.7
    assert plan_step(None, None, policy, kind) is None


def test_calibration_promotes_on_enough_good_reviews_only() -> None:
    cfg = AutopilotSettings(promote_precision=0.95, promote_min_reviewed=50, demote_precision=0.9)
    assert calibration.calibration_move("shadow", 49, 49, cfg) is None
    assert calibration.calibration_move("shadow", 50, 47, cfg) is None  # 94%
    move = calibration.calibration_move("shadow", 50, 48, cfg)
    assert move is not None and move[0] == "auto"
    assert calibration.calibration_move("auto", 20, 19, cfg) is None  # 95%: stays
    assert calibration.calibration_move("auto", 20, 17, cfg) == (
        "shadow",
        "only 17/20 reviewed decisions right (85%)",
    )
    assert calibration.calibration_move("auto", 9, 0, cfg) is None  # too few to demote
    assert calibration.calibration_move("shadow", 0, 0, cfg) is None


def test_owner_review_compares_the_owners_call_to_the_decision() -> None:
    assert calibration.owner_review("accept", "accept") == "ok"
    assert calibration.owner_review("accept", "reject") == "wrong"


# ---------------------------------------------------------------------- the loop


async def test_rule_settles_without_a_model_and_is_applied(fake_ledger: FakeLedger) -> None:
    store = Store(open={"sure": None})
    client = ScriptedClient({})
    out = await run(store, client, settings())
    assert client.calls == []
    assert out["applied"] == 1
    row = fake_ledger.rows[0]
    assert (row.status, row.decided_by, row.confidence) == ("applied", "rule:always", 1.0)
    assert row.revert_payload == {"ref": "sure"}
    assert store.applied[0][2].startswith("Autopilot (rule:always, 1.00)")


async def test_confident_model_decision_is_applied_and_logged(fake_ledger: FakeLedger) -> None:
    store = Store(open={"a": None})
    client = ScriptedClient({("gpt-small", "a"): ("accept", 0.95)})
    out = await run(store, client, settings())
    assert (out["applied"], out["escalated"]) == (1, 0)
    assert client.calls == [("gpt-small", "a")]  # no second opinion needed
    assert fake_ledger.rows[0].evidence["prompt"] == "a"
    assert store.open["a"] == "accept"


async def test_unsure_first_model_gets_a_second_opinion(fake_ledger: FakeLedger) -> None:
    store = Store(open={"a": None, "b": None})
    client = ScriptedClient(
        {
            ("gpt-small", "a"): ("accept", 0.6),
            ("gpt-big", "a"): ("accept", 0.97),
            ("gpt-small", "b"): ("accept", 0.95),
        }
    )
    out = await run(store, client, settings())
    assert out["second_opinions"] == 1
    assert client.calls.count(("gpt-big", "a")) == 1 and ("gpt-big", "b") not in client.calls
    decided = {r.subject_ref: r.decided_by for r in fake_ledger.rows}
    assert decided == {"a": "model:gpt-big", "b": "model:gpt-small"}


async def test_nothing_confident_is_escalated_not_applied(fake_ledger: FakeLedger) -> None:
    store = Store(open={"a": None, "b": None})
    client = ScriptedClient(
        {
            ("gpt-small", "a"): ("accept", 0.6),
            ("gpt-big", "a"): ("accept", 0.7),
            ("gpt-small", "b"): ("defer", 0.99),
            ("gpt-big", "b"): ("defer", 0.99),
        }
    )
    out = await run(store, client, settings())
    assert (out["applied"], out["escalated"]) == (0, 2)
    assert store.applied == []
    assert {r.status for r in fake_ledger.rows} == {"escalated"}


async def test_shadow_mode_logs_but_applies_nothing_and_does_not_rejudge(
    fake_ledger: FakeLedger,
) -> None:
    store = Store(open={"a": None})
    client = ScriptedClient({("gpt-small", "a"): ("accept", 0.95)})
    out = await run(store, client, settings(), mode="shadow")
    assert (out["shadow"], out["applied"]) == (1, 0)
    assert store.applied == [] and fake_ledger.rows[0].status == "shadow"
    again = await run(store, client, settings(), mode="shadow")
    assert again["judged"] == 0 and len(client.calls) == 1


async def test_off_mode_does_nothing(fake_ledger: FakeLedger) -> None:
    store = Store(open={"a": None})
    out = await run(store, ScriptedClient({}), settings(), mode="off")
    assert out["judged"] == 0 and fake_ledger.rows == []


async def test_apply_failure_escalates_with_the_reason(fake_ledger: FakeLedger) -> None:
    store = Store(open={"a": None}, fail_on={"a"})
    client = ScriptedClient({("gpt-small", "a"): ("accept", 0.95)})
    out = await run(store, client, settings())
    assert out["escalated"] == 1 and out["applied"] == 0
    assert "not applied (the item changed)" in fake_ledger.rows[0].summary


async def test_daily_cap_and_call_budget_limit_a_run(fake_ledger: FakeLedger) -> None:
    store = Store(open={f"r{i}": None for i in range(6)})
    client = ScriptedClient({("gpt-small", f"r{i}"): ("accept", 0.95) for i in range(6)})
    out = await run(store, client, settings(daily_cap=2))
    assert out["applied"] == 2
    store2 = Store(open={f"r{i}": None for i in range(6)})
    out2 = await run(store2, client, settings(), calls=3)
    assert out2["applied"] == 3 and out2["stopped"] == "max_calls_per_run reached"


async def test_missing_model_still_runs_rules_and_leaves_the_rest(fake_ledger: FakeLedger) -> None:
    store = Store(open={"sure": None, "a": None})
    out = await run(store, None, settings())
    assert out["applied"] == 1
    assert [r.subject_ref for r in fake_ledger.rows] == ["sure"]


async def test_escalated_item_is_not_rejudged_until_rejudge_days(fake_ledger: FakeLedger) -> None:
    store = Store(open={"a": None})
    client = ScriptedClient(
        {("gpt-small", "a"): ("accept", 0.5), ("gpt-big", "a"): ("accept", 0.5)}
    )
    await run(store, client, settings())
    n = len(client.calls)
    await run(store, client, settings())
    assert len(client.calls) == n  # skipped: escalated within rejudge_days
    assert len([r for r in fake_ledger.rows if r.status == "escalated"]) == 1


async def test_ttl_expiry_closes_an_old_escalation_with_the_kinds_expiry_decision(
    fake_ledger: FakeLedger,
) -> None:
    store = Store(open={"a": None})
    client = ScriptedClient(
        {("gpt-small", "a"): ("accept", 0.5), ("gpt-big", "a"): ("accept", 0.5)}
    )
    await run(store, client, settings())
    policy = AutopilotKindPolicy(ttl_days=14)
    kind = toy_kind(store)
    assert await judge.expire_stale(None, kind, policy, "auto", now=NOW + dt.timedelta(days=5)) == 0
    assert (
        await judge.expire_stale(None, kind, policy, "shadow", now=NOW + dt.timedelta(days=30)) == 0
    )
    assert (
        await judge.expire_stale(None, kind, policy, "auto", now=NOW + dt.timedelta(days=30)) == 1
    )
    assert store.open["a"] == "reject"
    statuses = [(r.status, r.decided_by) for r in fake_ledger.rows]
    assert ("closed", "model:gpt-small") in statuses or ("closed", "model:gpt-big") in statuses
    assert ("applied", "ttl") in statuses


async def test_calibration_promotes_a_reversible_kind_but_never_an_irreversible_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moves: list[tuple[str, str]] = []

    async def mode_states(pool: Any) -> dict[str, Any]:
        return {}

    async def review_tally(pool: Any, kind: str, since: dt.datetime) -> tuple[int, int]:
        return 60, 60

    async def set_mode(pool: Any, kind: str, mode: str, reason: str) -> None:
        moves.append((kind, mode))

    monkeypatch.setattr(
        calibration,
        "ledger",
        SimpleNamespace(mode_states=mode_states, review_tally=review_tally, set_mode=set_mode),
    )
    kinds = {
        "undoable": toy_kind(Store(), name="undoable"),
        "final": toy_kind(Store(), name="final", revert=None),
    }
    done = await calibration.calibrate(
        None,
        kinds,
        AutopilotSettings(promote_min_reviewed=50),
        NOW,
    )
    assert moves == [("undoable", "auto")]
    assert [m["kind"] for m in done] == ["undoable"]


async def test_revert_undoes_an_applied_decision_and_marks_it_wrong(
    fake_ledger: FakeLedger,
) -> None:
    store = Store(open={"sure": None})
    await run(store, ScriptedClient({}), settings())
    row = fake_ledger.rows[0]
    await judge.revert(None, toy_kind(store), row)  # type: ignore[arg-type]
    assert store.open["sure"] is None
    assert (row.status, row.review, row.reviewed_by) == ("reverted", "wrong", "owner")
    with pytest.raises(ApplyError, match="no longer applied"):
        await judge.revert(None, toy_kind(store), row)  # type: ignore[arg-type]


async def test_accept_applies_the_recommendation_of_an_escalation(
    fake_ledger: FakeLedger,
) -> None:
    store = Store(open={"a": None})
    client = ScriptedClient(
        {("gpt-small", "a"): ("accept", 0.6), ("gpt-big", "a"): ("accept", 0.7)}
    )
    await run(store, client, settings())
    row = fake_ledger.rows[0]
    await judge.accept(None, toy_kind(store), row)  # type: ignore[arg-type]
    assert store.open["a"] == "accept"
    assert store.applied[0][2].startswith("Owner accepted the autopilot")
    assert row.status == "closed"
    assert fake_ledger.rows[-1].decided_by == "owner-accepted"
    with pytest.raises(ApplyError, match="no longer waiting"):
        await judge.accept(None, toy_kind(store), row)  # type: ignore[arg-type]


async def test_accept_refuses_a_defer_recommendation(fake_ledger: FakeLedger) -> None:
    store = Store(open={"a": None})
    client = ScriptedClient({("gpt-small", "a"): ("defer", 0.9), ("gpt-big", "a"): ("defer", 0.9)})
    await run(store, client, settings())
    with pytest.raises(ApplyError, match="not something to apply"):
        await judge.accept(None, toy_kind(store), fake_ledger.rows[0])  # type: ignore[arg-type]
