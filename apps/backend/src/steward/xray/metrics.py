"""Concentration, weighted fundamentals and fee drag of the looked-through portfolio (plan M5.3,
M5.4).

Concentration reuses `analytics.exposure.concentration` (HHI, effective holdings, top-10) and adds
levels. Weights are shares of total portfolio value; HHI and effective holdings are computed on
each item's share of the *identified* weight at that level, because unknown, unclassified and cash
weight is kept outside them and shown beside them, never spread over the known items.

Fundamentals are weighted over the equity holdings the app holds statements and prices for, and
every figure says how much of the equity weight it covers and what was left out. A mean that would
not mean anything (negative aggregate earnings, financial companies mixed with industrials,
unknown fund weight) is withheld with its reason instead of being forced into a number.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from steward.analytics.exposure import (
    YieldInput,
    concentration,
    pe_from_yield,
    portfolio_earnings_yield,
)
from steward.lookthrough.read import NOT_LOOKED, UNKNOWN_FUND
from steward.xray.leaves import IssuerFacts, Leaf, LeafSet

ZERO = Decimal(0)
ONE = Decimal(1)
LEVELS = ("security", "issuer", "sector", "country", "fund")
THIN_COVERAGE = 0.5


def _d(x: float) -> Decimal:
    return Decimal(repr(x))


class XrayActive(BaseModel):
    key: str
    label: str
    portfolio_weight: float = Field(description="Share of the portfolio's identified equity.")
    benchmark_weight: float
    active: float = Field(description="Portfolio weight minus benchmark weight.")


class XrayItem(BaseModel):
    key: str
    label: str
    weight: float = Field(description="Share of total portfolio value.")
    share_of_known: float


class XrayConcentration(BaseModel):
    level: Literal["security", "issuer", "sector", "country", "fund"]
    label: str
    basis: str
    items: int
    known_weight: float = Field(description="Identified weight at this level.")
    unknown_weight: float = Field(
        description="Unknown fund weight; outside the HHI, never redistributed."
    )
    unclassified_weight: float = Field(
        description="Equity at this level with no label; outside the HHI."
    )
    top_items: list[XrayItem]
    largest: XrayItem | None
    top10_weight: float | None
    top10_of_known: float | None
    hhi: float | None
    effective_n: float | None
    overweights: list[XrayActive] | None = None
    underweights: list[XrayActive] | None = None
    active_note: str | None = None
    warnings: list[str]


def unknown_weight(leafset: LeafSet) -> float:
    return sum(x.weight for x in leafset.leaves if x.special in (NOT_LOOKED, UNKNOWN_FUND))


def _level(
    level: str,
    label: str,
    basis: str,
    weights: dict[str, tuple[str, float]],
    *,
    unknown: float,
    unclassified: float,
) -> XrayConcentration:
    known = sum(w for _, w in weights.values())
    ranked = sorted(weights.items(), key=lambda kv: -kv[1][1])
    warnings: list[str] = []
    items = [
        XrayItem(key=k, label=lab, weight=w, share_of_known=(w / known if known else 0.0))
        for k, (lab, w) in ranked[:10]
    ]
    if known <= 0:
        return XrayConcentration(
            level=level,
            label=label,
            basis=basis,
            items=0,
            known_weight=0.0,
            unknown_weight=unknown,
            unclassified_weight=unclassified,
            top_items=[],
            largest=None,
            top10_weight=None,
            top10_of_known=None,
            hhi=None,
            effective_n=None,
            warnings=["Nothing is identified at this level yet."],
        )
    shares = {k: _d(w / known) for k, (_, w) in weights.items() if w > 0}
    c = concentration(shares)
    top10 = sum(w for _, (_, w) in ranked[:10])
    if unknown > 0:
        warnings.append(
            f"{unknown:.1%} of the portfolio is unknown fund weight, outside these figures."
        )
    if unclassified > 0:
        warnings.append(
            f"{unclassified:.1%} of the portfolio has no label here, outside these figures."
        )
    return XrayConcentration(
        level=level,
        label=label,
        basis=basis,
        items=len(weights),
        known_weight=known,
        unknown_weight=unknown,
        unclassified_weight=unclassified,
        top_items=items,
        largest=items[0] if items else None,
        top10_weight=top10,
        top10_of_known=top10 / known,
        hhi=float(c.hhi) if c.hhi is not None else None,
        effective_n=float(c.effective_count) if c.effective_count is not None else None,
        warnings=warnings,
    )


def concentration_levels(
    leafset: LeafSet,
    *,
    actives: dict[str, tuple[list[XrayActive], list[XrayActive], str | None]] | None = None,
) -> list[XrayConcentration]:
    unknown = unknown_weight(leafset)
    equity = [x for x in leafset.leaves if x.is_equity]
    sec: dict[str, tuple[str, float]] = defaultdict(lambda: ("", 0.0))
    iss: dict[str, tuple[str, float]] = defaultdict(lambda: ("", 0.0))
    sector: dict[str, tuple[str, float]] = defaultdict(lambda: ("", 0.0))
    country: dict[str, tuple[str, float]] = defaultdict(lambda: ("", 0.0))
    unlab_sector = unlab_country = 0.0
    for x in equity:
        sk = x.isin or f"name:{(x.name or '').lower()}"
        sec[sk] = (x.name or sk, sec[sk][1] + x.weight)
        ik = x.issuer_key or sk
        iss[ik] = (x.issuer_label or x.name or ik, iss[ik][1] + x.weight)
        if x.sector:
            sector[x.sector] = (x.sector, sector[x.sector][1] + x.weight)
        else:
            unlab_sector += x.weight
        if x.country:
            country[x.country] = (x.country, country[x.country][1] + x.weight)
        else:
            unlab_country += x.weight
    funds: dict[str, tuple[str, float]] = {}
    for key, ch in leafset.channels.items():
        if ch.kind == "fund" and ch.weight > 0:
            funds[key] = (ch.label, ch.weight)

    out = [
        _level(
            "security",
            "Security",
            "Each listed security (an ISIN), direct and through funds, share classes kept apart.",
            sec,
            unknown=unknown,
            unclassified=0.0,
        ),
        _level(
            "issuer",
            "Issuer",
            "Securities merged by canonical issuer, direct and through funds: the single-issuer "
            "look-through weight is the largest item.",
            iss,
            unknown=unknown,
            unclassified=0.0,
        ),
        _level(
            "sector",
            "Sector",
            "GICS sector of identified equity; the labels' bases are in the sector view.",
            sector,
            unknown=unknown,
            unclassified=unlab_sector,
        ),
        _level(
            "country",
            "Country",
            "Country of identified equity as labelled; it mixes country of risk and listing "
            "country where the issuers differ (see the country view).",
            country,
            unknown=unknown,
            unclassified=unlab_country,
        ),
        _level(
            "fund",
            "Fund",
            "Each held fund's share of total portfolio value; direct holdings and cash are "
            "not funds and are left out.",
            funds,
            unknown=0.0,
            unclassified=0.0,
        ),
    ]
    if actives:
        for c in out:
            hit = actives.get(c.level)
            if hit is None:
                continue
            c.overweights, c.underweights, c.active_note = hit
    return out


# ----------------------------------------------------------------------------- fundamentals


class XrayExclusion(BaseModel):
    label: str
    weight: float
    reason: str


class XrayMetric(BaseModel):
    id: str
    label: str
    value: float | None
    unit: Literal["fraction", "multiple"]
    covered_weight: float = Field(description="Share of total portfolio value the figure covers.")
    coverage: float | None = Field(description="Covered weight over identified equity weight.")
    excluded: list[XrayExclusion]
    excluded_count: int
    formula: str
    thin: bool = Field(description="Covers under half of the equity weight: indicative only.")
    reason: str | None = Field(description="Why there is no figure, when there is none.")


class XrayFeeLine(BaseModel):
    isin: str
    label: str
    weight: float
    expense_ratio: float | None
    annual_drag: float | None = Field(
        description="weight x ongoing charge, share of portfolio a year."
    )


class XrayFees(BaseModel):
    annual_drag: float
    annual_cost_base: float
    weighted_expense_ratio: float | None
    funds_with_ratio: int
    funds_without_ratio: list[str]
    unpriced_weight: float
    lines: list[XrayFeeLine]
    note: str


class XrayFundamentals(BaseModel):
    equity_weight: float
    covered_weight: float
    scored_issuers: int
    financials_weight: float
    unknown_weight: float
    metrics: list[XrayMetric]
    fees: XrayFees | None
    notes: list[str]


@dataclass(frozen=True)
class IssuerLine:
    key: str
    label: str
    weight: Decimal
    sector: str | None
    facts: IssuerFacts | None


def issuer_lines(leaves: list[Leaf], facts: dict[str, IssuerFacts]) -> list[IssuerLine]:
    weight: dict[str, float] = defaultdict(float)
    label: dict[str, str] = {}
    sector: dict[str, str | None] = {}
    for x in leaves:
        if not x.is_equity:
            continue
        key = x.issuer_key or f"unidentified:{x.name or x.ticker}"
        weight[key] += x.weight
        label.setdefault(key, x.issuer_label or x.name or key)
        sector[key] = sector.get(key) or x.sector
    return [
        IssuerLine(k, label[k], _d(w), sector.get(k), facts.get(k))
        for k, w in sorted(weight.items(), key=lambda kv: -kv[1])
    ]


def _is_financial(sector: str | None) -> bool:
    return (sector or "").lower().startswith("financ")


def _exclusions(rows: list[tuple[str, Decimal, str]], limit: int = 8) -> list[XrayExclusion]:
    ranked = sorted(rows, key=lambda r: -r[1])
    return [XrayExclusion(label=n, weight=float(w), reason=r) for n, w, r in ranked[:limit]]


def _metric(
    *,
    id: str,
    label: str,
    unit: Literal["fraction", "multiple"],
    value: Decimal | None,
    covered: Decimal,
    equity: Decimal,
    excluded: list[tuple[str, Decimal, str]],
    formula: str,
    reason: str | None,
) -> XrayMetric:
    coverage = float(covered / equity) if equity > 0 else None
    return XrayMetric(
        id=id,
        label=label,
        value=float(value) if value is not None else None,
        unit=unit,
        covered_weight=float(covered),
        coverage=coverage,
        excluded=_exclusions(excluded),
        excluded_count=len(excluded),
        formula=formula,
        thin=value is not None and coverage is not None and coverage < THIN_COVERAGE,
        reason=None if value is not None else reason,
    )


def build_fees(leafset: LeafSet) -> XrayFees | None:
    held = [c for c in leafset.channels.values() if c.kind == "fund" and c.weight > 0]
    if not held:
        return None
    lines = [
        XrayFeeLine(
            isin=c.key,
            label=c.label,
            weight=c.weight,
            expense_ratio=float(c.expense_ratio) if c.expense_ratio is not None else None,
            annual_drag=float(c.expense_ratio) * c.weight if c.expense_ratio is not None else None,
        )
        for c in sorted(held, key=lambda c: -c.weight)
    ]
    priced = [x for x in lines if x.annual_drag is not None]
    drag = sum(x.annual_drag or 0.0 for x in priced)
    priced_weight = sum(x.weight for x in priced)
    unpriced = [x for x in lines if x.annual_drag is None]
    return XrayFees(
        annual_drag=drag,
        annual_cost_base=drag * leafset.total,
        weighted_expense_ratio=drag / priced_weight if priced_weight > 0 else None,
        funds_with_ratio=len(priced),
        funds_without_ratio=[x.label for x in unpriced],
        unpriced_weight=sum(x.weight for x in unpriced),
        lines=lines,
        note="Ongoing charge only: no transaction costs, spreads, taxes or direct-holding "
        "costs. A fund without a known charge is listed, not counted as free.",
    )


def build_fundamentals(leafset: LeafSet, facts: dict[str, IssuerFacts]) -> XrayFundamentals:
    lines = issuer_lines(leafset.leaves, facts)
    equity = sum((x.weight for x in lines), ZERO)
    unknown = unknown_weight(leafset)
    has_funds = any(c.kind == "fund" and c.weight > 0 for c in leafset.channels.values())
    covered_lines = [x for x in lines if x.facts is not None and x.facts.covered]
    covered_weight = sum((x.weight for x in covered_lines), ZERO)
    financials = sum((x.weight for x in covered_lines if _is_financial(x.sector)), ZERO)
    uncovered = [
        (x.label, x.weight, "no stored statements or prices for this issuer")
        for x in lines
        if x.facts is None or not x.facts.covered
    ]
    metrics: list[XrayMetric] = []

    # earnings yield and P/E
    items = [
        YieldInput(
            key=x.label,
            weight=x.weight,
            earnings_yield=x.facts.earnings_yield if x.facts else None,
            excluded_reason=None
            if (x.facts and x.facts.covered)
            else "no stored statements or prices",
        )
        for x in lines
    ]
    ey = portfolio_earnings_yield(items, includes_look_through=has_funds)
    ey_cov = ey.coverage.numerator if ey.coverage else ZERO
    ey_excluded = list(uncovered)
    for x in covered_lines:
        e = x.facts.earnings_yield if x.facts else None
        if e is None:
            ey_excluded.append((x.label, x.weight, "earnings missing"))
        elif e <= 0:
            ey_excluded.append((x.label, x.weight, "negative or zero earnings"))
    negative = sum((w for _, w, r in ey_excluded if r.startswith("negative")), ZERO)
    metrics.append(
        _metric(
            id="earnings_yield",
            label="Earnings yield",
            unit="fraction",
            value=ey.value,
            covered=ey_cov,
            equity=equity,
            excluded=ey_excluded,
            formula="sum(weight x E/P) / sum(weight) over covered holdings with positive earnings",
            reason=(ey.warnings[0] if ey.warnings else "no covered holding has positive earnings"),
        )
    )
    pe = pe_from_yield(ey.value)
    metrics.append(
        _metric(
            id="pe",
            label="Price to earnings",
            unit="multiple",
            value=pe,
            covered=ey_cov,
            equity=equity,
            excluded=ey_excluded,
            formula="1 / the aggregate earnings yield: an average of yields, not of P/E ratios",
            reason="the aggregate earnings yield is not positive, so no P/E exists",
        )
    )

    # price to book (harmonic)
    by = covered_pb = ZERO
    pb_excluded = list(uncovered)
    for x in covered_lines:
        v = x.facts.price_to_book if x.facts else None
        if v is None or v <= 0:
            pb_excluded.append(
                (
                    x.label,
                    x.weight,
                    "negative or zero book value" if v is not None else "book value missing",
                )
            )
            continue
        covered_pb += x.weight
        by += x.weight / v
    book_yield = by / covered_pb if covered_pb > 0 else None
    metrics.append(
        _metric(
            id="price_to_book",
            label="Price to book",
            unit="multiple",
            value=ONE / book_yield if book_yield else None,
            covered=covered_pb,
            equity=equity,
            excluded=pb_excluded,
            formula="1 / (sum(weight / P/B) / sum(weight)) over covered holdings with a positive "
            "book value",
            reason="no covered holding has a positive book value",
        )
    )

    # dividend yield (weighted mean; a company paying nothing counts as 0)
    acc = cov = ZERO
    dy_excluded = list(uncovered)
    for x in covered_lines:
        v = x.facts.dividend_yield if x.facts else None
        if v is None:
            dy_excluded.append((x.label, x.weight, "no dividend data"))
            continue
        acc += x.weight * v
        cov += x.weight
    metrics.append(
        _metric(
            id="dividend_yield",
            label="Dividend yield",
            unit="fraction",
            value=acc / cov if cov > 0 else None,
            covered=cov,
            equity=equity,
            excluded=dy_excluded,
            formula="sum(weight x dividend yield) / sum(weight) over covered holdings with "
            "dividend data",
            reason="no covered holding has dividend data",
        )
    )

    # profitability and leverage exclude financial companies
    for mid, mlabel, attr, formula in (
        (
            "profitability",
            "Gross profit to assets",
            "profitability",
            "sum(weight x gross profit / total assets) / sum(weight), financial companies excluded",
        ),
        (
            "leverage",
            "Net debt to equity",
            "net_debt_to_equity",
            "sum(weight x net debt / equity) / sum(weight), financial companies excluded",
        ),
        (
            "net_debt_to_ebitda",
            "Net debt to EBITDA",
            "net_debt_to_ebitda",
            "sum(weight x net debt / EBITDA) / sum(weight), financial companies and companies "
            "with non-positive EBITDA excluded",
        ),
    ):
        acc = cov = ZERO
        excluded = list(uncovered)
        for x in covered_lines:
            if _is_financial(x.sector):
                excluded.append(
                    (x.label, x.weight, "financial company: its assets and debt are not comparable")
                )
                continue
            v = getattr(x.facts, attr) if x.facts else None
            if v is None:
                excluded.append((x.label, x.weight, "inputs missing"))
                continue
            acc += x.weight * v
            cov += x.weight
        metrics.append(
            _metric(
                id=mid,
                label=mlabel,
                unit="fraction" if mid == "profitability" else "multiple",
                value=acc / cov if cov > 0 else None,
                covered=cov,
                equity=equity,
                excluded=excluded,
                formula=formula,
                reason="no covered non-financial holding has the inputs",
            )
        )

    fees = build_fees(leafset)
    notes = [
        "Each figure covers only the equity holdings the app has stored statements and prices "
        "for; the coverage and the largest exclusions are stated per figure.",
    ]
    if unknown > 0:
        notes.append(
            f"{unknown:.1%} of the portfolio is unknown fund weight (not looked through, or not "
            "accounted for by published rows); it is outside every figure."
        )
    if financials > 0:
        notes.append(
            f"Financial companies are {float(financials):.1%} of the portfolio. They stay in "
            "earnings yield, price to book and dividend yield, and are left out of profitability "
            "and leverage because their assets and debt mean something different."
        )
    if negative > 0:
        notes.append(
            f"{float(negative):.1%} of the portfolio has negative earnings and is left out of "
            "the earnings yield and P/E, not netted against profits."
        )
    if len(lines) > 0 and any(x.facts is None for x in lines):
        notes.append("Issuers beyond the largest scored are not scored (see the coverage).")
    return XrayFundamentals(
        equity_weight=float(equity),
        covered_weight=float(covered_weight),
        scored_issuers=len(covered_lines),
        financials_weight=float(financials),
        unknown_weight=unknown,
        metrics=metrics,
        fees=fees,
        notes=notes,
    )
