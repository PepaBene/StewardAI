"""Owner-only read models for phase 3-5 price, fundamentals and company research data."""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlsplit

import asyncpg
import httpx
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.auth import Owner
from api.db import Pool
from api.response_models import (
    ActionCoverageConfirmedResponse,
    ActionCoverageResponse,
    AlphaExposureRefreshResponse,
    CompanyResearchObservationsResponse,
    ConfirmedListingResponse,
    DerivedListingResponse,
    DiffbotRefreshResponse,
    EpoRefreshResponse,
    ExchangeSessionRow,
    ExpectedSessionsResponse,
    ExposureContextResponse,
    HoldingReturnsResponse,
    ListingOverviewResponse,
    PdlRefreshResponse,
    PeerSetResponse,
    RecordedExchangeSessionResponse,
    RecordedPeerResponse,
)
from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import JobsConfig
from steward.config.store import ConfigConflictError, StoredConfig, load_active, save_config
from steward.db_json import decode_rows, decode_structured_rows
from steward.disclosure.statement_read import with_valuation_gate
from steward.free_source_reads import (
    business_network_payload,
    company_research_rows,
    financial_history_rows,
    forward_payload,
    forward_rows,
    patent_trend_rows,
)
from steward.free_sources import (
    ForwardValue,
    collect_alpha_vantage_statement,
    collect_finnhub_research,
    peer_status_as_of,
    set_peer_status,
    store_company_edge,
    store_forward_value,
    upsert_peer,
)
from steward.fundamentals.ir import IrError, actual_vs_consensus, assert_fetchable
from steward.graph.deals import DealTransitionError, deal_key, record_deal_event
from steward.graph.decisions import GraphDecisionError, decide_disclosure
from steward.graph.edges import upsert_event
from steward.graph.kpi import validate_thesis_kpi
from steward.graph.patents import store_patent_family as _store_patent
from steward.graph.relationships import concentration, detect_contradictions, upsert_disclosure
from steward.macro.exposure_series import linked_series
from steward.market_analysis import financial_peer_metrics
from steward.peer_metrics import FORMULA_DETAIL, FORMULAS, peer_comparison
from steward.reference.calendar import expected_sessions
from steward.reference.listings import (
    ListingDecisionError,
    confirm_listing,
    derive_listings,
    holding_symbol_names,
    listing_overview,
)
from steward.reference.price_diagnostics import holding_price_diagnostics
from steward.reference.price_series import holding_symbols
from steward.reference.returns import actions_complete, compute_for, return_inputs
from steward.settings import get_settings
from steward.sources.company_research import (
    collect_alpha_exposure,
    collect_diffbot_candidates,
    collect_epo_patents,
    collect_pdl_company,
    pdl_skip_reason,
)
from steward.sources.free_market import FreeSourceError
from steward.sources.governor import QuotaExceeded

router = APIRouter()
TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


class RelationshipReview(BaseModel):
    relationship: Literal["customer", "supplier", "procurement_award"]
    direction: Literal["issuer_sells_to", "issuer_buys_from", "award_to_issuer", "unknown"]
    counterparty_key: str | None = None
    counterparty_name: str | None = None
    anonymous_counterparty: bool = False
    reported_share: Decimal | None = Field(default=None, ge=0, le=1)
    share_basis: str | None = None
    fiscal_period: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    evidence_id: int | None = None
    passage: str = Field(min_length=20, max_length=10000)
    source_url: str = Field(min_length=8, max_length=2000)
    source: str = Field(min_length=2, max_length=80)
    status: Literal["suggested", "confirmed", "rejected", "disputed"] = "suggested"
    decision_reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_review(self) -> "RelationshipReview":
        if self.anonymous_counterparty and self.counterparty_name:
            raise ValueError("anonymous counterparties cannot be assigned a name")
        if not self.anonymous_counterparty and not (
            self.counterparty_name or self.counterparty_key
        ):
            raise ValueError("a named counterparty or explicit anonymous flag is required")
        if self.valid_to and self.valid_from and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot precede valid_from")
        if self.status == "confirmed" and (not self.evidence_id and len(self.passage.strip()) < 40):
            raise ValueError("confirmation requires a source passage or linked evidence")
        return self


class CorporateActionInput(BaseModel):
    action_type: Literal[
        "split",
        "cash_dividend",
        "stock_dividend",
        "symbol_change",
        "merger",
        "spin_off",
        "delisting",
    ]
    symbol: str = Field(pattern=r"^[A-Za-z0-9.^_-]{1,40}$")
    ex_date: date
    record_date: date | None = None
    payment_date: date | None = None
    ratio: Decimal | None = Field(default=None, gt=0)
    cash_amount: Decimal | None = Field(default=None, ge=0)
    # ISO 4217, or a minor unit a dividend is declared in (pence GBp, cents ZAc, agorot ILa).
    currency: str | None = Field(default=None, pattern=r"^([A-Z]{3}|GBp|ZAc|ILa)$")
    # "ordinary_share": the dividend is per ordinary share and the holding is an ADS (the
    # profile's ADS ratio converts it); "split_adjusted": a provider already restated the
    # amount for later splits.
    per: Literal["listed_line", "ordinary_share"] = "listed_line"
    amount_basis: Literal["as_declared", "split_adjusted"] = "as_declared"
    source: str = Field(min_length=2, max_length=80)
    source_url: str = Field(min_length=8, max_length=2000)
    publisher_at: datetime | None = None
    evidence_id: int | None = None
    status: Literal["suggested", "confirmed", "rejected"] = "suggested"
    raw: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_action(self) -> "CorporateActionInput":
        if self.action_type == "split" and self.ratio is None:
            raise ValueError("split ratio is required")
        if self.action_type == "stock_dividend" and self.ratio is None:
            # A stock dividend is shares for shares: a cash amount cannot stand in for the
            # ratio (the return would silently skip it).
            raise ValueError("stock dividend ratio is required (a cash amount alone is not enough)")
        if self.action_type == "cash_dividend":
            if self.cash_amount is None:
                raise ValueError("cash dividend amount is required")
            if self.currency is None:
                raise ValueError("cash dividend currency is required")
        if self.status == "confirmed" and self.evidence_id is None:
            raise ValueError("confirmed actions require linked source evidence")
        return self


class CorporateActionDecision(BaseModel):
    """The owner's explicit decision on one recorded action: confirm it, reject it, or retract
    it back to review (`suggested`). Only this path changes a decided action."""

    decision: Literal["confirmed", "rejected", "suggested"]
    reason: str = Field(min_length=10, max_length=2000)
    evidence_id: int | None = None


class ListingDecision(BaseModel):
    decision: Literal["confirmed", "rejected"]
    reason: str = Field(min_length=10, max_length=2000)
    listing_id: int | None = Field(
        default=None, description="Another listing of the same security, instead of the derived."
    )


class ExchangeSessionInput(BaseModel):
    mic: str = Field(pattern=r"^[A-Z0-9]{4}$")
    session_date: date
    state: Literal["open", "half_day", "closed"]
    open_at: datetime | None = None
    close_at: datetime | None = None
    source: str = Field(min_length=2, max_length=80)
    evidence_url: str = Field(min_length=8, max_length=2000)
    reason: str | None = Field(
        default=None, max_length=300, description="e.g. ad hoc closure, early close, holiday."
    )

    @model_validator(mode="after")
    def validate_times(self) -> "ExchangeSessionInput":
        if self.state in {"open", "half_day"} and (self.open_at is None or self.close_at is None):
            raise ValueError("open sessions require their UTC open and close timestamps")
        if self.open_at and self.close_at and self.close_at <= self.open_at:
            raise ValueError("exchange close must be after open")
        return self


class PeerInput(BaseModel):
    peer_ticker: str = Field(pattern=r"^[A-Za-z0-9._-]{1,32}$")
    basis: str = Field(min_length=2, max_length=80)
    source: str = Field(min_length=2, max_length=80)
    status: Literal["suggested", "confirmed", "rejected"] = "suggested"
    evidence_id: int | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    detail: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_peer(self) -> "PeerInput":
        if self.status == "confirmed" and self.evidence_id is None:
            raise ValueError("confirmed peer sets require supporting evidence")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("peer validity end precedes start")
        return self


class CorporateActionCoverageInput(BaseModel):
    through: date
    source_url: str = Field(min_length=8, max_length=2000)
    reason: str = Field(min_length=10, max_length=2000)


class ForwardObservationInput(BaseModel):
    kind: Literal[
        "company_guidance",
        "owner_scenario",
        "published_analyst_consensus",
        "event_calendar_estimate",
    ]
    provider: str | None = Field(default=None, max_length=80)
    metric: str = Field(min_length=1, max_length=120)
    basis: str | None = Field(default=None, max_length=120)
    fiscal_period: str = Field(min_length=1, max_length=40)
    period_end: date | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    value: Decimal | None = None
    low: Decimal | None = None
    high: Decimal | None = None
    analyst_count: int | None = Field(default=None, ge=0)
    vintage: str | None = Field(default=None, max_length=80)
    published_at: datetime | None = None
    source_url: str = Field(min_length=8, max_length=2000)
    evidence_id: int | None = None
    raw: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_forward(self) -> "ForwardObservationInput":
        if self.value is None and self.low is None and self.high is None:
            raise ValueError("a value or range is required")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("estimate low exceeds high")
        if self.kind == "published_analyst_consensus" and self.analyst_count is None:
            raise ValueError("analyst consensus requires the reported analyst count")
        if self.kind == "owner_scenario" and self.provider not in (None, "owner"):
            raise ValueError("owner scenarios cannot be attributed to a vendor")
        return self


class CompanyEdgeInput(BaseModel):
    subject_key: str = Field(min_length=1, max_length=120)
    object_key: str = Field(min_length=1, max_length=160)
    object_name: str = Field(min_length=1, max_length=240)
    edge_type: Literal[
        "partner",
        "acquirer",
        "acquired",
        "investor",
        "investment",
        "product",
        "technology",
        "executive",
        "customer",
        "supplier",
        "competitor",
        "parent",
        "subsidiary",
    ]
    direction: Literal["outgoing", "incoming", "symmetric"]
    source: str = Field(min_length=2, max_length=80)
    source_url: str = Field(min_length=8, max_length=2000)
    evidence_tier: (
        Literal["primary", "regulatory", "owner_review", "vendor", "community"] | None
    ) = Field(
        default=None,
        description="Ignored. The server derives the tier from the linked document's source "
        "class (or the writing source); rejecting or disputing an edge uses the decision "
        "endpoint.",
    )
    status: Literal["suggested", "confirmed"] = "suggested"
    valid_from: date | None = None
    valid_to: date | None = None
    evidence_id: int | None = None
    passage: str | None = Field(default=None, max_length=10000)
    detail: dict[str, object] = Field(default_factory=dict)


class CompanyEventInput(BaseModel):
    event_type: Literal[
        "deal", "financing", "leadership", "guidance", "relationship", "product", "patent"
    ]
    event_state: Literal[
        "announced",
        "amended",
        "proposed",
        "approved",
        "closed",
        "terminated",
        "reversed",
        "unknown",
    ]
    announced_at: date | None = None
    effective_at: date | None = None
    source: str = Field(min_length=2, max_length=80)
    source_url: str = Field(min_length=8, max_length=2000)
    evidence_id: int | None = None
    counterparty_key: str | None = Field(default=None, max_length=160)
    terms: dict[str, object] = Field(default_factory=dict)
    status: Literal["suggested", "confirmed"] = "suggested"
    thesis_kpi: str | None = Field(default=None, max_length=120)
    detail: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_event(self) -> "CompanyEventInput":
        if self.status == "confirmed" and self.evidence_id is None:
            raise ValueError("confirmed company events require linked primary evidence")
        return self


class PatentFamilyInput(BaseModel):
    family_key: str = Field(min_length=1, max_length=160)
    priority_date: date | None = None
    title: str | None = Field(default=None, max_length=2000)
    cpc_codes: list[str] = Field(default_factory=list, max_length=100)
    jurisdictions: list[str] = Field(default_factory=list, max_length=100)
    forward_citations: int = Field(default=0, ge=0)
    backward_citations: int = Field(default=0, ge=0)
    legal_status: Literal["application", "granted", "active", "expired", "unknown"] = "unknown"
    source: str = Field(min_length=2, max_length=80)
    source_url: str = Field(min_length=8, max_length=2000)
    applicant: str = Field(min_length=1, max_length=240)
    ownership_confidence: Decimal = Field(ge=0, le=1)
    ownership_basis: Literal["direct", "dated_subsidiary_path", "suggested"] = "suggested"
    ownership_valid_from: date | None = None
    evidence_id: int | None = None
    raw: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ownership(self) -> "PatentFamilyInput":
        if self.ownership_basis == "direct" and self.evidence_id is None:
            raise ValueError("direct patent attribution requires primary ownership evidence")
        if self.ownership_basis == "dated_subsidiary_path" and self.priority_date is None:
            raise ValueError(
                "subsidiary attribution needs the family priority date, which a confirmed "
                "subsidiary edge must cover"
            )
        return self


class IssuerIRReleaseInput(BaseModel):
    release_type: Literal["guidance", "consensus", "segment_consensus", "capital_return"]
    source_url: str = Field(min_length=8, max_length=2000)
    content_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    document_version_id: int | None = None
    published_at: datetime | None = None
    parser_version: str | None = Field(default=None, max_length=80)
    analyst_count: int | None = Field(default=None, ge=0)
    basis: str | None = Field(default=None, max_length=120)
    status: Literal["available", "unavailable", "access_unverified", "parse_failed"] = "available"
    metadata: dict[str, object] = Field(default_factory=dict)


class AlphaExposureRequest(BaseModel):
    function: Literal[
        "WTI",
        "BRENT",
        "NATURAL_GAS",
        "COPPER",
        "ALUMINUM",
        "WHEAT",
        "CORN",
        "COFFEE",
        "SUGAR",
        "FX_DAILY",
    ]
    series_name: str = Field(min_length=1, max_length=80)
    from_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    to_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


def _holding_research_profile(
    stored: StoredConfig[HoldingProfilesConfig] | None, ticker: str
) -> HoldingProfile | None:
    config = stored.body if stored else HoldingProfilesConfig()
    return config.for_ticker(ticker)


def _profile_domain(profile: HoldingProfile) -> str | None:
    urls = [profile.ir_url, *profile.results_urls]
    for url in urls:
        host = urlsplit(str(url)).hostname if url else None
        if host:
            return host.removeprefix("www.").lower()
    return profile.extra_domains[0] if profile.extra_domains else None


@router.get(
    "/holdings/{ticker}/company-research-observations",
    tags=["holdings"],
    response_model=CompanyResearchObservationsResponse,
)
async def company_research_observations(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
) -> CompanyResearchObservationsResponse:
    rows = await company_research_rows(pool, ticker, as_of)
    return cast(
        CompanyResearchObservationsResponse,
        {"ticker": ticker, "known_as_of": as_of, "observations": rows},
    )


@router.post(
    "/holdings/{ticker}/exposure-context/alpha-vantage",
    tags=["holdings"],
    response_model=AlphaExposureRefreshResponse,
)
async def refresh_alpha_exposure(
    ticker: TickerPath, body: AlphaExposureRequest, pool: Pool, _owner: Owner
) -> AlphaExposureRefreshResponse:
    settings = get_settings()
    if not settings.alpha_vantage_api_key:
        raise HTTPException(409, "ALPHA_VANTAGE_API_KEY is not configured")
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profile = _holding_research_profile(stored, ticker)
    if profile is None:
        raise HTTPException(422, "a holding profile with an explicit exposure is required")
    if body.function == "FX_DAILY":
        pair = f"{body.from_currency}/{body.to_currency}"
        exposure_mapped = (
            any(
                exposure.kind == "revenue_currency"
                and exposure.name.casefold()
                in {body.from_currency.casefold(), body.to_currency.casefold(), pair.casefold()}
                for exposure in profile.exposures
            )
            if body.from_currency and body.to_currency
            else False
        )
        if not exposure_mapped:
            raise HTTPException(422, "FX pair must match a configured revenue-currency exposure")
    else:
        exposure_mapped = any(
            exposure.kind == "commodity_input"
            and exposure.name.casefold() == body.series_name.casefold()
            for exposure in profile.exposures
        )
        if not exposure_mapped:
            raise HTTPException(422, "commodity must match a configured input-cost exposure")
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            count = await collect_alpha_exposure(
                pool,
                http,
                ticker=ticker,
                function=body.function,
                series_name=body.series_name,
                api_key=settings.alpha_vantage_api_key.get_secret_value(),
                from_currency=body.from_currency,
                to_currency=body.to_currency,
            )
    except QuotaExceeded as exc:
        raise HTTPException(
            429, "Alpha Vantage shared daily free request budget exhausted"
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FreeSourceError as exc:
        raise HTTPException(502, "Alpha Vantage exposure series request failed") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Alpha Vantage network request failed") from exc
    return cast(
        AlphaExposureRefreshResponse,
        {"ticker": ticker, "series": body.series_name, "observations_stored": count},
    )


@router.get(
    "/holdings/{ticker}/exposure-context", tags=["holdings"], response_model=ExposureContextResponse
)
async def exposure_context(
    ticker: TickerPath, pool: Pool, _owner: Owner
) -> ExposureContextResponse:
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profile = _holding_research_profile(stored, ticker)
    observations = await pool.fetch(
        """select id,provider,dataset,metric,period_end,value,text_value,unit,currency,
                  source_url,known_at,raw from public.company_research_observation
           where ticker=$1 and dataset='exposure_context'
           order by metric,period_end desc,known_at desc""",
        ticker,
    )
    linked, unlinked = await linked_series(pool, profile.exposures if profile else [])
    payload: dict[str, Any] = {
        "ticker": ticker,
        "exposure_assumptions": [e.model_dump(mode="json") for e in profile.exposures]
        if profile
        else [],
        "observations": decode_structured_rows(observations, ("raw",)),
        "linked_series": linked,
        "unlinked": unlinked,
        "limitations": [
            "Price series are contextual inputs, not estimates of issuer margin sensitivity.",
            "Exposure shares are shown only when entered in the holding profile.",
        ],
    }
    return cast(ExposureContextResponse, payload)


class RemoveExposureRequest(BaseModel):
    kind: str
    name: str


class RemoveExposureResponse(BaseModel):
    ticker: str
    removed: int


@router.post(
    "/holdings/{ticker}/exposures/remove",
    tags=["holdings"],
    response_model=RemoveExposureResponse,
)
async def remove_exposure(
    ticker: TickerPath, body: RemoveExposureRequest, pool: Pool, owner: Owner
) -> RemoveExposureResponse:
    """Overturn an exposure the AI added (or remove one of your own). The builder never adds a
    removed row back: it remembers what it wrote and sees that the row is gone."""
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profile = _holding_research_profile(stored, ticker)
    if stored is None or profile is None:
        raise HTTPException(404, "no holding profile")
    key = (body.kind, body.name.casefold())
    kept = [e for e in profile.exposures if (e.kind, e.name.casefold()) != key]
    removed = len(profile.exposures) - len(kept)
    if removed:
        updated = stored.body.model_copy(
            update={
                "profiles": [
                    p.model_copy(update={"exposures": kept}) if p.ticker == ticker else p
                    for p in stored.body.profiles
                ]
            }
        )
        try:
            async with pool.acquire() as conn:
                await save_config(
                    conn,
                    "holding_profiles",
                    updated,
                    created_by=owner.user_id,
                    note=f"Removed exposure {body.kind}:{body.name} from {ticker}",
                    expected_active_id=stored.id,
                )
        except ConfigConflictError as exc:
            raise HTTPException(409, "the profiles changed; reload and try again") from exc
        if any(e.origin == "ai" and (e.kind, e.name.casefold()) == key for e in profile.exposures):
            # Remember it next to the AI's own record, so no later filing adds it back.
            await pool.execute(
                """update public.filing_extraction set detail = jsonb_set(
                     coalesce(detail, '{}'::jsonb), '{tombstones}',
                     to_jsonb(array(select distinct x from unnest(
                       array(select jsonb_array_elements_text(
                         coalesce(detail->'tombstones', '[]'::jsonb))) || $2::text) x)))
                   where ticker = $1 and kind = 'exposure' and accession = (
                     select accession from public.filing_extraction
                     where ticker = $1 and kind = 'exposure' order by extracted_at desc limit 1)""",
                ticker,
                f"{key[0]}:{key[1]}",
            )
    return RemoveExposureResponse(ticker=ticker, removed=removed)


@router.post(
    "/holdings/{ticker}/company-research/diffbot",
    tags=["holdings"],
    response_model=DiffbotRefreshResponse,
)
async def refresh_diffbot_company_research(
    ticker: TickerPath, pool: Pool, _owner: Owner
) -> DiffbotRefreshResponse:
    settings = get_settings()
    if not settings.diffbot_api_key:
        raise HTTPException(409, "DIFFBOT_API_KEY is not configured")
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profile = _holding_research_profile(stored, ticker)
    domain = _profile_domain(profile) if profile else None
    if not domain:
        raise HTTPException(422, "a resolved company website domain is required")
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            count = await collect_diffbot_candidates(
                pool,
                http,
                ticker=ticker,
                domain=domain,
                api_key=settings.diffbot_api_key.get_secret_value(),
            )
    except QuotaExceeded as exc:
        raise HTTPException(429, "Diffbot monthly free credit budget exhausted") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FreeSourceError as exc:
        raise HTTPException(502, "Diffbot request failed; inspect source access status") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Diffbot network request failed") from exc
    return cast(DiffbotRefreshResponse, {"ticker": ticker, "suggested_edges": count})


@router.post(
    "/holdings/{ticker}/company-research/pdl",
    tags=["holdings"],
    response_model=PdlRefreshResponse,
    response_model_exclude_unset=True,
)
async def refresh_pdl_company_research(
    ticker: TickerPath, pool: Pool, _owner: Owner
) -> PdlRefreshResponse:
    settings = get_settings()
    if not settings.people_data_labs_api_key:
        raise HTTPException(409, "PEOPLE_DATA_LABS_API_KEY is not configured")
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profile = _holding_research_profile(stored, ticker)
    domain = _profile_domain(profile) if profile else None
    if not domain:
        raise HTTPException(422, "a resolved company website domain is required")
    skipped = await pdl_skip_reason(pool, ticker)
    if skipped is not None:
        return cast(
            PdlRefreshResponse, {"ticker": ticker, "matched_records": 0, "skipped": skipped}
        )
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            count = await collect_pdl_company(
                pool,
                http,
                ticker=ticker,
                domain=domain,
                api_key=settings.people_data_labs_api_key.get_secret_value(),
            )
    except QuotaExceeded as exc:
        raise HTTPException(429, "PDL monthly free credit budget exhausted") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FreeSourceError as exc:
        raise HTTPException(
            502, "PDL company enrichment failed; inspect source access status"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "PDL network request failed") from exc
    return cast(PdlRefreshResponse, {"ticker": ticker, "matched_records": count})


@router.post(
    "/holdings/{ticker}/patent-families/epo-ops/refresh",
    tags=["holdings"],
    response_model=EpoRefreshResponse,
)
async def refresh_epo_patents(ticker: TickerPath, pool: Pool, _owner: Owner) -> EpoRefreshResponse:
    settings = get_settings()
    if not settings.epo_ops_client_id or not settings.epo_ops_client_secret:
        raise HTTPException(409, "EPO_OPS_CLIENT_ID and EPO_OPS_CLIENT_SECRET are required")
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profile = _holding_research_profile(stored, ticker)
    aliases = tuple(
        dict.fromkeys(
            [profile.company_name, *profile.aliases]
            if profile and profile.company_name
            else profile.aliases
            if profile
            else []
        )
    )
    aliases = tuple(alias for alias in aliases if alias.strip())
    if not aliases:
        raise HTTPException(422, "a resolved company name or applicant alias is required")
    try:
        async with httpx.AsyncClient(timeout=45) as http:
            count = await collect_epo_patents(
                pool,
                http,
                ticker=ticker,
                applicant_aliases=aliases,
                client_id=settings.epo_ops_client_id.get_secret_value(),
                client_secret=settings.epo_ops_client_secret.get_secret_value(),
            )
    except QuotaExceeded as exc:
        raise HTTPException(429, "EPO OPS request budget exhausted") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FreeSourceError as exc:
        raise HTTPException(
            502, "EPO OPS patent search failed; inspect source access status"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "EPO OPS network request failed") from exc
    return cast(EpoRefreshResponse, {"ticker": ticker, "patent_candidates": count})


@router.get("/exchange-sessions/{mic}", tags=["reference"], response_model=list[ExchangeSessionRow])
async def exchange_sessions(
    mic: Annotated[str, Path(pattern=r"^[A-Z0-9]{4}$")],
    pool: Pool,
    _owner: Owner,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
) -> list[ExchangeSessionRow]:
    if end < start:
        raise HTTPException(422, "end must not precede start")
    rows = await pool.fetch(
        """select mic,session_date,open_at,close_at,state,source,confirmed,evidence_url,reason,
                  known_at
           from public.exchange_session where mic=$1 and session_date between $2 and $3
           order by session_date,confirmed desc,known_at desc""",
        mic,
        start,
        end,
    )
    return cast(list[ExchangeSessionRow], [dict(row) for row in rows])


@router.get(
    "/exchange-sessions/{mic}/expected", tags=["reference"], response_model=ExpectedSessionsResponse
)
async def expected_exchange_sessions(
    mic: Annotated[str, Path(pattern=r"^[A-Z0-9]{4}$")],
    pool: Pool,
    _owner: Owner,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
) -> ExpectedSessionsResponse:
    """The sessions the exchange held in a window: confirmed `exchange_session` rows first
    (half-days, ad hoc closures), else weekdays less the closures confirmed in the Jobs config.
    This is the denominator of return-window coverage and the yardstick of price freshness."""
    if end < start:
        raise HTTPException(422, "end must not precede start")
    jobs = await load_active(pool, "jobs", JobsConfig)
    expected = await expected_sessions(
        pool, (jobs.body if jobs else JobsConfig()).calendar, mic, start, end
    )
    return cast(
        ExpectedSessionsResponse,
        {
            "mic": mic,
            "start": start,
            "end": end,
            "count": expected.count,
            "basis": expected.basis,
            "confirmed_days": expected.confirmed_days,
            "days": expected.days,
        },
    )


@router.post(
    "/exchange-sessions", tags=["reference"], response_model=RecordedExchangeSessionResponse
)
async def record_exchange_session(
    body: ExchangeSessionInput, pool: Pool, _owner: Owner
) -> RecordedExchangeSessionResponse:
    row_id = await pool.fetchval(
        """insert into public.exchange_session
           (mic,session_date,open_at,close_at,state,source,confirmed,evidence_url,reason)
           values($1,$2,$3,$4,$5,$6,true,$7,$8)
           on conflict(mic,session_date,source) do update set open_at=excluded.open_at,
             close_at=excluded.close_at,state=excluded.state,confirmed=true,
             evidence_url=excluded.evidence_url,reason=excluded.reason,known_at=now()
           returning session_date""",
        body.mic,
        body.session_date,
        body.open_at,
        body.close_at,
        body.state,
        body.source,
        body.evidence_url,
        body.reason,
    )
    return cast(RecordedExchangeSessionResponse, {"session_date": row_id})


@router.get("/holdings/{ticker}/peer-set", tags=["holdings"], response_model=PeerSetResponse)
async def peer_set(
    ticker: TickerPath, pool: Pool, _owner: Owner, as_of: Annotated[datetime | None, Query()] = None
) -> PeerSetResponse:
    """Peers as known at `as_of`, each with the status that was in force at that moment."""
    rows = await pool.fetch(
        f"""select p.id,p.peer_ticker,p.basis,{peer_status_as_of("p", "$2")} as status,
                  p.evidence_id,p.source,p.valid_from,p.valid_to,p.known_at,p.detail,
                  p.status_history
           from public.peer_set p where p.ticker=$1 and ($2::timestamptz is null or p.known_at<=$2)
           order by 4,p.peer_ticker,p.known_at desc""",  # noqa: S608 - status fragment is constant
        ticker,
        as_of,
    )
    return cast(
        PeerSetResponse,
        {
            "ticker": ticker,
            "known_as_of": as_of,
            "peers": decode_rows(rows, ("detail", "status_history")),
        },
    )


@router.post("/holdings/{ticker}/peer-set", tags=["holdings"], response_model=RecordedPeerResponse)
async def record_peer(
    ticker: TickerPath, body: PeerInput, pool: Pool, _owner: Owner
) -> RecordedPeerResponse:
    """Record a peer. Posting an existing peer again changes neither its status nor when it
    became known (`known_at` is set once); a status change is the `.../status` route."""
    if body.peer_ticker.casefold() == ticker.casefold():
        raise HTTPException(422, "a holding cannot be its own peer")
    row_id = await upsert_peer(
        pool,
        ticker=ticker,
        peer_ticker=body.peer_ticker,
        basis=body.basis,
        status=body.status,
        source=body.source,
        evidence_id=body.evidence_id,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        detail=body.detail,
    )
    return cast(RecordedPeerResponse, {"id": row_id})


class PeerStatusInput(BaseModel):
    status: Literal["suggested", "confirmed", "rejected"]
    reason: str | None = Field(default=None, min_length=3, max_length=2000)
    evidence_id: int | None = None


@router.put("/holdings/{ticker}/peer-set/{peer_id}/status", tags=["holdings"], response_model=None)
async def change_peer_status(
    ticker: TickerPath, peer_id: int, body: PeerStatusInput, pool: Pool, _owner: Owner
) -> dict[str, Any]:
    """The owner's decision on a peer in either direction: a confirmed peer can be rejected or
    returned to suggested. The change is dated in the peer's status history, so an `as_of` read
    still shows the earlier status. Confirming needs supporting evidence."""
    existing = await pool.fetchrow(
        "select id,evidence_id from public.peer_set where id=$1 and ticker=$2", peer_id, ticker
    )
    if existing is None:
        raise HTTPException(404, "peer not found for this holding")
    if body.status == "confirmed" and body.evidence_id is None and existing["evidence_id"] is None:
        raise HTTPException(422, "confirming a peer requires supporting evidence")
    if body.evidence_id is not None:
        try:
            await pool.execute(
                "update public.peer_set set evidence_id=$2 where id=$1", peer_id, body.evidence_id
            )
        except asyncpg.ForeignKeyViolationError as exc:
            raise HTTPException(422, "evidence_id does not exist") from exc
    changed = await set_peer_status(pool, peer_id, body.status, body.reason)
    return {"peer": changed}


@router.get("/holdings/{ticker}/peer-metrics", tags=["holdings"], response_model=None)
async def peer_metrics(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
    reconciled_only: Annotated[
        bool,
        Query(
            description="Use only facts a reconciliation check passed. Facts a check failed are "
            "never used; unchecked facts are used and flagged unless this is set."
        ),
    ] = False,
) -> dict[str, Any]:
    peers = await pool.fetch(
        f"""select p.peer_ticker,p.basis,p.source,p.evidence_id
           from public.peer_set p
           where p.ticker=$1 and {peer_status_as_of("p", "$2")}='confirmed'
             and ($2::timestamptz is null or p.known_at<=$2)
             and (p.valid_from is null or p.valid_from<=coalesce($2::date,current_date))
             and (p.valid_to is null or p.valid_to>=coalesce($2::date,current_date))
           order by p.peer_ticker""",  # noqa: S608 - status fragment is constant
        ticker,
        as_of,
    )
    peer_tickers = sorted({row["peer_ticker"] for row in peers})
    tickers = sorted(set(peer_tickers) | {ticker})
    # jsonb arrives as text (no pool codec): `dimensions` and `validation` are decoded by
    # `financial_peer_metrics`, which skips segment facts and failed reconciliations.
    facts = await pool.fetch(
        """select ticker,concept,period_start,period_end,basis,value,unit,dimensions,known_at,
                  validation,published
           from public.financial_statement_fact where ticker=any($1::text[])
             and value is not null and ($2::timestamptz is null or known_at<=$2)
           order by ticker,period_end,known_at,concept""",
        tickers,
        as_of,
    )
    metrics = financial_peer_metrics([dict(row) for row in facts], reconciled_only=reconciled_only)
    comparison = peer_comparison(ticker, peer_tickers, metrics)
    return {
        "ticker": ticker,
        "known_as_of": as_of,
        "confirmed_peers": [dict(row) for row in peers],
        "covered_peer_metrics": comparison["metrics"],
        "excluded_peers": comparison["excluded_peers"],
        "holding_period": comparison["holding_period"],
        "reconciled_only": reconciled_only,
        "formula_definitions": FORMULAS,
        "formula_detail": FORMULA_DETAIL,
        "universe": (
            "owner-confirmed peers with compatible filed facts; not a global sector average"
        ),
    }


class SessionState(BaseModel):
    session: date
    state: Literal["observed", "missing", "disagrees"]


class PriceDiagnostic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    symbol: str
    source: str
    mic: str | None
    currency: str | None
    quote_unit: str | None
    bar_type: str
    last_session: date | None
    observed_sessions: int
    disagreements: int
    missing_days: int
    access_state: str
    chosen: bool = False
    expected_sessions: int = 0
    calendar_basis: str | None = None
    stale_sessions: int | None = None
    recent_sessions: list[SessionState] = []


@router.get("/holdings/{ticker}/prices/diagnostics", tags=["holdings"])
async def price_diagnostics(ticker: TickerPath, pool: Pool, _owner: Owner) -> list[PriceDiagnostic]:
    """One row per stored series of the holding (every source and bar type), joined through the
    holding's provider symbols; the series readers serve is marked `chosen`. Missing days are
    judged against the exchange's real session calendar."""
    return [PriceDiagnostic(**row) for row in await holding_price_diagnostics(pool, ticker)]


@router.get(
    "/holdings/{ticker}/returns",
    tags=["holdings"],
    response_model=HoldingReturnsResponse,
    response_model_exclude_unset=True,
)
async def holding_returns(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
    withholding_rate: Annotated[Decimal, Query(ge=0, lt=1)] = Decimal(0),
) -> HoldingReturnsResponse:
    """Raw price, adjusted price and total return of the holding's chosen series (the one every
    reader serves). Computed on request and never written: the scheduled `returns_refresh` job
    stores them. Total return needs raw closes, confirmed actions and owner-verified action
    coverage; otherwise it is unavailable with the reason while the price returns stay."""
    if end < start:
        raise HTTPException(422, "end must not precede start")
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    body = profiles.body if profiles else HoldingProfilesConfig()
    symbols = (await holding_symbols(pool, [ticker], body))[ticker]
    inputs = await return_inputs(pool, ticker, symbols, body)
    if inputs is None:
        return cast(
            HoldingReturnsResponse,
            {
                "ticker": ticker,
                "known_as_of": end,
                "results": [],
                "reason": "no stored price series with a known quote currency",
            },
        )
    out = await compute_for(
        pool, inputs, start, end, store=False, withholding_rate=withholding_rate
    )
    coverage_ok = await actions_complete(pool, inputs.action_symbols, end)
    result = {
        "symbol": inputs.ref.symbol,
        "source": inputs.ref.name,
        "chosen": True,
        "currency": inputs.currency,
        "mic": inputs.mic,
        "price_return": out["price_adjusted"],
        "price_return_reason": out["price_adjusted_reason"],
        "raw_price_return": out["price_raw"],
        "total_return": out["total_return"],
        "total_return_reason": out["total_return_reason"],
        "action_coverage": "available" if coverage_ok else "incomplete",
        "coverage": out["coverage"],
        "expected_sessions": out["expected_sessions"],
        "observed_sessions": out["observed_sessions"],
        "calendar_basis": out["calendar_basis"],
        "missing_actions": out["missing_actions"],
        "observations": out["observed_sessions"],
        "start": start,
        "end": end,
    }
    return cast(HoldingReturnsResponse, {"ticker": ticker, "known_as_of": end, "results": [result]})


@router.get("/holdings/{ticker}/corporate-actions", tags=["holdings"], response_model=None)
async def list_corporate_actions(
    ticker: TickerPath, pool: Pool, _owner: Owner
) -> list[dict[str, Any]]:
    names = await _holding_symbol_names(pool, ticker)
    rows = await pool.fetch(
        """select id,symbol,action_type,ex_date,record_date,payment_date,ratio,cash_amount,
                  currency,source,source_url,publisher_at,known_at,evidence_id,status,raw,
                  decided_at,decision_reason
           from public.corporate_action where symbol = any($1::text[])
              or listing_id in (select listing_id from public.instrument_security_link
                                where ticker=$2 and listing_id is not null)
           order by ex_date desc,known_at desc""",
        sorted(names),
        ticker,
    )
    return decode_structured_rows(rows, ("raw",))


async def _holding_symbol_names(pool: Pool, ticker: str) -> set[str]:
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    body = profiles.body if profiles else HoldingProfilesConfig()
    return await holding_symbol_names(pool, ticker, body)


@router.post("/holdings/{ticker}/corporate-actions", tags=["holdings"])
async def record_corporate_action(
    ticker: TickerPath, body: CorporateActionInput, pool: Pool, _owner: Owner
) -> dict[str, Any]:
    """Record action evidence for one of the holding's listing symbols (the exchange ticker or
    any provider symbol that prices it). An already decided action keeps its status: change it
    through the decision route."""
    if body.symbol not in await _holding_symbol_names(pool, ticker):
        raise HTTPException(
            422, "symbol does not match this holding's listing ticker or provider symbols"
        )
    listing_id = await pool.fetchval(
        """select m.listing_id from public.instrument_security_link m
           where m.ticker=$1 and m.status='confirmed' and m.listing_id is not null""",
        ticker,
    )
    if listing_id is None:
        raise HTTPException(
            422,
            "a confirmed canonical listing match is required "
            "(derive it and confirm it on the holding's listing route)",
        )
    raw = {**body.raw, "per": body.per, "amount_basis": body.amount_basis}
    row = await pool.fetchrow(
        """insert into public.corporate_action
           (listing_id,symbol,action_type,ex_date,record_date,payment_date,ratio,cash_amount,currency,
            source,source_url,publisher_at,evidence_id,status,raw,decided_at)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,
                  case when $14 in ('confirmed','rejected') then now() else null end)
           on conflict(listing_id,action_type,ex_date,source,source_url) do update set
             ratio=coalesce(excluded.ratio,corporate_action.ratio),
             cash_amount=coalesce(excluded.cash_amount,corporate_action.cash_amount),
             currency=coalesce(excluded.currency,corporate_action.currency),
             evidence_id=coalesce(excluded.evidence_id,corporate_action.evidence_id),
             raw=corporate_action.raw || excluded.raw,
             status=case when corporate_action.status = 'suggested'
                         then excluded.status else corporate_action.status end,
             decided_at=case when corporate_action.status = 'suggested'
                                  and excluded.status in ('confirmed','rejected')
                             then now() else corporate_action.decided_at end
           returning id,status,(status = $14) as applied""",
        listing_id,
        body.symbol,
        body.action_type,
        body.ex_date,
        body.record_date,
        body.payment_date,
        body.ratio,
        body.cash_amount,
        body.currency,
        body.source,
        body.source_url,
        body.publisher_at,
        body.evidence_id,
        body.status,
        json.dumps(raw),
    )
    assert row is not None
    return {"id": row["id"], "status": row["status"], "status_applied": row["applied"]}


@router.post(
    "/holdings/{ticker}/corporate-actions/{action_id}/decision",
    tags=["holdings"],
    response_model=None,
)
async def decide_corporate_action(
    ticker: TickerPath,
    action_id: Annotated[int, Path(ge=1)],
    body: CorporateActionDecision,
    pool: Pool,
    owner: Owner,
) -> dict[str, Any]:
    """Confirm, reject or retract (back to `suggested`) one recorded action. This is the only
    way to change an action that already has a status; every decision keeps its reason and
    time, and a confirmed action needs linked evidence."""
    names = await _holding_symbol_names(pool, ticker)
    current = await pool.fetchrow(
        """select id,status,evidence_id,symbol,listing_id from public.corporate_action
           where id=$1 and (symbol = any($2::text[]) or listing_id in
             (select listing_id from public.instrument_security_link
              where ticker=$3 and listing_id is not null))""",
        action_id,
        sorted(names),
        ticker,
    )
    if current is None:
        raise HTTPException(404, "corporate action not found for this holding")
    evidence_id = body.evidence_id or current["evidence_id"]
    if body.decision == "confirmed" and evidence_id is None:
        raise HTTPException(422, "confirming an action requires linked source evidence")
    await pool.execute(
        """update public.corporate_action set status=$2, evidence_id=coalesce($3,evidence_id),
             decided_at=now(), decided_by=$4::uuid, decision_reason=$5 where id=$1""",
        action_id,
        body.decision,
        body.evidence_id,
        owner.user_id,
        body.reason,
    )
    return {"id": action_id, "previous_status": current["status"], "status": body.decision}


@router.get(
    "/holdings/{ticker}/corporate-actions/coverage",
    tags=["holdings"],
    response_model=ActionCoverageResponse,
)
async def get_action_coverage(
    ticker: TickerPath, pool: Pool, _owner: Owner
) -> ActionCoverageResponse:
    """Where corporate-action coverage stands for the holding's symbol: every provider's
    recorded state, newest first, with the owner-verified row (if any) marked."""
    symbol = await pool.fetchval(
        """select coalesce(price_symbol,t212_ticker) from public.instrument
           where t212_ticker=$1""",
        ticker,
    )
    if symbol is None:
        raise HTTPException(404, "holding not found")
    rows = await pool.fetch(
        """select provider, state, latest_period, checked_at, reason
           from public.coverage_status where dataset='corporate_actions' and entity_key=$1
           order by checked_at desc""",
        symbol,
    )
    entries = [{**dict(r), "owner_verified": r["provider"] == "owner_review"} for r in rows]
    return cast(ActionCoverageResponse, {"symbol": symbol, "entries": entries})


@router.post(
    "/holdings/{ticker}/corporate-actions/coverage",
    tags=["holdings"],
    response_model=ActionCoverageConfirmedResponse,
)
async def confirm_action_coverage(
    ticker: TickerPath,
    body: CorporateActionCoverageInput,
    pool: Pool,
    _owner: Owner,
) -> ActionCoverageConfirmedResponse:
    symbol = await pool.fetchval(
        """select coalesce(price_symbol,t212_ticker) from public.instrument
           where t212_ticker=$1""",
        ticker,
    )
    if symbol is None:
        raise HTTPException(404, "holding not found")
    await pool.execute(
        """insert into public.coverage_status
           (provider,dataset,entity_key,state,latest_period,reason)
           values('owner_review','corporate_actions',$1,'available',$2,$3)
           on conflict(provider,dataset,entity_key) do update set state='available',
             latest_period=excluded.latest_period,checked_at=now(),reason=excluded.reason""",
        symbol,
        body.through,
        f"Owner-verified coverage through {body.through}: {body.reason}; {body.source_url}",
    )
    return cast(
        ActionCoverageConfirmedResponse,
        {"symbol": symbol, "covered_through": body.through, "state": "available"},
    )


@router.get("/holdings/{ticker}/listing", tags=["holdings"], response_model=ListingOverviewResponse)
async def holding_listing(ticker: TickerPath, pool: Pool, _owner: Owner) -> ListingOverviewResponse:
    """The holding's derived canonical listing, the evidence it was matched on and the provider
    symbols mapped to it."""
    overview = await listing_overview(pool, ticker)
    if overview is None:
        raise HTTPException(404, "no listing derived for this holding yet")
    return cast(ListingOverviewResponse, overview)


@router.post(
    "/holdings/{ticker}/listing/derive", tags=["holdings"], response_model=DerivedListingResponse
)
async def derive_holding_listing(
    ticker: TickerPath, pool: Pool, _owner: Owner
) -> DerivedListingResponse:
    """(Re)derive the holding's listing from the instrument, ESMA FIRDS and OpenFIGI. A link is
    `confirmed` automatically only when all three agree; otherwise it is `suggested` and waits
    for the owner. An owner decision is never overwritten."""
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    results = await derive_listings(
        pool, [ticker], profiles.body if profiles else HoldingProfilesConfig()
    )
    result = results[0]
    return cast(
        DerivedListingResponse,
        {
            "ticker": ticker,
            "status": result.status,
            "listing_id": result.listing_id,
            "reason": result.reason,
            "score": result.score,
        },
    )


@router.post(
    "/holdings/{ticker}/listing/confirm", tags=["holdings"], response_model=ConfirmedListingResponse
)
async def confirm_holding_listing(
    ticker: TickerPath, body: ListingDecision, pool: Pool, owner: Owner
) -> ConfirmedListingResponse:
    """The owner confirms or rejects the holding's listing match. Confirmation is what lets
    corporate actions be recorded and the EODHD/Alpha Vantage fallbacks run for the holding."""
    try:
        return cast(
            ConfirmedListingResponse,
            await confirm_listing(
                pool, ticker, body.decision, owner.user_id, body.reason, listing_id=body.listing_id
            ),
        )
    except ListingDecisionError as exc:
        raise HTTPException(404 if exc.not_found else 422, str(exc)) from exc


@router.get("/holdings/{ticker}/financial-history", tags=["holdings"], response_model=None)
async def financial_history(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
    statement: Literal["income", "cash_flow", "balance_sheet", "shares", "segment", "note"]
    | None = None,
    line_item: Annotated[str | None, Query(max_length=80)] = None,
    reconciled_only: Annotated[
        bool, Query(description="Only facts a reconciliation check passed.")
    ] = False,
) -> list[dict[str, Any]]:
    """Stored statement facts as known at `as_of`; every row says whether it was reconciled and
    carries `valuation_gate` (usable_for_valuation and reasons) for its period."""
    rows = await financial_history_rows(
        pool, ticker, as_of, statement, reconciled_only=reconciled_only, line_item=line_item
    )
    return await with_valuation_gate(pool, rows, as_of)


@router.post(
    "/holdings/{ticker}/financial-history/alpha-vantage/{statement}",
    tags=["holdings"],
    response_model=None,
)
async def refresh_alpha_vantage_statement(
    ticker: TickerPath,
    statement: Literal["INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW"],
    pool: Pool,
    _owner: Owner,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.alpha_vantage_api_key:
        raise HTTPException(409, "ALPHA_VANTAGE_API_KEY is not configured")
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profiles = stored.body if stored else HoldingProfilesConfig()
    profile = profiles.for_ticker(ticker)
    if profile is None or not profile.alpha_vantage_symbol:
        raise HTTPException(422, "an explicit Alpha Vantage company-symbol mapping is required")
    mapped = await pool.fetchval(
        """select exists(select 1 from public.instrument_security_link
           where ticker=$1 and status='confirmed' and listing_id is not null)""",
        ticker,
    )
    if not mapped:
        raise HTTPException(422, "a confirmed canonical security listing is required")
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            result = await collect_alpha_vantage_statement(
                pool,
                http,
                ticker=ticker,
                symbol=profile.alpha_vantage_symbol,
                function=statement,
                api_key=settings.alpha_vantage_api_key.get_secret_value(),
            )
    except QuotaExceeded as exc:
        raise HTTPException(429, "Alpha Vantage daily free request budget exhausted") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FreeSourceError as exc:
        raise HTTPException(
            502, "Alpha Vantage statement request failed; inspect source access status"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Alpha Vantage network request failed") from exc
    return {"ticker": ticker, "statement": statement, **result}


@router.post(
    "/holdings/{ticker}/financial-history/finnhub-research", tags=["holdings"], response_model=None
)
async def refresh_finnhub_research(ticker: TickerPath, pool: Pool, _owner: Owner) -> dict[str, Any]:
    """Refresh free-gated peer candidates and reported quarterly EPS surprises."""
    settings = get_settings()
    if not settings.finnhub_api_key:
        raise HTTPException(409, "FINNHUB_API_KEY is not configured")
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profiles = stored.body if stored else HoldingProfilesConfig()
    profile = profiles.for_ticker(ticker)
    symbol = profile.edgar_ticker() if profile else None
    if not symbol:
        raise HTTPException(422, "an explicit SEC issuer profile is required")
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            result = await collect_finnhub_research(
                pool,
                http,
                ticker=ticker,
                symbol=symbol,
                api_key=settings.finnhub_api_key.get_secret_value(),
            )
    except QuotaExceeded as exc:
        raise HTTPException(429, "Finnhub request budget exhausted") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FreeSourceError as exc:
        raise HTTPException(
            502, "Finnhub research request failed; inspect source access status"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Finnhub network request failed") from exc
    return {"ticker": ticker, "symbol": symbol, **result}


@router.get("/holdings/{ticker}/forward-data", tags=["holdings"], response_model=None)
async def forward_data(
    ticker: TickerPath, pool: Pool, _owner: Owner, as_of: Annotated[datetime | None, Query()] = None
) -> list[dict[str, Any]]:
    return await forward_rows(pool, ticker, as_of)


@router.get("/holdings/{ticker}/forward-data/consensus", tags=["holdings"], response_model=None)
async def forward_consensus(
    ticker: TickerPath, pool: Pool, _owner: Owner, as_of: Annotated[datetime | None, Query()] = None
) -> dict[str, Any]:
    """Forward observations with consensus series per provider, currency and basis."""
    return await forward_payload(pool, ticker, as_of)


@router.post("/holdings/{ticker}/forward-data", tags=["holdings"], response_model=None)
async def record_forward_data(
    ticker: TickerPath,
    body: ForwardObservationInput,
    pool: Pool,
    _owner: Owner,
) -> dict[str, Any]:
    observation_id = await store_forward_value(
        pool,
        ForwardValue(
            ticker=ticker,
            kind=body.kind,
            provider=body.provider or ("owner" if body.kind == "owner_scenario" else None),
            metric=body.metric,
            basis=body.basis,
            fiscal_period=body.fiscal_period,
            period_end=body.period_end,
            currency=body.currency,
            value=body.value,
            low=body.low,
            high=body.high,
            analyst_count=body.analyst_count,
            vintage=body.vintage,
            published_at=body.published_at,
            evidence_id=body.evidence_id,
            source_url=body.source_url,
            raw=body.raw,
        ),
    )
    return {"id": observation_id}


@router.get("/holdings/{ticker}/issuer-ir-releases", tags=["holdings"], response_model=None)
async def issuer_ir_releases(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    rows = await pool.fetch(
        """select id,release_type,source_url,content_hash,document_version_id,published_at,
                  known_at,parser_version,analyst_count,basis,status,metadata,source_id,
                  previous_release_id,content_type,byte_size,fetched_at
           from public.issuer_ir_release where ticker=$1
             and ($2::timestamptz is null or known_at<=$2)
           order by published_at desc nulls last,known_at desc""",
        ticker,
        as_of,
    )
    return {
        "ticker": ticker,
        "known_as_of": as_of,
        "releases": decode_rows(rows, ("metadata",)),
    }


class IssuerIRSourceInput(BaseModel):
    url: str = Field(pattern=r"^https://[^\s]{4,2000}$")
    release_type: Literal["guidance", "consensus", "segment_consensus", "capital_return"]
    basis: str | None = Field(default=None, max_length=120)
    label: str | None = Field(default=None, max_length=200)
    active: bool = True


@router.get("/holdings/{ticker}/issuer-ir-sources", tags=["holdings"], response_model=None)
async def issuer_ir_sources(ticker: TickerPath, pool: Pool, _owner: Owner) -> dict[str, Any]:
    """The IR pages the `ir_releases` job polls for this holding, with their last outcome."""
    rows = await pool.fetch(
        """select id,url,release_type,basis,label,active,last_checked_at,last_changed_at,
                  last_hash,last_status,last_error,created_at
           from public.issuer_ir_source where ticker=$1 order by id""",
        ticker,
    )
    return {"ticker": ticker, "sources": [dict(row) for row in rows]}


@router.post("/holdings/{ticker}/issuer-ir-sources", tags=["holdings"], response_model=None)
async def register_issuer_ir_source(
    ticker: TickerPath, body: IssuerIRSourceInput, pool: Pool, _owner: Owner
) -> dict[str, Any]:
    """Register (or update) an IR page for the `ir_releases` job to fetch, version and parse."""
    try:
        assert_fetchable(body.url)
    except IrError as exc:
        raise HTTPException(422, str(exc)) from exc
    source_id = await pool.fetchval(
        """insert into public.issuer_ir_source (ticker,url,release_type,basis,label,active)
           values($1,$2,$3,$4,$5,$6)
           on conflict(ticker,url) do update set release_type=excluded.release_type,
             basis=excluded.basis,label=excluded.label,active=excluded.active
           returning id""",
        ticker,
        body.url,
        body.release_type,
        body.basis,
        body.label,
        body.active,
    )
    return {"id": source_id}


@router.get("/holdings/{ticker}/actual-vs-consensus", tags=["holdings"], response_model=None)
async def actual_vs_consensus_route(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    metric: Annotated[str, Query(min_length=1, max_length=80)],
    fiscal_period: Annotated[str, Query(pattern=r"^FY\d{4}(Q[1-4]|H[12]|M9)?$")],
) -> dict[str, Any]:
    """The reported figure of a fiscal period against the last consensus known before it."""
    return await actual_vs_consensus(pool, ticker, metric, fiscal_period)


@router.post("/holdings/{ticker}/issuer-ir-releases", tags=["holdings"], response_model=None)
async def record_issuer_ir_release(
    ticker: TickerPath,
    body: IssuerIRReleaseInput,
    pool: Pool,
    _owner: Owner,
) -> dict[str, Any]:
    release_id = await pool.fetchval(
        """insert into public.issuer_ir_release
           (ticker,release_type,source_url,content_hash,document_version_id,published_at,
            known_at,parser_version,analyst_count,basis,status,metadata)
           values($1,$2,$3,$4,$5,$6,now(),$7,$8,$9,$10,$11::jsonb)
           on conflict(ticker,source_url,content_hash) do update set
             document_version_id=coalesce(excluded.document_version_id,
                                           issuer_ir_release.document_version_id),
             published_at=coalesce(excluded.published_at,issuer_ir_release.published_at),
             parser_version=coalesce(excluded.parser_version,issuer_ir_release.parser_version),
             analyst_count=coalesce(excluded.analyst_count,issuer_ir_release.analyst_count),
             basis=coalesce(excluded.basis,issuer_ir_release.basis),status=excluded.status,
             metadata=issuer_ir_release.metadata || excluded.metadata
           returning id""",
        ticker,
        body.release_type,
        body.source_url,
        body.content_hash.lower(),
        body.document_version_id,
        body.published_at,
        body.parser_version,
        body.analyst_count,
        body.basis,
        body.status,
        json.dumps(body.metadata),
    )
    return {"id": release_id}


@router.post("/company-graph/edges", tags=["research"], response_model=None)
async def record_company_edge(body: CompanyEdgeInput, pool: Pool, _owner: Owner) -> dict[str, Any]:
    """Suggest (or, with primary evidence and a passage, confirm) an edge. A re-sync never
    changes a decided edge; move one with POST /company-graph/edges/{id}/decision."""
    try:
        edge_id = await store_company_edge(
            pool,
            subject_key=body.subject_key,
            object_key=body.object_key,
            object_name=body.object_name,
            edge_type=body.edge_type,
            direction=body.direction,
            source=body.source,
            source_url=body.source_url,
            status=body.status,
            valid_from=body.valid_from,
            valid_to=body.valid_to,
            evidence_id=body.evidence_id,
            passage=body.passage,
            detail=body.detail,
        )
    except (ValueError, GraphDecisionError) as exc:
        raise HTTPException(422, str(exc)) from exc
    status = await pool.fetchval(
        "select status from public.company_graph_edge where id=$1", edge_id
    )
    return {"id": edge_id, "status": status}


@router.post("/holdings/{ticker}/company-events", tags=["holdings"], response_model=None)
async def record_company_event(
    ticker: TickerPath,
    body: CompanyEventInput,
    pool: Pool,
    _owner: Owner,
) -> dict[str, Any]:
    """Record one event state. Deals and financings go through the deal state machine (invalid
    transitions are refused); a confirmed event needs linked primary evidence; a decided event
    is only changed by POST /holdings/{ticker}/company-events/{id}/decision."""
    try:
        await validate_thesis_kpi(pool, ticker, body.thesis_kpi)
        if body.event_type in {"deal", "financing"}:
            if body.event_state == "unknown":
                raise ValueError("a deal or financing needs a known state")
            key = (
                body.detail.get("deal_key")
                if isinstance(body.detail.get("deal_key"), str)
                else None
            )
            counterparty = body.counterparty_key or body.source_url
            event_id = await record_deal_event(
                pool,
                ticker=ticker,
                event_type=body.event_type,
                key=str(key or deal_key(ticker, body.event_type, counterparty)),
                state=body.event_state,
                source=body.source,
                source_url=body.source_url,
                announced_at=body.announced_at,
                effective_at=body.effective_at,
                counterparty=body.counterparty_key,
                terms=body.terms,
                evidence_id=body.evidence_id,
                status=body.status,
                thesis_kpi=body.thesis_kpi,
                detail=body.detail,
            )
        else:
            event_id = await upsert_event(
                pool,
                ticker=ticker,
                event_type=body.event_type,
                event_state=body.event_state,
                source=body.source,
                source_url=body.source_url,
                status=body.status,
                announced_at=body.announced_at,
                effective_at=body.effective_at,
                evidence_id=body.evidence_id,
                counterparty_key=body.counterparty_key,
                terms=body.terms,
                thesis_kpi=body.thesis_kpi,
                detail=body.detail,
            )
    except DealTransitionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, GraphDecisionError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": event_id}


@router.post("/holdings/{ticker}/patent-families", tags=["holdings"], response_model=None)
async def record_patent_family(
    ticker: TickerPath,
    body: PatentFamilyInput,
    pool: Pool,
    _owner: Owner,
) -> dict[str, Any]:
    try:
        family_id = await _store_patent(
            pool,
            family_key=body.family_key,
            priority_date=body.priority_date,
            title=body.title,
            cpc_codes=body.cpc_codes,
            jurisdictions=body.jurisdictions,
            forward_citations=body.forward_citations,
            backward_citations=body.backward_citations,
            legal_status=body.legal_status,
            source=body.source,
            source_url=body.source_url,
            ticker=ticker,
            applicant=body.applicant,
            ownership_confidence=body.ownership_confidence,
            ownership_basis=body.ownership_basis,
            ownership_valid_from=body.ownership_valid_from,
            evidence_id=body.evidence_id,
            raw=body.raw,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"family_id": family_id}


@router.get("/holdings/{ticker}/business-network", tags=["holdings"], response_model=None)
async def business_network(
    ticker: TickerPath, pool: Pool, _owner: Owner, as_of: Annotated[datetime | None, Query()] = None
) -> dict[str, Any]:
    payload = await business_network_payload(pool, ticker, as_of)
    # jsonb columns arrive as text (no pool codec); `decode_structured` is idempotent.
    for key, columns in (("edges", ("detail",)), ("events", ("terms", "detail", "thesis_kpi"))):
        payload[key] = decode_structured_rows(payload.get(key, []), columns)
    return payload


@router.post("/holdings/{ticker}/relationship-disclosures", tags=["holdings"])
async def record_relationship_review(
    ticker: TickerPath, body: RelationshipReview, pool: Pool, _owner: Owner
) -> dict[str, int]:
    """Record an evidence-passage review. Re-posting the same passage updates it instead of
    duplicating it. A non-suggested `status` is an owner decision and goes through the
    transition rules (a confirmed disclosure can be rejected or disputed here)."""
    try:
        row_id = await upsert_disclosure(
            pool,
            ticker=ticker,
            relationship=body.relationship,
            direction=body.direction,
            passage=body.passage,
            source_url=body.source_url,
            source=body.source,
            counterparty_key=body.counterparty_key,
            counterparty_name=body.counterparty_name,
            anonymous_counterparty=body.anonymous_counterparty,
            reported_share=body.reported_share,
            share_basis=body.share_basis,
            fiscal_period=body.fiscal_period,
            valid_from=body.valid_from,
            valid_to=body.valid_to,
            evidence_id=body.evidence_id,
            status="suggested",
            origin="owner_review",
        )
        current = await pool.fetchval(
            "select status from public.relationship_disclosure where id=$1", row_id
        )
        wanted = {"confirmed": "confirm", "rejected": "reject", "disputed": "dispute"}.get(
            body.status
        )
        if wanted and current != body.status:
            await decide_disclosure(
                pool,
                row_id,
                wanted,  # type: ignore[arg-type]
                reason=body.decision_reason or "owner review",
            )
        await detect_contradictions(pool, row_id)
    except GraphDecisionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": row_id}


@router.get(
    "/holdings/{ticker}/customer-supplier-concentration", tags=["holdings"], response_model=None
)
async def relationship_concentration(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    periods = await concentration(pool, ticker, as_of)
    for row in periods:
        row["disclosed_counterparties"] = row["named_count"] + row["anonymous_count"]
    return {
        "ticker": ticker,
        "periods": periods,
        "limitations": [
            "Only owner-confirmed, dated disclosures with a known direction are included.",
            "Only shares reported against the issuer's own revenue or purchases count; a share of"
            " another filer's revenue is that filer's concentration, not this holding's.",
            "Anonymous counterparties keep their reported share but are never merged with named"
            " ones; shares are not imputed where none was reported.",
        ],
    }


@router.get("/holdings/{ticker}/patent-trends", tags=["holdings"], response_model=None)
async def patent_trends(ticker: TickerPath, pool: Pool, _owner: Owner) -> list[dict[str, Any]]:
    return await patent_trend_rows(pool, ticker)
