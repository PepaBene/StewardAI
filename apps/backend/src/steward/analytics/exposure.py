"""Fund look-through, two-fund overlap, concentration and portfolio earnings yield
(methodology.md sections 6-9). Weights are fractions; unknown weight is kept, never spread.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from steward.analytics.contract import (
    AnalyticsResult,
    Coverage,
    Exclusion,
    Explain,
    Status,
    result,
    unavailable,
)
from steward.analytics.versions import version

ONE = Decimal(1)
ZERO = Decimal(0)


@dataclass(frozen=True)
class FundPosition:
    fund_id: str
    market_value: Decimal
    as_of: dt.date | None
    # Canonical issuer -> weight (fraction of the fund's net assets). None: holdings unavailable.
    weights: Mapping[str, Decimal] | None
    cash_weight: Decimal = ZERO
    derivative_weight: Decimal = ZERO


@dataclass
class IssuerExposure:
    issuer: str
    direct_value: Decimal = ZERO
    paths: dict[str, Decimal] = field(default_factory=dict)  # fund id -> look-through value

    @property
    def total_value(self) -> Decimal:
        return self.direct_value + sum(self.paths.values(), ZERO)


@dataclass(frozen=True)
class LookThrough:
    exposures: dict[str, IssuerExposure]
    cash_value: Decimal
    derivative_value: Decimal
    unknown_value: Decimal  # fund value the published rows do not account for
    unavailable_funds: list[str]  # funds with no holdings at all: their whole value is unknown
    total_value: Decimal
    status: Status
    warnings: list[str]
    method_version: str

    def weight(self, issuer: str) -> Decimal | None:
        if self.total_value <= 0 or issuer not in self.exposures:
            return None
        return self.exposures[issuer].total_value / self.total_value


def look_through(direct: Mapping[str, Decimal], funds: Sequence[FundPosition]) -> LookThrough:
    """Direct holding -> its market value. Fund holding -> `fund value x underlying weight`.

    Aggregates by canonical issuer and keeps every path. Cash, derivatives and the weight the
    rows do not cover stay in their own buckets: unknown weight is never redistributed over the
    known issuers. A fund with no holdings is wholly unknown, not zero overlap.
    """
    exposures: dict[str, IssuerExposure] = {}
    for issuer, value in direct.items():
        exposures.setdefault(issuer, IssuerExposure(issuer)).direct_value += value
    cash = deriv = unknown = ZERO
    missing: list[str] = []
    warnings: list[str] = []
    total = sum(direct.values(), ZERO) + sum((f.market_value for f in funds), ZERO)
    for fund in funds:
        if fund.weights is None:
            unknown += fund.market_value
            missing.append(fund.fund_id)
            warnings.append(f"{fund.fund_id}: holdings unavailable; its whole value is unknown")
            continue
        for issuer, weight in fund.weights.items():
            exposure = exposures.setdefault(issuer, IssuerExposure(issuer))
            exposure.paths[fund.fund_id] = exposure.paths.get(fund.fund_id, ZERO) + (
                fund.market_value * weight
            )
        cash += fund.market_value * fund.cash_weight
        deriv += fund.market_value * fund.derivative_weight
        covered = sum(fund.weights.values(), ZERO) + fund.cash_weight + fund.derivative_weight
        if covered > ONE:
            warnings.append(f"{fund.fund_id}: published weights sum to {covered:.4f}, above 100%")
        else:
            unknown += fund.market_value * (ONE - covered)
    return LookThrough(
        exposures=exposures,
        cash_value=cash,
        derivative_value=deriv,
        unknown_value=unknown,
        unavailable_funds=missing,
        total_value=total,
        status=Status.PARTIAL if (warnings or unknown > 0) else Status.COMPLETE,
        warnings=warnings,
        method_version=version("look_through"),
    )


def fund_overlap(
    a: Mapping[str, Decimal] | None,
    b: Mapping[str, Decimal] | None,
    *,
    as_of_a: dt.date | None = None,
    as_of_b: dt.date | None = None,
    stale_after_days: int = 45,
) -> AnalyticsResult:
    """`sum(min(w_A,i, w_B,i))` over canonical issuers, on aligned dates.

    Reports each fund's covered fraction (sum of its issuer weights) so the reader sees how
    much of each fund the number rests on. If either fund has no holdings the answer is
    `unavailable`, never zero overlap.
    """
    method = version("fund_overlap")
    inputs = {"a": a, "b": b, "as_of": (as_of_a, as_of_b)}
    if a is None or b is None:
        return unavailable("fraction", method, "holdings unavailable for one fund", inputs=inputs)
    shared = set(a) & set(b)
    value = sum((min(a[k], b[k]) for k in shared), ZERO)
    covered_a = sum(a.values(), ZERO)
    covered_b = sum(b.values(), ZERO)
    warnings: list[str] = []
    if as_of_a and as_of_b:
        if as_of_a != as_of_b:
            warnings.append(f"holdings dates differ: {as_of_a} vs {as_of_b}")
        newest = max(as_of_a, as_of_b)
        for label, day in (("A", as_of_a), ("B", as_of_b)):
            if (newest - day).days > stale_after_days:
                warnings.append(f"fund {label} holdings are stale ({day})")
    else:
        warnings.append("holdings date missing for at least one fund")
    if covered_a < Decimal("0.9") or covered_b < Decimal("0.9"):
        warnings.append(
            f"covered fund weight is {covered_a:.1%} and {covered_b:.1%}; the rest is unknown"
        )
    return result(
        value,
        "fraction",
        method,
        as_of=max(d for d in (as_of_a, as_of_b) if d) if (as_of_a or as_of_b) else None,
        coverage=Coverage(numerator=min(covered_a, covered_b), denominator=ONE, basis="weight"),
        warnings=warnings,
        inputs=inputs,
        explain=Explain(
            formula_version=method,
            formula="sum over shared canonical issuers of min(weight_A, weight_B)",
            exclusions=[],
            gaps=[],
        ),
    )


@dataclass(frozen=True)
class Concentration:
    hhi: Decimal | None
    effective_count: Decimal | None
    top10_weight: Decimal | None
    unknown_weight: Decimal
    known_weight: Decimal
    warnings: list[str]
    method_version: str


def concentration(
    weights: Mapping[str, Decimal],
    *,
    unknown_weight: Decimal = ZERO,
    as_of_dates: Sequence[dt.date] = (),
) -> Concentration:
    """`HHI = sum(w_i^2)` and effective count `1 / HHI` on weights of one portfolio value date.

    `weights` are fractions of total portfolio value (unknown is a separate bucket, outside the
    HHI and shown beside it). Mismatched as-of dates warn rather than mix silently.
    """
    warnings: list[str] = []
    if len(set(as_of_dates)) > 1:
        warnings.append("weights come from different as-of dates")
    known = sum(weights.values(), ZERO)
    if known <= 0:
        return Concentration(
            None, None, None, unknown_weight, known, warnings, version("concentration")
        )
    hhi = sum((w * w for w in weights.values()), ZERO)
    top10 = sum(sorted(weights.values(), reverse=True)[:10], ZERO)
    if unknown_weight > 0:
        warnings.append(f"{unknown_weight:.1%} of the portfolio is unknown and outside the HHI")
    return Concentration(
        hhi, ONE / hhi, top10, unknown_weight, known, warnings, version("concentration")
    )


@dataclass(frozen=True)
class YieldInput:
    key: str
    weight: Decimal
    earnings_yield: Decimal | None  # None: missing; <= 0: negative earnings
    excluded_reason: str | None = None  # cash, derivative, bank mixed with industrials, ...


def portfolio_earnings_yield(
    items: Sequence[YieldInput], *, includes_look_through: bool
) -> AnalyticsResult:
    """`sum(covered weight x earnings yield) / covered weight`, then P/E = 1 / yield.

    Only valid, positive-earnings equities enter. The aggregate P/E is shown only when the
    aggregate yield is positive. Everything left out is listed with its reason and the covered
    weight is the coverage; nothing is forced into a number.
    """
    method = version("portfolio_earnings_yield")
    total = sum((i.weight for i in items), ZERO)
    covered = ZERO
    weighted = ZERO
    exclusions: list[Exclusion] = []
    for item in items:
        if item.excluded_reason:
            exclusions.append(Exclusion(subject=item.key, reason=item.excluded_reason))
        elif item.earnings_yield is None:
            exclusions.append(Exclusion(subject=item.key, reason="earnings missing"))
        elif item.earnings_yield <= 0:
            exclusions.append(Exclusion(subject=item.key, reason="negative or zero earnings"))
        else:
            covered += item.weight
            weighted += item.weight * item.earnings_yield
    scope = "with fund look-through" if includes_look_through else "direct holdings only"
    explain = Explain(
        formula_version=method,
        formula=f"sum(w_i x E/P_i) / sum(w_i) over covered positive-earnings equities ({scope})",
        exclusions=exclusions,
    )
    inputs = {"items": list(items), "look_through": includes_look_through}
    if covered <= 0 or total <= 0:
        out = unavailable("ratio", method, "no position with positive earnings", inputs=inputs)
        out.explain = explain
        return out
    yield_ = weighted / covered
    return result(
        yield_,
        "ratio",
        method,
        coverage=Coverage(numerator=covered, denominator=total, basis="weight"),
        inputs=inputs,
        explain=explain,
    )


def pe_from_yield(earnings_yield: Decimal | None) -> Decimal | None:
    """Invert only a positive, meaningful aggregate yield; otherwise no P/E."""
    if earnings_yield is None or earnings_yield <= 0:
        return None
    return ONE / earnings_yield
