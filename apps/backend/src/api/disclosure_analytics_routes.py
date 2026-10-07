"""P8.1, P8.3, P8.4 and P9.7: owner-only cross-dataset reads.

Coverage over the held tickers (with the quality report), a one-call evidence summary per holding,
portfolio look-through to confirmed relationships and deals, and a screen over verified fields.
Every figure carries its denominator; a missing value is never zero. Model names start with
`Analytics` because OpenAPI model names must be unique across route files.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.routes import TickerPath
from steward.disclosure import coverage_report, look_through, screening

router = APIRouter(prefix="/disclosures", tags=["disclosures"])

AsOf = Annotated[AwareDatetime | None, Query(description="Show only what was known by this time.")]

CoverageState = Literal[
    "available",
    "stale",
    "unresolved_identity",
    "not_disclosed",
    "not_covered",
    "paid_only",
    "authentication_failed",
    "retrieval_failed",
    "parse_failed",
    "under_review",
    "none_known",
]


# ------------------------------------------------------------------------------ coverage report
class AnalyticsCoverageCellOut(BaseModel):
    dataset: str
    label: str
    jurisdiction: str
    tickers: int
    covered: int
    uncovered: int
    stale: int
    denominator_value: str = Field(description="Held market value of this jurisdiction.")
    covered_value: str
    uncovered_value: str
    covered_share: str | None = Field(description="Null when the denominator is zero.")
    first_known_at: dt.datetime | None
    last_known_at: dt.datetime | None
    states: dict[str, int]
    blind_spots: list[str]
    unit: str
    source: str
    method: str


class AnalyticsPeriodOut(BaseModel):
    dataset: str
    period: str
    issuers_covered: int
    issuers_total: int
    covered_value: str
    denominator_value: str


class AnalyticsFailureOut(BaseModel):
    kind: Literal["coverage", "source"]
    dataset: str
    jurisdiction: str
    state: str
    count: int
    last_checked_at: dt.datetime | None
    detail: str | None


class AnalyticsQualityOut(BaseModel):
    dataset: str
    labelled: int = Field(description="Denominator: items a person labelled.")
    errors: int = Field(description="Numerator: labels that disagreed with the machine value.")
    error_rate: str | None = Field(description="Null when nothing was sampled.")
    needed: int
    bar: str
    state: Literal["not_sampled", "sample_too_small", "meets_bar", "below_bar"]


class AnalyticsCostSourceOut(BaseModel):
    name: str
    reference: str
    cost_usd: str


class AnalyticsCostLedgerOut(BaseModel):
    total_usd: str
    basis: str
    registered_sources: int
    paid_sources: int
    sources: list[AnalyticsCostSourceOut]


class AnalyticsReleaseStageOut(BaseModel):
    order: int
    stage: str
    datasets: list[str]
    gates: dict[str, str | None]
    state: Literal["gates_met", "gates_open", "not_started"]


class AnalyticsCoverageOut(BaseModel):
    as_of: dt.datetime
    snapshot_date: dt.date | None
    holdings: int
    denominator_value: str
    cells: list[AnalyticsCoverageCellOut]
    by_period: list[AnalyticsPeriodOut]
    failures: list[AnalyticsFailureOut]
    quality: list[AnalyticsQualityOut]
    cost_ledger: AnalyticsCostLedgerOut
    release_order: list[AnalyticsReleaseStageOut]
    first_known_at: dt.datetime | None
    last_known_at: dt.datetime | None
    method_version: str
    notes: list[str]


@router.get("/data/coverage")
async def data_coverage(pool: Pool, _owner: Owner, as_of: AsOf = None) -> AnalyticsCoverageOut:
    """Coverage by dataset and jurisdiction over held tickers, with blind spots, collection
    failures, sampled error rates, the free-tier cost ledger and the release order."""
    return AnalyticsCoverageOut(**await coverage_report.coverage_view(pool, as_of))


# -------------------------------------------------------------------------------- company evidence
class AnalyticsEvidenceDatasetOut(BaseModel):
    dataset: str
    label: str
    count: int
    unit: str
    first_known_at: dt.datetime | None
    latest_known_at: dt.datetime | None
    first_period: str | None
    last_period: str | None
    coverage_state: CoverageState
    stale: bool
    source: str
    method: str
    method_version: str
    api_path: str | None = Field(description="The existing dataset route; null if no identity.")
    page_path: str


class AnalyticsCompanyEvidenceOut(BaseModel):
    ticker: str
    held: bool
    issuer_id: int | None
    jurisdiction: str | None
    weight: str | None
    as_of: dt.datetime
    datasets: list[AnalyticsEvidenceDatasetOut]
    covered_datasets: int
    dataset_total: int
    notes: list[str]


@router.get("/company/{ticker}/evidence")
async def company_evidence(
    ticker: TickerPath, pool: Pool, _owner: Owner, as_of: AsOf = None
) -> AnalyticsCompanyEvidenceOut:
    """One call per holding: each dataset's count, newest known date and coverage state."""
    return AnalyticsCompanyEvidenceOut(
        **await coverage_report.company_evidence_view(pool, ticker.upper(), as_of)
    )


# ------------------------------------------------------------------------------ look-through
class AnalyticsFundReachOut(BaseModel):
    fund_ticker: str
    fund_weight: str | None
    fund_pct: str
    contribution: str | None
    report_period_end: dt.date
    lag_days: int


class AnalyticsExposureOut(BaseModel):
    ticker: str
    direct_weight: str
    held_directly: bool
    fund_weight: str
    total_weight: str
    funds: list[AnalyticsFundReachOut]


class AnalyticsRelationshipOut(BaseModel):
    ticker: str
    relationship: Literal["customer", "supplier"]
    counterparty_key: str | None
    counterparty_name: str | None
    anonymous: bool
    share: str | None
    share_basis: str | None
    fiscal_period: str | None
    known_at: dt.datetime
    source_url: str | None
    holding_weight: str
    weighted_share: str | None = Field(description="Null when the share is unknown.")
    weight_state: Literal["reported", "unknown"]
    concentrated_customer: bool


class AnalyticsLookDealOut(BaseModel):
    ticker: str
    deal_id: int
    title: str
    deal_type: str
    state: str | None
    direct_weight: str
    holding_weight: str


class AnalyticsFundLagOut(BaseModel):
    fund_ticker: str
    weight: str | None
    report_period_end: dt.date
    known_at: dt.datetime
    lag_days: int
    mapped_pct: str
    unmapped_pct: str
    unknown_lines: int


class AnalyticsLookTotalsOut(BaseModel):
    denominator_weight: str | None
    relationship_covered_weight: str
    relationship_covered_holdings: int
    unknown_weight_holdings: list[str]
    unknown_weight: str
    customer_known_weight: str
    concentrated_customer_weight: str
    concentrated_customer_holdings: list[str]
    largest_customer_weighted: str
    deal_exposed_weight: str
    deal_exposed_holdings: list[str]
    fund_look_through_weight: str
    unmapped_fund_weight: str


class AnalyticsAsOfDatesOut(BaseModel):
    kind: str
    dates: list[str]


class AnalyticsLookThroughOut(BaseModel):
    as_of: dt.datetime
    snapshot_date: dt.date | None
    denominator_value: str
    holdings: int
    exposures: list[AnalyticsExposureOut]
    relationships: list[AnalyticsRelationshipOut]
    deals: list[AnalyticsLookDealOut]
    funds: list[AnalyticsFundLagOut]
    totals: AnalyticsLookTotalsOut
    as_of_dates: list[AnalyticsAsOfDatesOut]
    mixed_as_of: bool
    max_fund_lag_days: int | None
    concentration_share: str
    method_version: str
    notes: list[str]


@router.get("/portfolio/look-through")
async def portfolio_look_through(
    pool: Pool, _owner: Owner, as_of: AsOf = None
) -> AnalyticsLookThroughOut:
    """Direct and fund exposure to confirmed relationships, deals and concentrated customers.

    Totals are shares of the whole portfolio market value; unknown weights stay unknown."""
    return AnalyticsLookThroughOut(**await look_through.look_through_view(pool, as_of))


# ------------------------------------------------------------------------------------------ screen
class AnalyticsScreenValueOut(BaseModel):
    criterion: str
    label: str
    value: str
    unit: str
    period: str | None
    known_at: dt.datetime | None
    source: str


class AnalyticsScreenMatchOut(BaseModel):
    ticker: str
    weight: str
    values: list[AnalyticsScreenValueOut]


class AnalyticsScreenReasonOut(BaseModel):
    criterion: str
    kind: Literal["missing", "failed"]
    reason: str
    value: str | None
    threshold: str


class AnalyticsScreenExcludedOut(BaseModel):
    ticker: str
    weight: str
    kind: Literal["missing", "failed"]
    reasons: list[AnalyticsScreenReasonOut]


class AnalyticsScreenFieldCoverageOut(BaseModel):
    criterion: str
    label: str
    with_data: int
    without_data: int


class AnalyticsScreenOut(BaseModel):
    as_of: dt.datetime
    snapshot_date: dt.date | None
    universe_basis: str
    universe_size: int
    denominator_value: str
    active_criteria: list[str]
    matched: list[AnalyticsScreenMatchOut]
    excluded: list[AnalyticsScreenExcludedOut]
    matched_count: int
    excluded_missing: int
    excluded_failed: int
    matched_value: str
    matched_weight: str | None
    field_coverage: list[AnalyticsScreenFieldCoverageOut]
    insider_window_days: int
    method_version: str
    notes: list[str]


@router.get("/screen")
async def screen_holdings(
    pool: Pool,
    _owner: Owner,
    as_of: AsOf = None,
    min_cash_conversion: Annotated[
        Decimal | None, Query(description="Operating cash flow / net income.")
    ] = None,
    min_debt_maturity_coverage: Annotated[
        Decimal | None, Query(ge=0, description="Cash / debt due in year 1.")
    ] = None,
    min_discretionary_buys: Annotated[int | None, Query(ge=1)] = None,
    insider_window_days: Annotated[int, Query(ge=1, le=365)] = 90,
    min_stake_change_pct: Annotated[
        Decimal | None, Query(ge=0, description="Absolute points.")
    ] = None,
    min_customer_concentration: Annotated[Decimal | None, Query(ge=0, le=1)] = None,
    min_segment_surprise_pct: Annotated[
        Decimal | None, Query(ge=0, description="Absolute fraction.")
    ] = None,
    deal_exposure: Annotated[bool, Query(description="Only holdings with an active deal.")] = False,
) -> AnalyticsScreenOut:
    """Screen the held universe on verified fields. A holding missing a chosen field is excluded
    with that reason, never scored zero."""
    criteria: dict[str, Any] = {
        "min_cash_conversion": min_cash_conversion,
        "min_debt_maturity_coverage": min_debt_maturity_coverage,
        "min_discretionary_buys": min_discretionary_buys,
        "min_stake_change_pct": min_stake_change_pct,
        "min_customer_concentration": min_customer_concentration,
        "min_segment_surprise_pct": min_segment_surprise_pct,
        "deal_exposure": deal_exposure,
    }
    return AnalyticsScreenOut(
        **await screening.screen_view(pool, criteria, as_of, insider_window_days)
    )
