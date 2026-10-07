"""Compare two to five funds (plan M3.5): costs, holdings overlap, geography and sector,
concentration, return and look-through completeness.

Overlap is `sum(min(weight_A, weight_B))` over canonical issuers on each fund's own holdings
date (the shared analytics rule) and is also listed holding by holding. Two share classes of the
same portfolio are one portfolio: they are shown, but never compared with each other or added.
"""

# ruff: noqa: E501
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel

from steward.analytics.contract import AnalyticsResult
from steward.analytics.exposure import fund_overlap
from steward.fundresearch.analysis import (
    FUND_METHOD,
    FundBucket,
    HoldingRow,
    HoldingsView,
    StyleView,
)

ZERO = Decimal(0)
TOP_SHARED = 25
STALE_DAYS = 45


class ReturnCell(BaseModel):
    label: str
    value: Decimal | None
    reason: str | None = None


class CompareFund(BaseModel):
    isin: str
    name: str | None
    provider: str | None
    currency: str | None
    distribution_policy: str | None
    expense_ratio: Decimal | None
    total_assets: Decimal | None
    total_assets_currency: str | None
    holdings_as_of: dt.date | None
    holdings_lag_days: int | None
    stale: bool
    portfolio_id: str | None
    duplicate_of: str | None
    equity_weight: Decimal | None
    residual_weight: Decimal | None
    unidentified_equity_weight: Decimal | None
    issuers: int | None
    top10_weight: Decimal | None
    effective_issuers: Decimal | None
    returns: list[ReturnCell]


class SharedHolding(BaseModel):
    issuer: str
    weight_a: Decimal
    weight_b: Decimal
    overlap: Decimal


class PairOverlap(BaseModel):
    fund_a: str
    fund_b: str
    overlap: AnalyticsResult
    shared_issuers: int
    shared: list[SharedHolding]
    skipped: str | None = None


class CompareExposureRow(BaseModel):
    label: str
    weights: dict[str, Decimal | None]
    kind: str = "known"


class FundComparison(BaseModel):
    funds: list[CompareFund]
    pairs: list[PairOverlap]
    sectors: list[CompareExposureRow]
    countries: list[CompareExposureRow]
    warnings: list[str]
    method_version: str = FUND_METHOD


@dataclass(frozen=True)
class Prepared:
    fund: CompareFund
    rows: Sequence[HoldingRow]
    view: HoldingsView
    style: StyleView


def _exposure(prepared: Sequence[Prepared], attr: str) -> list[CompareExposureRow]:
    labels: dict[str, str] = {}
    table: dict[str, dict[str, Decimal | None]] = {}
    for p in prepared:
        buckets: list[FundBucket] = getattr(p.view, attr)
        for b in buckets:
            labels.setdefault(b.label, b.kind)
            table.setdefault(b.label, {})[p.fund.isin] = b.weight
    rows = [
        CompareExposureRow(
            label=label,
            kind=labels[label],
            weights={
                p.fund.isin: table[label].get(p.fund.isin) if p.view.row_count else None
                for p in prepared
            },
        )
        for label in table
    ]
    # A fund with no holdings is unknown (None), a fund with holdings but no row is 0 weight there.
    for r in rows:
        for p in prepared:
            if p.view.row_count and r.weights[p.fund.isin] is None:
                r.weights[p.fund.isin] = ZERO
    rows.sort(
        key=lambda r: (
            r.kind != "known",
            -max((w for w in r.weights.values() if w is not None), default=ZERO),
        )
    )
    return rows[:25]


def _issuer_weights(rows: Sequence[HoldingRow]) -> tuple[dict[str, Decimal], dict[str, str]]:
    w: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    for r in rows:
        if r.asset_class == "equity" and r.issuer_key:
            w[r.issuer_key] = w.get(r.issuer_key, ZERO) + r.weight
            names.setdefault(r.issuer_key, r.issuer_label or r.issuer_key)
    return w, names


def build_comparison(prepared: Sequence[Prepared]) -> FundComparison:
    warnings: list[str] = []
    seen: dict[tuple[str | None, str], str] = {}
    funds: list[CompareFund] = []
    for p in prepared:
        f = p.fund
        key = (f.provider, f.portfolio_id) if f.portfolio_id else None
        if key and key in seen:
            f = f.model_copy(update={"duplicate_of": seen[key]})
            warnings.append(
                f"{f.isin} is another share class of the same portfolio as {seen[key]}; it is shown but not compared with it or added to it."
            )
        elif key:
            seen[key] = f.isin
        funds.append(f)
    funds_by_isin = {f.isin: f for f in funds}
    for f in funds:
        if f.holdings_as_of is None:
            warnings.append(
                f"{f.isin} has no holdings snapshot: its overlap, geography and concentration are unknown, not zero."
            )
        elif f.stale:
            warnings.append(f"{f.isin} holdings are {f.holdings_lag_days} days old.")
    pairs: list[PairOverlap] = []
    for i, a in enumerate(prepared):
        for b in prepared[i + 1 :]:
            fa, fb = funds_by_isin[a.fund.isin], funds_by_isin[b.fund.isin]
            if (
                fa.duplicate_of == fb.isin
                or fb.duplicate_of == fa.isin
                or (fa.duplicate_of and fa.duplicate_of == fb.duplicate_of)
            ):
                pairs.append(
                    PairOverlap(
                        fund_a=fa.isin,
                        fund_b=fb.isin,
                        overlap=fund_overlap(None, None),
                        shared_issuers=0,
                        shared=[],
                        skipped="same portfolio, different share class",
                    )
                )
                continue
            wa, na = _issuer_weights(a.rows) if a.view.row_count else ({}, {})
            wb, nb = _issuer_weights(b.rows) if b.view.row_count else ({}, {})
            res = fund_overlap(
                wa or None,
                wb or None,
                as_of_a=a.view.as_of,
                as_of_b=b.view.as_of,
                stale_after_days=STALE_DAYS,
            )
            shared_keys = set(wa) & set(wb)
            shared = sorted(
                (
                    SharedHolding(
                        issuer=na.get(k) or nb.get(k) or k,
                        weight_a=wa[k],
                        weight_b=wb[k],
                        overlap=min(wa[k], wb[k]),
                    )
                    for k in shared_keys
                ),
                key=lambda s: -s.overlap,
            )
            pairs.append(
                PairOverlap(
                    fund_a=fa.isin,
                    fund_b=fb.isin,
                    overlap=res,
                    shared_issuers=len(shared_keys),
                    shared=shared[:TOP_SHARED],
                )
            )
    pairs.sort(key=lambda p: -(p.overlap.value or -1))
    return FundComparison(
        funds=funds,
        pairs=pairs,
        sectors=_exposure(prepared, "sectors"),
        countries=_exposure(prepared, "countries"),
        warnings=warnings,
    )
