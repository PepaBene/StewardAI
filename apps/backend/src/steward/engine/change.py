"""Self-change control (autonomous engine plan, L5).

A change the engine wants to make to itself goes through four steps:
1. `propose`: stored with its hypothesis, the metric it should move and the gates it must pass.
   The gates are written now, before any result exists.
2. shadow: the candidate runs beside the current setting on live inputs; both sets of numbers
   are stored with `record_results`.
3. `decide`: pure code compares the numbers with the gates. The model that proposed the change
   has no say. Passing promotes; failing rejects.
4. `check_rollback`: after promotion the next window is compared with the baseline; a
   regression rolls the change back.

Some things the engine may never change on its own (`FROZEN`): the rule that it never trades,
the spending caps, the gates themselves, and credentials. A proposal that touches one is refused.
"""

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel

ChangeKind = Literal["prompt", "threshold", "rule", "source_weight", "schedule", "model"]
Status = Literal["shadow", "promoted", "rejected", "rolled_back"]

# A target that starts with one of these is outside the engine's reach.
FROZEN: tuple[str, ...] = (
    "trading",
    "broker",
    "order",
    "budget",
    "gates",
    "engine_change",
    "credentials",
    "secret",
    "provenance",
    "freeze",
)


class FrozenTarget(Exception):
    """The engine may not change this on its own."""


def ensure_changeable(target: str) -> None:
    # Every part of the target counts, so `autopilot:budget` is as frozen as `budget`.
    parts = re.split(r"[.:/]", target.lower())
    if any(part.strip().startswith(p) for part in parts for p in FROZEN):
        raise FrozenTarget(f"{target!r} is frozen: only the owner can change it")


@dataclass(frozen=True)
class Metrics:
    """What one arm (the current setting or the candidate) did over the same inputs."""

    n: int  # decisions made
    reviewed: int  # of those, how many the owner or an audit judged
    wrong: int  # of the reviewed, how many were wrong
    escalated: int
    verifier_failures: int
    cost: float  # in the account currency

    @property
    def wrong_rate(self) -> float | None:
        return self.wrong / self.reviewed if self.reviewed else None

    @property
    def escalation_rate(self) -> float | None:
        return self.escalated / self.n if self.n else None


@dataclass(frozen=True)
class Gates:
    min_n: int = 30
    min_reviewed: int = 10
    max_wrong_rate_increase: float = 0.0  # candidate may not be worse than the current setting
    max_cost_increase: float = 0.2  # relative
    max_escalation_drop: float = 0.15  # fewer escalations must not be what "improved" it
    require_golden: bool = True


@dataclass(frozen=True)
class Verdict:
    promote: bool
    reasons: list[str]


def decide(
    baseline: Metrics, candidate: Metrics, gates: Gates, *, golden_passed: bool | None
) -> Verdict:
    """Compare the candidate with the current setting. Every gate that fails is listed."""
    fails: list[str] = []
    if candidate.n < gates.min_n or baseline.n < gates.min_n:
        fails.append(f"too few decisions (need {gates.min_n} in each arm)")
    if candidate.reviewed < gates.min_reviewed or baseline.reviewed < gates.min_reviewed:
        fails.append(f"too few reviewed decisions (need {gates.min_reviewed} in each arm)")
    cw, bw = candidate.wrong_rate, baseline.wrong_rate
    if cw is not None and bw is not None and cw > bw + gates.max_wrong_rate_increase:
        fails.append(f"more wrong decisions ({cw:.0%} against {bw:.0%})")
    if candidate.verifier_failures > baseline.verifier_failures and candidate.n >= baseline.n:
        fails.append("more decisions failed the checks before applying")
    if baseline.cost > 0 and candidate.cost > baseline.cost * (1 + gates.max_cost_increase):
        fails.append("costs more than the allowed margin")
    ce, be = candidate.escalation_rate, baseline.escalation_rate
    if ce is not None and be is not None and be - ce > gates.max_escalation_drop:
        fails.append("it looks better mostly because it escalates much less")
    if gates.require_golden and golden_passed is not True:
        fails.append("the golden set was not passed" if golden_passed is False else "no golden run")
    return Verdict(not fails, fails or ["all gates passed"])


def rollback_due(baseline: Metrics, after: Metrics, gates: Gates) -> list[str]:
    """Why a promoted change should be undone, from the window after it went live."""
    reasons: list[str] = []
    aw, bw = after.wrong_rate, baseline.wrong_rate
    if (
        after.reviewed >= gates.min_reviewed
        and aw is not None
        and bw is not None
        and aw > bw + gates.max_wrong_rate_increase
    ):
        reasons.append(f"wrong decisions rose to {aw:.0%} from {bw:.0%}")
    if (
        after.n
        and baseline.n
        and after.verifier_failures / after.n > 2 * (baseline.verifier_failures / baseline.n) + 0.05
    ):
        reasons.append("failed checks before applying rose sharply")
    return reasons


class Change(BaseModel):
    id: int
    target: str
    change_kind: ChangeKind
    hypothesis: str
    metric: str
    gates: dict[str, Any]
    before: Any
    after: Any
    baseline: dict[str, Any] | None
    candidate: dict[str, Any] | None
    status: Status
    verdict: str | None


COLUMNS = (
    "id, target, change_kind, hypothesis, metric, gates, before, after, baseline, candidate,"
    " status, verdict"
)


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def from_row(row: asyncpg.Record) -> Change:
    data = dict(row)
    for key in ("gates", "before", "after", "baseline", "candidate"):
        data[key] = _load(data[key])
    return Change.model_validate(data)


async def propose(
    pool: asyncpg.Pool,
    target: str,
    change_kind: ChangeKind,
    hypothesis: str,
    metric: str,
    before: Any,
    after: Any,
    gates: Gates | None = None,
) -> Change:
    ensure_changeable(target)
    row = await pool.fetchrow(
        "insert into public.engine_change (target, change_kind, hypothesis, metric, gates,"  # noqa: S608
        " before, after) values ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb)"
        " returning " + COLUMNS,
        target,
        change_kind,
        hypothesis,
        metric,
        json.dumps(asdict(gates or Gates())),
        json.dumps(before),
        json.dumps(after),
    )
    return from_row(row)


async def record_results(
    pool: asyncpg.Pool, change_id: int, baseline: Metrics, candidate: Metrics
) -> None:
    await pool.execute(
        "update public.engine_change set baseline = $2::jsonb, candidate = $3::jsonb"
        " where id = $1 and status = 'shadow'",
        change_id,
        json.dumps(asdict(baseline)),
        json.dumps(asdict(candidate)),
    )


async def settle(pool: asyncpg.Pool, change_id: int, *, golden_passed: bool | None) -> Change:
    """Apply the gates to a shadow change's stored numbers; promoted or rejected, once."""
    row = await pool.fetchrow(
        "select " + COLUMNS + " from public.engine_change where id = $1",  # noqa: S608
        change_id,
    )
    if row is None:
        raise LookupError(f"No engine change {change_id}")
    change = from_row(row)
    if change.status != "shadow" or change.baseline is None or change.candidate is None:
        raise ValueError("the change is not waiting for a verdict")
    verdict = decide(
        Metrics(**change.baseline),
        Metrics(**change.candidate),
        Gates(**change.gates),
        golden_passed=golden_passed,
    )
    status: Status = "promoted" if verdict.promote else "rejected"
    await pool.execute(
        "update public.engine_change set status = $2, verdict = $3, decided_at = now()"
        " where id = $1 and status = 'shadow'",
        change_id,
        status,
        "; ".join(verdict.reasons),
    )
    return change.model_copy(update={"status": status, "verdict": "; ".join(verdict.reasons)})


async def roll_back(pool: asyncpg.Pool, change_id: int, reasons: list[str]) -> bool:
    result = await pool.execute(
        "update public.engine_change set status = 'rolled_back', verdict = $2, decided_at = now()"
        " where id = $1 and status = 'promoted'",
        change_id,
        "rolled back: " + "; ".join(reasons),
    )
    return bool(result.endswith(" 1"))
