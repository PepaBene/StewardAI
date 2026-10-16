"""The figures every check reads, gathered once per company (pure data; no database here).

Every field is optional: a missing input makes the checks that need it `unknown` with the
reason, never a guessed value. `load.py` fills this from the stored read models; tests build it
directly.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from steward.analytics.versions import version
from steward.checks.contract import Axis, Check, State
from steward.checks.sentences import fmt, label, sentence

D = Decimal


@dataclass
class RatioFacts:
    """One ratio as the Ratios tab shows it."""

    latest: Decimal | None = None
    reason: str | None = None
    own_5y: Decimal | None = None
    peer_median: Decimal | None = None
    peer_count: int = 0
    peer_reason: str | None = None
    peer_scope: str | None = None
    series: list[tuple[int, Decimal | None]] = field(default_factory=list)  # fiscal year, value

    def peer_basis(self) -> str:
        return f"{self.peer_scope or 'peers'}, {self.peer_count} peers"


@dataclass
class CheckInputs:
    ticker: str
    name: str | None = None
    currency: str | None = None  # statement currency
    price: Decimal | None = None  # in the statement currency, else None
    price_note: str | None = None
    is_financial: bool = False
    sector: str | None = None
    fiscal_year: int | None = None
    ratios: dict[str, RatioFacts] = field(default_factory=dict)
    fig: dict[str, Decimal | None] = field(default_factory=dict)  # latest fiscal year
    fig_prev: dict[str, Decimal | None] = field(default_factory=dict)
    series: dict[str, list[tuple[int, Decimal | None]]] = field(default_factory=dict)
    debt: Decimal | None = None
    debt_prev_5y: Decimal | None = None
    # valuation
    risk_free: Decimal = field(default_factory=lambda: D("0.04"))
    dcf_base: Decimal | None = None
    dcf_reason: str | None = None
    owner_value: Decimal | None = None
    owner_basis: str | None = None
    owner_reason: str | None = None
    # consensus and estimates
    consensus_target: Decimal | None = None
    consensus_basis: str | None = None
    consensus_reason: str | None = "no consensus snapshot stored"
    analyst_count: int | None = None
    eps_next: Decimal | None = None
    eps_trailing: Decimal | None = None
    revenue_next: Decimal | None = None
    revenue_trailing: Decimal | None = None
    estimates_reason: str | None = "no stored analyst estimates"
    peer_eps_growth: Decimal | None = None
    peer_revenue_growth: Decimal | None = None
    peer_forecast_count: int = 0
    # dividends
    payer: bool | None = None
    dps_by_year: list[tuple[int, Decimal]] = field(default_factory=list)
    years_without_cut: int | None = None
    # ownership and market
    insider_has_filings: bool = False
    insider_net_sold_value: Decimal | None = None
    insider_sellers: int = 0
    market_cap: Decimal | None = None
    market_cap_currency: str | None = None
    avg_turnover: Decimal | None = None
    turnover_basis: str | None = None
    volatility_1y: Decimal | None = None
    years_listed: Decimal | None = None
    cash_runway_years: Decimal | None = None
    cash_runway_reason: str | None = None
    amendments_12m: int | None = None
    amendment_basis: str | None = None
    next_report: dt.date | None = None
    today: dt.date = field(default_factory=dt.date.today)
    debt_due_1y: Decimal | None = None
    customer_max_share: Decimal | None = None
    customer_known: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def r(self, rid: str) -> RatioFacts:
        return self.ratios.get(rid) or RatioFacts(reason="ratio not computed")


def mk(
    check_id: str,
    axis: Axis,
    state: State,
    *,
    value: Decimal | None = None,
    threshold: Decimal | None = None,
    unit: str | None = None,
    v: str | None = None,
    t: str | None = None,
    basis: str = "",
    metric: str = "",
    reason: str = "",
    inputs: dict[str, Any] | None = None,
    section: str | None = None,
    informational: bool = False,
) -> Check:
    vt = v if v is not None else fmt(value, unit)
    tt = t if t is not None else fmt(threshold, unit)
    return Check(
        id=check_id,
        axis=axis,
        label=label(check_id),
        state=state,
        value=value,
        threshold=threshold,
        unit=unit,
        basis=basis or reason or None,
        sentence=sentence(check_id, state, v=vt, t=tt, basis=basis, metric=metric, reason=reason),
        inputs={k: (str(x) if isinstance(x, Decimal) else x) for k, x in (inputs or {}).items()},
        section_ref=section,
        method_version=version("checks"),
        informational=informational,
    )


def compare(
    check_id: str,
    axis: Axis,
    value: Decimal | None,
    threshold: Decimal | None,
    *,
    good: str,
    unit: str | None,
    missing: str,
    basis: str = "",
    metric: str = "",
    inputs: dict[str, Any] | None = None,
    section: str | None = None,
) -> Check:
    """pass when value `good` threshold (one of '>=', '>', '<=', '<'); unknown when either is
    missing, with `missing` as the reason."""
    if value is None or threshold is None:
        return mk(check_id, axis, "unknown", reason=missing, inputs=inputs, section=section)
    ok = {
        ">=": value >= threshold,
        ">": value > threshold,
        "<=": value <= threshold,
        "<": value < threshold,
    }[good]
    return mk(
        check_id, axis, "pass" if ok else "fail", value=value, threshold=threshold, unit=unit,
        basis=basis, metric=metric, inputs=inputs, section=section,
    )  # fmt: skip


def cagr(series: list[tuple[int, Decimal | None]], years: int) -> tuple[Decimal | None, str]:
    """Compound growth over the last `years` fiscal years; (None, reason) when not computable."""
    pts = [(y, v) for y, v in series if v is not None]
    if len(pts) < 2:
        return None, "fewer than two fiscal years"
    last_y, last_v = pts[-1]
    first = next(((y, v) for y, v in pts if y >= last_y - years), None)
    if first is None or first[0] == last_y:
        return None, f"no value {years} years back"
    y0, v0 = first
    n = last_y - y0
    if v0 is None or last_v is None or v0 <= 0 or last_v <= 0:
        return None, "n/m: a non-positive start or end value"
    return D(str(float(last_v / v0) ** (1 / n) - 1)), f"{y0}-{last_y}"
