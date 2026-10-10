"""P7: owner-only read routes for published estimates.

One route returns the five series as separate lanes, the revision pairs (a break carries its reason
and no number), the event comparisons (estimate known strictly before the release against the
reported actual), the segment bridges and an explicit coverage list.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import AwareDatetime, BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.routes import TickerPath
from steward.disclosure import estimate_read

router = APIRouter(prefix="/disclosures", tags=["disclosures"])

AsOf = Annotated[AwareDatetime | None, Query(description="Show only what was known by this time.")]


class EstimateLineOut(BaseModel):
    id: int
    snapshot_id: int
    series: str
    metric: str
    segment: str | None
    segment_taxonomy: str | None
    non_additive: bool
    restated_prior: bool
    fiscal_period: str
    fiscal_calendar: str | None
    period_start: dt.date | None
    period_end: dt.date | None
    currency: str | None
    unit: str
    scale: int
    basis: str
    basis_definition: str | None
    mean: str | None
    median: str | None
    high: str | None
    low: str | None
    analyst_count: int | None
    panel_analyst_count: int | None
    panel_description: str | None
    panel_method: str | None
    source: str | None
    broker: str | None
    analyst: str | None
    published_at: dt.datetime
    known_at: dt.datetime
    supersedes_snapshot_id: int | None
    corrected: bool
    document_url: str | None
    evidence_id: int | None
    original_string: str | None
    problems: list[str]


class EstimateLanesOut(BaseModel):
    published_analyst_consensus: list[EstimateLineOut]
    named_individual_estimate: list[EstimateLineOut]
    company_guidance: list[EstimateLineOut]
    event_calendar_estimate: list[EstimateLineOut]
    owner_scenario: list[EstimateLineOut]


class RevisionPairOut(BaseModel):
    earlier_id: int
    later_id: int
    series: str
    metric: str
    segment: str | None
    fiscal_period: str
    earlier_known_at: dt.datetime
    later_known_at: dt.datetime
    comparable: bool
    break_reasons: list[str]
    statistic: str | None
    earlier_value: str | None
    later_value: str | None
    change: str | None
    change_pct: str | None
    pct_note: str | None
    elapsed_days: int
    analyst_count_change: int | None
    analyst_count_note: str | None
    direction: str | None
    note: str | None
    formula_version: str


class EventComparisonOut(BaseModel):
    status: str
    metric: str
    segment: str | None
    fiscal_period: str | None
    period_end: dt.date | None
    release_at: dt.datetime | None
    estimate_value_id: int | None
    estimate_known_at: dt.datetime | None
    statistic: str | None
    estimate: str | None
    actual: str | None
    surprise: str | None
    surprise_pct: str | None
    pct_note: str | None
    analyst_count: int | None
    basis: str | None
    currency: str | None
    unit: str | None
    later_files_only: bool
    warnings: list[str]
    actual_observation_id: int | None
    release_basis: str | None = None
    formula_version: str
    formula: str


class SegmentLineOut(BaseModel):
    segment: str
    estimate_value_id: int | None
    actual_observation_id: int | None
    estimate: str | None
    actual: str | None
    delta: str | None
    delta_pct: str | None
    status: str
    non_additive: bool
    flags: list[str]
    estimate_evidence_id: int | None
    actual_evidence_id: int | None


class EstimateSegmentBridgeOut(BaseModel):
    metric: str
    fiscal_period: str | None
    period_end: dt.date
    lines: list[SegmentLineOut]
    additive_estimate_total: str | None
    additive_actual_total: str | None
    unmapped_estimates: list[str]
    notes: list[str]
    inputs: list[str]


class CoverageGapOut(BaseModel):
    kind: str
    state: str
    detail: str


class ConsensusSourceOut(BaseModel):
    source_key: str
    publisher: str
    origin: str
    format: str
    access_state: str
    gap_reason: str | None
    url: str
    cadence: str | None
    tested_on: dt.date | None
    selected_metrics_only: bool
    selected_brokers_only: bool


class RevisionGateOut(BaseModel):
    reviewed: int
    needed: int
    correct: int
    rate: str | None
    bar: str
    met: bool
    reason: str


class EstimateIssuerOut(BaseModel):
    id: int
    name: str


class SurpriseFormulaOut(BaseModel):
    surprise: str
    version: str


class EstimatesOut(BaseModel):
    ticker: str
    as_of: dt.datetime
    issuer: EstimateIssuerOut | None
    lanes: EstimateLanesOut
    pairs: list[RevisionPairOut]
    events: list[EventComparisonOut]
    segment_bridges: list[EstimateSegmentBridgeOut]
    coverage: list[CoverageGapOut]
    sources: list[ConsensusSourceOut]
    revision_gate: RevisionGateOut
    formula: SurpriseFormulaOut
    notes: list[str]


@router.get("/company/{ticker}/estimates", include_in_schema=False)
@router.get("/holdings/{ticker}/estimates")
async def holding_estimates(
    ticker: TickerPath, pool: Pool, _owner: Owner, as_of: AsOf = None
) -> EstimatesOut:
    """Published estimates known by `as_of`, as five separate lanes. Guidance, consensus, named
    estimates, event-calendar estimates and owner scenarios are never merged or averaged; a
    revision pair across a panel, basis, currency or period break carries its reason and no number;
    a surprise uses only a snapshot known strictly before the release."""
    return EstimatesOut(**await estimate_read.estimate_view(pool, ticker.upper(), as_of))


class PairReviewItemOut(RevisionPairOut):
    reviewed: bool
    correct: bool | None
    review_note: str | None
    reviewed_at: dt.datetime | None


class PairReviewsOut(BaseModel):
    ticker: str
    revision_gate: RevisionGateOut
    pairs: list[PairReviewItemOut]


class PairReviewIn(BaseModel):
    earlier_value_id: int
    later_value_id: int
    correct: bool
    note: str | None = Field(default=None, max_length=1000)


class PairReviewCreatedOut(BaseModel):
    id: int


@router.get("/estimates/pair-reviews")
async def pair_reviews(
    pool: Pool, _owner: Owner, ticker: Annotated[str, Query(min_length=1, max_length=20)]
) -> PairReviewsOut:
    """Snapshot pairs for one holding with the latest label each, plus the revision-alert gate.
    Revision alerts stay off until enough distinct pairs are labelled correct."""
    return PairReviewsOut(**await estimate_read.pair_review_view(pool, ticker.upper()))


@router.post("/estimates/pair-reviews", status_code=status.HTTP_201_CREATED)
async def record_pair_review(body: PairReviewIn, pool: Pool, owner: Owner) -> PairReviewCreatedOut:
    """Record whether the code treated a pair correctly (a number, or a refusal with its reason)."""
    try:
        new_id = await estimate_read.record_pair_review(
            pool,
            earlier_value_id=body.earlier_value_id,
            later_value_id=body.later_value_id,
            correct=body.correct,
            reviewer=f"owner:{owner.user_id}",
            note=body.note,
        )
    except estimate_read.PairRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return PairReviewCreatedOut(id=new_id)
