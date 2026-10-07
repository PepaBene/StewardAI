"""P5: customer, supplier and revenue-dependency claims, as pure functions.

The rules this module enforces:
  * a named customer, an anonymised "Customer A", a group ("three customers") and an industry- or
    geography-only exposure are different claim shapes; an anonymous or group claim never carries a
    name or a company id, and nothing here ever guesses who "Customer A" is;
  * only confirmed claims reach a concentration figure or a scenario; a suggestion stays visible
    with its state and never counts;
  * a reported share is a share OF something (total revenue, a segment, purchases...) in a period;
    shares of different denominators or periods are never added;
  * what the claims do not account for is an explicit unknown remainder, with a lower and an upper
    bound, not a rounded-off zero;
  * a scenario propagates only numeric, confirmed, dated edges, at most two hops, and lists every
    qualitative edge as non-quantified;
  * a procurement award is a contract signal, never a share of the issuer's revenue.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

ZERO = Decimal(0)
ONE = Decimal(1)

Shape = Literal["named", "anonymous", "group", "segment_only"]
Relationship = Literal["customer", "supplier", "procurement_award"]
DenominatorKind = Literal[
    "total_revenue", "segment_revenue", "cost_of_sales", "purchases", "receivables", "other"
]
State = Literal["suggested", "confirmed", "rejected", "disputed"]


@dataclass(frozen=True)
class Dependency:
    id: int
    reporter: str  # the issuer that made the disclosure
    relationship: Relationship
    shape: Shape
    status: State
    known_at: dt.datetime
    passage: str
    label: str | None = None  # an anonymous claim's own wording, e.g. "Customer A"
    counterparty_key: str | None = None
    counterparty_name: str | None = None
    counterparty_count: int | None = None
    share: Decimal | None = None  # a fraction, 0 to 1
    denominator: DenominatorKind | None = None
    period_end: dt.date | None = None
    fiscal_period: str | None = None
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    evidence_id: int | None = None


def problems(d: Dependency) -> list[str]:
    """Why a claim is malformed. A claim with problems is never counted."""
    out: list[str] = []
    named = d.shape == "named"
    if named and not d.counterparty_name:
        out.append("a named claim must name its counterparty")
    if not named and (d.counterparty_name or d.counterparty_key):
        out.append(f"a {d.shape} claim must not carry a name or company id")
    if d.shape == "group" and (d.counterparty_count or 0) < 2:
        out.append("a group claim must say how many parties it covers")
    if d.share is not None and not ZERO <= d.share <= ONE:
        out.append("a share must be between 0 and 1")
    if d.share is not None and d.denominator is None:
        out.append("a share needs to say what it is a share of")
    if d.relationship == "procurement_award" and (d.share is not None or d.denominator is not None):
        out.append("a procurement award is a contract signal, not a share of revenue")
    if not d.passage.strip():
        out.append("a claim needs its source passage")
    return out


def visible(deps: Iterable[Dependency], as_of: dt.datetime) -> list[Dependency]:
    """Claims known by `as_of` and valid that day. `status` must already be the status at as_of."""
    day = as_of.date()
    return [
        d
        for d in deps
        if d.known_at <= as_of
        and (d.valid_from is None or d.valid_from <= day)
        and (d.valid_to is None or day <= d.valid_to)
    ]


# ---------------------------------------------------------------------------- concentration
@dataclass(frozen=True)
class Entry:
    id: int
    label: str
    shape: Shape
    share: Decimal
    evidence_id: int | None
    known_at: dt.datetime


@dataclass(frozen=True)
class GroupClaim:
    id: int
    label: str
    count: int
    share: Decimal


@dataclass(frozen=True)
class Concentration:
    reporter: str
    relationship: str
    denominator: str
    period: str
    entries: tuple[Entry, ...]  # single parties, largest first
    groups: tuple[GroupClaim, ...]
    lower_bound: Decimal  # the single-party shares added up
    reported_floor: Decimal  # at least this much is concentrated (a group figure is a floor too)
    unknown_remainder: Decimal  # what no confirmed claim accounts for
    largest: Entry | None
    exceeds_whole: bool
    suggested_not_counted: int
    notes: tuple[str, ...] = field(default_factory=tuple)


def _entry_label(d: Dependency) -> str:
    if d.shape == "named":
        return d.counterparty_name or "Unnamed"
    return d.label or (
        "Customer (not named)" if d.relationship == "customer" else "Supplier (not named)"
    )


def concentration(
    deps: Iterable[Dependency], as_of: dt.datetime, *, relationship: Relationship = "customer"
) -> list[Concentration]:
    """One result per (denominator, period): shares of different bases are never combined."""
    live = [d for d in visible(deps, as_of) if d.relationship == relationship]
    groups: dict[tuple[str, str], list[Dependency]] = defaultdict(list)  # (denominator, period)
    pending: dict[tuple[str, str], int] = defaultdict(int)
    for d in live:
        if d.share is None or d.denominator is None or d.shape == "segment_only" or problems(d):
            continue
        key = (
            d.denominator,
            d.period_end.isoformat() if d.period_end else d.fiscal_period or "undated",
        )
        if d.status == "confirmed":
            groups[key].append(d)
        elif d.status in ("suggested", "disputed"):
            pending[key] += 1
    out: list[Concentration] = []
    for (den, period), rows in sorted(groups.items()):
        singles = sorted(
            (
                Entry(r.id, _entry_label(r), r.shape, r.share or ZERO, r.evidence_id, r.known_at)
                for r in rows
                if r.shape in ("named", "anonymous")
            ),
            key=lambda e: (-e.share, e.label),
        )
        grp = tuple(
            GroupClaim(r.id, _entry_label(r), r.counterparty_count or 0, r.share or ZERO)
            for r in sorted(rows, key=lambda r: r.id)
            if r.shape == "group"
        )
        lower = sum((e.share for e in singles), ZERO)
        floor = max([lower, *(g.share for g in grp)])
        notes: list[str] = []
        if grp:
            notes.append(
                "A group figure may already include customers listed separately, so it is not "
                "added to them; it only raises the floor."
            )
        if lower > ONE:
            notes.append("The single-party shares add to more than the whole: the claims disagree.")
        out.append(
            Concentration(
                rows[0].reporter,
                relationship,
                den,
                period,
                tuple(singles),
                grp,
                lower,
                floor,
                max(ZERO, ONE - floor),
                singles[0] if singles else None,
                lower > ONE,
                pending.get((den, period), 0),
                tuple(notes),
            )
        )
    # a period with only suggestions still shows that something is waiting
    for waiting, n in pending.items():
        if waiting not in groups:
            out.append(
                Concentration(
                    "",
                    relationship,
                    waiting[0],
                    waiting[1],
                    (),
                    (),
                    ZERO,
                    ZERO,
                    ONE,
                    None,
                    False,
                    n,
                    ("Only suggested claims exist for this period; none is confirmed.",),
                )
            )
    return sorted(out, key=lambda c: (c.period, c.denominator), reverse=True)


# ------------------------------------------------------------------------ passage screening
_NEGATED = re.compile(
    r"\b(no (single )?customer|did not (have|derive|generate)"
    r"|no (individual )?customers? accounted|not (dependent|reliant) on)\b",
    re.I,
)
_HYPOTHETICAL = re.compile(
    r"\b(if we (lose|were to lose)|could (lose|be adversely)|may (lose|be adversely)"
    r"|would (adversely|materially)|in the event (that )?(a|our) (customer|supplier))\b",
    re.I,
)
_HISTORICAL = re.compile(
    r"\b(prior to 20\d\d|formerly|until 20\d\d|no longer (a|our)|ceased to be"
    r"|terminated (our|the) (agreement|relationship))\b",
    re.I,
)
_THIRD_PARTY = re.compile(
    r"\b(our competitors?|the industry|companies in our industry"
    r"|other (companies|issuers)|analysts?)\b",
    re.I,
)


def screen_passage(text: str) -> list[str]:
    """Cheap hints that a passage is NOT a current reported relationship. A hint puts the claim at
    the front of the audit queue; it never promotes or rejects anything by itself."""
    flags: list[str] = []
    for name, rx in (
        ("negated", _NEGATED),
        ("hypothetical", _HYPOTHETICAL),
        ("historical", _HISTORICAL),
        ("third_party", _THIRD_PARTY),
    ):
        if rx.search(text):
            flags.append(name)
    return flags


AUDIT_LABELS = (
    "true_relationship",
    "historical_only",
    "hypothetical",
    "negated",
    "unrelated_third_party",
    "ambiguous",
)


def audit_sample(ids: Sequence[int], n: int, seed: str = "p5.1") -> list[int]:
    """A reproducible sample: the same ids and seed always give the same claims."""
    ranked = sorted(ids, key=lambda i: hashlib.sha256(f"{seed}:{i}".encode()).hexdigest())
    return ranked[:n]


@dataclass(frozen=True)
class AuditResult:
    total: int
    true_relationships: int
    precision: Decimal | None
    by_label: Mapping[str, int]
    meets_bar: bool
    reason: str


def audit_precision(
    labels: Mapping[int, str],
    *,
    min_claims: int = 100,
    min_precision: Decimal = Decimal("0.95"),
    deliberate_false_positive_ids: Iterable[int] = (),
    min_deliberate: int = 5,
) -> AuditResult:
    """Precision of suggested relationships on a labelled sample. The bar needs enough claims and
    enough deliberate false-positive phrases, so a sample of easy cases cannot pass."""
    bad = [v for v in labels.values() if v not in AUDIT_LABELS]
    if bad:
        raise ValueError(f"unknown audit label {bad[0]!r}")
    counts: dict[str, int] = defaultdict(int)
    for v in labels.values():
        counts[v] += 1
    total = len(labels)
    true = counts.get("true_relationship", 0)
    precision = Decimal(true) / total if total else None
    planted = [i for i in deliberate_false_positive_ids if i in labels]
    if total < min_claims:
        return AuditResult(
            total, true, precision, dict(counts), False, f"{total} of {min_claims} claims labelled"
        )
    if len(planted) < min_deliberate:
        return AuditResult(
            total,
            true,
            precision,
            dict(counts),
            False,
            f"only {len(planted)} deliberate false-positive phrases in the sample",
        )
    caught = sum(1 for i in planted if labels[i] != "true_relationship")
    if caught < len(planted):
        return AuditResult(
            total,
            true,
            precision,
            dict(counts),
            False,
            "a deliberate false positive was labelled a true relationship",
        )
    ok = precision is not None and precision >= min_precision
    return AuditResult(
        total,
        true,
        precision,
        dict(counts),
        ok,
        "meets the bar" if ok else f"precision {precision} is below {min_precision}",
    )


# --------------------------------------------------------------------------------- scenarios
@dataclass(frozen=True)
class Edge:
    id: int
    supplier: str  # the party that sells
    customer: str  # the party that buys
    share: (
        Decimal | None
    )  # the supplier's revenue from this customer, a fraction; None = qualitative
    status: State
    known_at: dt.datetime
    valid_to: dt.date | None = None


@dataclass(frozen=True)
class Impact:
    entity: str
    hop: int
    lower: Decimal  # revenue hit from disclosed edges only
    upper: Decimal  # adds the undisclosed remainder at the full shock
    coverage: Decimal  # share of the entity's revenue its disclosed edges account for
    via: tuple[int, ...]  # edge ids, origin first
    source_age_days: int


@dataclass(frozen=True)
class NonQuantified:
    supplier: str
    customer: str
    edge_id: int
    note: str


@dataclass(frozen=True)
class Scenario:
    origin: str
    shock: Decimal
    impacts: tuple[Impact, ...]
    non_quantified: tuple[NonQuantified, ...]
    notes: tuple[str, ...]


def shock_scenario(
    edges: Iterable[Edge],
    origin: str,
    shock: Decimal,
    as_of: dt.datetime,
    *,
    max_hops: int = 2,
) -> Scenario:
    """The origin's revenue falls by `shock` (a fraction). Each supplier to the origin loses
    share x shock of its revenue, and each supplier to THEM loses share x that, up to `max_hops`.
    Only confirmed numeric edges known and valid at `as_of` carry numbers. The upper bound adds the
    supplier's undisclosed remainder at the full shock; the lower bound is disclosed edges only."""
    if not ZERO <= shock <= ONE:
        raise ValueError("a shock is a fraction between 0 and 1")
    if not 1 <= max_hops <= 2:
        raise ValueError("a scenario runs one or two hops")
    day = as_of.date()
    live = [
        e
        for e in edges
        if e.known_at <= as_of
        and (e.valid_to is None or day <= e.valid_to)
        and e.status == "confirmed"
    ]
    numeric = [e for e in live if e.share is not None]
    qualitative = [e for e in live if e.share is None]
    by_customer: dict[str, list[Edge]] = defaultdict(list)
    by_supplier: dict[str, list[Edge]] = defaultdict(list)
    for e in numeric:
        by_customer[e.customer].append(e)
        by_supplier[e.supplier].append(e)

    def coverage(entity: str) -> Decimal:
        return min(ONE, sum((e.share or ZERO for e in by_supplier.get(entity, [])), ZERO))

    impacts: list[Impact] = []
    frontier: list[tuple[str, Decimal, Decimal, tuple[int, ...]]] = [(origin, shock, shock, ())]
    seen = {origin}
    for hop in range(1, max_hops + 1):
        nxt: list[tuple[str, Decimal, Decimal, tuple[int, ...]]] = []
        for party, lo, _up, path in frontier:
            for e in sorted(by_customer.get(party, []), key=lambda x: x.id):
                if e.supplier in seen:
                    continue
                share = e.share or ZERO
                lower = share * lo
                cov = coverage(e.supplier)
                upper = min(ONE, lower + (ONE - cov) * shock)
                age = max(0, (as_of - e.known_at).days)
                impacts.append(Impact(e.supplier, hop, lower, upper, cov, (*path, e.id), age))
                nxt.append((e.supplier, lower, upper, (*path, e.id)))
        seen.update(p for p, *_ in nxt)
        frontier = nxt
    reached = {i.entity for i in impacts} | {origin}
    non_q = tuple(
        NonQuantified(
            e.supplier,
            e.customer,
            e.id,
            "A confirmed relationship with no reported share: not quantified",
        )
        for e in sorted(qualitative, key=lambda x: x.id)
        if e.customer in reached or e.supplier in reached
    )
    notes = [
        "Only confirmed, dated edges with a reported share carry numbers.",
        "The upper bound treats every undisclosed customer as hit as hard as the origin.",
    ]
    return Scenario(origin, shock, tuple(impacts), non_q, tuple(notes))


# ----------------------------------------------------------------------------- both sides
def side_conflict(a: Dependency, b: Dependency, a_party: str, b_party: str) -> str | None:
    """Do two disclosures about the same pair contradict each other? Different denominators are not
    compared (a share of revenue and a share of purchases are different numbers)."""
    if a.reporter != a_party or b.reporter != b_party:
        return None
    if a.relationship == "customer" and b.relationship == "customer":
        return "each company says the other is its customer"
    if a.relationship == "supplier" and b.relationship == "supplier":
        return "each company says the other is its supplier"
    if (
        a.denominator is not None
        and a.denominator == b.denominator
        and a.share is not None
        and b.share is not None
        and a.period_end == b.period_end
        and a.relationship == b.relationship
    ):
        return None
    return None
