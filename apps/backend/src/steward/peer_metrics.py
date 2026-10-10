"""Peer ratios from filed statement facts (US GAAP and IFRS), with the reason for every gap.

`financial_peer_metrics` takes `financial_statement_fact` rows and derives, per ticker and
period, gross margin, operating margin, net debt / equity, free-cash-flow conversion, ROIC and
revenue growth. The rules (audit A10 / A11 / A13):

- Facts are grouped by (ticker, accounting basis, period_start, period_end, unit): a 3-month
  and a 6-month figure with the same end are different periods, and USD revenue is never
  divided by EUR gross profit. Balance-sheet (instant) facts join the flow period that ends on
  their date, in the same unit.
- A concept is chosen by a fixed priority list per accounting basis (revenue: the contract
  revenue tag before `Revenues`; net income: attributable to the parent before `ProfitLoss`);
  within a concept the fact of the latest filing wins (a restatement replaces the original).
- Debt is a sum of components, never one element (`xbrl_concepts.DEBT_*`).
- Cover-page (`dei:`) facts and issuer extensions are not statement facts and are skipped;
  dimensional facts are skipped (a segment is not the consolidated figure).
- A ratio whose denominator is zero or negative is "n/m" with the reason, not a number;
  ROIC is only computed for a full-year period, with a tax rate between 0 and 100%.
- A fact the reconciliation (`steward.fundamentals.statements`) failed is never used; with
  `reconciled_only` a fact nothing checked is not used either, otherwise the period is flagged
  `unreconciled_inputs`.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from steward.analytics.ratio_set import nopat_roic
from steward.sources.xbrl_concepts import DEBT_EXTRA, DEBT_PARTS, DEBT_TOTAL

METRIC_NAMES = (
    "revenue_growth",
    "gross_margin",
    "operating_margin",
    "roic",
    "net_debt_to_equity",
    "free_cash_flow_conversion",
)

# Local element names (lower case) by basis, most preferred first.
ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "US_GAAP": {
        "revenue": (
            "revenuefromcontractwithcustomerexcludingassessedtax",
            "revenues",
            "revenuefromcontractwithcustomerincludingassessedtax",
            "salesrevenuenet",
            "revenue",
        ),
        "gross_profit": ("grossprofit",),
        "operating_income": ("operatingincomeloss",),
        "net_income": ("netincomeloss", "profitloss"),
        "tax": ("incometaxexpensebenefit",),
        "pretax": (
            "incomelossfromcontinuingoperationsbeforeincometaxesextraordinaryitemsnoncontrollinginterest",
            "incomelossfromcontinuingoperationsbeforeincometaxesminorityinterestandincomelossfromequitymethodinvestments",
        ),
        "equity": (
            "stockholdersequity",
            "stockholdersequityincludingportionattributabletononcontrollinginterest",
            "equity",
        ),
        "cash": (
            "cashandcashequivalentsatcarryingvalue",
            "cashcashequivalentsrestrictedcashandrestrictedcashequivalents",
        ),
        "cfo": (
            "netcashprovidedbyusedinoperatingactivities",
            "netcashprovidedbyusedinoperatingactivitiescontinuingoperations",
        ),
        "capex": (
            "paymentstoacquirepropertyplantandequipment",
            "paymentstoacquireproductiveassets",
        ),
    },
    "IFRS": {
        "revenue": ("revenue", "revenuefromcontractswithcustomers"),
        "gross_profit": ("grossprofit",),
        "operating_income": ("profitlossfromoperatingactivities",),
        "net_income": ("profitlossattributabletoownersofparent", "profitloss"),
        "tax": ("incometaxexpensecontinuingoperations",),
        "pretax": ("profitlossbeforetax",),
        "equity": ("equityattributabletoownersofparent", "equity"),
        "cash": ("cashandcashequivalents",),
        "cfo": ("cashflowsfromusedinoperatingactivities",),
        "capex": (
            "purchaseofpropertyplantandequipmentclassifiedasinvestingactivities",
            "purchaseofpropertyplantandequipment",
        ),
    },
}
BASIS_TAXONOMY = {"US_GAAP": "us-gaap", "IFRS": "ifrs-full"}
INSTANT_INPUTS = ("equity", "cash")
# Non-controlling interest is read beside the inputs, not as one of them: it is optional (absent
# means none) and does not decide whether a period is reconciled.
NCI_NAMES = {"US_GAAP": ("minorityinterest",), "IFRS": ("noncontrollinginterests",)}
# Equity elements that already include the non-controlling interest.
EQUITY_WITH_NCI = {
    "stockholdersequityincludingportionattributabletononcontrollinginterest",
    "equity",
}
FLOW_INPUTS = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "tax",
    "pretax",
    "cfo",
    "capex",
)

FORMULAS = {
    "gross_margin": "gross profit / revenue (revenue > 0)",
    "revenue_growth": (
        "revenue / revenue of the comparable prior-year period (same length, same unit, "
        "both > 0) - 1"
    ),
    "operating_margin": "operating income / revenue (revenue > 0)",
    "roic": (
        "operating income * (1 - tax rate) / average invested capital now and a year earlier, "
        "invested capital = equity + non-controlling interest + debt - cash; tax rate = tax "
        "expense / pretax income capped to 0-50%; full-year periods only; pretax income > 0, "
        "average invested capital > 0"
    ),
    "net_debt_to_equity": "(debt - cash) / equity (equity > 0)",
    "free_cash_flow_conversion": (
        "(cash from operations - absolute capital expenditure) / net income (net income > 0)"
    ),
}
FORMULA_DETAIL: dict[str, Any] = {
    "aliases": ALIASES,
    "debt": (
        "debt = the total element (US GAAP LongTermDebt, IFRS Borrowings) at the period end, "
        "else its non-current plus current portion, plus short-term borrowings and commercial "
        "paper (US GAAP); lease liabilities are not included"
    ),
    "debt_elements": {
        "total": DEBT_TOTAL,
        "parts": DEBT_PARTS,
        "extra": DEBT_EXTRA,
    },
    "net_income": "net income attributable to the parent before ProfitLoss (includes minorities)",
    "period_choice": (
        "latest period end with a computed metric; an annual duration before a shorter one"
    ),
    "peer_period_match": (
        "a peer period of the same accounting basis and duration (within 10 days) whose end is "
        "within 45 days of the holding's"
    ),
}
PEER_END_TOLERANCE_DAYS = 45
DURATION_TOLERANCE_DAYS = 10
ANNUAL_DAYS = (330, 380)


def _decode_dimensions(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _is_dimensional(row: dict[str, object]) -> bool:
    dims = _decode_dimensions(row.get("dimensions"))
    return bool(dims.get("members")) or bool(dims.get("typed"))


def _validation(row: dict[str, object]) -> dict[str, Any]:
    return _decode_dimensions(row.get("validation"))


def _reconciled(row: dict[str, object]) -> bool | None:
    """True/False from `validation.reconciled` (None: nothing checked this fact)."""
    value = _validation(row).get("reconciled")
    return value if isinstance(value, bool) else None


def _local(concept: object) -> str:
    return str(concept or "").split(":")[-1].lower()


def _known(row: dict[str, object]) -> dt.datetime:
    known = row.get("known_at")
    return known if isinstance(known, dt.datetime) else dt.datetime.min.replace(tzinfo=dt.UTC)


@dataclass
class _Picked:
    value: Decimal
    concept: str
    reconciled: bool | None


def _pick(rows: list[dict[str, object]], names: tuple[str, ...]) -> _Picked | None:
    """The fact of the highest-priority concept present; the latest filing's if repeated."""
    for name in names:
        found = [r for r in rows if _local(r.get("concept")) == name]
        if not found:
            continue
        best = max(found, key=_known)
        value = best.get("value")
        assert isinstance(value, Decimal)
        return _Picked(value, str(best.get("concept")), _reconciled(best))
    return None


def _debt(rows: list[dict[str, object]], taxonomy: str) -> tuple[Decimal | None, list[str]]:
    """(debt, element names used): the total, else non-current + current, plus extras."""
    by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_name[_local(row.get("concept"))].append(row)

    def latest(names: tuple[str, ...]) -> tuple[Decimal, str] | None:
        for name in names:
            if name in by_name:
                row = max(by_name[name], key=_known)
                value = row.get("value")
                if isinstance(value, Decimal):
                    return value, name
        return None

    used: list[str] = []
    total = latest(DEBT_TOTAL.get(taxonomy, ()))
    value = Decimal(0)
    if total is not None:
        value, name = total
        used.append(name)
    else:
        groups = DEBT_PARTS.get(taxonomy, ())
        parts = [latest(group) for group in groups]
        if not groups or parts[0] is None:
            return None, []  # no non-current portion: not a complete debt figure
        for part in parts:
            if part is not None:
                value += part[0]
                used.append(part[1])
    for group in DEBT_EXTRA.get(taxonomy, ()):
        extra = latest(group)
        if extra is not None:
            value += extra[0]
            used.append(extra[1])
    return value, used


def _duration(start: object, end: object) -> int | None:
    if isinstance(start, dt.date) and isinstance(end, dt.date):
        return (end - start).days
    return None


def _is_annual(days: int | None) -> bool:
    return days is not None and ANNUAL_DAYS[0] <= days <= ANNUAL_DAYS[1]


def invested_capital(inputs: dict[str, Any]) -> Decimal | None:
    """Equity + non-controlling interest + debt - cash from a period's `inputs`. The interest is
    added only when the equity element excludes it; a period that files none has none."""

    def get(key: str) -> Decimal | None:
        item = inputs.get(key)
        return item["value"] if item else None

    equity, debt, cash = get("equity"), get("debt"), get("cash")
    if equity is None or debt is None or cash is None:
        return None
    nci = Decimal(0)
    if _local(inputs["equity"]["concept"]) not in EQUITY_WITH_NCI:
        nci = get("noncontrolling_interest") or Decimal(0)
    return equity + nci + debt - cash


def _ratio(
    numerator: Decimal | None,
    denominator: Decimal | None,
    *,
    what: str,
    missing: str,
    reasons: dict[str, str],
    metric: str,
) -> Decimal | None:
    if numerator is None or denominator is None:
        reasons[metric] = f"missing {missing}"
        return None
    if denominator <= 0:
        reasons[metric] = f"n/m: non-positive {what}"
        return None
    return numerator / denominator


def _build_period(
    ticker: str,
    basis: str,
    start: dt.date | None,
    end: dt.date,
    unit: str | None,
    flows: list[dict[str, object]],
    instants: list[dict[str, object]],
) -> dict[str, Any]:
    names = ALIASES[basis]
    taxonomy = BASIS_TAXONOMY[basis]
    picked: dict[str, _Picked | None] = {}
    # A fact with no start date cannot be told from a balance; a period with no dated flow
    # facts reads such facts as its flows (older rows and fixtures carry no period start).
    undated = flows or (instants if start is None else [])
    for key in FLOW_INPUTS:
        picked[key] = _pick(undated, names[key]) if undated else None
    for key in INSTANT_INPUTS:
        picked[key] = _pick(instants, names[key]) if instants else None
    debt, debt_elements = _debt(instants, taxonomy) if instants else (None, [])

    def val(key: str) -> Decimal | None:
        item = picked[key]
        return item.value if item else None

    reasons: dict[str, str] = {}
    revenue = val("revenue")
    metrics: dict[str, Decimal | None] = {}
    metrics["gross_margin"] = _ratio(
        val("gross_profit"),
        revenue,
        what="revenue",
        missing="gross profit or revenue",
        reasons=reasons,
        metric="gross_margin",
    )
    metrics["operating_margin"] = _ratio(
        val("operating_income"),
        revenue,
        what="revenue",
        missing="operating income or revenue",
        reasons=reasons,
        metric="operating_margin",
    )
    cash = val("cash")
    equity = val("equity")
    metrics["net_debt_to_equity"] = _ratio(
        (debt - cash) if debt is not None and cash is not None else None,
        equity,
        what="equity",
        missing="debt, cash or equity",
        reasons=reasons,
        metric="net_debt_to_equity",
    )
    cfo, capex = val("cfo"), val("capex")
    metrics["free_cash_flow_conversion"] = _ratio(
        (cfo - abs(capex)) if cfo is not None and capex is not None else None,
        val("net_income"),
        what="net income",
        missing="cash from operations, capital expenditure or net income",
        reasons=reasons,
        metric="free_cash_flow_conversion",
    )
    days = _duration(start, end)
    metrics["roic"] = None  # set by `_attach_roic`, which needs the year-earlier period
    nci = _pick(instants, NCI_NAMES[basis]) if instants else None
    states = {item.reconciled for item in picked.values() if item is not None}
    if not states:
        states = {None}
    return {
        "ticker": ticker,
        "period_start": start,
        "period_end": end,
        "duration_days": days,
        "basis": basis,
        "unit": unit,
        "revenue": revenue,
        **metrics,
        "revenue_growth": None,
        "not_meaningful": reasons,
        "inputs": {
            key: {"concept": item.concept, "value": item.value}
            for key, item in picked.items()
            if item is not None
        }
        | ({"debt": {"concept": "+".join(debt_elements), "value": debt}} if debt_elements else {})
        | (
            {"noncontrolling_interest": {"concept": nci.concept, "value": nci.value}}
            if nci is not None
            else {}
        ),
        "unreconciled_inputs": sorted(
            key for key, item in picked.items() if item is not None and item.reconciled is None
        ),
        "reconciliation": (
            "reconciled" if states == {True} else "unreconciled" if None in states else "mixed"
        ),
    }


def _attach_growth(periods: list[dict[str, Any]]) -> None:
    for current in periods:
        current["revenue_growth"] = None
        start, end, revenue = current["period_start"], current["period_end"], current["revenue"]
        if not isinstance(start, dt.date) or revenue is None:
            current["not_meaningful"].setdefault("revenue_growth", "missing period or revenue")
            continue
        duration = (end - start).days
        prior = [
            p
            for p in periods
            if p["basis"] == current["basis"]
            and p["unit"] == current["unit"]
            and isinstance(p["period_start"], dt.date)
            and 330 <= (end - p["period_end"]).days <= 400
            and abs((p["period_end"] - p["period_start"]).days - duration) <= 7
            and p["revenue"] is not None
        ]
        if not prior:
            current["not_meaningful"].setdefault(
                "revenue_growth", "no comparable prior-year period of the same unit"
            )
            continue
        base = max(prior, key=lambda p: p["period_end"])["revenue"]
        if base <= 0 or revenue <= 0:
            current["not_meaningful"]["revenue_growth"] = "n/m: non-positive revenue"
            continue
        current["revenue_growth"] = revenue / base - Decimal(1)


def prior_year(period: dict[str, Any], periods: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The full-year period of the same basis that ended about a year before `period`."""
    return next(
        (
            p
            for p in periods
            if _is_annual(p["duration_days"])
            and p["basis"] == period["basis"]
            and 320 <= (period["period_end"] - p["period_end"]).days <= 410
        ),
        None,
    )


def _attach_roic(periods: list[dict[str, Any]]) -> None:
    """ROIC by the one shared definition (`ratio_set.nopat_roic`), on full-year periods."""
    for current in periods:
        if not _is_annual(current["duration_days"]):
            current["not_meaningful"]["roic"] = "n/m: ROIC is computed for a full-year period only"
            continue
        prior = prior_year(current, periods)
        got = {k: v["value"] for k, v in current["inputs"].items()}
        result = nopat_roic(
            got.get("operating_income"),
            got.get("pretax"),
            got.get("tax"),
            invested_capital(current["inputs"]),
            invested_capital(prior["inputs"]) if prior else None,
        )
        current["roic"] = result.value
        if result.value is None:
            current["not_meaningful"]["roic"] = result.reason or "missing inputs"


def financial_peer_metrics(
    rows: list[dict[str, object]], *, reconciled_only: bool = False
) -> dict[str, dict[str, object]]:
    """Per ticker: `periods` (newest first) and the metric `formulas`. See the module doc."""
    flow_groups: dict[tuple[Any, ...], list[dict[str, object]]] = defaultdict(list)
    instant_groups: dict[tuple[Any, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        ticker, basis = row.get("ticker"), row.get("basis")
        if ticker is None or basis not in ALIASES:
            continue
        concept = str(row.get("concept") or "")
        if concept.startswith("dei:") or not isinstance(row.get("value"), Decimal):
            continue
        if _is_dimensional(row):
            continue
        end = row.get("period_end")
        if not isinstance(end, dt.date):
            continue
        state = _reconciled(row)
        if state is False or (reconciled_only and state is not True):
            continue
        unit = row.get("unit")
        start = row.get("period_start")
        if isinstance(start, dt.date):
            flow_groups[(ticker, basis, start, end, unit)].append(row)
        else:
            instant_groups[(ticker, basis, end, unit)].append(row)

    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    used_instants: set[tuple[Any, ...]] = set()
    for (ticker, basis, start, end, unit), flows in flow_groups.items():
        instants = instant_groups.get((ticker, basis, end, unit), [])
        used_instants.add((ticker, basis, end, unit))
        by_ticker[str(ticker)].append(
            _build_period(ticker, basis, start, end, unit, flows, instants)
        )
    for (ticker, basis, end, unit), instants in instant_groups.items():
        if (ticker, basis, end, unit) not in used_instants:
            by_ticker[str(ticker)].append(
                _build_period(ticker, basis, None, end, unit, [], instants)
            )
    out: dict[str, dict[str, object]] = {}
    for ticker, periods in by_ticker.items():
        _attach_growth(periods)
        _attach_roic(periods)
        periods.sort(
            key=lambda p: (p["period_end"], p["duration_days"] or 0),
            reverse=True,
        )
        out[ticker] = {
            "periods": periods,
            "formulas": FORMULAS,
            "formula_detail": FORMULA_DETAIL,
        }
    return out


def has_metric(period: dict[str, Any]) -> bool:
    return any(period.get(name) is not None for name in METRIC_NAMES)


def latest_period(periods: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest period end that has a computed metric, a full year before a shorter span."""
    usable = [p for p in periods if has_metric(p)]
    if not usable:
        return None
    end = max(p["period_end"] for p in usable)
    at_end = [p for p in usable if p["period_end"] == end]
    return max(at_end, key=lambda p: (_is_annual(p["duration_days"]), p["duration_days"] or 0))


def comparable_period(
    reference: dict[str, Any], periods: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str]:
    """(period, reason): the peer period of the reference's basis and duration nearest its end."""
    same_basis = [p for p in periods if p["basis"] == reference["basis"]]
    if not periods:
        return None, "no filed statement facts"
    if not same_basis:
        bases = sorted({str(p["basis"]) for p in periods})
        return None, (
            f"accounting basis {'/'.join(bases)} differs from {reference['basis']}: not comparable"
        )
    duration = reference["duration_days"]
    candidates = [
        p
        for p in same_basis
        if has_metric(p)
        and abs((p["period_end"] - reference["period_end"]).days) <= PEER_END_TOLERANCE_DAYS
        and (
            duration is None
            or p["duration_days"] is None
            or abs(p["duration_days"] - duration) <= DURATION_TOLERANCE_DAYS
        )
    ]
    if not candidates:
        return None, (
            f"no {reference['basis']} period of about {duration} days ending within "
            f"{PEER_END_TOLERANCE_DAYS} days of {reference['period_end']}"
        )
    return (
        min(candidates, key=lambda p: abs((p["period_end"] - reference["period_end"]).days)),
        "",
    )


def compatible_peer_mean(
    values: list[tuple[str, Decimal | None, str]],
    *,
    metric: str,
    reasons: dict[str, str] | None = None,
) -> dict[str, object]:
    """Mean of explicitly comparable peer facts; excludes missing/incompatible values and says
    why (`excluded_reasons`, from `reasons` by ticker when given)."""
    included = [
        (ticker, value)
        for ticker, value, definition in values
        if value is not None and definition == metric
    ]
    excluded = [
        ticker for ticker, value, definition in values if value is None or definition != metric
    ]
    why = {
        ticker: (reasons or {}).get(ticker)
        or ("different metric definition" if definition != metric else "no value")
        for ticker, value, definition in values
        if value is None or definition != metric
    }
    mean = sum((value for _, value in included), Decimal(0)) / len(included) if included else None
    return {
        "metric": metric,
        "mean": mean,
        "covered_peers": len(included),
        "included": [ticker for ticker, _ in included],
        "excluded": excluded,
        "excluded_reasons": why,
    }


def peer_comparison(
    ticker: str, peers: list[str], metrics: dict[str, dict[str, object]]
) -> dict[str, Any]:
    """The holding's latest period against its peers' comparable periods, per metric, with the
    reason every peer that is not in a mean is not (D4.6)."""
    own_periods: list[dict[str, Any]] = list(metrics.get(ticker, {}).get("periods", []))  # type: ignore[call-overload]
    latest = latest_period(own_periods)
    peer_periods: dict[str, dict[str, Any] | None] = {}
    peer_reason: dict[str, str] = {}
    for peer in peers:
        if latest is None:
            peer_periods[peer], peer_reason[peer] = None, "the holding has no computed period"
            continue
        periods: list[dict[str, Any]] = list(metrics.get(peer, {}).get("periods", []))  # type: ignore[call-overload]
        peer_periods[peer], peer_reason[peer] = comparable_period(latest, periods)
    output: dict[str, Any] = {}
    for name in METRIC_NAMES:
        values: list[tuple[str, Decimal | None, str]] = []
        reasons: dict[str, str] = {}
        for peer in peers:
            period = peer_periods[peer]
            if period is None:
                values.append((peer, None, name))
                reasons[peer] = peer_reason[peer]
                continue
            value = period.get(name)
            values.append((peer, value, name))
            if value is None:
                reasons[peer] = period["not_meaningful"].get(name, "metric not computable")
        mean = compatible_peer_mean(values, metric=name, reasons=reasons)
        output[name] = {
            **mean,
            "holding_value": latest.get(name) if latest else None,
            "holding_reason": (latest or {}).get("not_meaningful", {}).get(name),
            "period_end": latest["period_end"] if latest else None,
            "period_start": latest["period_start"] if latest else None,
            "basis": latest["basis"] if latest else None,
            "peer_period_ends": {
                peer: period["period_end"] if period else None
                for peer, period in peer_periods.items()
            },
        }
    return {
        "metrics": output,
        "excluded_peers": [
            {"peer": peer, "reason": peer_reason[peer]}
            for peer in peers
            if peer_periods[peer] is None
        ],
        "holding_period": latest,
    }
