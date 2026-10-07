"""P6: public deals, financing and corporate actions, as pure functions.

The rules this module enforces:
  * every deal has a type with required and optional terms and the states it may pass through;
  * a state change needs evidence and a known-at date; an illegal change is rejected with its reason
    and a terminated deal stays in history; "rumored" needs its source named;
  * the state and the terms at a date use only what was KNOWN by then (`known_at <= as_of`), and the
    effective date of an event is never mistaken for the date it became known;
  * an amendment adds a term version; the latest version known by the date wins, none is edited;
  * an absent term is a typed unknown (`not_stated`), never zero, and is never filled from another
    term, a headline or an average;
  * an impact is computed only from stated inputs, lists every input and a formula version, and is
    null with a reason when an input is missing; premium uses an unaffected price dated BEFORE the
    announcement (an unaffected date on or after it is rejected, an old one is flagged stale);
  * announced terms, management projections and owner scenarios are separate labelled kinds;
  * the legal closing, the first reporting period that includes the target, and the broker's
    corporate-action posting are three separate dates, none inferred from another.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

ZERO = Decimal(0)
ONE = Decimal(1)
FORMULA_VERSION = "deal-impacts-1"
STALE_UNAFFECTED_DAYS = 10  # calendar days between the unaffected price and the announcement
FAR_FUTURE = dt.datetime(9999, 12, 31, tzinfo=dt.UTC)

DealType = Literal[
    "acquisition",
    "merger",
    "divestiture",
    "ipo",
    "follow_on",
    "debt_issuance",
    "spin_off",
    "joint_venture",
    "strategic_investment",
    "buyback",
    "tender_offer",
    "material_agreement",
]
State = Literal[
    "rumored", "announced", "definitive", "approved", "closed", "terminated", "contested"
]
Origin = Literal["announced", "management_projection"]
ImpactOrigin = Literal["announced", "management_projection", "owner_scenario"]

NUMERIC_TERMS: tuple[str, ...] = (
    "price_per_share",
    "cash_amount",
    "stock_amount",
    "debt_amount",
    "earnout_amount",
    "cash_pct",
    "stock_pct",
    "exchange_ratio",
    "stake_pct",
    "financing_amount",
    "refinanced_amount",
    "termination_fee",
    "expected_synergies",
    "authorized_amount",
    "executed_amount",
    "unaffected_price",
)
CONSIDERATION: tuple[str, ...] = ("cash_amount", "stock_amount", "debt_amount", "earnout_amount")
FRACTION_TERMS = ("cash_pct", "stock_pct", "stake_pct")
TERM_LABEL: dict[str, str] = {
    "price_per_share": "Price per share",
    "cash_amount": "Cash",
    "stock_amount": "Stock",
    "debt_amount": "Debt",
    "earnout_amount": "Earnout",
    "cash_pct": "Cash share",
    "stock_pct": "Stock share",
    "exchange_ratio": "Exchange ratio",
    "stake_pct": "Stake",
    "financing_amount": "Financing",
    "refinanced_amount": "Debt refinanced",
    "termination_fee": "Termination fee",
    "expected_synergies": "Expected synergies",
    "authorized_amount": "Buyback authorization",
    "executed_amount": "Bought back so far",
    "unaffected_price": "Unaffected price",
    "closing_conditions": "Closing conditions",
    "currency": "Currency",
    "consideration": "Consideration",
    "unaffected_date": "Unaffected price date",
}


# -------------------------------------------------------------------------------------- taxonomy
@dataclass(frozen=True)
class TypeSpec:
    label: str
    required: tuple[
        str, ...
    ]  # a term, "currency", or "consideration" (any of cash/stock/debt/earnout)
    optional: tuple[str, ...]
    states: tuple[State, ...]  # states this type may reach


_FULL: tuple[State, ...] = (
    "rumored",
    "announced",
    "definitive",
    "approved",
    "closed",
    "terminated",
    "contested",
)
_NO_CONTEST: tuple[State, ...] = (
    "rumored",
    "announced",
    "definitive",
    "approved",
    "closed",
    "terminated",
)
_PLAIN: tuple[State, ...] = ("rumored", "announced", "definitive", "closed", "terminated")
_ISSUE: tuple[State, ...] = ("announced", "definitive", "closed", "terminated")
_MA_OPTIONAL = (
    "price_per_share",
    "cash_amount",
    "stock_amount",
    "debt_amount",
    "earnout_amount",
    "cash_pct",
    "stock_pct",
    "exchange_ratio",
    "stake_pct",
    "financing_amount",
    "termination_fee",
    "expected_synergies",
    "unaffected_price",
    "closing_conditions",
)

TAXONOMY: dict[str, TypeSpec] = {
    "acquisition": TypeSpec("Acquisition", ("consideration", "currency"), _MA_OPTIONAL, _FULL),
    "merger": TypeSpec("Merger", ("exchange_ratio",), _MA_OPTIONAL, _FULL),
    "divestiture": TypeSpec(
        "Divestiture or asset sale", ("consideration", "currency"), _MA_OPTIONAL, _FULL
    ),
    "ipo": TypeSpec(
        "IPO", ("price_per_share", "currency"), ("financing_amount", "stake_pct"), _PLAIN
    ),
    "follow_on": TypeSpec(
        "Follow-on offering", ("price_per_share", "currency"), ("financing_amount",), _ISSUE
    ),
    "debt_issuance": TypeSpec(
        "Debt issuance or refinancing",
        ("financing_amount", "currency"),
        ("refinanced_amount", "debt_amount"),
        _ISSUE,
    ),
    "spin_off": TypeSpec(
        "Spin-off", ("exchange_ratio",), ("closing_conditions", "stake_pct"), _NO_CONTEST
    ),
    "joint_venture": TypeSpec(
        "Joint venture", ("stake_pct",), ("cash_amount", "closing_conditions"), _FULL
    ),
    "strategic_investment": TypeSpec(
        "Strategic investment",
        ("consideration", "currency"),
        ("stake_pct", "price_per_share", "closing_conditions"),
        _FULL,
    ),
    "buyback": TypeSpec(
        "Buyback", ("authorized_amount", "currency"), ("executed_amount", "price_per_share"), _ISSUE
    ),
    "tender_offer": TypeSpec(
        "Tender offer",
        ("price_per_share", "currency"),
        ("stake_pct", "unaffected_price", "financing_amount", "closing_conditions"),
        _FULL,
    ),
    "material_agreement": TypeSpec(
        "Material commercial agreement", (), ("cash_amount", "closing_conditions"), _ISSUE
    ),
}

START_STATES: frozenset[str] = frozenset({"rumored", "announced", "definitive"})
NEXT_STATES: dict[str, frozenset[str]] = {
    "rumored": frozenset({"announced", "definitive", "terminated"}),
    "announced": frozenset({"definitive", "approved", "closed", "terminated", "contested"}),
    "definitive": frozenset({"approved", "closed", "terminated", "contested"}),
    "approved": frozenset({"closed", "terminated", "contested"}),
    "contested": frozenset({"announced", "definitive", "approved", "closed", "terminated"}),
    "closed": frozenset(),
    "terminated": frozenset(),
}
ACTIVE_STATES: frozenset[str] = frozenset(
    {"rumored", "announced", "definitive", "approved", "contested"}
)


def transition_problem(deal_type: str, from_state: str | None, to_state: str) -> str | None:
    """Why a move from `from_state` to `to_state` is illegal for this type, or None."""
    spec = TAXONOMY.get(deal_type)
    if spec is None:
        return f"unknown deal type {deal_type!r}"
    if to_state not in NEXT_STATES:
        return f"unknown state {to_state!r}"
    if to_state not in spec.states:
        return f"a {spec.label.lower()} cannot be {to_state}"
    if from_state is None:
        if to_state in START_STATES:
            return None
        return f"a deal cannot first be seen as {to_state}; it needs an earlier state"
    if from_state == to_state:
        return f"already {to_state}; an amendment adds a term version, not a state"
    if to_state not in NEXT_STATES.get(from_state, frozenset()):
        end = (
            " (a closed or terminated deal is final)"
            if from_state in ("closed", "terminated")
            else ""
        )
        return f"{from_state} cannot move to {to_state}{end}"
    return None


# ----------------------------------------------------------------------------------------- state
@dataclass(frozen=True)
class Transition:
    id: int
    state: str
    effective_at: dt.datetime
    known_at: dt.datetime | None
    evidence_id: int | None
    document_version_id: int | None = None
    rumor_source: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class StateResolution:
    state: str | None
    applied: tuple[Transition, ...]
    rejected: tuple[tuple[Transition, str], ...]


def own_problem(t: Transition) -> str | None:
    if t.evidence_id is None:
        return "evidence is mandatory for a state change"
    if t.known_at is None:
        return "a known-at date is mandatory for a state change"
    if t.state == "rumored" and not (t.rumor_source or "").strip():
        return "a rumor must name its source and is shown labelled as a rumor"
    return None


def resolve_state(
    deal_type: str, transitions: Sequence[Transition], as_of: dt.datetime
) -> StateResolution:
    """The state at `as_of` from the transitions KNOWN by then, with every rejection and its reason.
    A later-known transition never changes an earlier view."""
    rejected: list[tuple[Transition, str]] = []
    known: list[Transition] = []
    for t in transitions:
        problem = own_problem(t)
        if problem and t.known_at is None:
            rejected.append((t, problem))
        elif t.known_at is not None and t.known_at <= as_of:
            known.append(t)
    known.sort(key=lambda t: (t.known_at or FAR_FUTURE, t.effective_at, t.id))  # order learned
    applied: list[Transition] = []
    current: str | None = None
    for t in known:
        problem = own_problem(t) or transition_problem(deal_type, current, t.state)
        if problem is None and applied and t.effective_at < applied[-1].effective_at:
            problem = "effective date is before the previous state's"
        if problem:
            rejected.append((t, problem))
            continue
        applied.append(t)
        current = t.state
    return StateResolution(current, tuple(applied), tuple(rejected))


def validate_history(deal_type: str, transitions: Sequence[Transition]) -> list[tuple[int, str]]:
    """Every transition that can never apply, whatever the date."""
    return [(t.id, why) for t, why in resolve_state(deal_type, transitions, FAR_FUTURE).rejected]


# ----------------------------------------------------------------------------------------- terms
@dataclass(frozen=True)
class TermVersion:
    id: int
    version_no: int
    known_at: dt.datetime
    evidence_id: int | None
    origin: Origin = "announced"
    supersedes_id: int | None = None
    currency: str | None = None
    values: Mapping[str, Decimal] = field(default_factory=lambda: {})
    closing_conditions: str | None = None
    unaffected_date: dt.date | None = None
    unaffected_source: str | None = None
    document_version_id: int | None = None
    event_effective_at: dt.datetime | None = None


@dataclass(frozen=True)
class TypedTerm:
    name: str
    state: Literal["stated", "not_stated"]
    value: Decimal | None


@dataclass(frozen=True)
class TermChange:
    field: str
    label: str
    old: str | None
    new: str | None


def typed_terms(tv: TermVersion | None) -> list[TypedTerm]:
    """Every numeric term, stated or typed unknown. A missing version has no stated term."""
    vals = tv.values if tv else {}
    return [
        TypedTerm(n, "stated", vals[n]) if n in vals else TypedTerm(n, "not_stated", None)
        for n in NUMERIC_TERMS
    ]


def version_problems(tv: TermVersion) -> list[str]:
    out: list[str] = []
    if tv.evidence_id is None:
        out.append("a term version needs the evidence it came from")
    for name, v in tv.values.items():
        if v < 0:
            out.append(f"{TERM_LABEL[name].lower()} is negative")
        if name in FRACTION_TERMS and v > ONE:
            out.append(f"{TERM_LABEL[name].lower()} should be a fraction between 0 and 1")
    if tv.values.get("cash_pct", ZERO) + tv.values.get("stock_pct", ZERO) > ONE:
        out.append("cash share and stock share add to more than 100%")
    if tv.unaffected_date is not None and "unaffected_price" not in tv.values:
        out.append("an unaffected date is given without an unaffected price")
    return out


def latest_version(
    versions: Sequence[TermVersion], as_of: dt.datetime, origin: Origin | None = "announced"
) -> TermVersion | None:
    """The newest version known by `as_of` (an amendment supersedes by version number)."""
    seen = [v for v in versions if v.known_at <= as_of and (origin is None or v.origin == origin)]
    return max(seen, key=lambda v: (v.version_no, v.known_at, v.id), default=None)


def versions_known(versions: Sequence[TermVersion], as_of: dt.datetime) -> list[TermVersion]:
    return sorted((v for v in versions if v.known_at <= as_of), key=lambda v: (v.version_no, v.id))


def term_changes(old: TermVersion | None, new: TermVersion) -> list[TermChange]:
    """What an amendment changed, in plain terms: "price 42.00 -> 44.50", added or removed."""
    out: list[TermChange] = []
    ov = old.values if old else {}
    for name in NUMERIC_TERMS:
        a, b = ov.get(name), new.values.get(name)
        if a != b:
            out.append(
                TermChange(
                    name,
                    TERM_LABEL[name],
                    None if a is None else str(a),
                    None if b is None else str(b),
                )
            )
    oc = old.currency if old else None
    if oc != new.currency:
        out.append(TermChange("currency", "Currency", oc, new.currency))
    ot = old.closing_conditions if old else None
    if ot != new.closing_conditions:
        out.append(
            TermChange("closing_conditions", "Closing conditions", ot, new.closing_conditions)
        )
    return out


def missing_fields(deal_type: str, tv: TermVersion | None) -> list[str]:
    """Required terms the latest version does not state (plus the unaffected price a premium
    needs)."""
    spec = TAXONOMY[deal_type]
    vals = tv.values if tv else {}
    out: list[str] = []
    for f in spec.required:
        if f == "consideration":
            ok = any(c in vals for c in CONSIDERATION)
        elif f == "currency":
            ok = bool(tv and tv.currency)
        else:
            ok = f in vals
        if not ok:
            out.append(f)
    if deal_type in ("acquisition", "tender_offer") and "price_per_share" in vals:
        if "unaffected_price" not in vals:
            out.append("unaffected_price")
        if tv and tv.unaffected_date is None:
            out.append("unaffected_date")
    return out


def deal_size(tv: TermVersion | None) -> Decimal | None:
    """The size used by comparison filters: stated consideration parts added (a lower bound when a
    part is not stated), else the stated financing or buyback authorization. None when nothing
    is."""
    if tv is None:
        return None
    parts = [tv.values[c] for c in CONSIDERATION if c in tv.values]
    if parts:
        return sum(parts, ZERO)
    for f in ("financing_amount", "authorized_amount"):
        if f in tv.values:
            return tv.values[f]
    return None


# --------------------------------------------------------------------------------------- impacts
@dataclass(frozen=True)
class DealContext:
    """Facts that live outside the deal (share counts, net debt) and where they came from."""

    announced_on: dt.date | None = None
    acquirer_shares: Decimal | None = None
    target_shares: Decimal | None = None
    parent_shares: Decimal | None = None
    net_debt_before: Decimal | None = None
    enterprise_value_before: Decimal | None = None
    acquirer_segments: Mapping[str, Decimal] | None = None
    target_segments: Mapping[str, Decimal] | None = None


@dataclass(frozen=True)
class Impact:
    kind: str
    label: str
    origin: ImpactOrigin
    value: Decimal | None
    unit: Literal["fraction", "currency", "shares", "ratio"]
    reason: str | None  # why value is None
    inputs: Mapping[str, str]
    formula: str
    term_version_ids: tuple[int, ...]
    currency: str | None = None
    flags: tuple[str, ...] = ()
    detail: Mapping[str, str] = field(default_factory=lambda: {})
    formula_version: str = FORMULA_VERSION


def _impact(
    kind: str,
    label: str,
    origin: ImpactOrigin,
    unit: Literal["fraction", "currency", "shares", "ratio"],
    formula: str,
    tv: TermVersion | None,
    inputs: Mapping[str, Decimal | str | None],
    *,
    value: Decimal | None = None,
    reason: str | None = None,
    flags: tuple[str, ...] = (),
    detail: Mapping[str, str] | None = None,
) -> Impact:
    if value is None and reason is None:
        reason = "an input is missing"
    return Impact(
        kind,
        label,
        origin,
        value if reason is None else None,
        unit,
        reason,
        {k: str(v) for k, v in inputs.items() if v is not None},
        formula,
        (tv.id,) if tv else (),
        tv.currency if tv else None,
        flags,
        detail or {},
    )


def premium(
    tv: TermVersion | None,
    announced_on: dt.date | None,
    *,
    offer_price: Decimal | None = None,
    origin: ImpactOrigin = "announced",
) -> Impact:
    """Offer price over the unaffected price, minus one. The unaffected price must be dated BEFORE
    the
    announcement; one dated on or after it already reflects the deal and is rejected. A price more
    than
    STALE_UNAFFECTED_DAYS older than the announcement is computed but flagged stale."""
    scenario = origin == "owner_scenario"
    label = "Premium in your scenario" if scenario else "Announced premium"
    offer = offer_price if scenario else (tv.values.get("price_per_share") if tv else None)
    unaffected = tv.values.get("unaffected_price") if tv else None
    inputs: dict[str, Decimal | str | None] = {
        "offer_price": offer,
        "unaffected_price": unaffected,
        "unaffected_date": tv.unaffected_date.isoformat() if tv and tv.unaffected_date else None,
        "announced_on": announced_on.isoformat() if announced_on else None,
        "currency": tv.currency if tv else None,
    }

    def null(reason: str) -> Impact:
        return _impact(
            "premium",
            label,
            origin,
            "fraction",
            "offer / unaffected - 1",
            tv,
            inputs,
            reason=reason,
        )

    if tv is None:
        return null("no terms are known at this date")
    if offer is None:
        return null("no per-share price is stated; a headline total is never divided into a price")
    if unaffected is None:
        return null("no unaffected price is stated")
    if tv.unaffected_date is None:
        return null("the unaffected price has no date, so it cannot be called unaffected")
    if announced_on is None:
        return null("the announcement date is not known")
    if tv.unaffected_date >= announced_on:
        return null(
            f"the unaffected date {tv.unaffected_date} is on or after the announcement "
            f"{announced_on}, so that price already reflects the deal"
        )
    if tv.currency is None:
        return null("the currency is not stated")
    if unaffected <= 0 or offer <= 0:
        return null("a price must be positive")
    flags = (
        ("stale_unaffected_price",)
        if (announced_on - tv.unaffected_date).days > STALE_UNAFFECTED_DAYS
        else ()
    )
    return _impact(
        "premium",
        label,
        origin,
        "fraction",
        "offer / unaffected - 1",
        tv,
        inputs,
        value=offer / unaffected - ONE,
        flags=flags,
    )


def dilution(
    tv: TermVersion | None, ctx: DealContext, origin: ImpactOrigin = "announced"
) -> Impact:
    """Share of the combined company the target's holders own after a stated exchange ratio:
    new / (acquirer + new), new = ratio x target shares. Needs both share counts."""
    ratio = tv.values.get("exchange_ratio") if tv else None
    inputs: dict[str, Decimal | str | None] = {
        "exchange_ratio": ratio,
        "acquirer_shares": ctx.acquirer_shares,
        "target_shares": ctx.target_shares,
    }
    formula = "ratio * target / (acquirer + ratio * target)"
    if tv is None or ratio is None:
        why = "no exchange ratio is stated (an all-cash price issues no shares we can count)"
    elif ctx.acquirer_shares is None or ctx.target_shares is None:
        why = "the share counts are not known"
    elif ctx.acquirer_shares <= 0 or ctx.target_shares <= 0:
        why = "a share count must be positive"
    else:
        new = ratio * ctx.target_shares
        return _impact(
            "dilution",
            "Dilution from new shares",
            origin,
            "fraction",
            formula,
            tv,
            inputs,
            value=new / (ctx.acquirer_shares + new),
            detail={"new_shares": str(new), "pro_forma_shares": str(ctx.acquirer_shares + new)},
        )
    return _impact(
        "dilution", "Dilution from new shares", origin, "fraction", formula, tv, inputs, reason=why
    )


def debt_change(deal_type: str, tv: TermVersion | None, ctx: DealContext) -> Impact:
    """New debt as a share of enterprise value before the deal. A refinancing nets out the debt it
    repays only when that amount is stated; otherwise the figure is gross and flagged."""
    field_ = "financing_amount" if deal_type == "debt_issuance" else "debt_amount"
    gross = tv.values.get(field_) if tv else None
    repaid = tv.values.get("refinanced_amount") if tv else None
    inputs: dict[str, Decimal | str | None] = {
        field_: gross,
        "refinanced_amount": repaid,
        "enterprise_value_before": ctx.enterprise_value_before,
        "net_debt_before": ctx.net_debt_before,
    }
    formula = "(new debt - refinanced) / enterprise value before"
    if gross is None:
        why = "no debt amount is stated"
    elif ctx.enterprise_value_before is None or ctx.enterprise_value_before <= 0:
        why = "the enterprise value before the deal is not known"
    else:
        net = gross - (repaid if repaid is not None else ZERO)
        flags = (
            () if repaid is not None or deal_type != "debt_issuance" else ("gross_of_refinancing",)
        )
        detail = {"net_new_debt": str(net)}
        if ctx.net_debt_before is not None:
            detail["net_debt_after"] = str(ctx.net_debt_before + net)
        return _impact(
            "debt_ev",
            "New debt against enterprise value",
            "announced",
            "fraction",
            formula,
            tv,
            inputs,
            value=net / ctx.enterprise_value_before,
            flags=flags,
            detail=detail,
        )
    return _impact(
        "debt_ev",
        "New debt against enterprise value",
        "announced",
        "fraction",
        formula,
        tv,
        inputs,
        reason=why,
    )


def segment_mix(ctx: DealContext, tv: TermVersion | None = None) -> Impact:
    """Largest shift in the revenue mix when the target's segments are added to the acquirer's."""
    a, t = ctx.acquirer_segments, ctx.target_segments
    formula = "max |share after - share before| over segments"
    if not a or not t:
        why = "segment revenue is not known for " + ("the acquirer" if not a else "the target")
        return _impact(
            "segment_mix",
            "Change in segment mix",
            "announced",
            "fraction",
            formula,
            tv,
            {},
            reason=why,
        )
    ta, tt = sum(a.values(), ZERO), sum(t.values(), ZERO)
    if ta <= 0 or tt < 0:
        return _impact(
            "segment_mix",
            "Change in segment mix",
            "announced",
            "fraction",
            formula,
            tv,
            {},
            reason="segment revenue must be positive",
        )
    names = sorted(set(a) | set(t))
    detail: dict[str, str] = {}
    shift = ZERO
    for n in names:
        before = a.get(n, ZERO) / ta
        after = (a.get(n, ZERO) + t.get(n, ZERO)) / (ta + tt)
        shift = max(shift, abs(after - before))
        detail[n] = f"{before:.4f}->{after:.4f}"
    return _impact(
        "segment_mix",
        "Change in segment mix",
        "announced",
        "fraction",
        formula,
        tv,
        {"acquirer_revenue": ta, "target_revenue": tt},
        value=shift,
        detail=detail,
    )


def spin_off_distribution(tv: TermVersion | None, ctx: DealContext) -> Impact:
    """Shares handed out in a spin-off: stated distribution ratio x parent shares."""
    ratio = tv.values.get("exchange_ratio") if tv else None
    inputs: dict[str, Decimal | str | None] = {
        "distribution_ratio": ratio,
        "parent_shares": ctx.parent_shares,
    }
    formula = "ratio * parent shares"
    if ratio is None:
        why = "no distribution ratio is stated"
    elif ctx.parent_shares is None:
        why = "the parent's share count is not known"
    else:
        return _impact(
            "spin_off",
            "Shares distributed",
            "announced",
            "shares",
            formula,
            tv,
            inputs,
            value=ratio * ctx.parent_shares,
        )
    return _impact(
        "spin_off", "Shares distributed", "announced", "shares", formula, tv, inputs, reason=why
    )


def buyback_progress(tv: TermVersion | None) -> Impact:
    """Purchases so far over the authorization. An authorization is a ceiling, never a purchase."""
    auth = tv.values.get("authorized_amount") if tv else None
    done = tv.values.get("executed_amount") if tv else None
    inputs: dict[str, Decimal | str | None] = {"authorized_amount": auth, "executed_amount": done}
    formula = "executed / authorized"
    if auth is None:
        why = "no authorization amount is stated"
    elif done is None:
        why = "no purchases are stated; an authorization is not a purchase"
    elif auth <= 0:
        why = "the authorization must be positive"
    else:
        flags = ("executed_exceeds_authorization",) if done > auth else ()
        return _impact(
            "buyback",
            "Share of authorization used",
            "announced",
            "fraction",
            formula,
            tv,
            inputs,
            value=done / auth,
            flags=flags,
        )
    return _impact(
        "buyback",
        "Share of authorization used",
        "announced",
        "fraction",
        formula,
        tv,
        inputs,
        reason=why,
    )


def synergy_projection(versions: Sequence[TermVersion], as_of: dt.datetime) -> Impact:
    """Expected synergies are what management says it expects, from whichever known version states
    them. Always labelled as a management projection, never added to announced terms."""
    having = [v for v in versions if v.known_at <= as_of and "expected_synergies" in v.values]
    tv = max(having, key=lambda v: (v.version_no, v.known_at, v.id), default=None)
    if tv is None:
        return _impact(
            "synergies",
            "Management's expected synergies",
            "management_projection",
            "currency",
            "as stated",
            None,
            {},
            reason="no synergy figure is stated",
        )
    return _impact(
        "synergies",
        "Management's expected synergies",
        "management_projection",
        "currency",
        "as stated",
        tv,
        {"expected_synergies": tv.values["expected_synergies"]},
        value=tv.values["expected_synergies"],
    )


@dataclass(frozen=True)
class BridgeSegment:
    kind: str
    state: Literal["stated", "not_stated"]
    amount: Decimal | None


@dataclass(frozen=True)
class Bridge:
    currency: str | None
    segments: tuple[BridgeSegment, ...]
    known_total: Decimal
    unstated: tuple[str, ...]
    total_is_lower_bound: bool
    cash_pct: Decimal | None
    stock_pct: Decimal | None
    term_version_id: int | None


def consideration_bridge(tv: TermVersion | None) -> Bridge:
    """Cash, stock, debt and earnout as stated. A part that is not stated is its own unknown and is
    never counted as zero, so the total is a lower bound when any part is unknown."""
    vals = tv.values if tv else {}
    segs = tuple(
        BridgeSegment(c.removesuffix("_amount"), "stated", vals[c])
        if c in vals
        else BridgeSegment(c.removesuffix("_amount"), "not_stated", None)
        for c in CONSIDERATION
    )
    unstated = tuple(s.kind for s in segs if s.state == "not_stated")
    return Bridge(
        tv.currency if tv else None,
        segs,
        sum((s.amount or ZERO for s in segs), ZERO),
        unstated,
        bool(unstated),
        vals.get("cash_pct"),
        vals.get("stock_pct"),
        tv.id if tv else None,
    )


def impacts_for(
    deal_type: str,
    versions: Sequence[TermVersion],
    as_of: dt.datetime,
    ctx: DealContext,
    *,
    owner_offer_price: Decimal | None = None,
) -> list[Impact]:
    """Every supportable impact for one deal at `as_of`, each from the version ids it lists, so a
    recalculation with the same ids reproduces it and an amendment (new id) re-runs it."""
    tv = latest_version(versions, as_of, "announced")
    out: list[Impact] = []
    if deal_type in ("acquisition", "tender_offer", "merger"):
        out.append(premium(tv, ctx.announced_on))
        if owner_offer_price is not None:
            out.append(
                premium(
                    tv, ctx.announced_on, offer_price=owner_offer_price, origin="owner_scenario"
                )
            )
    if deal_type in ("acquisition", "merger"):
        out.append(dilution(tv, ctx))
        out.append(segment_mix(ctx, tv))
    if deal_type in ("acquisition", "debt_issuance", "tender_offer", "divestiture"):
        out.append(debt_change(deal_type, tv, ctx))
    if deal_type == "spin_off":
        out.append(spin_off_distribution(tv, ctx))
    if deal_type == "buyback":
        out.append(buyback_progress(tv))
    if deal_type in ("acquisition", "merger", "tender_offer", "divestiture"):
        out.append(synergy_projection(versions, as_of))
    return out


# ----------------------------------------------------------------------------------- portfolio
@dataclass(frozen=True)
class DealRef:
    ticker: str
    deal_id: int
    deal_type: str
    state: str | None


@dataclass(frozen=True)
class Exposure:
    denominator: Decimal
    covered_weight: Decimal
    by_state: dict[str, Decimal]
    by_type: dict[str, Decimal]
    rows: tuple[tuple[DealRef, Decimal], ...]
    none_known: tuple[str, ...]
    unknown_state: tuple[str, ...]
    notes: tuple[str, ...]


def portfolio_exposure(weights: Mapping[str, Decimal], deals: Sequence[DealRef]) -> Exposure:
    """Weight of the portfolio sitting in holdings with an ACTIVE deal known at the date, by state
    and
    type. The denominator is the total weight given. A holding with no known deal is listed as
    "none known", which is not proof that none exists; a closed or terminated deal is history."""
    denominator = sum(weights.values(), ZERO)
    rows = tuple(
        (d, weights[d.ticker]) for d in deals if d.ticker in weights and d.state in ACTIVE_STATES
    )
    covered_tickers = {d.ticker for d, _ in rows}
    by_state: dict[str, Decimal] = {}
    by_type: dict[str, Decimal] = {}
    for d, w in rows:
        by_state[d.state or "unknown"] = by_state.get(d.state or "unknown", ZERO) + w
        by_type[d.deal_type] = by_type.get(d.deal_type, ZERO) + w
    notes: list[str] = []
    if len(rows) > len(covered_tickers):
        notes.append(
            "A holding with two active deals appears in both, so the rows can add up to more than "
            "the covered weight."
        )
    if denominator <= 0:
        notes.append("No portfolio weights are known at this date.")
    return Exposure(
        denominator,
        sum((weights[t] for t in covered_tickers), ZERO),
        by_state,
        by_type,
        rows,
        tuple(
            sorted(
                t
                for t in weights
                if t not in covered_tickers and not any(d.ticker == t for d in deals)
            )
        ),
        tuple(sorted({d.ticker for d in deals if d.ticker in weights and d.state is None})),
        tuple(notes),
    )


# ------------------------------------------------------------------------- corporate structure
@dataclass(frozen=True)
class StructureLink:
    id: int
    known_at: dt.datetime
    evidence_id: int | None = None
    legal_closing_date: dt.date | None = None
    first_reporting_period_end: dt.date | None = None
    broker_posting_date: dt.date | None = None
    successor_name: str | None = None
    successor_ticker: str | None = None
    successor_issuer_id: int | None = None


@dataclass(frozen=True)
class ClosingView:
    legal_closing_date: dt.date | None
    first_reporting_period_end: dt.date | None
    broker_posting_date: dt.date | None
    successor_name: str | None
    successor_ticker: str | None
    successor_issuer_id: int | None
    missing: tuple[str, ...]
    problems: tuple[str, ...]


def closing_view(
    links: Sequence[StructureLink], as_of: dt.datetime, *, closed: bool
) -> ClosingView:
    """The three dates and the successor, each the latest value KNOWN by `as_of`. None of them is
    derived from another; one that is not recorded is listed as missing for a closed deal."""
    ordered = sorted((x for x in links if x.known_at <= as_of), key=lambda x: (x.known_at, x.id))

    def pick(name: str):
        for x in reversed(ordered):
            v = getattr(x, name)
            if v is not None:
                return v
        return None

    legal = pick("legal_closing_date")
    first = pick("first_reporting_period_end")
    posted = pick("broker_posting_date")
    problems: list[str] = []
    if legal and first and first < legal:
        problems.append("the first reporting period ends before the legal closing")
    if legal and posted and posted < legal:
        problems.append("the broker posted the corporate action before the legal closing")
    if legal and legal > as_of.date():
        problems.append("a legal closing date is recorded in the future of this view")
    wanted = {
        "legal_closing_date": legal,
        "first_reporting_period_end": first,
        "broker_posting_date": posted,
    }
    missing = tuple(k for k, v in wanted.items() if v is None) if closed else ()
    return ClosingView(
        legal,
        first,
        posted,
        pick("successor_name"),
        pick("successor_ticker"),
        pick("successor_issuer_id"),
        missing,
        tuple(problems),
    )


# --------------------------------------------------------------------------------------- summary
def confidence(
    state: str | None, rejected: int, missing_required: int
) -> Literal["high", "medium", "low"]:
    """How far the record supports the deal as shown: low for a rumor, an unresolved state or a
    rejected transition; medium when a required term is missing; otherwise high."""
    if state in (None, "rumored") or rejected:
        return "low"
    return "medium" if missing_required else "high"


@dataclass(frozen=True)
class DealSnapshot:
    state: StateResolution
    latest: TermVersion | None
    history: tuple[TermVersion, ...]
    missing: tuple[str, ...]
    impacts: tuple[Impact, ...]
    bridge: Bridge
    size: Decimal | None
    confidence: Literal["high", "medium", "low"]


def snapshot(
    deal_type: str,
    transitions: Sequence[Transition],
    versions: Sequence[TermVersion],
    as_of: dt.datetime,
    ctx: DealContext,
    *,
    owner_offer_price: Decimal | None = None,
) -> DealSnapshot:
    """Everything the page shows for one deal, from what was known at `as_of` and nothing later."""
    st = resolve_state(deal_type, transitions, as_of)
    tv = latest_version(versions, as_of, "announced")
    missing = tuple(missing_fields(deal_type, tv))
    return DealSnapshot(
        st,
        tv,
        tuple(versions_known(versions, as_of)),
        missing,
        tuple(impacts_for(deal_type, versions, as_of, ctx, owner_offer_price=owner_offer_price)),
        consideration_bridge(tv),
        deal_size(tv),
        confidence(st.state, len(st.rejected), len(missing)),
    )
