"""Proposed-portfolio X-Ray (plan M5.7).

You edit hypothetical weights, or add a watchlist security, and see exposure, overlap, fees and
concentration before and after. The proposal is applied to an in-memory copy of the portfolio:
nothing here reads a write path, and the saved Trading212 snapshot is never touched.

Weights are shares of the current total portfolio value. A position you do not mention keeps its
weight; cash is what is left, so the proposal always adds up to the same total and cannot go
below zero. A security you add with no stored fund holdings, or a fund that is not looked
through, enters as unknown weight and the coverage difference says so.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from steward.lookthrough.read import CASH, NOT_LOOKED, PortfolioIn, PositionIn
from steward.xray.leaves import XrayData, build_leaves
from steward.xray.metrics import (
    XrayConcentration,
    XrayFundamentals,
    build_fundamentals,
    concentration_levels,
    unknown_weight,
)
from steward.xray.views import ViewContext, XrayView, build_view

COMPARED: tuple[str, ...] = (
    "asset_class", "sector", "industry", "country", "domicile", "listing", "region", "currency",
    "issuer", "fund", "style", "size", "fees", "revenue_country", "commodity", "trade_product",
)  # fmt: skip
OPTIONAL_LENSES = frozenset(
    {"industry", "listing", "revenue_country", "commodity", "trade_product"}
)
LOW = 1e-9


class ProposedWeight(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    weight: float = Field(ge=0, le=1, description="Share of total portfolio value, 0 to 1.")


class ProposedRequest(BaseModel):
    """Hypothetical weights; held positions not listed keep their weight."""

    weights: list[ProposedWeight] = Field(default_factory=list, max_length=100)
    additions: list[ProposedWeight] = Field(
        default_factory=list,
        max_length=20,
        description="Securities from the investable universe or a watchlist to add.",
    )
    deposit: float = Field(
        default=0,
        ge=-1e12,
        le=1e12,
        description="Cash added (positive) or taken out (negative), in the base currency. The "
        "weights are then shares of the total after it; a position not listed keeps its value.",
    )


class ProposalError(ValueError):
    pass


@dataclass(frozen=True)
class Candidate:
    """What the universe knows of a security you may add."""

    ticker: str
    name: str | None
    isin: str | None
    asset_type: str | None
    country: str | None
    currency: str | None


def apply_proposal(
    portfolio: PortfolioIn,
    request: ProposedRequest,
    candidates: dict[str, Candidate],
) -> tuple[PortfolioIn, list[str]]:
    total = sum(p.value_base for p in portfolio.positions) + portfolio.cash_base + request.deposit
    if total <= 0:
        raise ProposalError("The portfolio has no value to reweight.")
    by_ticker = {p.ticker: p for p in portfolio.positions}
    notes: list[str] = []
    wanted: dict[str, float] = {}
    for w in request.weights:
        if w.ticker not in by_ticker:
            raise ProposalError(f"{w.ticker} is not a held position; add it under additions.")
        wanted[w.ticker] = w.weight
    positions: list[PositionIn] = []
    for p in portfolio.positions:
        value = wanted[p.ticker] * total if p.ticker in wanted else p.value_base
        if value > 0:
            positions.append(_with_value(p, value))
    added: dict[str, float] = {}
    for a in request.additions:
        if a.ticker in by_ticker:
            raise ProposalError(f"{a.ticker} is already held; edit its weight instead.")
        if a.ticker not in candidates:
            raise ProposalError(f"{a.ticker} is not in the investable universe.")
        added[a.ticker] = added.get(a.ticker, 0.0) + a.weight  # the same ticker twice adds up
    for ticker, weight in added.items():
        c = candidates[ticker]
        if weight > 0:
            positions.append(
                PositionIn(
                    ticker=c.ticker,
                    name=c.name,
                    isin=c.isin,
                    security_type=c.asset_type,
                    home_country=c.country,
                    currency=c.currency,
                    value_base=weight * total,
                )
            )
    invested = sum(p.value_base for p in positions)
    cash = total - invested
    if cash < -LOW * total:
        raise ProposalError(
            f"The weights add to {invested / total:.1%} of the portfolio; they cannot exceed 100%."
        )
    if cash < 0:
        cash = 0.0
    moved = f", after a deposit of {request.deposit:,.0f}" if request.deposit else ""
    notes.append(
        f"Total value is held at {total:,.0f} {portfolio.base_currency}{moved}; cash is the "
        f"remainder ({cash / total:.1%})."
    )
    return (
        PortfolioIn(
            snapshot_date=portfolio.snapshot_date,
            base_currency=portfolio.base_currency,
            account_currency=portfolio.account_currency,
            cash_base=cash,
            positions=positions,
        ),
        notes,
    )


def _with_value(p: PositionIn, value: float) -> PositionIn:
    return PositionIn(
        ticker=p.ticker,
        name=p.name,
        isin=p.isin,
        security_type=p.security_type,
        home_country=p.home_country,
        currency=p.currency,
        value_base=value,
        strategy_sector=p.strategy_sector,
    )


# ----------------------------------------------------------------------------- comparison


class XraySide(BaseModel):
    total_value: float
    cash_weight: float
    equity_weight: float
    unknown_weight: float
    not_looked_through_weight: float
    issuer_count: int
    shared_issuers: int
    redundant_weight: float = Field(
        description="Exposure to issuers reached through 2+ channels beyond the largest channel."
    )
    largest_issuer: str | None
    largest_issuer_weight: float | None
    hhi: float | None
    effective_issuers: float | None
    top10_weight: float | None
    fee_drag: float | None
    fee_cost: float | None
    funds_without_ratio: list[str]
    earnings_yield: float | None
    earnings_yield_coverage: float | None
    dividend_yield: float | None
    dividend_yield_coverage: float | None


class XrayPositionChange(BaseModel):
    ticker: str
    name: str | None
    kind: Literal["held", "added"]
    before: float
    after: float
    delta: float
    looked_through: bool | None = Field(
        description="For a fund: whether its holdings are stored. None for a stock."
    )


class XrayDeltaRow(BaseModel):
    key: str
    label: str
    kind: str
    before: float
    after: float
    delta: float


class XrayDeltaView(BaseModel):
    dimension: str
    label: str
    rows: list[XrayDeltaRow]
    reconciles_before: bool
    reconciles_after: bool


class XrayProposal(BaseModel):
    base_currency: str
    as_of: str
    saved_holdings_changed: bool = Field(
        default=False, description="Always false: the proposal is applied to a copy."
    )
    before: XraySide
    after: XraySide
    positions: list[XrayPositionChange]
    views: list[XrayDeltaView]
    issuers: list[XrayDeltaRow]
    coverage: list[str] = Field(description="Where the two sides differ in how much is known.")
    notes: list[str]


@dataclass
class Analysis:
    data: XrayData
    ctx: ViewContext
    concentration: list[XrayConcentration]
    fundamentals: XrayFundamentals

    @property
    def leafset(self):  # type: ignore[no-untyped-def]
        return self.ctx.leafset


def analyse(data: XrayData, facts: dict) -> Analysis:  # type: ignore[type-arg]
    leafset = build_leaves(data)
    ctx = ViewContext(data=data, leafset=leafset, facts=facts)
    return Analysis(
        data=data,
        ctx=ctx,
        concentration=concentration_levels(leafset),
        fundamentals=build_fundamentals(leafset, facts),
    )


def _issuer_totals(a: Analysis) -> tuple[dict[str, tuple[str, float]], dict[str, dict[str, float]]]:
    weights: dict[str, tuple[str, float]] = {}
    channels: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for x in a.leafset.leaves:
        if not x.is_equity:
            continue
        key = x.issuer_key or f"unidentified:{x.name}"
        label, w = weights.get(key, (x.issuer_label or x.name or key, 0.0))
        weights[key] = (label, w + x.weight)
        channels[key][x.channel_key] += x.weight
    return weights, channels


def side(a: Analysis) -> XraySide:
    ls = a.leafset
    weights, channels = _issuer_totals(a)
    shared = {k: ch for k, ch in channels.items() if len(ch) > 1}
    issuer_level = next((c for c in a.concentration if c.level == "issuer"), None)
    fees = a.fundamentals.fees
    metric = {m.id: m for m in a.fundamentals.metrics}
    largest = issuer_level.largest if issuer_level else None
    return XraySide(
        total_value=ls.total,
        cash_weight=sum(x.weight for x in ls.leaves if x.special == CASH),
        equity_weight=sum(x.weight for x in ls.leaves if x.is_equity),
        unknown_weight=unknown_weight(ls),
        not_looked_through_weight=sum(x.weight for x in ls.leaves if x.special == NOT_LOOKED),
        issuer_count=len(weights),
        shared_issuers=len(shared),
        redundant_weight=sum(sum(ch.values()) - max(ch.values()) for ch in shared.values()),
        largest_issuer=largest.label if largest else None,
        largest_issuer_weight=largest.weight if largest else None,
        hhi=issuer_level.hhi if issuer_level else None,
        effective_issuers=issuer_level.effective_n if issuer_level else None,
        top10_weight=issuer_level.top10_weight if issuer_level else None,
        fee_drag=fees.annual_drag if fees else None,
        fee_cost=fees.annual_cost_base if fees else None,
        funds_without_ratio=fees.funds_without_ratio if fees else [],
        earnings_yield=metric["earnings_yield"].value,
        earnings_yield_coverage=metric["earnings_yield"].coverage,
        dividend_yield=metric["dividend_yield"].value,
        dividend_yield_coverage=metric["dividend_yield"].coverage,
    )


def _delta_view(before: XrayView, after: XrayView, limit: int = 14) -> XrayDeltaView:
    b = {x.key: x for x in before.buckets}
    a = {x.key: x for x in after.buckets}
    rows = []
    for key in set(b) | set(a):
        src = a.get(key) or b[key]
        bw = b[key].weight if key in b else 0.0
        aw = a[key].weight if key in a else 0.0
        rows.append(
            XrayDeltaRow(
                key=key, label=src.label, kind=src.kind, before=bw, after=aw, delta=aw - bw
            )
        )
    rows.sort(key=lambda r: -max(r.before, r.after))
    keep = rows[:limit]
    rest = rows[limit:]
    if rest:
        bw = sum(r.before for r in rest)
        aw = sum(r.after for r in rest)
        keep.append(
            XrayDeltaRow(
                key="__rest__",
                label="Everything smaller",
                kind="other",
                before=bw,
                after=aw,
                delta=aw - bw,
            )
        )
    return XrayDeltaView(
        dimension=after.dimension,
        label=after.label,
        rows=keep,
        reconciles_before=before.reconciles,
        reconciles_after=after.reconciles,
    )


def compare(
    before: Analysis,
    after: Analysis,
    portfolio_before: PortfolioIn,
    portfolio_after: PortfolioIn,
    *,
    as_of: str,
    notes: list[str],
    held_funds_looked_through: dict[str, bool],
) -> XrayProposal:
    sb, sa = side(before), side(after)
    total_b = sb.total_value or 1.0
    total_a = sa.total_value or 1.0
    held_before = {p.ticker: p for p in portfolio_before.positions}
    held_after = {p.ticker: p for p in portfolio_after.positions}
    positions = []
    for ticker in sorted(set(held_before) | set(held_after)):
        pb, pa = held_before.get(ticker), held_after.get(ticker)
        src = pa or pb
        assert src is not None
        wb = pb.value_base / total_b if pb else 0.0
        wa = pa.value_base / total_a if pa else 0.0
        positions.append(
            XrayPositionChange(
                ticker=ticker,
                name=src.name,
                kind="held" if pb else "added",
                before=wb,
                after=wa,
                delta=wa - wb,
                looked_through=held_funds_looked_through.get(ticker),
            )
        )
    positions.sort(key=lambda p: -abs(p.delta))

    views = []
    for d in COMPARED:
        delta = _delta_view(
            build_view(d, before.ctx),  # type: ignore[arg-type]
            build_view(d, after.ctx),  # type: ignore[arg-type]
        )
        # A lens that rests on owner mappings or the provider profile says nothing when no
        # holding carries a label either side; leave it out rather than show a lone unknown bar.
        if d in OPTIONAL_LENSES and not any(r.kind == "classified" for r in delta.rows):
            continue
        views.append(delta)

    wb_i, _ = _issuer_totals(before)
    wa_i, _ = _issuer_totals(after)
    issuers = []
    for key in set(wb_i) | set(wa_i):
        label = (wa_i.get(key) or wb_i[key])[0]
        bw = wb_i[key][1] if key in wb_i else 0.0
        aw = wa_i[key][1] if key in wa_i else 0.0
        issuers.append(
            XrayDeltaRow(
                key=key, label=label, kind="classified", before=bw, after=aw, delta=aw - bw
            )
        )
    issuers.sort(key=lambda r: -abs(r.delta))

    coverage = []
    if abs(sa.unknown_weight - sb.unknown_weight) > 1e-6:
        coverage.append(
            f"Unknown fund weight goes from {sb.unknown_weight:.1%} to {sa.unknown_weight:.1%}."
        )
    if abs(sa.not_looked_through_weight - sb.not_looked_through_weight) > 1e-6:
        coverage.append(
            "Funds not looked through go from "
            f"{sb.not_looked_through_weight:.1%} to {sa.not_looked_through_weight:.1%}; "
            "their exposure is unknown, not zero."
        )
    for label, cb, ca in (
        ("Earnings yield", sb.earnings_yield_coverage, sa.earnings_yield_coverage),
        ("Dividend yield", sb.dividend_yield_coverage, sa.dividend_yield_coverage),
    ):
        if cb is not None and ca is not None and abs(ca - cb) > 0.005:
            coverage.append(f"{label} coverage of equity goes from {cb:.0%} to {ca:.0%}.")
    added_unknown = [p.ticker for p in positions if p.kind == "added" and p.looked_through is False]
    if added_unknown:
        coverage.append(
            f"{', '.join(added_unknown)} added as a fund without stored holdings: its exposure is "
            "unknown until its holdings are fetched."
        )
    if sa.funds_without_ratio:
        coverage.append(
            "Fee drag leaves out funds without a known ongoing charge: "
            + ", ".join(sa.funds_without_ratio)
            + "."
        )
    return XrayProposal(
        base_currency=portfolio_after.base_currency,
        as_of=as_of,
        before=sb,
        after=sa,
        positions=positions,
        views=views,
        issuers=issuers[:15],
        coverage=coverage,
        notes=[
            *notes,
            "This is a copy: your Trading212 holdings and snapshots are not changed.",
            "Proposed weights are applied at today's values and the proposal ignores prices "
            "moving, costs, taxes and the cash needed to trade.",
        ],
    )
