"""Response models for routes that used to return untyped dicts.

Wire-format rules (the web parsers already consume the current JSON):

* asyncpg `numeric` columns arrive as `Decimal`, which FastAPI's dict path serialised as a JSON
  number. A pydantic `Decimal` field would serialise as a string, so numeric fields are
  annotated `float` here and stay JSON numbers.
* Dates and datetimes stay ISO strings.
* Free-form jsonb columns are `Any` (they are decoded at the read site; shape is the data's).
* Every model allows extra keys, so a producer adding a key never turns into a 500.
* Routes whose producer omits a key (rather than sending null) use
  `response_model_exclude_unset=True`, so the key is still omitted.
"""

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict

from steward.lookthrough.read import LookThroughReport


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")


# --- company research / exposure context -------------------------------------------------------


class CompanyResearchObservation(_Model):
    """One `company_research_observation` row; `raw` is the decoded jsonb payload."""

    id: int
    provider: str
    dataset: str
    metric: str
    period_end: dt.date | None = None
    value: float | None = None
    text_value: str | None = None
    unit: str | None = None
    currency: str | None = None
    source_url: str
    published_at: dt.datetime | None = None
    known_at: dt.datetime
    match_confidence: float | None = None
    raw: Any = None


class CompanyResearchObservationsResponse(_Model):
    """`GET /holdings/{ticker}/company-research-observations`."""

    ticker: str
    known_as_of: dt.datetime | None = None
    observations: list[CompanyResearchObservation]


class ExposureObservation(_Model):
    """An exposure-context price-series observation (the research row without published_at)."""

    id: int
    provider: str
    dataset: str
    metric: str
    period_end: dt.date | None = None
    value: float | None = None
    text_value: str | None = None
    unit: str | None = None
    currency: str | None = None
    source_url: str
    known_at: dt.datetime
    raw: Any = None


class ExposureContextResponse(_Model):
    """`GET /holdings/{ticker}/exposure-context`. Assumptions are the holding-profile
    exposures dumped as JSON (a config shape, free-form here)."""

    ticker: str
    exposure_assumptions: list[dict[str, Any]]
    observations: list[ExposureObservation]
    limitations: list[str]


class AlphaExposureRefreshResponse(_Model):
    ticker: str
    series: str
    observations_stored: int


class DiffbotRefreshResponse(_Model):
    ticker: str
    suggested_edges: int


class PdlRefreshResponse(_Model):
    """`skipped` is present only when the refresh was skipped (use exclude_unset)."""

    ticker: str
    matched_records: int
    skipped: str | None = None


class EpoRefreshResponse(_Model):
    ticker: str
    patent_candidates: int


# --- exchange sessions ---------------------------------------------------------------------------


class ExchangeSessionRow(_Model):
    """One stored `exchange_session` row."""

    mic: str
    session_date: dt.date
    open_at: dt.datetime | None = None
    close_at: dt.datetime | None = None
    state: str
    source: str
    confirmed: bool
    evidence_url: str | None = None
    reason: str | None = None
    known_at: dt.datetime


class ExpectedSessionsResponse(_Model):
    """`GET /exchange-sessions/{mic}/expected`. `basis` is one of confirmed_calendar,
    partial_confirmed_calendar, weekdays_minus_configured_closures."""

    mic: str
    start: dt.date
    end: dt.date
    count: int
    basis: str
    confirmed_days: int
    days: list[dt.date]


class RecordedExchangeSessionResponse(_Model):
    session_date: dt.date


# --- peer set ---------------------------------------------------------------------------


class PeerRow(_Model):
    """A peer as known at `known_as_of`; `status` is the status in force then
    (suggested, confirmed or rejected)."""

    id: int
    peer_ticker: str
    basis: str
    status: str
    evidence_id: int | None = None
    source: str
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    known_at: dt.datetime
    detail: Any = None
    status_history: Any = None


class PeerSetResponse(_Model):
    """`GET /holdings/{ticker}/peer-set`."""

    ticker: str
    known_as_of: dt.datetime | None = None
    peers: list[PeerRow]


class RecordedPeerResponse(_Model):
    id: int


# --- holding returns --------------------------------------------------------------------


class MissingActions(_Model):
    price_raw: list[str] = []
    price_adjusted: list[str] = []
    total_return: list[str] = []


class HoldingReturnResult(_Model):
    """Returns of the chosen price series over one window. Returns and coverage are fractions
    (JSON numbers); a null return carries its reason."""

    symbol: str
    source: str
    chosen: bool
    currency: str | None = None
    mic: str | None = None
    price_return: float | None = None
    price_return_reason: str | None = None
    raw_price_return: float | None = None
    total_return: float | None = None
    total_return_reason: str | None = None
    action_coverage: str
    coverage: float
    expected_sessions: int
    observed_sessions: int
    calendar_basis: str
    missing_actions: MissingActions
    observations: int
    start: dt.date
    end: dt.date


class HoldingReturnsResponse(_Model):
    """`GET /holdings/{ticker}/returns` (use exclude_unset: `reason` appears only when there is
    no stored price series)."""

    ticker: str
    known_as_of: dt.date
    results: list[HoldingReturnResult]
    reason: str | None = None


# --- corporate-action coverage and listing -----------------------------------------------------


class CoverageEntry(_Model):
    provider: str
    state: str
    latest_period: dt.date | None = None
    checked_at: dt.datetime
    reason: str | None = None
    owner_verified: bool


class ActionCoverageResponse(_Model):
    """`GET /holdings/{ticker}/corporate-actions/coverage`."""

    symbol: str
    entries: list[CoverageEntry]


class ActionCoverageConfirmedResponse(_Model):
    symbol: str
    covered_through: dt.date
    state: str


class ListingRef(_Model):
    """The canonical listing; every column but `id` can be null (left join)."""

    id: int
    mic: str | None = None
    ticker: str | None = None
    currency: str | None = None
    status: str | None = None
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    security_id: int | None = None
    isin: str | None = None


class ProviderSymbol(_Model):
    provider: str
    identifier: str
    match_status: str | None = None


class ListingOverviewResponse(_Model):
    """`GET /holdings/{ticker}/listing`. `evidence` is the jsonb match evidence."""

    ticker: str
    status: str
    score: float | None = None
    decided_at: dt.datetime | None = None
    listing: ListingRef | None = None
    evidence: Any = None
    provider_symbols: list[ProviderSymbol]


class DerivedListingResponse(_Model):
    ticker: str
    status: str
    listing_id: int | None = None
    reason: str | None = None
    score: float | None = None


class ConfirmedListingResponse(_Model):
    ticker: str
    listing_id: int
    status: str


# --- funds ------------------------------------------------------------------------------


class LookThroughAsOfResponse(_Model):
    """`GET /funds/look-through`: the base report plus the snapshot ids it rests on."""

    as_of: dt.date
    snapshot_ids: dict[str, int]
    report: LookThroughReport


class FactsheetRow(_Model):
    """A `fund_factsheet` row (minus the raw `evidence` blob) with the evidence-derived
    `field_names_verified`, `field_provenance` and `extraction_warnings`. Weights and charges
    are fractions (JSON numbers)."""

    id: int
    fund_isin: str
    as_of: dt.date
    source: str
    snapshot_id: int | None = None
    provider: str | None = None
    share_class: str | None = None
    fund_name: str | None = None
    fund_currency: str | None = None
    total_assets: float | None = None
    total_assets_currency: str | None = None
    total_assets_as_of: dt.date | None = None
    expense_ratio: float | None = None
    charges: Any = None
    benchmark_name: str | None = None
    distribution_policy: str | None = None
    nav_basis: str | None = None
    holdings_count: int
    equity_weight: float | None = None
    cash_weight: float | None = None
    derivative_weight: float | None = None
    other_weight: float | None = None
    total_weight: float | None = None
    residual_weight: float | None = None
    unclassified_sector_weight: float | None = None
    unclassified_country_weight: float | None = None
    reconcile_tolerance: float
    reconciles: bool | None = None
    lookthrough_basis: str
    coverage_note: str | None = None
    missing_fields: list[str] = []
    fetched_at: dt.datetime
    field_names_verified: bool
    field_provenance: Any = None
    extraction_warnings: list[Any] = []


class CharacteristicsAuditRow(_Model):
    """One parser-drift audit observation of issuer fund P/E and P/B."""

    fund_key: str
    provider: str
    as_of: dt.date
    fingerprint: str
    basis: str
    rules: Any = None
    pe: float | None = None
    pb: float | None = None
    index_pe: float | None = None
    index_pb: float | None = None
    verdict: str
    alert: bool
    detail: Any = None
    observed_at: dt.datetime


class FactsheetStoredResponse(_Model):
    id: int


class PinnedSnapshotsResponse(_Model):
    pinned: list[int]
