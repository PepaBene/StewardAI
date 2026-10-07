"""Response models for the Phase 4 performance API. Figures are floats at the edge (the frontend
formats, never recalculates); every computed number keeps its status, method version and the
warnings that qualify it."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from steward.analytics.contract import AnalyticsResult

ReportStatus = Literal["complete", "partial", "unavailable", "paid_only", "disputed"]


def num(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


class Figure(BaseModel):
    value: float | None = Field(description="Null means unavailable, never zero.")
    unit: str
    status: ReportStatus
    method_version: str
    as_of: dt.date | None = None
    coverage: float | None = Field(default=None, description="Share of inputs that were usable.")
    warnings: list[str] = Field(default_factory=list)
    input_hash: str | None = None


def figure(result: AnalyticsResult) -> Figure:
    return Figure(
        value=num(result.value),
        unit=result.unit,
        status=result.status.value,
        method_version=result.method_version,
        as_of=result.as_of,
        coverage=num(result.coverage.fraction) if result.coverage else None,
        warnings=result.warnings,
        input_hash=result.input_hash,
    )


class PerformanceExplain(BaseModel):
    formula_version: str
    formula: list[str]
    gaps: list[str] = Field(default_factory=list)


# --- performance ------------------------------------------------------------------------------


class BenchmarkOut(BaseModel):
    symbol: str
    label: str
    benchmark_return: float | None
    active_return: Figure
    basis: str
    currency: str | None
    missing_share: float | None


class WindowOut(BaseModel):
    label: str
    start: dt.date | None
    end: dt.date | None
    calendar_days: int | None
    observations: int
    start_value: float | None
    end_value: float | None
    net_flows: float
    gain: float | None
    twr: Figure
    twr_annualized: float | None
    xirr: Figure
    reason: str | None
    benchmark: BenchmarkOut | None


class PositionBreakOut(BaseModel):
    ticker: str
    ledger_quantity: float
    broker_quantity: float
    difference: float
    reason: str


class CashBreakOut(BaseModel):
    start_date: dt.date
    end_date: dt.date
    difference: float
    explanation: str


class LedgerHealth(BaseModel):
    status: Literal["clean", "breaks", "empty"]
    entries: int
    first: dt.date | None
    last: dt.date | None
    by_kind: dict[str, int]
    unsupported: list[str]
    needs_fx: int
    fees_not_captured: int
    position_breaks: list[PositionBreakOut]
    cash_breaks: list[CashBreakOut]
    cash_breaks_total: int
    unexplained_cash: float = Field(description="Sum of unexplained daily cash changes.")
    cash_tolerance: float


class PerformanceSeriesPoint(BaseModel):
    date: dt.date
    value: float | None


class CostsOut(BaseModel):
    currency: str | None
    fill_fees: float
    cash_fees: float
    total: float
    fraction_of_average_value: float | None
    window_start: dt.date | None
    note: str


class TimelineMonth(BaseModel):
    month: str
    deposits: float
    withdrawals: float
    dividends: float
    fees: float


class PerformanceResponse(BaseModel):
    status: ReportStatus
    as_of: dt.date | None
    base_currency: str | None
    account_currency: str | None
    gap: str | None
    windows: list[WindowOut]
    index: list[PerformanceSeriesPoint] = Field(
        description="Portfolio value indexed to 100, flows removed (time-weighted)."
    )
    benchmark_index: list[PerformanceSeriesPoint]
    benchmark_label: str | None
    rolling_90d: list[PerformanceSeriesPoint]
    values: list[PerformanceSeriesPoint] = Field(
        description="Total value in the base currency, flows in."
    )
    ledger: LedgerHealth
    costs: CostsOut
    timeline: list[TimelineMonth]
    freshness_note: str | None
    explain: PerformanceExplain


# --- lots ------------------------------------------------------------------------------------


class LotOut(BaseModel):
    lot_id: str
    ticker: str
    name: str | None
    bought_on: dt.date
    quantity: float
    unit_cost: float
    cost: float
    passes_on: dt.date
    months_remaining: float
    passed: bool
    sell_target: float | None
    current_price: float | None
    target_reached: bool | None
    forfeits_exemption_warning: bool


class RealizedOut(BaseModel):
    sale_id: str
    ticker: str
    sold_on: dt.date
    quantity: float
    proceeds: float
    cost: float
    gain: float
    holding_days: int | None
    gross_within_time_test: float


class UnmatchedOut(BaseModel):
    sale_id: str
    ticker: str
    sold_on: dt.date
    quantity: float
    reason: str


class ExemptionOut(BaseModel):
    year: int
    currency: str
    gross_proceeds: float
    limit: float
    headroom: float
    exceeded: bool
    sales_counted: int
    sales_without_rate: int


class LotsResponse(BaseModel):
    status: ReportStatus
    as_of: dt.date | None
    method: str
    currency: str | None
    lots: list[LotOut]
    realized: list[RealizedOut]
    unmatched: list[UnmatchedOut]
    realized_gain: float
    dividends: float
    fees: float
    exemption: list[ExemptionOut]
    time_test_years: int
    warnings: list[str]
    disclaimer: str
    explain: PerformanceExplain


# --- attribution -------------------------------------------------------------------------------


class ContributionOut(BaseModel):
    ticker: str
    name: str | None
    price: float
    fx: float
    income: float
    fees: float
    total: float
    pct_of_start: float | None
    attributed: bool
    benchmark_equivalent: float | None = Field(
        default=None,
        description="What this holding's opening value would have gained in the benchmark.",
    )
    active: float | None = Field(
        default=None, description="Total contribution minus the benchmark equivalent."
    )
    active_reason: str | None = Field(
        default=None, description="Why there is no active figure for this holding."
    )


class BenchmarkRelativeOut(BaseModel):
    label: str
    benchmark_return: float = Field(description="Benchmark return over the same dates.")
    basis: str = Field(description="total, price or unknown: whether dividends are included.")
    currency: str | None
    like_for_like: bool = Field(
        description="The benchmark is quoted in the base currency and includes dividends."
    )
    active_total: float = Field(description="Sum of the holdings' active contributions.")
    notes: list[str]


class BridgeOut(BaseModel):
    gain: Figure
    start_value: float
    end_value: float
    net_external_flows: float
    net_trading: float
    components: dict[str, float]
    residual: float
    unattributed_tickers: list[str]


class AttributionResponse(BaseModel):
    status: ReportStatus
    start: dt.date | None
    end: dt.date | None
    base_currency: str | None
    days: int
    gap: str | None
    bridge: BridgeOut | None
    holdings: list[ContributionOut]
    groups: dict[str, dict[str, float]]
    benchmark: BenchmarkRelativeOut | None = None
    explain: PerformanceExplain


# --- sleeves -----------------------------------------------------------------------------------


class SleeveOut(BaseModel):
    name: str
    start: dt.date | None
    end: dt.date | None
    days: int
    total_return: float | None
    annualized: float | None
    reason: str | None
    tickers: list[str]


class SleevesResponse(BaseModel):
    status: ReportStatus
    as_of: dt.date | None
    gap: str | None
    core: SleeveOut | None
    stocks: SleeveOut | None
    history_days: int
    annual_gap_points: float | None
    gap_limit_points: float
    too_early: bool
    review_item: bool
    message: str
    reconciliation_error: float | None
    explain: PerformanceExplain
