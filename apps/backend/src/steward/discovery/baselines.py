"""Market and sector peer baselines from *covered* securities (plan M1.4).

A baseline states its count and geographic scope. Stale or incomparable periods are excluded
with the reason; a median is withheld below the minimum comparable-peer count
(`thresholds.peer_median_allowed`). A sector label alone never defines the group: the owner
picks home-market peers, global peers or a saved peer set.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from steward.analytics.thresholds import THRESHOLDS, peer_median_allowed
from steward.discovery.engine import Record
from steward.discovery.metrics import MetricDef

STALE_AFTER_DAYS = 548  # 18 months: a period ending earlier than this is not current
ANNUAL_FILED = {
    "revenue_growth",
    "gross_margin",
    "operating_margin",
    "roic",
    "net_debt_to_equity",
    "free_cash_flow_conversion",
    "gross_profit_to_assets",
    "accruals_ratio",
    "earnings_yield",
    "fcf_yield",
    "ev_ebit",
    "price_to_book",
    "pe_ratio",
    "asset_growth",
    "share_change_1y",
    "share_change_3y",
}

Scope = Literal["home_market", "global", "saved_set"]


class Excluded(BaseModel):
    key: str
    reason: str


class Baseline(BaseModel):
    metric: str
    label: str
    scope: Scope
    scope_detail: str
    members: int
    comparable: int
    median: Decimal | None
    p25: Decimal | None
    p75: Decimal | None
    low: Decimal | None
    high: Decimal | None
    median_withheld_reason: str | None
    countries: dict[str, int]
    excluded: list[Excluded]


def quantile(sorted_values: Sequence[Decimal], q: Decimal) -> Decimal:
    """Linear interpolation between order statistics (type 7)."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    pos = (n - 1) * q
    lo = int(pos)
    frac = pos - lo
    hi = min(lo + 1, n - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def pick_group(
    records: Sequence[Record],
    scope: Scope,
    *,
    home_country: str | None = None,
    saved_tickers: Sequence[str] = (),
) -> tuple[list[Record], str]:
    if scope == "home_market":
        if not home_country:
            raise ValueError("home_market scope needs a country")
        return [
            r for r in records if (r.identity.get("country") or "").upper() == home_country.upper()
        ], (f"issuers with country {home_country.upper()} in the covered universe")
    if scope == "saved_set":
        wanted = {t.upper() for t in saved_tickers}
        return [
            r for r in records if r.key.upper() in wanted
        ], f"saved peer set of {len(wanted)} tickers"
    return list(records), "all covered issuers, every market"


def baseline(
    group: Sequence[Record],
    metric: MetricDef,
    *,
    scope: Scope,
    scope_detail: str,
    as_of: dt.date,
    basis: str | None = None,
) -> Baseline:
    """Baseline of one metric. `basis` pins an accounting basis (US_GAAP, IFRS); statements of
    another basis are excluded rather than mixed."""
    included: list[tuple[str, Decimal]] = []
    excluded: list[Excluded] = []
    countries: dict[str, int] = {}
    for r in group:
        v = r.values.get(metric.id)
        if v is None or not isinstance(v.value, Decimal):
            excluded.append(
                Excluded(key=r.key, reason=(v.reason if v and v.reason else "no value"))
            )
            continue
        if metric.id in ANNUAL_FILED:
            if v.period_end is not None and (as_of - v.period_end).days > STALE_AFTER_DAYS:
                excluded.append(Excluded(key=r.key, reason=f"stale: period ended {v.period_end}"))
                continue
            if basis and v.basis and v.basis != basis:
                excluded.append(
                    Excluded(key=r.key, reason=f"accounting basis {v.basis} differs from {basis}")
                )
                continue
        included.append((r.key, v.value))
        c = r.identity.get("country") or "unknown"
        countries[c] = countries.get(c, 0) + 1
    values = sorted(v for _, v in included)
    ok, why = peer_median_allowed(len(values))
    return Baseline(
        metric=metric.id,
        label=metric.label,
        scope=scope,
        scope_detail=scope_detail,
        members=len(group),
        comparable=len(values),
        median=quantile(values, Decimal("0.5")) if ok else None,
        p25=quantile(values, Decimal("0.25")) if ok else None,
        p75=quantile(values, Decimal("0.75")) if ok else None,
        low=values[0] if values else None,
        high=values[-1] if values else None,
        median_withheld_reason=None if ok else why,
        countries=dict(sorted(countries.items(), key=lambda kv: (-kv[1], kv[0]))),
        excluded=excluded,
    )


def baselines(
    records: Sequence[Record],
    metrics: Sequence[MetricDef],
    *,
    scope: Scope,
    as_of: dt.date,
    home_country: str | None = None,
    saved_tickers: Sequence[str] = (),
    basis: str | None = None,
) -> list[Baseline]:
    group, detail = pick_group(
        records, scope, home_country=home_country, saved_tickers=saved_tickers
    )
    return [
        baseline(group, m, scope=scope, scope_detail=detail, as_of=as_of, basis=basis)
        for m in metrics
        if m.kind == "numeric"
    ]


def minimum_peers() -> int:
    return THRESHOLDS.min_peers_for_median


def baseline_lookup(items: Sequence[Baseline]) -> Mapping[str, Baseline]:
    return {b.metric: b for b in items}
