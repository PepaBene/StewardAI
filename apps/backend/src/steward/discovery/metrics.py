# ruff: noqa: E501 - one catalogue entry per line keeps definitions readable
"""Metric catalogue for stock and fund screens (plan M1.2, M1.3).

Every metric has one published definition (`definition_version`, formula, period rule). A
filter names the definition and period it was written against; it is applied only when both
match the catalogue's, so a saved screen never silently changes meaning. A metric whose source
is not available (no data at all, or a provider endpoint the free plan denies) is *disabled*
with the reason, not applied as an empty filter.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from steward.analytics.ratios import RATIOS
from steward.peer_metrics import FORMULAS

Kind = Literal["numeric", "category"]
Unit = Literal["fraction", "multiple", "money", "years", "count", "text", "ratio"]


@dataclass(frozen=True)
class MetricDef:
    id: str
    label: str
    group: str
    kind: Kind
    unit: Unit
    formula: str
    period: str = "latest_annual"
    definition_version: str = "1"
    higher_is_better: bool | None = None
    # (provider, endpoint) the metric needs; its provider_access state must be 'available'.
    requires_access: tuple[str, str] | None = None
    # Fixed text for a metric no free source can currently fill (disabled in the UI).
    no_source_reason: str | None = None
    # An optional provider whose key unlocks more of this metric's data. Set by hand where a
    # provider fills the metric without being a hard gate (`requires_access` is the hard gate).
    optional_provider: str | None = None

    @property
    def needs_provider(self) -> str | None:
        """The provider a user must connect for this metric, or None when it works key-free."""
        if self.requires_access is not None:
            return self.requires_access[0]
        return self.optional_provider


_RATIO_PERIOD = {
    "momentum_12_1": "12-1m",
    "share_change_1y": "1y",
    "share_change_3y": "3y",
    "cash_runway_years": "ttm",
}


def _ratio(rid: str, group: str | None = None) -> MetricDef:
    r = RATIOS[rid]
    return MetricDef(
        rid,
        r.label,
        group or r.group,
        "numeric",
        r.unit,  # type: ignore[arg-type]
        r.formula,
        period=_RATIO_PERIOD.get(rid, "latest_annual"),
        higher_is_better=r.higher_is_better,
    )


def _filed(mid: str, label: str, unit: Unit, better: bool | None) -> MetricDef:
    return MetricDef(
        mid, label, "financials", "numeric", unit, FORMULAS[mid], higher_is_better=better
    )


_STOCK: tuple[MetricDef, ...] = (
    MetricDef(
        "country",
        "Country",
        "identity",
        "category",
        "text",
        "issuer country, else listing home country",
        period="n/a",
    ),
    MetricDef(
        "mic",
        "Exchange (MIC)",
        "identity",
        "category",
        "text",
        "market identifier code of the listing",
        period="n/a",
    ),
    MetricDef(
        "currency",
        "Trading currency",
        "identity",
        "category",
        "text",
        "quote currency of the listing",
        period="n/a",
    ),
    MetricDef(
        "asset_type",
        "Asset type",
        "identity",
        "category",
        "text",
        "security kind from identity resolution",
        period="n/a",
    ),
    MetricDef(
        "sector",
        "Sector",
        "identity",
        "category",
        "text",
        "provider profile sector (FMP), where fetched",
        period="n/a",
        optional_provider="fmp",
    ),
    MetricDef(
        "industry",
        "Industry",
        "identity",
        "category",
        "text",
        "provider profile industry (FMP), where fetched",
        period="n/a",
        optional_provider="fmp",
    ),
    MetricDef(
        "market_cap",
        "Market capitalisation",
        "size",
        "numeric",
        "money",
        "latest close x latest annual diluted weighted-average share count, only when the price currency equals the statement currency",
        period="price_date",
    ),
    MetricDef(
        "avg_daily_turnover",
        "Average daily turnover",
        "liquidity",
        "numeric",
        "money",
        "FIRDS/FITRS average daily turnover of the most relevant market",
        period="published_period",
    ),
    MetricDef(
        "dividend_yield",
        "Dividend yield",
        "income",
        "numeric",
        "fraction",
        "cash dividends with ex-date in the last 12 months / latest close; same currency only",
        period="ttm",
        higher_is_better=True,
    ),
    MetricDef(
        "return_1y",
        "1-year return",
        "performance",
        "numeric",
        "fraction",
        "adjusted close / adjusted close 365 days earlier - 1",
        period="1y",
        higher_is_better=True,
    ),
    MetricDef(
        "return_3y",
        "3-year return",
        "performance",
        "numeric",
        "fraction",
        "adjusted close / adjusted close 3 years earlier - 1 (cumulative)",
        period="3y",
        higher_is_better=True,
    ),
    MetricDef(
        "return_5y",
        "5-year return",
        "performance",
        "numeric",
        "fraction",
        "adjusted close / adjusted close 5 years earlier - 1 (cumulative)",
        period="5y",
        higher_is_better=True,
    ),
    MetricDef(
        "volatility_1y",
        "1-year volatility",
        "risk",
        "numeric",
        "fraction",
        "stdev of daily log returns over 252 sessions x sqrt(252)",
        period="1y",
        higher_is_better=False,
    ),
    _filed("revenue_growth", "Revenue growth", "fraction", True),
    _filed("gross_margin", "Gross margin", "fraction", True),
    _filed("operating_margin", "Operating margin", "fraction", True),
    _filed("roic", "Return on invested capital", "fraction", True),
    _filed("net_debt_to_equity", "Net debt to equity", "multiple", False),
    _filed("free_cash_flow_conversion", "Free cash flow conversion", "fraction", True),
    _ratio("earnings_yield"),
    MetricDef(
        "pe_ratio",
        "P/E",
        "value",
        "numeric",
        "multiple",
        "1 / earnings yield, only when earnings are positive",
        higher_is_better=False,
    ),
    _ratio("fcf_yield"),
    _ratio("ev_ebit"),
    MetricDef(
        "price_to_book",
        "P/B",
        "value",
        "numeric",
        "multiple",
        "market cap / shareholders' equity (equity > 0)",
        higher_is_better=False,
    ),
    _ratio("gross_profit_to_assets"),
    _ratio("momentum_12_1"),
    _ratio("share_change_1y"),
    _ratio("share_change_3y"),
    _ratio("asset_growth"),
    _ratio("accruals_ratio"),
    _ratio("cash_runway_years"),
    MetricDef(
        "years_since_listing",
        "Years since first trade",
        "red_flag",
        "numeric",
        "years",
        "(as-of date - first trade date of the listing) / 365",
        period="as_of",
    ),
    MetricDef(
        "insider_buyers_90d",
        "Insiders buying (90 days)",
        "signal",
        "numeric",
        "count",
        "distinct reporting insiders with open-market, non-planned, non-derivative buys in 90 days",
        period="90d",
    ),
    MetricDef(
        "forward_pe",
        "Forward P/E",
        "estimates",
        "numeric",
        "multiple",
        "latest close / published analyst-consensus EPS for the next fiscal year",
        period="next_fy",
        requires_access=("fmp", "analyst_estimates"),
        higher_is_better=False,
    ),
    MetricDef(
        "qvm_score",
        "Quality-value-momentum score",
        "composite",
        "numeric",
        "ratio",
        "weighted percentile ranks (default quality 40 %, value 40 %, momentum 20 %); see composite_qvm",
        period="as_of",
        higher_is_better=True,
    ),
    MetricDef(
        "data_coverage",
        "Data coverage",
        "coverage",
        "numeric",
        "fraction",
        "share of the numeric screen metrics that have a value for this issuer",
        period="as_of",
    ),
)

_FUND: tuple[MetricDef, ...] = (
    MetricDef(
        "asset_class",
        "Asset class",
        "identity",
        "category",
        "text",
        "inferred from published holdings: equity if equity weight >= 85 %, else mixed/other",
        period="holdings_date",
    ),
    MetricDef(
        "domicile",
        "Domicile (ISIN country)",
        "identity",
        "category",
        "text",
        "country prefix of the fund ISIN, usually the fund's domicile",
        period="n/a",
    ),
    MetricDef(
        "distribution_policy",
        "Distribution policy",
        "identity",
        "category",
        "text",
        "accumulating or distributing, as published in the factsheet",
        period="factsheet_date",
    ),
    MetricDef(
        "benchmark_name",
        "Benchmark",
        "identity",
        "category",
        "text",
        "benchmark named in the factsheet",
        period="factsheet_date",
    ),
    MetricDef(
        "fund_currency",
        "Fund currency",
        "identity",
        "category",
        "text",
        "fund base currency from the factsheet",
        period="factsheet_date",
    ),
    MetricDef(
        "expense_ratio",
        "Ongoing charge / expense ratio",
        "costs",
        "numeric",
        "fraction",
        "annual expense ratio as published; unknown is never zero",
        period="factsheet_date",
        higher_is_better=False,
    ),
    MetricDef(
        "total_assets",
        "Fund size",
        "size",
        "numeric",
        "money",
        "total net assets from the factsheet, in the stated currency",
        period="factsheet_date",
    ),
    MetricDef(
        "holdings_age_days",
        "Holdings age (days)",
        "freshness",
        "numeric",
        "count",
        "as-of date - holdings date",
        period="as_of",
        higher_is_better=False,
    ),
    MetricDef(
        "holdings_count",
        "Number of holdings",
        "holdings",
        "numeric",
        "count",
        "rows in the published holdings",
        period="holdings_date",
    ),
    MetricDef(
        "top10_weight",
        "Top-10 weight",
        "concentration",
        "numeric",
        "fraction",
        "sum of the ten largest issuer weights / covered weight",
        period="holdings_date",
        higher_is_better=False,
    ),
    MetricDef(
        "effective_holdings",
        "Effective number of holdings",
        "concentration",
        "numeric",
        "ratio",
        "1 / HHI of covered issuer weights",
        period="holdings_date",
        higher_is_better=True,
    ),
    MetricDef(
        "top_sector_weight",
        "Largest sector weight",
        "concentration",
        "numeric",
        "fraction",
        "largest sector weight / covered weight (sector as classified by the issuer file)",
        period="holdings_date",
    ),
    MetricDef(
        "top_country_weight",
        "Largest country weight",
        "concentration",
        "numeric",
        "fraction",
        "largest country weight / covered weight",
        period="holdings_date",
    ),
    MetricDef(
        "lookthrough_coverage",
        "Look-through coverage",
        "coverage",
        "numeric",
        "fraction",
        "sum of published row weights (1 - unexplained residual)",
        period="holdings_date",
        higher_is_better=True,
    ),
    MetricDef(
        "tracking_difference",
        "Tracking difference",
        "performance",
        "numeric",
        "fraction",
        "annualised fund return - benchmark return over the longest period both cover, on the same basis and currency; "
        "only for funds where you recorded a benchmark",
    ),
)

STOCK_CATALOGUE: dict[str, MetricDef] = {m.id: m for m in _STOCK}
FUND_CATALOGUE: dict[str, MetricDef] = {m.id: m for m in _FUND}

NUMERIC_OPS = ("gte", "lte")
CATEGORY_OPS = ("in",)
Op = Literal["gte", "lte", "in"]


class MetricValue(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: Decimal | str | None = None
    reason: str | None = Field(
        default=None, description="Why the value is unknown or not meaningful."
    )
    period_end: dt.date | None = None
    basis: str | None = None
    source: str | None = None


class DiscoverFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    op: Op
    value: Decimal | list[str] = Field(description="Threshold, or the allowed categories for `in`.")
    definition_version: str | None = Field(
        default=None, description="Pin the definition; a mismatch is rejected."
    )
    period: str | None = Field(
        default=None, description="Pin the period rule; a mismatch is rejected."
    )


@dataclass(frozen=True)
class FilterProblem:
    metric: str
    message: str


def metric_state(
    metric: MetricDef, *, access: dict[tuple[str, str], str], coverage: dict[str, int], total: int
) -> tuple[bool, str | None]:
    """(enabled, reason it is disabled). Disabled: no source, access denied, or no value at all."""
    if metric.no_source_reason:
        return False, metric.no_source_reason
    if metric.requires_access is not None:
        state = access.get(metric.requires_access, "access_unverified")
        if state != "available":
            return False, (
                f"{metric.requires_access[0]} {metric.requires_access[1]} is {state.replace('_', ' ')} "
                "on the current plan; no consensus is estimated in its place"
            )
    if total > 0 and coverage.get(metric.id, 0) == 0:
        return False, "no issuer in this universe has a value for this metric yet"
    return True, None


def validate_filters(
    filters: list[DiscoverFilter],
    catalogue: dict[str, MetricDef],
    *,
    access: dict[tuple[str, str], str],
    coverage: dict[str, int],
    total: int,
) -> list[FilterProblem]:
    problems: list[FilterProblem] = []
    seen: set[tuple[str, str]] = set()
    for f in filters:
        metric = catalogue.get(f.metric)
        if metric is None:
            problems.append(FilterProblem(f.metric, "unknown metric"))
            continue
        if (f.metric, f.op) in seen:
            problems.append(FilterProblem(f.metric, f"only one '{f.op}' filter per metric"))
        seen.add((f.metric, f.op))
        allowed = NUMERIC_OPS if metric.kind == "numeric" else CATEGORY_OPS
        if f.op not in allowed:
            problems.append(
                FilterProblem(
                    f.metric, f"operator '{f.op}' does not apply to a {metric.kind} metric"
                )
            )
        if metric.kind == "numeric" and not isinstance(f.value, Decimal):
            problems.append(FilterProblem(f.metric, "a numeric threshold is required"))
        if metric.kind == "category" and (not isinstance(f.value, list) or not f.value):
            problems.append(FilterProblem(f.metric, "choose at least one value"))
        if f.definition_version is not None and f.definition_version != metric.definition_version:
            problems.append(
                FilterProblem(
                    f.metric,
                    f"definition version {f.definition_version} is not the published {metric.definition_version}",
                )
            )
        if f.period is not None and f.period != metric.period:
            problems.append(
                FilterProblem(
                    f.metric, f"period '{f.period}' does not match the metric's '{metric.period}'"
                )
            )
        enabled, why = metric_state(metric, access=access, coverage=coverage, total=total)
        if not enabled:
            problems.append(FilterProblem(f.metric, f"filter disabled: {why}"))
    return problems
