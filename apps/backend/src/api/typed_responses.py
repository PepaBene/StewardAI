"""Response models for routes that used to publish no response schema (audit 2026-10-01).

These routes return plain dicts (rows straight from SQL, read models assembled from several
queries) and are declared with `response_model=None`, so OpenAPI typed their bodies as `{}`.  They
now name their body with `typed(Model)`, which sets `responses={200: {"model": Model}}`.  That is
documentation only: FastAPI does not validate or re-serialise the handler's return value through
the model, so the JSON on the wire is exactly what the handler produced before (same keys and
order, `Decimal` as a JSON number, `datetime` as `+00:00` ISO text).  Putting the model in
`response_model` instead would turn every `Decimal` into a string and every UTC time into `Z`,
which the web parsers do not expect.

The models therefore describe the wire, not the Python values:

* a `Decimal` column is `float` (JSON number; a whole value may arrive without a fraction);
* a `jsonb` column is `dict[str, Any]` / `Any` (provider payload, kept as stored);
* a key the handler writes only on some paths has a default (`= None`), so it is optional.

`tests/test_typed_responses.py` pins each model to a seeded response: the body validates, every
key is declared, and the bytes equal the pre-change serialisation.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, Field, WithJsonSchema

from steward.config.holding_profiles import Exposure
from steward.data_context.evidence import (
    Consensus,
    Deals,
    Dependencies,
    FiledNotes,
    Insider,
    Positions,
)
from steward.lookthrough.read import LookThroughReport


def typed(model: Any, description: str | None = None) -> dict[int | str, dict[str, Any]]:
    """`responses=` argument that documents a 200 body without validating it (see module doc)."""
    entry: dict[str, Any] = {"model": model}
    if description:
        entry["description"] = description
    return {200: entry}


# --------------------------------------------------------------------------- shared pieces
class CompanyResearchObservation(BaseModel):
    id: int
    provider: str
    dataset: str
    metric: str
    period_end: dt.date | None
    value: float | None
    text_value: str | None
    unit: str | None
    currency: str | None
    source_url: str
    published_at: dt.datetime | None
    known_at: dt.datetime
    match_confidence: float | None = Field(description="0-1 identity-match confidence.")
    raw: Any = Field(description="Provider payload (jsonb), as stored.")


class CompanyResearchObservations(BaseModel):
    ticker: str
    known_as_of: dt.datetime | None = Field(description="The `as_of` cutoff; null = now.")
    observations: list[CompanyResearchObservation]


class FinancialHistoryRow(BaseModel):
    """One stored statement fact (`financial_statement_fact`) with its reconciliation state."""

    id: int
    concept: str
    label: str | None
    statement: str
    line_item: str | None
    period_start: dt.date | None
    period_end: dt.date
    fiscal_period: str | None
    basis: str
    value: float | None
    value_text: str | None
    unit: str
    currency: str | None
    dimensions: Any = Field(description="Segment members (jsonb): `{members: [...]}`; `{}` = none.")
    source: str
    source_url: str
    published_at: dt.datetime | None
    accepted_at: dt.datetime | None
    known_at: dt.datetime
    revision: int
    amends_fact_id: int | None
    amends_accession: str | None
    is_amendment: bool
    accession: str | None
    form: str | None
    cik: str | None
    issuer_id: int | None
    text_block: str | None
    evidence_id: int | None
    validation: dict[str, Any] = Field(description="Reconciliation checks (jsonb object).")
    published: bool
    reconciliation: str = Field(description='"reconciled", "failed" or "unchecked".')


FinancialHistoryRows = list[FinancialHistoryRow]


class ForwardObservation(BaseModel):
    id: int
    kind: str
    provider: str | None
    metric: str
    basis: str | None
    fiscal_period: str
    period_end: dt.date | None
    currency: str | None
    value: float | None
    low: float | None
    high: float | None
    analyst_count: int | None
    vintage: str | None
    published_at: dt.datetime | None
    known_at: dt.datetime
    source_url: str
    evidence_id: int | None
    raw: Any = Field(description="Provider payload (jsonb), as stored.")
    vintage_date: str | None = Field(
        description="ISO date a free-text `vintage` starts with, else null (never guessed)."
    )


ForwardObservations = list[ForwardObservation]


class ForwardConsensusSeries(BaseModel):
    metric: str
    fiscal_period: str
    provider: str | None
    currency: str | None
    basis: str | None
    vintages: list[ForwardObservation] = Field(description="Oldest first.")
    revision_since_previous: float | None = Field(
        description="Last vintage minus the one before it, in the series' own unit."
    )
    revision_inputs: list[int] = Field(description="Observation ids of the two vintages used.")


class ForwardConsensus(BaseModel):
    ticker: str
    known_as_of: dt.datetime | None
    observations: list[ForwardObservation]
    consensus_series: list[ForwardConsensusSeries]


class ActualFigure(BaseModel):
    value: float
    unit: str
    currency: str | None
    known_at: dt.datetime
    accession: str | None
    reconciled: bool = Field(description="True only when a reconciliation check passed.")
    reconciliation_state: str = Field(description='"unchecked", "failed" or "passed".')


class ConsensusFigure(BaseModel):
    id: int
    provider: str | None
    value: float
    low: float | None
    high: float | None
    analyst_count: int | None
    currency: str | None
    basis: str | None
    vintage: str | None
    known_at: dt.datetime
    vintage_date: str | None


class ActualVsConsensus(BaseModel):
    """`state` says which of the other keys are present: `unsupported_metric` (only `metric`),
    `no_actual` (`reason`), `no_consensus_before_report` (`actual`, `reason`),
    `currency_mismatch` (`actual`, `consensus`, `reason`) and `available` (`actual`, `consensus`,
    `surprise`, `surprise_percent`)."""

    state: str
    metric: str | None = None
    fiscal_period: str | None = None
    actual: ActualFigure | None = None
    consensus: ConsensusFigure | None = None
    surprise: float | None = Field(
        default=None, description="Actual minus consensus, in the figure's own unit."
    )
    surprise_percent: float | None = Field(
        default=None, description="Surprise as a fraction of |consensus| (0.05 = 5%); null at 0."
    )
    reason: str | None = None


# ----------------------------------------------------------------------------- peers
class PeerSetRow(BaseModel):
    id: int
    peer_ticker: str
    basis: str
    status: str = Field(description="The status in force at `known_as_of`.")
    evidence_id: int | None
    source: str
    valid_from: dt.date | None
    valid_to: dt.date | None
    known_at: dt.datetime
    detail: dict[str, Any]
    status_history: Any = Field(description="jsonb list of `{at, status}` changes.")


class PeerSet(BaseModel):
    ticker: str
    known_as_of: dt.datetime | None
    peers: list[PeerSetRow]


class ConfirmedPeer(BaseModel):
    peer_ticker: str
    basis: str
    source: str
    evidence_id: int | None
    sources: list[str] = Field(description="Every source that lists the peer.")


class PeerMetricValue(BaseModel):
    ticker: str
    basis: str | None
    period: dt.date | None = Field(description="Period end of the peer period compared.")
    period_start: dt.date | None
    value: float | None = Field(description="Fraction (0.3 = 30%) or ratio; null with `reason`.")
    definition_version: str
    reason: str | None
    name: str | None = Field(description="Instrument name when known.")


class PeerMetric(BaseModel):
    metric: str
    mean: float | None
    covered_peers: int
    included: list[str]
    excluded: list[str]
    excluded_reasons: dict[str, str]
    peers: list[PeerMetricValue]
    holding_value: float | None
    holding_reason: str | None
    period_end: dt.date | None
    period_start: dt.date | None
    basis: str | None
    peer_period_ends: dict[str, dt.date | None]


class ExcludedPeer(BaseModel):
    peer: str
    reason: str


class PeerMetrics(BaseModel):
    ticker: str
    known_as_of: dt.datetime | None
    confirmed_peers: list[ConfirmedPeer]
    covered_peer_metrics: dict[str, PeerMetric] = Field(
        description="By metric: revenue_growth, gross_margin, operating_margin, roic, "
        "net_debt_to_equity, free_cash_flow_conversion."
    )
    excluded_peers: list[ExcludedPeer]
    holding_period: dict[str, Any] | None = Field(
        description="The holding's latest computed period (metrics as fractions, inputs, "
        "`not_meaningful` reasons); null when none is computable."
    )
    reconciled_only: bool
    formula_definitions: dict[str, str]
    formula_detail: dict[str, Any]
    universe: str


# ------------------------------------------------------------------------ issuer IR
class IssuerIrRelease(BaseModel):
    id: int
    release_type: str
    source_url: str
    content_hash: str
    document_version_id: int | None
    published_at: dt.datetime | None
    known_at: dt.datetime
    parser_version: str | None
    analyst_count: int | None
    basis: str | None
    status: str
    metadata: Any = Field(description="Parser output (jsonb), as stored.")
    source_id: int | None
    previous_release_id: int | None
    content_type: str | None
    byte_size: int | None
    fetched_at: dt.datetime | None


class IssuerIrReleases(BaseModel):
    ticker: str
    known_as_of: dt.datetime | None
    releases: list[IssuerIrRelease]


class IssuerIrSource(BaseModel):
    id: int
    url: str
    release_type: str
    basis: str | None
    label: str | None
    active: bool
    last_checked_at: dt.datetime | None
    last_changed_at: dt.datetime | None
    last_hash: str | None
    last_status: str | None
    last_error: str | None
    created_at: dt.datetime


class IssuerIrSources(BaseModel):
    ticker: str
    sources: list[IssuerIrSource]


# -------------------------------------------------------------- company graph and deals
class BusinessNetworkEdge(BaseModel):
    id: int | str = Field(
        description='Row id; a legacy `entity_relationship` row is "legacy:<id>".'
    )
    subject_key: str
    object_key: str
    object_name: str
    edge_type: str
    direction: str
    status: str = Field(description="The status in force at `known_as_of`.")
    valid_from: dt.date | None
    valid_to: dt.date | None
    evidence_tier: str
    source: str
    source_url: str | None
    evidence_id: int | None
    passage: str | None
    known_at: dt.datetime | None
    decision_reason: str | None
    detail: Any = Field(description="Provider payload (jsonb), as stored.")


class BusinessNetworkEvent(BaseModel):
    id: int
    event_type: str
    event_state: str
    announced_at: dt.date | None
    effective_at: dt.date | None
    known_at: dt.datetime
    source: str
    source_url: str
    evidence_id: int | None
    counterparty_key: str | None
    terms: Any = Field(description="Deal terms as recorded (jsonb); numbers are often strings.")
    status: str
    thesis_kpi: Any = Field(description="Id of the linked thesis KPI, or null.")
    detail: Any


class BusinessNetworkDisclosure(BaseModel):
    id: int
    relationship: str
    direction: str
    counterparty_key: str | None
    counterparty_name: str | None
    anonymous_counterparty: bool
    reported_share: float | None = Field(description="Fraction of revenue/purchases (0.31 = 31%).")
    share_basis: str | None
    fiscal_period: str | None
    valid_from: dt.date | None
    valid_to: dt.date | None
    evidence_id: int | None
    passage: str
    source_url: str
    source: str
    status: str
    decision_reason: str | None
    known_at: dt.datetime


class BusinessNetworkPatent(BaseModel):
    family_key: str
    priority_date: dt.date | None
    first_publication_date: dt.date | None
    family_title: str | None
    cpc_codes: list[str]
    jurisdictions: list[str]
    citations_forward: int | None = Field(description="Null = the source did not supply it.")
    citations_backward: int | None
    legal_status: str
    source: str
    source_url: str
    known_at: dt.datetime
    applicant: str
    ownership_confidence: float = Field(description="0-1.")
    ownership_basis: str
    ownership_valid_from: dt.date | None
    ownership_valid_to: dt.date | None
    evidence_id: int | None
    attributed: bool = Field(description="Counted in the holding's patent trend.")


class BusinessNetwork(BaseModel):
    ticker: str
    known_as_of: dt.datetime | None
    edges: list[BusinessNetworkEdge]
    events: list[BusinessNetworkEvent]
    disclosures: list[BusinessNetworkDisclosure]
    patents: list[BusinessNetworkPatent]
    limitations: list[str]


class ConcentrationPeriod(BaseModel):
    """Shares are fractions of the issuer's own revenue / purchases."""

    fiscal_period: str
    relationship: str
    share_basis: str
    named_count: int
    anonymous_count: int
    named_share_sum: float
    anonymous_share_sum: float
    reported_share_sum: float
    missing_share_count: int
    named_missing_share_count: int
    anonymous_missing_share_count: int
    named_reported_share_sum: float | None = Field(description="Null when no share was reported.")
    anonymous_reported_share_sum: float | None
    total_reported_share_sum: float | None
    largest_share: float | None
    named_at_least: bool = Field(description="The named sum is only a lower bound.")
    anonymous_at_least: bool
    total_at_least: bool
    share_exceeds_total: bool
    open_contradictions: int
    disclosed_counterparties: int


class CustomerSupplierConcentration(BaseModel):
    ticker: str
    periods: list[ConcentrationPeriod]
    limitations: list[str]


class PatentLegalStatusCounts(BaseModel):
    application: int
    granted: int
    active: int
    expired: int
    unknown: int


class PatentTrendRow(BaseModel):
    cpc: str
    year: int = Field(description="Priority year.")
    families: int
    forward_citations: int | None = Field(description="Null when no family supplied it.")
    families_with_citation_data: int
    legal_status_counts: PatentLegalStatusCounts
    median_years_to_grant: float | None
    expiring_within_5y: int
    expired: int
    expiry_is_estimated: bool
    interpretation: str


PatentTrends = list[PatentTrendRow]


class PatentOverlapPeer(BaseModel):
    peer: str
    peer_families: int
    own_families: int
    overlap_share: float | None = Field(description="Fraction 0-1; null without families.")
    jaccard: float | None
    shared_subclasses: list[str]
    interpretation: str


class PatentCompetitorOverlap(BaseModel):
    ticker: str
    peers: list[PatentOverlapPeer]


class DealTimelineEntry(BaseModel):
    event_id: int
    state: str
    status: str
    date: dt.date | None = Field(description="Effective date, else announced date.")
    known_at: dt.datetime
    evidence_id: int | None
    source: str
    source_url: str
    evidence_tier: str | None


class DealIgnoredEvent(BaseModel):
    event_id: int
    reason: str


class DealProForma(BaseModel):
    """Dilution and leverage implied by confirmed terms; a figure is null with its missing input
    listed in `inputs_missing`. Ratios are fractions / multiples."""

    currency: str | None
    shares_issued: float | None
    share_count_increase: float | None
    new_holders_ownership: float | None
    added_gross_debt: float
    net_debt_increase: float | None
    gross_debt_after: float | None
    gross_leverage_before: float | None
    gross_leverage_after: float | None
    net_leverage_before: float | None
    net_leverage_after: float | None
    inputs_missing: list[str]


class Deal(BaseModel):
    deal_key: str
    event_type: str
    counterparty_key: str | None
    state: str | None = Field(description="Displayed state, suggested events included.")
    confirmed_state: str | None
    confirmed_terms: dict[str, Any]
    terms_provenance: dict[str, int] = Field(description="Term key to the event id that set it.")
    timeline: list[DealTimelineEntry]
    ignored: list[DealIgnoredEvent]
    pro_forma: DealProForma | None
    pro_forma_basis: str | None = None
    pro_forma_unavailable: str | None = None


class HoldingDeals(BaseModel):
    ticker: str
    known_as_of: dt.datetime | None
    deals: list[Deal]
    limitations: list[str]


class ThesisKpiVerdict(BaseModel):
    status: str
    observed_value: float | None
    threshold: float
    operator: str
    fiscal_year: int | None
    fiscal_period: str | None
    created_at: dt.datetime | None


class ThesisKpiNewsCluster(BaseModel):
    id: int
    headline: str
    status: str | None
    confidence: str | None
    severity: int | None
    event_date: dt.date | None
    first_seen: dt.datetime | None


class ThesisKpiLink(BaseModel):
    event_id: int
    event_type: str
    event_state: str
    status: str
    date: dt.date
    kpi: dict[str, Any] | None = Field(description="The thesis KPI definition; null if undefined.")
    kpi_id: str
    kpi_defined: bool
    latest_verdict: ThesisKpiVerdict | None
    news_clusters: list[ThesisKpiNewsCluster]
    can_support_breach: bool = Field(description="Confirmed and evidence-linked.")
    source_url: str


class ThesisKpiLinks(BaseModel):
    ticker: str
    links: list[ThesisKpiLink]


class RelationshipKnockOnLink(BaseModel):
    ticker: str
    counterparty_ticker: str
    relationship: str
    direction: str
    reported_share: float | None = Field(description="Fraction, as the filer reported it.")
    share_basis: str | None
    fiscal_period: str | None
    source: str = Field(description='"disclosure:<id>" or "edge:<id>".')


class RelationshipKnockOn(BaseModel):
    known_as_of: dt.datetime | None
    links: list[RelationshipKnockOnLink]
    limitations: list[str]


class FilingHit(BaseModel):
    id: int
    ticker: str
    filer_name: str | None
    form: str | None
    filed_at: dt.date | None
    url: str
    review_state: str | None
    review_note: str | None
    attempts: int | None
    disclosure_id: int | None


class SuggestedDisclosure(BaseModel):
    id: int
    ticker: str
    relationship: str
    direction: str
    counterparty_name: str | None
    anonymous_counterparty: bool
    reported_share: float | None
    share_basis: str | None
    fiscal_period: str | None
    passage: str
    source_url: str
    known_at: dt.datetime
    origin: str


class RelationshipContradiction(BaseModel):
    id: int
    ticker: str
    relationship: str
    fiscal_period: str | None
    kind: str
    disclosure_id: int
    other_disclosure_id: int | None
    detail: Any
    created_at: dt.datetime | None


class RelationshipReviewQueue(BaseModel):
    filing_hits: list[FilingHit]
    suggested_disclosures: list[SuggestedDisclosure]
    contradictions: list[RelationshipContradiction]


class GraphReconciliation(BaseModel):
    id: int
    ticker: str
    change_kind: str
    from_key: str | None
    to_key: str | None
    observed_source: str
    observed_at: dt.datetime | None
    outcome: str
    deal_event_id: int | None
    detail: Any


class GraphReconciliations(BaseModel):
    open: list[GraphReconciliation]


class IdentityDecision(BaseModel):
    id: int
    instrument_ticker: str | None
    candidate_issuer_id: int | None
    candidate_security_id: int | None
    candidate_listing_id: int | None
    decision: str
    score: Annotated[
        Decimal | None, WithJsonSchema({"anyOf": [{"type": "string"}, {"type": "null"}]})
    ] = Field(
        description="Match score 0-1. Serialised as a decimal STRING (this route has always "
        "sent it that way)."
    )
    reasons: Any = Field(description="jsonb list of reason strings.")
    decided_at: dt.datetime | None
    decided_by: uuid.UUID | None


IdentityDecisions = list[IdentityDecision]

# One evidence domain of `evidence-summary`; which model depends on the `{domain}` path value.
EvidenceDomainBody = FiledNotes | Positions | Insider | Dependencies | Deals | Consensus


# ------------------------------------------------------------ returns, listing, calendar
class ReturnResult(BaseModel):
    """Returns are fractions (0.07 = 7%)."""

    symbol: str
    source: str
    chosen: bool
    currency: str
    mic: str | None
    price_return: float | None = Field(description="Adjusted-price return; null with the reason.")
    price_return_reason: str | None
    raw_price_return: float | None
    total_return: float | None
    total_return_reason: str | None
    action_coverage: str = Field(description='"available" or "incomplete".')
    coverage: float = Field(description="Observed / expected sessions, 0-1.")
    expected_sessions: int
    observed_sessions: int
    calendar_basis: str
    missing_actions: dict[str, list[str]] = Field(
        description="By return kind (price_raw, price_adjusted, total_return)."
    )
    observations: int
    start: dt.date
    end: dt.date


class HoldingReturns(BaseModel):
    ticker: str
    known_as_of: dt.date = Field(description="The window end.")
    results: list[ReturnResult] = Field(description="Empty when `reason` is set.")
    reason: str | None = None


class ListingRef(BaseModel):
    id: int
    mic: str | None
    ticker: str | None
    currency: str | None
    status: str | None
    valid_from: dt.date | None
    valid_to: dt.date | None
    security_id: int | None
    isin: str | None


class ProviderSymbol(BaseModel):
    provider: str
    identifier: str
    match_status: str


class HoldingListing(BaseModel):
    ticker: str
    status: str = Field(description='"confirmed", "suggested" or "rejected".')
    score: float | None
    decided_at: dt.datetime | None
    listing: ListingRef | None
    evidence: Any = Field(description="What the match was derived from (jsonb).")
    provider_symbols: list[ProviderSymbol]


class CorporateAction(BaseModel):
    id: int
    symbol: str
    action_type: str
    ex_date: dt.date
    record_date: dt.date | None
    payment_date: dt.date | None
    ratio: float | None = Field(description="Split / stock-dividend ratio.")
    cash_amount: float | None = Field(description="Per share, in `currency`.")
    currency: str | None
    source: str
    source_url: str
    publisher_at: dt.datetime | None
    known_at: dt.datetime
    evidence_id: int | None
    status: str
    raw: Any = Field(description="Provider payload and recording options (jsonb).")
    decided_at: dt.datetime | None
    decision_reason: str | None


CorporateActions = list[CorporateAction]


class ExchangeSession(BaseModel):
    mic: str
    session_date: dt.date
    open_at: dt.datetime | None
    close_at: dt.datetime | None
    state: str
    source: str
    confirmed: bool
    evidence_url: str | None
    reason: str | None
    known_at: dt.datetime


ExchangeSessions = list[ExchangeSession]


class ExpectedExchangeSessions(BaseModel):
    mic: str
    start: dt.date
    end: dt.date
    count: int
    basis: str = Field(
        description='"confirmed_calendar", "partial_confirmed_calendar" or '
        '"weekdays_minus_configured_closures".'
    )
    confirmed_days: int
    days: list[dt.date]


# ------------------------------------------------------------------------------- funds
class FundFactsheet(BaseModel):
    """One factsheet version. Weights and the expense ratio are fractions (0.0007 = 0.07%)."""

    id: int
    fund_isin: str
    as_of: dt.date
    source: str
    snapshot_id: int | None
    provider: str | None
    share_class: str | None
    fund_name: str | None
    fund_currency: str | None
    total_assets: float | None
    total_assets_currency: str | None
    total_assets_as_of: dt.date | None
    expense_ratio: float | None
    charges: Any = Field(
        description="Charges by name (jsonb). The stored value is returned as the database "
        "gives it, which for this column is a JSON-encoded string."
    )
    benchmark_name: str | None
    distribution_policy: str | None
    nav_basis: str | None
    holdings_count: int | None
    equity_weight: float | None
    cash_weight: float | None
    derivative_weight: float | None
    other_weight: float | None
    total_weight: float | None
    residual_weight: float | None
    unclassified_sector_weight: float | None
    unclassified_country_weight: float | None
    reconcile_tolerance: float
    reconciles: bool | None
    lookthrough_basis: str | None
    coverage_note: str | None
    missing_fields: list[str]
    fetched_at: dt.datetime
    field_names_verified: bool = Field(
        description="False when the issuer payload field names are unverified candidates."
    )
    field_provenance: dict[str, Any]
    extraction_warnings: list[str]


FundFactsheets = list[FundFactsheet]


class CharacteristicsAuditRow(BaseModel):
    fund_key: str
    provider: str
    as_of: dt.date
    fingerprint: str
    basis: str | None
    rules: Any = Field(description="The issuer's stated P/E and P/B rules (jsonb).")
    pe: float | None
    pb: float | None
    index_pe: float | None
    index_pb: float | None
    verdict: str
    alert: bool | None
    detail: Any
    observed_at: dt.datetime


CharacteristicsAudit = list[CharacteristicsAuditRow]


class FundLookThroughAsOf(BaseModel):
    as_of: dt.date
    snapshot_ids: dict[str, int] = Field(description="Fund ISIN to the snapshot used.")
    report: LookThroughReport


# ------------------------------------------------------------------------ verification
class ZeroCostFinding(BaseModel):
    provider: str
    endpoint: str
    severity: str = Field(description='"violation" or "warning".')
    code: str
    message: str


class ZeroCostGate(BaseModel):
    passed: bool
    checked: int
    free_available: int
    disabled_ok: int
    gated_unverified: int
    violation_count: int
    warning_count: int
    warnings_by_code: dict[str, int]
    findings: list[ZeroCostFinding]


class DomainGate(BaseModel):
    allowed: bool
    threshold_version: str
    samples: int
    distinct_subjects: int
    failures: int
    reasons: list[str]


class ConclusionGate(BaseModel):
    domains: list[str]
    allowed: bool


class DomainGates(BaseModel):
    domains: dict[str, DomainGate]
    conclusions: dict[str, ConclusionGate]


class SignalGate(BaseModel):
    publishable: bool
    sample_size: int
    evidence_precision: float | None = Field(description="Fraction 0-1; null if never audited.")
    pit_checked: int
    pit_violations: int
    audited_at: dt.datetime | None
    reviewer: str | None
    reasons: list[str]


SignalGates = dict[str, SignalGate]


class MeasuredCoverageRow(BaseModel):
    jurisdiction: str
    domain: str
    metric: str
    observations: int
    entities: int | None = Field(description="Null where not applicable (macro series).")
    unknown_values: int
    sources: int
    earliest_period: dt.date | None
    latest_period: dt.date | None
    newest_known_at: dt.datetime | None


MeasuredCoverage = list[MeasuredCoverageRow]


class ShadowReport(BaseModel):
    generated_at: dt.datetime
    report: Any = Field(description="The shadow report (jsonb), as stored.")
    markdown: str


# --------------------------------------------------------------- exposure context
class ExposureObservation(BaseModel):
    id: int
    provider: str
    dataset: str
    metric: str
    period_end: dt.date | None
    value: float | None
    text_value: str | None
    unit: str | None
    currency: str | None
    source_url: str
    known_at: dt.datetime
    raw: Any = Field(description="Provider payload (jsonb), as stored.")


class ExposureContext(BaseModel):
    ticker: str
    exposure_assumptions: list[Exposure] = Field(
        description="The exposures entered in the holding profile (`share` is a 0-1 fraction)."
    )
    observations: list[ExposureObservation] = Field(description="Contextual price series only.")
    limitations: list[str]


# ----------------------------------------------------------- write-route acknowledgements
class RecordedId(BaseModel):
    id: int = Field(description="Id of the row written (an existing row for a repeat post).")


class ExchangeSessionRecorded(BaseModel):
    session_date: dt.date


class PeerStatusChanged(BaseModel):
    peer: dict[str, Any] | None = Field(
        description="`{id, ticker, peer_ticker, status}` after the change; null for an unknown id."
    )


class CompanyEdgeRecorded(BaseModel):
    id: int
    status: str


class PatentFamilyRecorded(BaseModel):
    family_id: int


class CorporateActionRecorded(BaseModel):
    id: int
    status: str
    status_applied: bool = Field(description="False when an earlier decision kept its status.")


class CorporateActionDecided(BaseModel):
    id: int
    previous_status: str
    status: str


class CorporateActionCoverageConfirmed(BaseModel):
    symbol: str
    covered_through: dt.date
    state: str


class ListingDerived(BaseModel):
    ticker: str
    status: str = Field(description='"confirmed", "suggested", "unchanged" or "skipped".')
    listing_id: int | None
    reason: str | None
    score: float | None


class ListingConfirmed(BaseModel):
    ticker: str
    listing_id: int
    status: str


class GraphDecisionResult(BaseModel):
    """Edge and company-event decisions."""

    id: int
    status: str
    evidence_tier: str | None


class DisclosureDecisionResult(BaseModel):
    id: int
    status: str


class ContradictionResolved(BaseModel):
    id: int
    status: str


class ProviderBlockReset(BaseModel):
    provider: str
    endpoint: str
    state: str
    hard_blocked: bool


class FactsheetStored(BaseModel):
    id: int


class SnapshotsPinned(BaseModel):
    pinned: list[int] = Field(description="Snapshot ids now protected from pruning.")


# -------------------------------------------------- provider refreshes (need a provider key)
class AlphaExposureRefreshed(BaseModel):
    ticker: str
    series: str
    observations_stored: int


class DiffbotRefreshed(BaseModel):
    ticker: str
    suggested_edges: int


class PdlRefreshed(BaseModel):
    ticker: str
    matched_records: int
    skipped: str | None = Field(default=None, description="Why no lookup was spent, when set.")


class EpoPatentsRefreshed(BaseModel):
    ticker: str
    patent_candidates: int


class AlphaStatementRefreshed(BaseModel):
    ticker: str
    statement: str = Field(description="INCOME_STATEMENT, BALANCE_SHEET or CASH_FLOW.")
    facts: int = Field(description="New fact rows stored.")
    fields: int = Field(description="Fields the provider returned.")
    symbol: str


class FinnhubResearchRefreshed(BaseModel):
    ticker: str
    symbol: str
    peers: int
    earnings: int


class FilingHitReviewed(BaseModel):
    hit_id: int
    state: str = Field(description='"pending", "failed", "dismissed", "fetched" or "resolved".')
    disclosures: list[int] | None = Field(
        default=None, description="Suggested disclosure ids; absent when the fetch failed."
    )
