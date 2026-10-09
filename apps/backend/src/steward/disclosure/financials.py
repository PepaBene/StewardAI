"""P2: point-in-time financial statements and notes, as pure functions.

Nothing here touches the database. The functions take already-extracted facts and answer the
questions P2 asks: which metric is this, is the period what it claims, does the arithmetic hold,
how do the segments bridge to the total, which value was known on a date and was it restated.
A check never "fixes" a value; it reports what it expected, what it found and the tolerance, so a
reviewer or a screener gate can decide.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise
from typing import Literal, Protocol

TAXONOMY_VERSION = "metrics-1"
ACCOUNTING_BASES = ("as_filed", "restated", "recast", "adjusted_non_gaap")

# Default relative tolerance for "adds up" checks. Filers round to thousands or millions, so an
# exact match is the wrong test; 0.5% of the larger side (or one reporting unit) is the default.
DEFAULT_TOLERANCE = Decimal("0.005")


# ----------------------------------------------------------------------------------- taxonomy
@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    statement: Literal["income", "cash_flow", "balance_sheet", "shares", "note"]
    instant: bool
    unit_kind: Literal["monetary", "shares", "ratio"] = "monetary"
    concepts: tuple[str, ...] = ()


def _m(
    key: str, label: str, statement: str, instant: bool, *concepts: str, unit_kind: str = "monetary"
) -> Metric:
    return Metric(key, label, statement, instant, unit_kind, concepts)  # type: ignore[arg-type]


METRICS: tuple[Metric, ...] = (
    _m(
        "revenue",
        "Revenue",
        "income",
        False,
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "ifrs-full:Revenue",
    ),
    _m(
        "cost_of_revenue",
        "Cost of revenue",
        "income",
        False,
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "ifrs-full:CostOfSales",
    ),
    _m("gross_profit", "Gross profit", "income", False, "GrossProfit", "ifrs-full:GrossProfit"),
    _m(
        "operating_income",
        "Operating income",
        "income",
        False,
        "OperatingIncomeLoss",
        "ifrs-full:ProfitLossFromOperatingActivities",
    ),
    _m(
        "net_income",
        "Net income",
        "income",
        False,
        "NetIncomeLoss",
        "ProfitLoss",
        "ifrs-full:ProfitLoss",
    ),
    _m(
        "income_tax",
        "Income tax",
        "income",
        False,
        "IncomeTaxExpenseBenefit",
        "ifrs-full:IncomeTaxExpenseContinuingOperations",
    ),
    _m(
        "pretax_income",
        "Income before tax",
        "income",
        False,
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "ifrs-full:ProfitLossBeforeTax",
    ),
    _m(
        "operating_cash_flow",
        "Operating cash flow",
        "cash_flow",
        False,
        "NetCashProvidedByUsedInOperatingActivities",
        "ifrs-full:CashFlowsFromUsedInOperatingActivities",
    ),
    _m(
        "capex",
        "Capital expenditure",
        "cash_flow",
        False,
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
    _m("free_cash_flow", "Free cash flow", "cash_flow", False),
    _m(
        "share_repurchases",
        "Share repurchases",
        "cash_flow",
        False,
        "PaymentsForRepurchaseOfCommonStock",
    ),
    _m(
        "cash",
        "Cash and equivalents",
        "balance_sheet",
        True,
        "CashAndCashEquivalentsAtCarryingValue",
        "ifrs-full:CashAndCashEquivalents",
    ),
    _m(
        "total_debt",
        "Total debt",
        "balance_sheet",
        True,
        "DebtAndCapitalLeaseObligations",
        "LongTermDebt",
        "DebtInstrumentCarryingAmount",
    ),
    _m(
        "lease_liability",
        "Lease liability",
        "balance_sheet",
        True,
        "OperatingLeaseLiability",
        "FinanceLeaseLiability",
        "ifrs-full:LeaseLiabilities",
    ),
    _m(
        "total_equity",
        "Total equity",
        "balance_sheet",
        True,
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "ifrs-full:Equity",
    ),
    _m("working_capital", "Working capital", "balance_sheet", True),
    _m(
        "shares_outstanding",
        "Shares outstanding",
        "shares",
        True,
        "EntityCommonStockSharesOutstanding",
        "CommonStockSharesOutstanding",
        unit_kind="shares",
    ),
    _m(
        "diluted_shares",
        "Diluted shares",
        "shares",
        False,
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        unit_kind="shares",
    ),
    _m(
        "pension_obligation",
        "Pension obligation",
        "note",
        True,
        "DefinedBenefitPlanBenefitObligation",
    ),
    _m("commitments", "Commitments", "note", True, "LongTermPurchaseCommitmentAmount"),
)
_BY_CONCEPT = {c: m for m in METRICS for c in m.concepts}
_BY_KEY = {m.key: m for m in METRICS}


def metric_for(concept: str) -> Metric | None:
    """The metric a taxonomy concept maps to. A company extension never maps by guess."""
    return _BY_CONCEPT.get(concept.removeprefix("us-gaap:"))


def metric_by_key(key: str) -> Metric | None:
    return _BY_KEY.get(key)


# ----------------------------------------------------------------------------------- periods
PeriodKind = Literal["instant", "quarter", "half", "nine_months", "annual", "transition", "other"]


def period_kind(start: dt.date | None, end: dt.date) -> PeriodKind:
    """Classify a period by its length, so a 9-month year-to-date is never read as a year."""
    if start is None:
        return "instant"
    days = (end - start).days + 1
    if 350 <= days <= 380:
        return "annual"
    if 80 <= days <= 100:
        return "quarter"
    if 170 <= days <= 195:
        return "half"
    if 260 <= days <= 285:
        return "nine_months"
    if 28 <= days < 350 and days not in range(80, 101):
        return "transition" if days > 100 else "other"
    return "other"


def fiscal_year_changes(annual_ends: Iterable[dt.date]) -> list[tuple[dt.date, dt.date]]:
    """Consecutive fiscal-year ends not about 12 months apart: a year-end change."""
    ends = sorted(set(annual_ends))
    return [(a, b) for a, b in pairwise(ends) if not 350 <= (b - a).days <= 380]


def scale_value(raw: Decimal | str | int | float, scale: int = 0) -> Decimal:
    """`raw` is the displayed number; `scale` is the filing's power of ten (6 = millions)."""
    return Decimal(str(raw)) * (Decimal(10) ** scale)


# ------------------------------------------------------------------------------------ checks
@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    expected: Decimal | None
    actual: Decimal | None
    tolerance: Decimal
    detail: str
    # "needs_review" means the data cannot support the check (missing piece), not that it failed.
    status: Literal["pass", "fail", "needs_review"] = "pass"

    @property
    def usable(self) -> bool:
        return self.status == "pass"


def _within(expected: Decimal, actual: Decimal, tol: Decimal, unit: Decimal) -> bool:
    allowed = max(abs(expected), abs(actual)) * tol
    return abs(expected - actual) <= max(allowed, unit)


def _result(
    name: str, expected: Decimal, actual: Decimal, tol: Decimal, unit: Decimal, what: str
) -> Check:
    ok = _within(expected, actual, tol, unit)
    return Check(
        name,
        ok,
        expected,
        actual,
        tol,
        f"{what}: expected {expected}, found {actual}",
        "pass" if ok else "fail",
    )


def _review(name: str, tol: Decimal, why: str) -> Check:
    return Check(name, False, None, None, tol, why, "needs_review")


def check_gross_profit(
    revenue: Decimal | None,
    cost: Decimal | None,
    gross: Decimal | None,
    *,
    unit: Decimal = Decimal(1),
    tol: Decimal = DEFAULT_TOLERANCE,
) -> Check:
    if revenue is None or cost is None or gross is None:
        return _review(
            "gross_profit", tol, "revenue, cost of revenue and gross profit must all be reported"
        )
    return _result(
        "gross_profit", revenue - cost, gross, tol, unit, "revenue minus cost of revenue"
    )


def check_free_cash_flow(
    operating: Decimal | None,
    capex: Decimal | None,
    reported: Decimal | None,
    *,
    unit: Decimal = Decimal(1),
    tol: Decimal = DEFAULT_TOLERANCE,
) -> Check:
    """Capex is read as a positive outflow; a negative one is a sign-convention error to review."""
    if operating is None or capex is None or reported is None:
        return _review(
            "free_cash_flow",
            tol,
            "operating cash flow, capex and the reported figure must all be present",
        )
    if capex < 0:
        return _review(
            "free_cash_flow", tol, "capex is negative: the sign convention needs a reviewer"
        )
    return _result(
        "free_cash_flow", operating - capex, reported, tol, unit, "operating cash flow minus capex"
    )


def check_debt_maturities(
    buckets: Mapping[str, Decimal],
    total: Decimal | None,
    *,
    unamortized_discount: Decimal = Decimal(0),
    unit: Decimal = Decimal(1),
    tol: Decimal = DEFAULT_TOLERANCE,
) -> Check:
    """Maturity buckets (years and "thereafter") against the total, net of any stated discount."""
    if total is None or not buckets:
        return _review(
            "debt_maturities", tol, "maturity buckets and a reported total are both required"
        )
    if any(v < 0 for v in buckets.values()):
        return _review("debt_maturities", tol, "a negative maturity bucket needs a reviewer")
    return _result(
        "debt_maturities",
        total,
        sum(buckets.values(), Decimal(0)) - unamortized_discount,
        tol,
        unit,
        "maturity buckets less discount versus total debt",
    )


def check_lease_schedule(
    undiscounted_by_year: Mapping[str, Decimal],
    imputed_interest: Decimal | None,
    liability: Decimal | None,
    *,
    unit: Decimal = Decimal(1),
    tol: Decimal = DEFAULT_TOLERANCE,
) -> Check:
    if liability is None or imputed_interest is None or not undiscounted_by_year:
        return _review(
            "lease_schedule",
            tol,
            "the schedule, imputed interest and the liability are all required",
        )
    return _result(
        "lease_schedule",
        liability,
        sum(undiscounted_by_year.values(), Decimal(0)) - imputed_interest,
        tol,
        unit,
        "undiscounted payments less imputed interest versus liability",
    )


@dataclass(frozen=True)
class Repurchase:
    value: Decimal | None
    shares: Decimal | None
    period_start: dt.date | None
    period_end: dt.date | None
    share_class: str | None


def check_repurchase(r: Repurchase) -> Check:
    """A buyback is only usable with its period and class; the implied price must be positive.
    A reported zero (nothing bought back in the period) is a value, not a missing one."""
    tol = DEFAULT_TOLERANCE
    missing = [
        n
        for n, v in (
            ("value", r.value),
            ("shares", r.shares),
            ("period", r.period_end),
            ("class", r.share_class),
        )
        if v is None or v == ""
    ]
    if missing:
        return _review("repurchase", tol, f"missing {', '.join(missing)}")
    assert r.value is not None and r.shares is not None
    if r.value == 0 and r.shares == 0:
        return Check("repurchase", True, None, None, tol, "no shares repurchased in the period")
    if r.value <= 0 or r.shares <= 0:
        return _review("repurchase", tol, "value and shares must both be positive")
    price = r.value / r.shares
    return Check("repurchase", True, None, price, tol, f"implied average price {price:.4f}")


def check_customer_share(
    percent: Decimal | None, denominator: str | None, period: str | None
) -> Check:
    tol = DEFAULT_TOLERANCE
    if percent is None or not denominator or not period:
        return _review(
            "customer_share", tol, "a customer percentage needs its denominator and period"
        )
    if not Decimal(0) <= percent <= Decimal(100):
        return _review("customer_share", tol, "percentage outside 0 to 100")
    return Check(
        "customer_share", True, None, percent, tol, f"{percent}% of {denominator}, {period}"
    )


def release_gate(checks: Sequence[Check]) -> tuple[bool, list[str]]:
    """True only if every check passed. A failed or unsupported one blocks a valuation."""
    blockers = [f"{c.name}: {c.detail}" for c in checks if not c.usable]
    return not blockers, blockers


# ---------------------------------------------------------------------------- segment bridge
@dataclass(frozen=True)
class SegmentMember:
    axis: str
    member: str
    value: Decimal
    subtotal: bool = False  # the member is the sum of others on the same axis
    eliminations: bool = False  # inter-segment eliminations / reconciling items, kept visible


@dataclass(frozen=True)
class AxisBridge:
    axis: str
    members: tuple[SegmentMember, ...]
    members_sum: Decimal
    eliminations: Decimal
    unallocated: Decimal  # total - members - eliminations: shown as unknown, never hidden
    reconciles: bool


@dataclass(frozen=True)
class SegmentBridge:
    total: Decimal
    axes: tuple[AxisBridge, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


def build_segment_bridge(
    total: Decimal,
    members: Iterable[SegmentMember],
    *,
    unit: Decimal = Decimal(1),
    tol: Decimal = DEFAULT_TOLERANCE,
) -> SegmentBridge:
    """Each axis is bridged to the reported total on its own. Axes are never added together, because
    geography and product both describe the same revenue; subtotals are not double-counted; the
    remainder is reported as unallocated so a gap is visible rather than silently dropped."""
    by_axis: dict[str, list[SegmentMember]] = defaultdict(list)
    for m in members:
        by_axis[m.axis].append(m)
    axes: list[AxisBridge] = []
    notes: list[str] = []
    for axis, rows in sorted(by_axis.items()):
        plain = [m for m in rows if not m.subtotal and not m.eliminations]
        elim = sum((m.value for m in rows if m.eliminations), Decimal(0))
        s = sum((m.value for m in plain), Decimal(0))
        unalloc = total - s - elim
        ok = _within(total, s + elim, tol, unit)
        axes.append(AxisBridge(axis, tuple(rows), s, elim, unalloc, ok))
        if not ok:
            notes.append(
                f"{axis}: members and eliminations are {unalloc} short of the reported total"
            )
        if any(m.subtotal for m in rows):
            notes.append(f"{axis}: subtotal members shown but not counted")
    if len(axes) > 1:
        notes.append(
            "axes describe the same total from different angles and are not added together"
        )
    notes.extend(detect_axis_overlap(a.axis for a in axes))
    return SegmentBridge(total, tuple(axes), tuple(notes))


def _is_geography(axis: str) -> bool:
    low = axis.lower()
    return any(w in low for w in ("geograph", "country", "region", "area"))


def _is_product(axis: str) -> bool:
    low = axis.lower()
    return any(w in low for w in ("product", "service"))


def detect_axis_overlap(axes: Iterable[str]) -> list[str]:
    """Say so when a geography axis and a product axis both describe the same total."""
    names = sorted(set(axes))
    geo = [a for a in names if _is_geography(a)]
    prod = [a for a in names if _is_product(a)]
    if geo and prod:
        return [
            f"geography ({', '.join(geo)}) and product ({', '.join(prod)}) axes overlap: "
            "each splits the same total and they are never added"
        ]
    return []


def is_reconciling_member(label: str) -> bool:
    """Eliminations and reconciling items are kept visible and are not segments."""
    low = label.lower()
    return any(
        w in low
        for w in ("elimination", "reconcil", "intersegment", "inter-segment", "unallocated")
    )


# --------------------------------------------------------------------------- segment metrics
# Each segment metric has its own consolidated total and its own bridge. They are never mixed:
# a segment's operating profit is not a revenue member and assets are not flows.
SEGMENT_METRICS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "ifrs-full:Revenue",
    ),
    "operating_profit": ("OperatingIncomeLoss", "ifrs-full:ProfitLossFromOperatingActivities"),
    "assets": ("Assets", "ifrs-full:Assets"),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
}


def segment_metric_for(concept: str) -> str | None:
    bare = concept.removeprefix("us-gaap:")
    return next((k for k, cs in SEGMENT_METRICS.items() if bare in cs), None)


@dataclass(frozen=True)
class SegmentFact:
    """One segment value: its metric and the full axis/member tuple of its context."""

    metric: str
    axes: tuple[str, ...]
    members: tuple[str, ...]
    value: Decimal


@dataclass(frozen=True)
class CrossRow:
    """A value defined by two or more axes at once (a cross-tab cell). It is its own breakdown."""

    axes: tuple[str, ...]
    members: tuple[str, ...]
    value: Decimal


@dataclass(frozen=True)
class UnbridgedAxis:
    axis: str
    members: tuple[SegmentMember, ...]
    members_sum: Decimal
    eliminations: Decimal


@dataclass(frozen=True)
class MetricBridge:
    metric: str
    total: Decimal | None
    bridge: SegmentBridge | None  # None when no consolidated total is known
    unbridged: tuple[UnbridgedAxis, ...]  # members shown without a total to bridge to
    cross_axis: tuple[CrossRow, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class SegmentReport:
    metrics: tuple[MetricBridge, ...]


def build_segment_report(
    totals: Mapping[str, Decimal | None],
    facts: Iterable[SegmentFact],
    *,
    unit: Decimal = Decimal(1),
    tol: Decimal = DEFAULT_TOLERANCE,
) -> SegmentReport:
    """Bridge every segment metric on its own. Single-axis members go to the per-axis bridge;
    a fact with two or more axes is returned as a cross-axis row and is never placed on one axis
    nor summed with single-axis members. A missing consolidated total is named, not invented."""
    by_metric: dict[str, list[SegmentFact]] = defaultdict(list)
    for f in facts:
        by_metric[f.metric].append(f)
    out: list[MetricBridge] = []
    for metric in SEGMENT_METRICS:
        rows = by_metric.get(metric, [])
        if not rows and totals.get(metric) is None:
            continue
        single = [f for f in rows if len(f.axes) == 1 and len(f.members) == 1]
        cross = tuple(
            CrossRow(f.axes, f.members, f.value)
            for f in sorted(rows, key=lambda f: (f.axes, f.members))
            if len(f.axes) > 1
        )
        members = [
            SegmentMember(
                f.axes[0], f.members[0], f.value, eliminations=is_reconciling_member(f.members[0])
            )
            for f in single
        ]
        notes: list[str] = []
        total = totals.get(metric)
        bridge: SegmentBridge | None = None
        unbridged: list[UnbridgedAxis] = []
        if total is None:
            notes.append(
                f"no consolidated {metric.replace('_', ' ')} is known for this period: "
                "members are shown without a bridge"
            )
            for axis in sorted({m.axis for m in members}):
                ms = tuple(m for m in members if m.axis == axis)
                unbridged.append(
                    UnbridgedAxis(
                        axis,
                        ms,
                        sum((m.value for m in ms if not m.eliminations), Decimal(0)),
                        sum((m.value for m in ms if m.eliminations), Decimal(0)),
                    )
                )
            notes.extend(detect_axis_overlap(a.axis for a in unbridged))
        else:
            bridge = build_segment_bridge(total, members, unit=unit, tol=tol)
            notes.extend(bridge.notes)
        if cross:
            notes.append(
                "cross-axis rows are a separate breakdown: never placed on one axis and never "
                "summed with single-axis members"
            )
            cross_axes = {a for c in cross for a in c.axes}
            notes.extend(detect_axis_overlap(cross_axes))
        if metric == "assets":
            notes.append("assets are a balance at period end, not a flow")
        out.append(
            MetricBridge(
                metric, total, bridge, tuple(unbridged), cross, tuple(dict.fromkeys(notes))
            )
        )
    return SegmentReport(tuple(out))


@dataclass(frozen=True)
class FiledSegments:
    known_at: dt.datetime
    values: Mapping[str, Decimal]  # member -> value for one period, as that filing reported it


def detect_recast(
    filings: Sequence[FiledSegments], *, tol: Decimal = DEFAULT_TOLERANCE
) -> list[str]:
    """Compare what successive filings said about the SAME past period. A changed member set
    is a redefinition; changed values for the same members are a recast. History is flagged,
    not rewritten."""
    flags: list[str] = []
    ordered = sorted(filings, key=lambda f: f.known_at)
    for prev, cur in pairwise(ordered):
        added = set(cur.values) - set(prev.values)
        dropped = set(prev.values) - set(cur.values)
        if added or dropped:
            flags.append(
                f"redefined at {cur.known_at:%Y-%m-%d}: "
                f"added {sorted(added)}, dropped {sorted(dropped)}"
            )
            continue
        moved = [
            k for k in cur.values if not _within(prev.values[k], cur.values[k], tol, Decimal(1))
        ]
        if moved:
            flags.append(f"recast at {cur.known_at:%Y-%m-%d}: {sorted(moved)} changed")
    return flags


# ------------------------------------------------------------------------- as-filed history
@dataclass(frozen=True)
class FiledFact:
    metric: str
    period_start: dt.date | None
    period_end: dt.date
    value: Decimal | None
    known_at: dt.datetime
    accession: str
    amends: str | None = None  # accession of the filing this one restates
    fact_id: int | None = None  # financial_statement_fact.id, for citations


@dataclass(frozen=True)
class HistoryPoint:
    metric: str
    period_end: dt.date
    kind: PeriodKind
    value: Decimal | None
    accession: str
    known_at: dt.datetime
    restated: bool
    original_value: Decimal | None  # what the first-known filing said; set only when restated
    fact_id: int | None = None


def statement_history(
    facts: Iterable[FiledFact], as_of: dt.datetime, *, kinds: Sequence[PeriodKind] = ("annual",)
) -> list[HistoryPoint]:
    """One point per (metric, period): the latest value known by `as_of`, with the original
    kept when a later filing restated it. A filing after `as_of` contributes nothing, not even
    the fact that a restatement happened later."""
    visible = [f for f in facts if f.known_at <= as_of]
    groups: dict[tuple[str, dt.date, dt.date | None], list[FiledFact]] = defaultdict(list)
    for f in visible:
        groups[(f.metric, f.period_end, f.period_start)].append(f)
    out: list[HistoryPoint] = []
    for (metric, end, start), rows in groups.items():
        kind = period_kind(start, end)
        if kind not in kinds:
            continue
        rows.sort(key=lambda r: r.known_at)
        first, last = rows[0], rows[-1]
        changed = len(rows) > 1 and first.value != last.value
        out.append(
            HistoryPoint(
                metric,
                end,
                kind,
                last.value,
                last.accession,
                last.known_at,
                changed,
                first.value if changed else None,
                last.fact_id,
            )
        )
    return sorted(out, key=lambda p: (p.metric, p.period_end))


# ---------------------------------------------------------------------- jurisdiction adapters
@dataclass(frozen=True)
class AdapterContract:
    """What a national registry adapter must state before a pilot is enabled (P2.6). Every
    field is a plain fact about the source; `coverage_measured` stays None until a coverage
    report exists, and nothing may be labelled global fundamentals while it is None."""

    key: str
    jurisdiction: str
    discovery: str
    form_taxonomy: tuple[str, ...]
    currency_unit_rule: str
    publication_date_field: str | None
    extraction: Literal["xbrl", "ixbrl", "text", "pdf"]
    identifier_mapping: tuple[str, ...]
    authentication: Literal["none", "api_key", "session", "unknown"]
    coverage_measured: float | None = None


class JurisdictionAdapter(Protocol):
    contract: AdapterContract

    async def discover(self, issuer_key: str, since: dt.date | None) -> list[str]: ...

    async def facts(self, filing_ref: str) -> list[FiledFact]: ...


ADAPTER_CONTRACTS: dict[str, AdapterContract] = {
    "sec": AdapterContract(
        "sec",
        "US",
        "submissions API + archive index",
        ("10-K", "10-Q", "20-F", "40-F", "8-K"),
        "unit and decimals attributes on each fact",
        "acceptanceDateTime",
        "ixbrl",
        ("cik",),
        "none",
    ),
    "esef": AdapterContract(
        "esef",
        "EU",
        "national OAM or issuer IR package",
        ("annual financial report",),
        "ixbrl unit; conversion source recorded",
        None,
        "ixbrl",
        ("lei", "isin"),
        "none",
    ),
    "edinet": AdapterContract(
        "edinet",
        "JP",
        "EDINET document list API",
        ("securities report", "quarterly report"),
        "JPY, stated scale",
        "submitDateTime",
        "xbrl",
        ("edinet_code", "sec_code"),
        "api_key",
    ),
    "sedarplus": AdapterContract(
        "sedarplus",
        "CA",
        "portal search/export",
        ("annual filings", "interim filings"),
        "CAD or USD per filing",
        None,
        "pdf",
        ("sedar_profile",),
        "session",
    ),
}


def global_coverage_label(contracts: Iterable[AdapterContract]) -> str:
    """ "Global fundamentals" is allowed only when every adapter has a measured coverage figure."""
    cs = list(contracts)
    if cs and all(c.coverage_measured is not None for c in cs):
        return "Global fundamentals"
    measured = [c.jurisdiction for c in cs if c.coverage_measured is not None]
    return (
        f"Fundamentals for {', '.join(measured)}"
        if measured
        else "Fundamentals: coverage not yet measured"
    )
