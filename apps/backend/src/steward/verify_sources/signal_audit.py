"""Audit samples, point-in-time checks and the derived-signal publish gate (plan D8.6).

A derived signal (insider net activity, control-stake change, customer concentration, deal
state, consensus vintage ...) may publish only after a person reviewed a sample of its source
evidence and the recorded result shows (a) enough sampled records, (b) evidence precision at or
above the signal's requirement and (c) no point-in-time violation - the value shown "as of" a
date must be the one that was knowable on that date.  `publish_gate` reads the recorded
`signal_audit_result` rows; with none recorded it blocks and says the signal was never audited.

The small pure helpers (`point_in_time_pick`, `deal_state_at`, `consensus_is_stale`,
`mention_is_relationship`) are reference oracles for the review samples in
tests/fixtures/audit: they define what a correct answer looks like so a reviewer's label and
the system's output can be compared; they do not produce published data.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import asyncpg


@dataclass(frozen=True, slots=True)
class SignalRequirement:
    min_samples: int = 30
    min_precision: Decimal = Decimal("0.95")
    min_pit_checked: int = 20
    max_pit_violations: int = 0
    max_audit_age_days: int = 180


# Every derived signal the app can publish.  Stricter requirements for signals that can move
# money-relevant conclusions (control stakes, deal states).
SIGNAL_REQUIREMENTS: dict[str, SignalRequirement] = {
    "financial_restatement_alert": SignalRequirement(),
    "segment_revenue": SignalRequirement(min_precision=Decimal("0.97")),
    "filed_notes_evidence": SignalRequirement(),
    "control_stake_change": SignalRequirement(min_precision=Decimal("0.98")),
    "insider_net_discretionary": SignalRequirement(min_precision=Decimal("0.98")),
    "customer_concentration": SignalRequirement(min_precision=Decimal("0.97")),
    "supplier_dependency": SignalRequirement(min_precision=Decimal("0.97")),
    "deal_state": SignalRequirement(min_precision=Decimal("0.98")),
    "analyst_consensus_vintage": SignalRequirement(),
}


@dataclass(frozen=True, slots=True)
class AuditObservation:
    """One reviewed record of a signal: was its evidence right, and was it point-in-time?"""

    sample_id: str
    evidence_correct: bool
    pit_ok: bool | None = None  # None = not point-in-time sensitive / not checked
    jurisdiction: str | None = None
    metric: str | None = None


@dataclass(frozen=True, slots=True)
class AuditSummary:
    sample_size: int
    evidence_correct: int
    pit_checked: int
    pit_violations: int
    by_jurisdiction: dict[str, dict[str, int]] = field(default_factory=dict)
    by_metric: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def precision(self) -> Decimal | None:
        return Decimal(self.evidence_correct) / self.sample_size if self.sample_size else None


def summarize(observations: Iterable[AuditObservation]) -> AuditSummary:
    rows = list(observations)
    ids = [r.sample_id for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate sample ids in an audit")
    by_j: dict[str, dict[str, int]] = defaultdict(lambda: {"samples": 0, "correct": 0})
    by_m: dict[str, dict[str, int]] = defaultdict(lambda: {"samples": 0, "correct": 0})
    for r in rows:
        for table, key in ((by_j, r.jurisdiction or "unknown"), (by_m, r.metric or "unknown")):
            table[key]["samples"] += 1
            table[key]["correct"] += 1 if r.evidence_correct else 0
    checked = [r for r in rows if r.pit_ok is not None]
    return AuditSummary(
        len(rows),
        sum(1 for r in rows if r.evidence_correct),
        len(checked),
        sum(1 for r in checked if r.pit_ok is False),
        dict(by_j),
        dict(by_m),
    )


async def record_audit_result(
    pool: asyncpg.Pool,
    signal_name: str,
    observations: Sequence[AuditObservation],
    *,
    reviewer: str | None = None,
    jurisdiction: str | None = None,
    metric: str | None = None,
    audited_at: dt.datetime | None = None,
) -> AuditSummary:
    if signal_name not in SIGNAL_REQUIREMENTS:
        raise ValueError(f"unknown signal {signal_name!r}")
    summary = summarize(observations)
    await pool.execute(
        """insert into public.signal_audit_result
           (signal_name,jurisdiction,metric,sample_size,evidence_correct,pit_checked,
            pit_violations,reviewer,details,audited_at)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,coalesce($10, now()))""",
        signal_name,
        jurisdiction,
        metric,
        summary.sample_size,
        summary.evidence_correct,
        summary.pit_checked,
        summary.pit_violations,
        reviewer,
        json.dumps(
            {
                "by_jurisdiction": summary.by_jurisdiction,
                "by_metric": summary.by_metric,
                "incorrect": [o.sample_id for o in observations if not o.evidence_correct],
                "pit_violations": [o.sample_id for o in observations if o.pit_ok is False],
            }
        ),
        audited_at,
    )
    return summary


@dataclass(frozen=True, slots=True)
class PublishDecision:
    signal: str
    allowed: bool
    sample_size: int
    precision: Decimal | None
    pit_checked: int
    pit_violations: int
    audited_at: dt.datetime | None
    reasons: tuple[str, ...]


class PublishBlocked(RuntimeError):
    def __init__(self, decision: PublishDecision) -> None:
        self.decision = decision
        super().__init__(
            f"signal {decision.signal!r} is not publishable: " + "; ".join(decision.reasons)
        )


def evaluate_requirement(
    signal: str,
    requirement: SignalRequirement,
    *,
    sample_size: int,
    evidence_correct: int,
    pit_checked: int,
    pit_violations: int,
    audited_at: dt.datetime | None,
    now: dt.datetime,
) -> PublishDecision:
    reasons: list[str] = []
    precision = Decimal(evidence_correct) / sample_size if sample_size else None
    if audited_at is None:
        reasons.append("never audited: no reviewed sample is recorded")
    else:
        age = now - audited_at
        if age > dt.timedelta(days=requirement.max_audit_age_days):
            reasons.append(
                f"audit is {age.days} days old (limit {requirement.max_audit_age_days}); re-sample"
            )
        if sample_size < requirement.min_samples:
            reasons.append(f"{sample_size} sampled records, {requirement.min_samples} required")
        if precision is not None and precision < requirement.min_precision:
            reasons.append(
                f"evidence precision {precision:.3f} below required {requirement.min_precision}"
            )
        if pit_checked < requirement.min_pit_checked:
            reasons.append(
                f"{pit_checked} point-in-time checks, {requirement.min_pit_checked} required"
            )
        if pit_violations > requirement.max_pit_violations:
            reasons.append(
                f"{pit_violations} point-in-time violations "
                f"(allowed {requirement.max_pit_violations})"
            )
    return PublishDecision(
        signal,
        not reasons,
        sample_size,
        precision,
        pit_checked,
        pit_violations,
        audited_at,
        tuple(reasons),
    )


async def publish_gate(
    pool: asyncpg.Pool, signal_name: str, *, now: dt.datetime | None = None
) -> PublishDecision:
    """Whether the signal may publish, from the latest recorded audit result."""
    now = now or dt.datetime.now(dt.UTC)
    requirement = SIGNAL_REQUIREMENTS.get(signal_name)
    if requirement is None:
        return PublishDecision(
            signal_name, False, 0, None, 0, 0, None, (f"unknown signal {signal_name!r}",)
        )
    # The overall (jurisdiction-less) audit is what authorizes publication; a jurisdiction-only
    # row narrows nothing and is reported in the coverage report instead.
    row = await pool.fetchrow(
        """select sample_size, evidence_correct, pit_checked, pit_violations, audited_at
             from public.signal_audit_result
            where signal_name = $1 and jurisdiction is null and metric is null
            order by audited_at desc, id desc limit 1""",
        signal_name,
    )
    if row is None:
        return evaluate_requirement(
            signal_name,
            requirement,
            sample_size=0,
            evidence_correct=0,
            pit_checked=0,
            pit_violations=0,
            audited_at=None,
            now=now,
        )
    return evaluate_requirement(
        signal_name,
        requirement,
        sample_size=row["sample_size"],
        evidence_correct=row["evidence_correct"],
        pit_checked=row["pit_checked"],
        pit_violations=row["pit_violations"],
        audited_at=row["audited_at"],
        now=now,
    )


async def require_publishable(pool: asyncpg.Pool, signal_name: str) -> PublishDecision:
    decision = await publish_gate(pool, signal_name)
    if not decision.allowed:
        raise PublishBlocked(decision)
    return decision


# ----------------------------------------------------------------------------- coverage
async def coverage_by_jurisdiction_and_metric(
    pool: asyncpg.Pool, *, since: dt.datetime | None = None
) -> list[dict[str, Any]]:
    """Measured coverage: records, entities and freshness per jurisdiction and metric, from the
    observation tables.  A jurisdiction/metric with no rows does not appear - absence is shown
    by the caller as "no coverage", never as a zero measurement."""
    rows = await pool.fetch(
        """select coalesce(geography, 'unknown') jurisdiction, domain, metric,
                  count(*) observations,
                  count(distinct coalesce(security_id, issuer_id, 0)) entities,
                  count(*) filter (where value is null and text_value is null) unknown_values,
                  count(distinct source) sources,
                  min(period_end) earliest_period, max(period_end) latest_period,
                  max(known_at) newest_known_at
             from public.source_observation
            where ($1::timestamptz is null or known_at >= $1)
            group by 1, 2, 3
         union all
           select geography, 'macro', series_id, count(*), 0, count(*) filter (where value is null),
                  count(distinct source), min(period), max(period), max(known_at)
             from public.macro_observation
            where ($1::timestamptz is null or known_at >= $1)
            group by geography, series_id
            order by 1, 2, 3""",
        since,
    )
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------------- oracles
@dataclass(frozen=True, slots=True)
class VersionedRecord:
    key: str
    value: Decimal | None
    known_at: dt.datetime
    revision: int = 0


def point_in_time_pick(
    records: Sequence[VersionedRecord], as_of: dt.datetime
) -> VersionedRecord | None:
    """The record knowable at `as_of`: latest known_at (then revision) not after it."""
    visible = [r for r in records if r.known_at <= as_of]
    return max(visible, key=lambda r: (r.known_at, r.revision), default=None)


def pit_correct(
    records: Sequence[VersionedRecord], as_of: dt.datetime, claimed: VersionedRecord | None
) -> bool:
    return point_in_time_pick(records, as_of) == claimed


DealState = Literal["announced", "pending", "completed", "terminated", "reversed", "unknown"]


@dataclass(frozen=True, slots=True)
class DealEvent:
    state: DealState
    known_at: dt.datetime
    reverses_completed: bool = False


def deal_state_at(events: Sequence[DealEvent], as_of: dt.datetime) -> DealState:
    """State of a deal as knowable at `as_of`.  A later termination or unwind (reversal)
    supersedes completion only from the moment it became known."""
    visible = sorted((e for e in events if e.known_at <= as_of), key=lambda e: e.known_at)
    if not visible:
        return "unknown"
    state: DealState = "unknown"
    for e in visible:
        if e.state == "terminated" and state == "completed" and e.reverses_completed:
            state = "reversed"
        else:
            state = e.state
    return state


def consensus_is_stale(
    published_at: dt.datetime | None, as_of: dt.datetime, *, max_age_days: int = 120
) -> bool:
    """A consensus vintage with no publication date, or older than the limit, is stale."""
    return published_at is None or as_of - published_at > dt.timedelta(days=max_age_days)


_ROLE = re.compile(
    r"\b(?:customers?|clients?|supplier|suppliers|vendors?|supplies|supplied by|purchases? from"
    r"|sells? to|sold to|purchased from|accounted for|contracted with|revenue from)\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(?:no|not|never|none|nor|neither)\b[^.]{0,60}\b(?:customers?|suppliers?|vendors?)\b"
    r"|\b(?:competitors?|competes? with|compared (?:to|with)|peers?)\b",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%")


def mention_is_relationship(passage: str, counterparty: str | None) -> bool:
    """A genuine, named customer/supplier relationship stated in the passage.

    Rejects negations, competitor/peer mentions and passages where the counterparty is not
    named.  An anonymous counterparty ("one customer accounted for 12%") is *not* a named
    relationship - see `anonymous_share`."""
    if not counterparty or counterparty.casefold() not in passage.casefold():
        return False
    if _NEGATION.search(passage):
        return False
    return bool(_ROLE.search(passage))


_ANON = re.compile(
    r"\b(?:one|two|three|a single|a)\s+(?:large\s+)?(?:customers?|clients?)\b"
    r"|\b(?:customer|client)\s+[A-Z]\b|\bmajor customer\b",
    re.IGNORECASE,
)


def anonymous_share(passage: str) -> Decimal | None:
    """The disclosed percentage for an unnamed customer, as a fraction; None if not stated or
    the customer is named.  The share may not be attached to any named issuer."""
    if not _ANON.search(passage):
        return None
    match = _PERCENT.search(passage)
    return Decimal(match.group(1)) / 100 if match else None
