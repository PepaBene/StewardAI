"""Per-share fundamentals from XBRL facts: EPS over the trailing twelve months, book value
per share and dividends per share over the trailing twelve months, each with the period,
the method and the facts it was computed from.

The facts come from SEC companyfacts, a filing's XBRL instance or an ESEF report; this
module does not care which, and never mixes accounting bases (a US GAAP and an IFRS EPS
are different concepts and are computed separately).

Trailing twelve months, for a flow per share (EPS, dividends per share), from the facts
ending at the latest period end:

1. a full year ending there is the TTM itself;
2. else the year-to-date figure plus the previous full year minus the prior year's same
   year-to-date period (e.g. FY2025 + H1 2026 - H1 2025), the usual way to roll interim
   reports forward;
3. else the sum of four consecutive quarters;
4. else the latest full year at an earlier period end (flagged: newer interim figures
   could not be combined).

Per-share sums are an approximation when the share count moves during the year; that is
how TTM EPS is conventionally quoted. For each period the value from the latest report is
used, so later restatements (e.g. share splits) win.
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from steward.earnings.reconcile import CONCEPTS, REPORT_FORMS
from steward.sources.xbrl_concepts import (
    DEBT_EXTRA,
    DEBT_PARTS,
    DEBT_TOTAL,
    STATEMENT_GROUP,
    STATEMENT_ITEMS,
)

# The existing reconciliation's diluted EPS concepts: us-gaap first, then ifrs-full.
EPS_CONCEPTS: list[str] = [f"{t}:{c}" for t, c in CONCEPTS["eps_diluted"]]
EPS_BASIC_CONCEPTS = ["us-gaap:EarningsPerShareBasic", "ifrs-full:BasicEarningsLossPerShare"]
EQUITY_CONCEPTS: dict[str, list[str]] = {
    "us-gaap": [
        "us-gaap:StockholdersEquity",
        "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "ifrs-full": ["ifrs-full:EquityAttributableToOwnersOfParent", "ifrs-full:Equity"],
}
SHARES_CONCEPTS = [
    "dei:EntityCommonStockSharesOutstanding",
    "us-gaap:CommonStockSharesOutstanding",
    "ifrs-full:NumberOfSharesOutstanding",
]
DPS_CONCEPTS: dict[str, list[str]] = {
    "us-gaap": [
        "us-gaap:CommonStockDividendsPerShareDeclared",
        "us-gaap:CommonStockDividendsPerShareCashPaid",
    ],
    "ifrs-full": ["ifrs-full:DividendsRecognisedAsDistributionsToOwnersPerShare"],
}
FLOW_CONCEPTS: dict[str, dict[str, list[str]]] = {
    "us-gaap": {
        "revenue": [
            "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "us-gaap:Revenues",
            "us-gaap:SalesRevenueNet",
        ],
        "gross_profit": ["us-gaap:GrossProfit"],
        "operating_income": ["us-gaap:OperatingIncomeLoss"],
        "net_income": ["us-gaap:NetIncomeLoss", "us-gaap:ProfitLoss"],
        "pretax_income": [
            "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"
        ],
        "income_tax": ["us-gaap:IncomeTaxExpenseBenefit"],
    },
    "ifrs-full": {
        "revenue": ["ifrs-full:Revenue", "ifrs-full:RevenueFromContractsWithCustomers"],
        "gross_profit": ["ifrs-full:GrossProfit"],
        "operating_income": ["ifrs-full:ProfitLossFromOperatingActivities"],
        "net_income": ["ifrs-full:ProfitLoss"],
        "pretax_income": ["ifrs-full:ProfitLossBeforeTax"],
        "income_tax": ["ifrs-full:IncomeTaxExpenseContinuingOperations"],
    },
}


def _debt_concepts(taxonomy: str) -> list[str]:
    """Every element that can be a debt component, from the shared concept catalog."""
    return [
        concept
        for concept, group in STATEMENT_GROUP.items()
        if group == "debt"
        and STATEMENT_ITEMS[concept][0] == "balance_sheet"
        and concept.startswith(f"{taxonomy}:")
    ]


BALANCE_CONCEPTS: dict[str, dict[str, list[str]]] = {
    "us-gaap": {
        "debt": _debt_concepts("us-gaap"),
        "cash": [
            "us-gaap:CashAndCashEquivalentsAtCarryingValue",
            "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ],
    },
    "ifrs-full": {
        "debt": _debt_concepts("ifrs-full"),
        "cash": ["ifrs-full:CashAndCashEquivalents"],
    },
}
ALL_CONCEPTS: set[str] = {
    *EPS_CONCEPTS,
    *EPS_BASIC_CONCEPTS,
    *SHARES_CONCEPTS,
    *(c for cs in EQUITY_CONCEPTS.values() for c in cs),
    *(c for cs in DPS_CONCEPTS.values() for c in cs),
    *(c for source in FLOW_CONCEPTS.values() for cs in source.values() for c in cs),
    *(c for source in BALANCE_CONCEPTS.values() for cs in source.values() for c in cs),
}
BASIS_LABEL = {"us-gaap": "US GAAP", "ifrs-full": "IFRS"}

END_SLACK = 7  # days: 52/53-week fiscal years
YEAR = (350, 380)
QUARTER = (80, 100)
# Shares counted more than this far from the balance-sheet date are not matched to it.
SHARES_MAX_GAP_DAYS = 200
# Dividends per share ending this long before the EPS period are too old to use.
DPS_MAX_LAG_DAYS = 180
STEP_YEARS = 3


@dataclass(frozen=True)
class Fact:
    concept: str
    start: dt.date | None
    end: dt.date
    value: Decimal
    unit: str
    source: str  # 'sec' (companyfacts), 'sec_instance', 'esef'
    ref: str  # accession number or filings.xbrl.org filing id
    filed: dt.date | None
    form: str | None = None
    url: str | None = None

    @property
    def days(self) -> int:
        return (self.end - self.start).days if self.start else 0

    @property
    def taxonomy(self) -> str:
        return self.concept.split(":", 1)[0]


class FundamentalFactRef(BaseModel):
    """One input fact, as filed."""

    concept: str
    start: dt.date | None
    end: dt.date
    value: float
    unit: str
    source: str = Field(
        description="sec (companyfacts), sec_instance (the filing's XBRL "
        "instance) or esef (filings.xbrl.org)."
    )
    ref: str = Field(description="Accession number or filings.xbrl.org filing id.")
    form: str | None
    filed: dt.date | None
    url: str | None


class Figure(BaseModel):
    value: float
    currency: str
    period_start: dt.date | None
    period_end: dt.date
    method: str = Field(description="How the figure was derived, in words.")
    annual_only: bool = Field(
        default=False,
        description="The latest figure is a full year because the source has no interim "
        "figures (20-F filers), or newer interim figures could not be combined.",
    )
    inputs: list[FundamentalFactRef]


class EpsStep(BaseModel):
    """EPS TTM as it stood from `effective` (the first filing of that period) onwards."""

    effective: dt.date
    period_end: dt.date
    eps: float
    method: str


class SourceFigures(BaseModel):
    source: str
    basis: str | None = Field(description="US GAAP or IFRS.")
    eps_ttm: Figure | None
    eps_kind: str = Field(
        default="diluted", description="diluted, or basic when no diluted EPS is tagged."
    )
    book_value_per_share: Figure | None
    dividend_per_share: Figure | None
    shares_outstanding: float | None
    shares_as_of: dt.date | None
    eps_steps: list[EpsStep] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    revenue_ttm: Figure | None = None
    gross_profit_ttm: Figure | None = None
    operating_income_ttm: Figure | None = None
    net_income_ttm: Figure | None = None
    pretax_income_ttm: Figure | None = None
    income_tax_ttm: Figure | None = None
    equity_value: Figure | None = None
    debt_value: Figure | None = None
    cash_value: Figure | None = None
    equity_previous_value: Figure | None = None
    alternatives: list["SourceFigures"] = Field(
        default_factory=list, description="The same source on another accounting basis."
    )


# ---------------------------------------------------------------------------
# Loading facts
# ---------------------------------------------------------------------------


def companyfacts_facts(
    company_facts: dict[str, Any], concepts: Iterable[str] = ALL_CONCEPTS, url: str | None = None
) -> list[Fact]:
    """Facts of `concepts` from periodic reports (10-Q, 10-K, 20-F, 40-F) in companyfacts."""
    facts: list[Fact] = []
    tree = company_facts.get("facts", {})
    for name in concepts:
        taxonomy, _, concept = name.partition(":")
        units = tree.get(taxonomy, {}).get(concept, {}).get("units", {})
        for unit, rows in units.items():
            for r in rows:
                # Cover-page share counts come from any periodic report; flows and balances
                # only from the periodic reports themselves.
                if r.get("form") not in REPORT_FORMS:
                    continue
                try:
                    facts.append(
                        Fact(
                            concept=name,
                            start=dt.date.fromisoformat(r["start"]) if r.get("start") else None,
                            end=dt.date.fromisoformat(r["end"]),
                            value=Decimal(str(r["val"])),
                            unit=unit,
                            source="sec",
                            ref=str(r.get("accn") or ""),
                            filed=dt.date.fromisoformat(r["filed"]) if r.get("filed") else None,
                            form=r.get("form"),
                            url=url,
                        )
                    )
                except (KeyError, ValueError, ArithmeticError):
                    continue
    return facts


def companyfacts_subset(company_facts: dict[str, Any], concepts: Iterable[str]) -> dict[str, Any]:
    """The part of a companyfacts document the figures use, verbatim (for provenance)."""
    tree = company_facts.get("facts", {})
    out: dict[str, Any] = {}
    for name in concepts:
        taxonomy, _, concept = name.partition(":")
        if concept in tree.get(taxonomy, {}):
            out.setdefault(taxonomy, {})[concept] = tree[taxonomy][concept]
    return {
        "cik": company_facts.get("cik"),
        "entityName": company_facts.get("entityName"),
        "facts": out,
    }


def accessions(facts: Iterable[Fact]) -> set[str]:
    return {f.ref for f in facts if f.ref}


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------


def _near(a: dt.date, b: dt.date, slack: int = END_SLACK) -> bool:
    return abs((a - b).days) <= slack


def _within(days: int, bounds: tuple[int, int]) -> bool:
    return bounds[0] <= days <= bounds[1]


def latest_per_period(facts: Iterable[Fact]) -> list[Fact]:
    """One fact per (start, end): the latest filed (restatements win)."""
    best: dict[tuple[dt.date | None, dt.date], Fact] = {}
    for f in facts:
        key = (f.start, f.end)
        current = best.get(key)
        if current is None or (f.filed or dt.date.min, f.ref) > (
            current.filed or dt.date.min,
            current.ref,
        ):
            best[key] = f
    return sorted(best.values(), key=lambda f: (f.end, f.start or dt.date.min))


def _ref(f: Fact) -> FundamentalFactRef:
    return FundamentalFactRef(
        concept=f.concept,
        start=f.start,
        end=f.end,
        value=float(f.value),
        unit=f.unit,
        source=f.source,
        ref=f.ref,
        form=f.form,
        filed=f.filed,
        url=f.url,
    )


def _span(f: Fact) -> str:
    months = round(f.days / 30.44)
    label = "FY" if months >= 12 else f"{months}M"
    return f"{label} to {f.end.isoformat()}"


@dataclass(frozen=True)
class Ttm:
    value: Decimal
    start: dt.date | None
    end: dt.date
    method: str
    annual_only: bool
    inputs: list[Fact] = field(default_factory=list)


def ttm(facts: Sequence[Fact]) -> Ttm | None:
    """Trailing twelve months of a per-share flow (one concept, one unit)."""
    points = [f for f in latest_per_period(facts) if f.start is not None]
    if not points:
        return None
    latest = max(f.end for f in points)
    interim = any(not _within(f.days, YEAR) for f in points)
    at_latest = [f for f in points if _near(f.end, latest)]

    for year in at_latest:
        if _within(year.days, YEAR):
            return Ttm(
                year.value,
                year.start,
                year.end,
                f"full year ({_span(year)})",
                annual_only=not interim,
                inputs=[year],
            )

    for ytd in sorted(at_latest, key=lambda f: -f.days):
        assert ytd.start is not None
        fy = [
            f
            for f in points
            if _within(f.days, YEAR) and _near(f.end, ytd.start - dt.timedelta(days=1))
        ]
        if not fy:
            continue
        prior_year = max(fy, key=lambda f: f.end)
        assert prior_year.start is not None
        prior = [
            f
            for f in points
            if abs(f.days - ytd.days) <= 10
            and _near(f.start or dt.date.min, prior_year.start)
            and f is not ytd
        ]
        if not prior:
            continue
        before = prior[0]
        value = prior_year.value + ytd.value - before.value
        method = f"{_span(prior_year)} + {_span(ytd)} - {_span(before)}"
        start = ytd.end - dt.timedelta(days=364)
        return Ttm(value, start, ytd.end, method, False, [prior_year, ytd, before])

    quarters: list[Fact] = []
    end = latest
    for _ in range(4):
        found = [f for f in points if _within(f.days, QUARTER) and _near(f.end, end)]
        if not found:
            break
        quarter = found[0]
        quarters.append(quarter)
        assert quarter.start is not None
        end = quarter.start - dt.timedelta(days=1)
    if len(quarters) == 4:
        total = sum((q.value for q in quarters), Decimal(0))
        return Ttm(
            total,
            quarters[-1].start,
            latest,
            "sum of the last four quarters",
            False,
            list(reversed(quarters)),
        )

    years = [f for f in points if _within(f.days, YEAR)]
    if years:
        year = max(years, key=lambda f: f.end)
        return Ttm(
            year.value,
            year.start,
            year.end,
            f"latest full year ({_span(year)}); newer interim figures could not be combined",
            annual_only=True,
            inputs=[year],
        )
    return None


def eps_steps(facts: Sequence[Fact], today: dt.date) -> list[EpsStep]:
    """EPS TTM at each period end of the last few years, effective from its first filing."""
    points = latest_per_period(facts)
    first_filed: dict[dt.date, dt.date] = {}
    for f in facts:
        if f.filed is not None:
            key = f.end
            first_filed[key] = min(first_filed.get(key, f.filed), f.filed)
    since = today - dt.timedelta(days=365 * STEP_YEARS + 120)
    steps: list[EpsStep] = []
    for end in sorted({f.end for f in points if f.end >= since}):
        value = ttm([f for f in points if f.end <= end + dt.timedelta(days=END_SLACK)])
        if value is None or not _near(value.end, end):
            continue
        effective = first_filed.get(value.end)
        if effective is None:
            continue
        if steps and steps[-1].period_end == value.end:
            continue
        steps.append(
            EpsStep(
                effective=effective,
                period_end=value.end,
                eps=float(value.value),
                method=value.method,
            )
        )
    steps.sort(key=lambda s: s.effective)
    return steps


def _currency(unit: str) -> str:
    return unit.split("/", 1)[0]


def choose_currency(available: Iterable[str], preferred: Sequence[str]) -> str | None:
    """The first of `preferred` among `available`, else the alphabetically first."""
    have = set(available)
    for c in preferred:
        if c in have:
            return c
    return min(have) if have else None


def _by_concept(facts: Iterable[Fact], concepts: Sequence[str]) -> list[Fact]:
    """Facts of the concept in `concepts` that reports the latest period (ties: the one listed
    first). An old filing's `RevenueFromContract...` for FY2018 must not beat the `Revenues`
    of FY2024 only because it is listed first (B27)."""
    pool = list(facts)
    best: list[Fact] = []
    best_end: dt.date | None = None
    for c in concepts:
        found = [f for f in pool if f.concept == c]
        if not found:
            continue
        end = max(f.end for f in found)
        if best_end is None or end > best_end:
            best, best_end = found, end
    return best


def _figure(t: Ttm, currency: str) -> Figure:
    return Figure(
        value=float(t.value),
        currency=currency,
        period_start=t.start,
        period_end=t.end,
        method=t.method,
        annual_only=t.annual_only,
        inputs=[_ref(f) for f in t.inputs],
    )


def _flow_figure(
    facts: Sequence[Fact], concepts: Sequence[str], preferred: Sequence[str]
) -> Figure | None:
    selected = _by_concept(facts, concepts)
    selected = [fact for fact in selected if fact.start is not None and "/" not in fact.unit]
    if not selected:
        return None
    currency = choose_currency({_currency(fact.unit) for fact in selected}, preferred)
    if currency is None:
        return None
    result = ttm([fact for fact in selected if _currency(fact.unit) == currency])
    return _figure(result, currency) if result is not None else None


def _balance_figure(
    facts: Sequence[Fact],
    concepts: Sequence[str],
    preferred: Sequence[str],
    before: dt.date | None = None,
) -> Figure | None:
    selected = _by_concept([fact for fact in facts if fact.start is None], concepts)
    selected = [
        fact for fact in selected if "/" not in fact.unit and (before is None or fact.end < before)
    ]
    if not selected:
        return None
    currency = choose_currency({fact.unit for fact in selected}, preferred)
    chosen = max(
        (fact for fact in selected if fact.unit == currency),
        key=lambda fact: (fact.end, fact.filed or dt.date.min),
    )
    return Figure(
        value=float(chosen.value),
        currency=chosen.unit,
        period_start=None,
        period_end=chosen.end,
        method=f"reported balance at {chosen.end.isoformat()}",
        inputs=[_ref(chosen)],
    )


def _debt_figure(facts: Sequence[Fact], taxonomy: str, preferred: Sequence[str]) -> Figure | None:
    """Total debt at the latest balance-sheet date for which it can be built from the filing:
    the total element (US GAAP `LongTermDebt`, IFRS `Borrowings`), else the non-current plus the
    current portion, plus short-term borrowings and commercial paper where tagged. A date at
    which only the current portion is tagged is skipped: it would report a fraction of the debt
    as all of it (B27). Lease liabilities are not included."""
    total_names = DEBT_TOTAL.get(taxonomy, ())
    part_groups = DEBT_PARTS.get(taxonomy, ())
    extra_groups = DEBT_EXTRA.get(taxonomy, ())
    wanted = {n for n in total_names} | {n for g in (*part_groups, *extra_groups) for n in g}
    pool = [
        f
        for f in facts
        if f.start is None
        and f.taxonomy == taxonomy
        and "/" not in f.unit
        and f.concept.split(":", 1)[1].lower() in wanted
    ]
    if not pool:
        return None
    currency = choose_currency({f.unit for f in pool}, preferred)
    pool = [f for f in pool if f.unit == currency]

    def at(end: dt.date, names: Iterable[str]) -> Fact | None:
        for name in names:
            found = [f for f in pool if f.end == end and f.concept.split(":", 1)[1].lower() == name]
            if found:
                return max(found, key=lambda f: (f.filed or dt.date.min, f.ref))
        return None

    for end in sorted({f.end for f in pool}, reverse=True):
        used: list[Fact] = []
        total = at(end, total_names)
        if total is not None:
            used.append(total)
            how = f"reported {total.concept.split(':', 1)[1]}"
        else:
            parts = [at(end, group) for group in part_groups]
            if not part_groups or parts[0] is None:
                continue
            used.extend(p for p in parts if p is not None)
            how = " + ".join(p.concept.split(":", 1)[1] for p in used)
        for group in extra_groups:
            extra = at(end, group)
            if extra is not None:
                used.append(extra)
                how += f" + {extra.concept.split(':', 1)[1]}"
        value = sum((f.value for f in used), Decimal(0))
        return Figure(
            value=float(value),
            currency=used[0].unit,
            period_start=None,
            period_end=end,
            method=f"debt at {end.isoformat()}: {how}",
            inputs=[_ref(f) for f in used],
        )
    return None


def compute(
    source: str, facts: Sequence[Fact], preferred_currencies: Sequence[str], today: dt.date
) -> SourceFigures | None:
    """Figures from one source's facts, on the basis with the latest EPS (the other basis,
    when the source has both, as an alternative). None when the source has no EPS."""
    by_basis: dict[str, SourceFigures] = {}
    for taxonomy in ("us-gaap", "ifrs-full"):
        figures = _compute_basis(source, taxonomy, facts, preferred_currencies, today)
        if figures is not None:
            by_basis[taxonomy] = figures
    if not by_basis:
        return None
    ranked = sorted(
        by_basis.values(),
        key=lambda f: (f.eps_ttm.period_end if f.eps_ttm else dt.date.min, f.basis == "US GAAP"),
        reverse=True,
    )
    primary = ranked[0]
    primary.alternatives = ranked[1:]
    return primary


def _compute_basis(
    source: str,
    taxonomy: str,
    facts: Sequence[Fact],
    preferred: Sequence[str],
    today: dt.date,
) -> SourceFigures | None:
    notes: list[str] = []
    eps_kind = "diluted"
    eps_facts = [f for f in facts if f.concept in EPS_CONCEPTS and f.taxonomy == taxonomy]
    if not eps_facts:
        eps_facts = [f for f in facts if f.concept in EPS_BASIC_CONCEPTS and f.taxonomy == taxonomy]
        eps_kind = "basic"
    eps_facts = [f for f in eps_facts if f.unit.endswith("/shares")]
    if not eps_facts:
        return None
    currency = choose_currency({_currency(f.unit) for f in eps_facts}, preferred)
    eps_in = [f for f in eps_facts if _currency(f.unit) == currency]
    eps = ttm(eps_in)
    if eps_kind == "basic":
        notes.append("No diluted EPS is tagged; basic EPS is used.")
    reported = sorted({_currency(f.unit) for f in eps_facts} - {currency})
    if currency and reported:
        notes.append(
            f"EPS is also reported in {', '.join(reported)}; the {currency} figures are used "
            "(the price's currency, or one the stored reference rates can convert)."
        )

    equity_all = [
        f
        for f in _by_concept(
            [f for f in facts if f.start is None], EQUITY_CONCEPTS.get(taxonomy, [])
        )
        if "/" not in f.unit
    ]
    bvps: Figure | None = None
    equity_value: Figure | None = None
    equity_previous_value: Figure | None = None
    shares_value: float | None = None
    shares_as_of: dt.date | None = None
    if equity_all:
        eq_currency = choose_currency({f.unit for f in equity_all}, [currency or "", *preferred])
        assert eq_currency is not None  # Non-empty equity facts always have a currency.
        equity = max(
            (f for f in equity_all if f.unit == eq_currency),
            key=lambda f: (f.end, f.filed or dt.date.min),
        )
        equity_value = Figure(
            value=float(equity.value),
            currency=equity.unit,
            period_start=None,
            period_end=equity.end,
            method=f"reported equity at {equity.end.isoformat()}",
            inputs=[_ref(equity)],
        )
        equity_previous_value = _balance_figure(
            facts, EQUITY_CONCEPTS.get(taxonomy, []), [eq_currency], before=equity.end
        )
        shares = _shares_for(facts, equity.end)
        if shares is None:
            notes.append(
                f"No share count within {SHARES_MAX_GAP_DAYS} days of the "
                f"{equity.end.isoformat()} balance sheet; no book value per share."
            )
        elif shares.value > 0:
            shares_value, shares_as_of = float(shares.value), shares.end
            bvps = Figure(
                value=float(equity.value / shares.value),
                currency=equity.unit,
                period_start=None,
                period_end=equity.end,
                method=f"equity at {equity.end.isoformat()} / shares outstanding at "
                f"{shares.end.isoformat()}",
                inputs=[_ref(equity), _ref(shares)],
            )

    dps: Figure | None = None
    dps_facts = [
        f
        for f in facts
        if f.concept in DPS_CONCEPTS.get(taxonomy, []) and f.unit.endswith("/shares")
    ]
    if dps_facts:
        # Any concept and currency, the preferred ones first, whose TTM is recent enough to
        # set against the EPS.
        order = [currency or "", *preferred, *sorted({_currency(f.unit) for f in dps_facts})]
        recent_from = (eps.end if eps else today) - dt.timedelta(days=DPS_MAX_LAG_DAYS)
        for concept in DPS_CONCEPTS.get(taxonomy, []):
            for ccy in dict.fromkeys(order):
                found = ttm(
                    [f for f in dps_facts if f.concept == concept and _currency(f.unit) == ccy]
                )
                if found is not None and found.end >= recent_from:
                    dps = _figure(found, ccy)
                    break
            if dps is not None:
                break
        if dps is None:
            notes.append(
                "The dividends per share tagged are not recent enough (or not in a full-year "
                "or quarterly form) to give a trailing-year figure."
            )
    else:
        notes.append("No dividends-per-share figure is tagged (the company may pay none).")

    flow = FLOW_CONCEPTS.get(taxonomy, {})
    balances = BALANCE_CONCEPTS.get(taxonomy, {})
    financial_currency_order = [currency or "", *preferred]
    return SourceFigures(
        source=source,
        basis=BASIS_LABEL.get(taxonomy),
        eps_ttm=_figure(eps, currency) if eps is not None and currency else None,
        eps_kind=eps_kind,
        book_value_per_share=bvps,
        dividend_per_share=dps,
        shares_outstanding=shares_value,
        shares_as_of=shares_as_of,
        eps_steps=eps_steps(eps_in, today),
        notes=[n for n in notes if n],
        revenue_ttm=_flow_figure(facts, flow.get("revenue", []), financial_currency_order),
        gross_profit_ttm=_flow_figure(
            facts, flow.get("gross_profit", []), financial_currency_order
        ),
        operating_income_ttm=_flow_figure(
            facts, flow.get("operating_income", []), financial_currency_order
        ),
        net_income_ttm=_flow_figure(facts, flow.get("net_income", []), financial_currency_order),
        pretax_income_ttm=_flow_figure(
            facts, flow.get("pretax_income", []), financial_currency_order
        ),
        income_tax_ttm=_flow_figure(facts, flow.get("income_tax", []), financial_currency_order),
        equity_value=equity_value,
        equity_previous_value=equity_previous_value,
        debt_value=_debt_figure(facts, taxonomy, financial_currency_order),
        cash_value=_balance_figure(facts, balances.get("cash", []), financial_currency_order),
    )


def _shares_for(facts: Sequence[Fact], at: dt.date) -> Fact | None:
    """The share count closest to `at` (a balance-sheet date), preferring one at that date."""
    candidates = [
        f
        for f in facts
        if f.concept in SHARES_CONCEPTS
        and f.start is None
        and f.unit == "shares"
        and abs((f.end - at).days) <= SHARES_MAX_GAP_DAYS
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda f: (
            abs((f.end - at).days),
            SHARES_CONCEPTS.index(f.concept),
            -(f.filed or dt.date.min).toordinal(),
        ),
    )
