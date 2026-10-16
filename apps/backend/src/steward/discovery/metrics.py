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
    # Range-slider scale (insight I10.8): tick values in the metric's unit, spaced evenly on the
    # slider, so a non-linear spread (a P/E of 5 to 100) gets usable resolution at the low end.
    ticks: tuple[Decimal, ...] | None = None
    # Plain-English names the screen parser maps to this metric (insight I10.2).
    aliases: tuple[str, ...] = ()

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
    MetricDef(
        "net_margin",
        "Net margin",
        "financials",
        "numeric",
        "fraction",
        "net income / revenue, latest fiscal year (revenue > 0)",
        higher_is_better=True,
    ),
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
    MetricDef(
        "price_to_sales",
        "P/S",
        "value",
        "numeric",
        "multiple",
        "market cap / latest annual revenue (revenue > 0)",
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
    *(
        MetricDef(
            f"checks_{axis}_score",
            f"{label} checks score",
            "checks",
            "numeric",
            "ratio",
            f"passes over applicable {label.lower()} checks, scaled to 0-6 (method checks; not applicable and unknown checks are left out); from the latest company checks",
            period="as_of",
            higher_is_better=True,
        )
        for axis, label in (
            ("value", "Value"),
            ("future", "Future"),
            ("past", "Past"),
            ("health", "Health"),
            ("dividend", "Dividend"),
        )
    ),
    MetricDef(
        "checks_risk_flags",
        "Risk checks failed",
        "checks",
        "numeric",
        "count",
        "risk checks in the fail state (informational ones excluded); from the latest company checks",
        period="as_of",
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


def _d(*xs: str | int) -> tuple[Decimal, ...]:
    return tuple(Decimal(str(x)) for x in xs)


# Insight I10.5: metrics the statements, dividends and insider filings already support.
_PARITY: tuple[MetricDef, ...] = (
    MetricDef(
        "buyback_yield",
        "Buyback yield",
        "income",
        "numeric",
        "fraction",
        "share repurchases paid in the latest fiscal year / market cap (same statement currency)",
        higher_is_better=True,
    ),
    MetricDef(
        "shareholder_yield",
        "Total shareholder yield",
        "income",
        "numeric",
        "fraction",
        "(dividends paid + share repurchases paid) in the latest fiscal year / market cap; both lines must be filed",
        higher_is_better=True,
    ),
    MetricDef(
        "payout_ratio",
        "Payout ratio (earnings)",
        "income",
        "numeric",
        "fraction",
        "dividends paid / net income, latest fiscal year (net income > 0)",
    ),
    MetricDef(
        "payout_ratio_fcf",
        "Payout ratio (free cash flow)",
        "income",
        "numeric",
        "fraction",
        "dividends paid / (operating cash flow - capital expenditure), latest fiscal year (free cash flow > 0)",
    ),
    MetricDef(
        "dividend_growth_1y",
        "Dividend growth (1 year)",
        "income",
        "numeric",
        "fraction",
        "split-adjusted dividends per share with ex-date in the last 12 months / the 12 months before - 1; same currency",
        period="ttm",
        higher_is_better=True,
    ),
    MetricDef(
        "dividend_growth_5y",
        "Dividend growth (5 years, a year)",
        "income",
        "numeric",
        "fraction",
        "(split-adjusted dividends per share in the last 12 months / the same 12 months five years earlier) ^ (1/5) - 1",
        period="5y",
        higher_is_better=True,
    ),
    MetricDef(
        "net_debt_to_ebitda",
        "Net debt to EBITDA",
        "financials",
        "numeric",
        "multiple",
        "(debt - cash) / (operating income + depreciation and amortisation), latest fiscal year (EBITDA > 0)",
        higher_is_better=False,
    ),
    MetricDef(
        "insider_buy_ratio_90d",
        "Insider buying ratio (90 days)",
        "signal",
        "numeric",
        "fraction",
        "distinct insiders with open-market buys / distinct insiders with open-market buys or sells, 90 days (Form 4; planned trades excluded)",
        period="90d",
        higher_is_better=True,
    ),
    MetricDef(
        "forward_eps_growth",
        "Forecast EPS growth (next year)",
        "estimates",
        "numeric",
        "fraction",
        "published consensus EPS for the next fiscal year / latest annual diluted EPS (net income / diluted shares) - 1; trailing EPS > 0, one currency",
        period="next_fy",
        requires_access=("fmp", "analyst_estimates"),
        higher_is_better=True,
    ),
)

# Slider ticks and parser aliases per metric (insight I10.2, I10.8).
_PCT_TICKS = _d("-0.5", "-0.2", "-0.1", "0", "0.02", "0.05", "0.1", "0.2", "0.3", "0.5", "1")
_YIELD_TICKS = _d("0", "0.01", "0.02", "0.03", "0.04", "0.05", "0.06", "0.08", "0.1", "0.15")
_MULT_TICKS = _d("0", "5", "8", "10", "12", "15", "20", "25", "30", "40", "60", "100")
_EXTRA: dict[str, dict[str, object]] = {
    "market_cap": {
        "ticks": _d(
            0,
            "50000000",
            "300000000",
            "2000000000",
            "10000000000",
            "50000000000",
            "200000000000",
            "1000000000000",
        ),
        "aliases": ("market cap", "size", "large cap", "small cap", "mega cap", "micro cap"),
    },
    "dividend_yield": {
        "ticks": _YIELD_TICKS,
        "aliases": ("dividend yield", "yield", "yielding", "dividend payers", "pays a dividend"),
    },
    "return_1y": {
        "ticks": _PCT_TICKS,
        "aliases": ("1 year return", "one-year return", "performance this year"),
    },
    "return_3y": {"ticks": _PCT_TICKS, "aliases": ("3 year return",)},
    "return_5y": {"ticks": _PCT_TICKS, "aliases": ("5 year return",)},
    "volatility_1y": {
        "ticks": _d("0", "0.1", "0.15", "0.2", "0.25", "0.3", "0.4", "0.6", "1"),
        "aliases": ("volatility", "low volatility", "risk"),
    },
    "revenue_growth": {
        "ticks": _PCT_TICKS,
        "aliases": ("revenue growth", "sales growth", "growing revenue"),
    },
    "gross_margin": {
        "ticks": _d("0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8"),
        "aliases": ("gross margin",),
    },
    "operating_margin": {"ticks": _PCT_TICKS, "aliases": ("operating margin", "ebit margin")},
    "net_margin": {
        "ticks": _PCT_TICKS,
        "aliases": ("net margin", "profit margin", "profitable", "net profit margin"),
    },
    "roic": {
        "ticks": _PCT_TICKS,
        "aliases": ("roic", "return on invested capital", "return on capital"),
    },
    "net_debt_to_equity": {
        "ticks": _d("-1", "-0.5", "0", "0.25", "0.5", "1", "1.5", "2", "3"),
        "aliases": (
            "net debt to equity",
            "debt to equity",
            "debt",
            "leverage",
            "low debt",
            "gearing",
        ),
    },
    "free_cash_flow_conversion": {
        "ticks": _d("0", "0.25", "0.5", "0.75", "1", "1.25", "1.5"),
        "aliases": ("cash conversion", "fcf conversion"),
    },
    "earnings_yield": {"ticks": _YIELD_TICKS, "aliases": ("earnings yield",)},
    "pe_ratio": {
        "ticks": _MULT_TICKS,
        "aliases": ("p/e", "pe", "pe ratio", "price to earnings", "cheap", "earnings multiple"),
    },
    "fcf_yield": {"ticks": _YIELD_TICKS, "aliases": ("free cash flow yield", "fcf yield")},
    "ev_ebit": {"ticks": _MULT_TICKS, "aliases": ("ev/ebit", "ev to ebit", "enterprise multiple")},
    "price_to_book": {
        "ticks": _d("0", "0.5", "1", "1.5", "2", "3", "5", "10", "20"),
        "aliases": ("p/b", "price to book", "book value"),
    },
    "price_to_sales": {
        "ticks": _d("0", "0.5", "1", "2", "3", "5", "8", "12", "20"),
        "aliases": ("p/s", "price to sales", "sales multiple"),
    },
    "momentum_12_1": {
        "ticks": _PCT_TICKS,
        "aliases": ("momentum", "price momentum", "trending up"),
    },
    "share_change_1y": {
        "ticks": _d("-0.1", "-0.05", "-0.02", "0", "0.02", "0.05", "0.1", "0.2"),
        "aliases": ("dilution", "share dilution", "share count change", "issuing shares"),
    },
    "share_change_3y": {
        "ticks": _d("-0.1", "-0.05", "-0.02", "0", "0.02", "0.05", "0.1"),
        "aliases": ("3 year dilution",),
    },
    "cash_runway_years": {
        "ticks": _d("0", "0.5", "1", "2", "3", "5", "10"),
        "aliases": ("cash runway", "runway"),
    },
    "insider_buyers_90d": {
        "ticks": _d("0", "1", "2", "3", "5", "10"),
        "aliases": ("insider buying", "insiders buying", "insider buyers"),
    },
    "forward_pe": {
        "ticks": _MULT_TICKS,
        "aliases": ("forward p/e", "forward pe", "forward earnings multiple"),
    },
    "buyback_yield": {
        "ticks": _YIELD_TICKS,
        "aliases": ("buyback yield", "buybacks", "repurchasing shares", "share buybacks"),
    },
    "shareholder_yield": {
        "ticks": _YIELD_TICKS,
        "aliases": ("shareholder yield", "total shareholder yield", "capital returned"),
    },
    "payout_ratio": {
        "ticks": _d("0", "0.2", "0.4", "0.5", "0.6", "0.75", "0.9", "1", "1.5"),
        "aliases": (
            "payout ratio",
            "payout",
            "earnings payout",
            "covered dividend",
            "sustainable dividend",
        ),
    },
    "payout_ratio_fcf": {
        "ticks": _d("0", "0.2", "0.4", "0.5", "0.6", "0.75", "0.9", "1", "1.5"),
        "aliases": ("cash payout ratio", "fcf payout"),
    },
    "dividend_growth_1y": {
        "ticks": _PCT_TICKS,
        "aliases": ("dividend growth", "growing dividend", "rising dividend", "dividend raise"),
    },
    "dividend_growth_5y": {
        "ticks": _PCT_TICKS,
        "aliases": ("5 year dividend growth", "long-term dividend growth"),
    },
    "net_debt_to_ebitda": {
        "ticks": _d("-1", "0", "0.5", "1", "1.5", "2", "3", "4", "6"),
        "aliases": ("net debt to ebitda", "debt to ebitda", "leverage ratio"),
    },
    "insider_buy_ratio_90d": {
        "ticks": _d("0", "0.25", "0.5", "0.75", "1"),
        "aliases": ("insider buying ratio", "net insider buying"),
    },
    "forward_eps_growth": {
        "ticks": _PCT_TICKS,
        "aliases": (
            "earnings growth",
            "eps growth",
            "forecast growth",
            "expected earnings growth",
            "positive earnings growth",
        ),
    },
    "years_since_listing": {
        "ticks": _d("0", "1", "2", "3", "5", "10", "20", "50"),
        "aliases": ("years listed", "recent ipo", "new listing"),
    },
    "avg_daily_turnover": {
        "ticks": _d("0", "100000", "1000000", "10000000", "100000000", "1000000000"),
        "aliases": ("liquidity", "trading volume", "turnover"),
    },
    "qvm_score": {
        "ticks": _d("0", "0.2", "0.4", "0.5", "0.6", "0.8", "1"),
        "aliases": ("quality value momentum", "qvm"),
    },
    "checks_risk_flags": {
        "ticks": _d("0", "1", "2", "3", "5"),
        "aliases": ("risk flags", "risk checks failed"),
    },
    "sector": {"aliases": ("sector",)},
    "industry": {"aliases": ("industry",)},
    "country": {"aliases": ("country", "market", "based in", "listed in")},
}
for _axis in ("value", "future", "past", "health", "dividend"):
    _EXTRA[f"checks_{_axis}_score"] = {
        "ticks": _d(0, 1, 2, 3, 4, 5, 6),
        "aliases": (
            f"{_axis} score",
            f"{_axis} checks",
            "financially healthy" if _axis == "health" else f"strong {_axis}",
        ),
    }


def _with_extra(m: MetricDef) -> MetricDef:
    extra = _EXTRA.get(m.id)
    if not extra:
        return m
    from dataclasses import replace

    return replace(m, **extra)  # type: ignore[arg-type]


STOCK_CATALOGUE: dict[str, MetricDef] = {m.id: _with_extra(m) for m in (*_STOCK, *_PARITY)}
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
