"""Owner-only read models for phase 3-5 price, fundamentals and company research data."""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.auth import Owner
from api.db import Pool
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.store import load_active
from steward.free_sources import (
    ForwardValue,
    calculate_and_store_returns,
    collect_alpha_vantage_statement,
    collect_finnhub_research,
    store_company_edge,
    store_forward_value,
    store_patent_family,
)
from steward.market_analysis import PricePoint, compatible_peer_mean, financial_peer_metrics
from steward.settings import get_settings
from steward.sources.company_research import (
    collect_alpha_exposure,
    collect_diffbot_candidates,
    collect_epo_patents,
    collect_pdl_company,
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
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
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
        if (
            self.action_type in {"cash_dividend", "stock_dividend"}
            and self.cash_amount is None
            and self.ratio is None
        ):
            raise ValueError("dividend amount or ratio is required")
        if self.status == "confirmed" and self.evidence_id is None:
            raise ValueError("confirmed actions require linked source evidence")
        return self


class ExchangeSessionInput(BaseModel):
    mic: str = Field(pattern=r"^[A-Z0-9]{4}$")
    session_date: date
    state: Literal["open", "half_day", "closed"]
    open_at: datetime | None = None
    close_at: datetime | None = None
    source: str = Field(min_length=2, max_length=80)
    evidence_url: str = Field(min_length=8, max_length=2000)

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
    evidence_tier: Literal["primary", "regulatory", "owner_review", "vendor", "community"]
    status: Literal["suggested", "confirmed", "rejected", "disputed"] = "suggested"
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
    status: Literal["suggested", "confirmed", "rejected", "disputed"] = "suggested"
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
        if self.ownership_basis == "dated_subsidiary_path" and self.ownership_valid_from is None:
            raise ValueError("subsidiary attribution requires a dated ownership path")
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


def _holding_research_profile(stored, ticker: str):
    config = stored.body if stored else HoldingProfilesConfig()
    return config.for_ticker(ticker)


def _profile_domain(profile) -> str | None:
    urls = [profile.ir_url, *profile.results_urls]
    for url in urls:
        host = urlsplit(str(url)).hostname if url else None
        if host:
            return host.removeprefix("www.").lower()
    return profile.extra_domains[0] if profile.extra_domains else None


@router.get("/holdings/{ticker}/company-research-observations", tags=["holdings"])
async def company_research_observations(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
):
    rows = await pool.fetch(
        """select id,provider,dataset,metric,period_end,value,text_value,unit,currency,source_url,
                  published_at,known_at,match_confidence,raw
           from public.company_research_observation where ticker=$1
             and ($2::timestamptz is null or known_at<=$2)
           order by dataset,metric,period_end desc nulls last,known_at desc""",
        ticker,
        as_of,
    )
    return {"ticker": ticker, "known_as_of": as_of, "observations": [dict(r) for r in rows]}


@router.post("/holdings/{ticker}/exposure-context/alpha-vantage", tags=["holdings"])
async def refresh_alpha_exposure(
    ticker: TickerPath, body: AlphaExposureRequest, pool: Pool, _owner: Owner
):
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
    return {"ticker": ticker, "series": body.series_name, "observations_stored": count}


@router.get("/holdings/{ticker}/exposure-context", tags=["holdings"])
async def exposure_context(ticker: TickerPath, pool: Pool, _owner: Owner):
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profile = _holding_research_profile(stored, ticker)
    observations = await pool.fetch(
        """select id,provider,dataset,metric,period_end,value,text_value,unit,currency,
                  source_url,known_at,raw from public.company_research_observation
           where ticker=$1 and dataset='exposure_context'
           order by metric,period_end desc,known_at desc""",
        ticker,
    )
    return {
        "ticker": ticker,
        "exposure_assumptions": [e.model_dump(mode="json") for e in profile.exposures]
        if profile
        else [],
        "observations": [dict(row) for row in observations],
        "limitations": [
            "Price series are contextual inputs, not estimates of issuer margin sensitivity.",
            "Exposure shares are shown only when entered in the holding profile.",
        ],
    }


@router.post("/holdings/{ticker}/company-research/diffbot", tags=["holdings"])
async def refresh_diffbot_company_research(ticker: TickerPath, pool: Pool, _owner: Owner):
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
    return {"ticker": ticker, "suggested_edges": count}


@router.post("/holdings/{ticker}/company-research/pdl", tags=["holdings"])
async def refresh_pdl_company_research(ticker: TickerPath, pool: Pool, _owner: Owner):
    settings = get_settings()
    if not settings.people_data_labs_api_key:
        raise HTTPException(409, "PEOPLE_DATA_LABS_API_KEY is not configured")
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profile = _holding_research_profile(stored, ticker)
    domain = _profile_domain(profile) if profile else None
    if not domain:
        raise HTTPException(422, "a resolved company website domain is required")
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
    return {"ticker": ticker, "matched_records": count}


@router.post("/holdings/{ticker}/patent-families/epo-ops/refresh", tags=["holdings"])
async def refresh_epo_patents(ticker: TickerPath, pool: Pool, _owner: Owner):
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
    return {"ticker": ticker, "patent_candidates": count}


@router.get("/exchange-sessions/{mic}", tags=["reference"])
async def exchange_sessions(
    mic: Annotated[str, Path(pattern=r"^[A-Z0-9]{4}$")],
    pool: Pool,
    _owner: Owner,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
):
    if end < start:
        raise HTTPException(422, "end must not precede start")
    rows = await pool.fetch(
        """select mic,session_date,open_at,close_at,state,source,confirmed,evidence_url,known_at
           from public.exchange_session where mic=$1 and session_date between $2 and $3
           order by session_date,confirmed desc,known_at desc""",
        mic,
        start,
        end,
    )
    return [dict(row) for row in rows]


@router.post("/exchange-sessions", tags=["reference"])
async def record_exchange_session(body: ExchangeSessionInput, pool: Pool, _owner: Owner):
    row_id = await pool.fetchval(
        """insert into public.exchange_session
           (mic,session_date,open_at,close_at,state,source,confirmed,evidence_url)
           values($1,$2,$3,$4,$5,$6,true,$7)
           on conflict(mic,session_date,source) do update set open_at=excluded.open_at,
             close_at=excluded.close_at,state=excluded.state,confirmed=true,
             evidence_url=excluded.evidence_url,known_at=now()
           returning session_date""",
        body.mic,
        body.session_date,
        body.open_at,
        body.close_at,
        body.state,
        body.source,
        body.evidence_url,
    )
    return {"session_date": row_id}


@router.get("/holdings/{ticker}/peer-set", tags=["holdings"])
async def peer_set(
    ticker: TickerPath, pool: Pool, _owner: Owner, as_of: Annotated[datetime | None, Query()] = None
):
    rows = await pool.fetch(
        """select id,peer_ticker,basis,status,evidence_id,source,valid_from,valid_to,known_at,detail
           from public.peer_set where ticker=$1 and ($2::timestamptz is null or known_at<=$2)
           order by status,peer_ticker,known_at desc""",
        ticker,
        as_of,
    )
    return {"ticker": ticker, "known_as_of": as_of, "peers": [dict(row) for row in rows]}


@router.post("/holdings/{ticker}/peer-set", tags=["holdings"])
async def record_peer(ticker: TickerPath, body: PeerInput, pool: Pool, _owner: Owner):
    if body.peer_ticker.casefold() == ticker.casefold():
        raise HTTPException(422, "a holding cannot be its own peer")
    row_id = await pool.fetchval(
        """insert into public.peer_set
           (ticker,peer_ticker,basis,status,evidence_id,source,valid_from,valid_to,detail)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
           on conflict(ticker,peer_ticker,basis,source) do update set
             status=case when peer_set.status in ('rejected','confirmed')
                         then peer_set.status else excluded.status end,
             evidence_id=coalesce(excluded.evidence_id,peer_set.evidence_id),
             valid_from=coalesce(excluded.valid_from,peer_set.valid_from),
             valid_to=coalesce(excluded.valid_to,peer_set.valid_to),
             detail=peer_set.detail || excluded.detail,known_at=now()
           returning id""",
        ticker,
        body.peer_ticker,
        body.basis,
        body.status,
        body.evidence_id,
        body.source,
        body.valid_from,
        body.valid_to,
        json.dumps(body.detail),
    )
    return {"id": row_id}


@router.get("/holdings/{ticker}/peer-metrics", tags=["holdings"])
async def peer_metrics(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
):
    peers = await pool.fetch(
        """select peer_ticker,basis,source,evidence_id from public.peer_set
           where ticker=$1 and status='confirmed'
             and ($2::timestamptz is null or known_at<=$2)
             and (valid_from is null or valid_from<=coalesce($2::date,current_date))
             and (valid_to is null or valid_to>=coalesce($2::date,current_date))
           order by peer_ticker""",
        ticker,
        as_of,
    )
    peer_tickers = sorted({row["peer_ticker"] for row in peers})
    tickers = sorted(set(peer_tickers) | {ticker})
    facts = await pool.fetch(
        """select ticker,concept,period_start,period_end,basis,value,unit,dimensions,known_at
           from public.financial_statement_fact where ticker=any($1::text[])
             and value is not null and ($2::timestamptz is null or known_at<=$2)
           order by ticker,period_end,known_at,concept""",
        tickers,
        as_of,
    )
    metrics = financial_peer_metrics([dict(row) for row in facts])
    own_periods = metrics.get(ticker, {}).get("periods", [])
    latest = own_periods[0] if own_periods else None
    output = {}
    metric_names = (
        "revenue_growth",
        "gross_margin",
        "operating_margin",
        "roic",
        "net_debt_to_equity",
        "free_cash_flow_conversion",
    )
    for name in metric_names:
        comparable: list[tuple[str, Decimal | None, str]] = []
        if latest:
            comparable.append((ticker, latest.get(name), name))
        for peer in peer_tickers:
            peer_periods = metrics.get(peer, {}).get("periods", [])
            peer_fact = (
                next(
                    (
                        p
                        for p in peer_periods
                        if p["period_end"] == latest["period_end"] and p["basis"] == latest["basis"]
                    ),
                    None,
                )
                if latest
                else None
            )
            comparable.append((peer, peer_fact.get(name) if peer_fact else None, name))
        peer_mean = compatible_peer_mean(comparable[1:], metric=name)
        output[name] = {
            **peer_mean,
            "holding_value": comparable[0][1] if comparable else None,
            "period_end": latest["period_end"] if latest else None,
            "basis": latest["basis"] if latest else None,
        }
    return {
        "ticker": ticker,
        "known_as_of": as_of,
        "confirmed_peers": [dict(row) for row in peers],
        "covered_peer_metrics": output,
        "formula_definitions": metrics.get(ticker, {}).get("formulas", {}),
        "universe": (
            "owner-confirmed peers with compatible filed facts; not a global sector average"
        ),
    }


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


@router.get("/holdings/{ticker}/prices/diagnostics", tags=["holdings"])
async def price_diagnostics(ticker: TickerPath, pool: Pool, _owner: Owner) -> list[PriceDiagnostic]:
    rows = await pool.fetch(
        """with bars as (
             select b.* from public.price_bar b where b.symbol in
               (select coalesce(price_symbol,t212_ticker) from public.instrument
                 where t212_ticker=$1)
           ), spans as (
             select symbol,mic,bar_type,min(coalesce(session_date,date)) first_day,
                    max(coalesce(session_date,date)) last_day from bars group by symbol,mic,bar_type
           ), missing as (
             select s.symbol,s.mic,s.bar_type,count(*)::int missing_days
               from spans s join public.exchange_session x on x.mic=s.mic and x.confirmed
                 and x.state in ('open','half_day')
                 and x.session_date between s.first_day and s.last_day
              where not exists(select 1 from bars b where b.symbol=s.symbol and b.mic=s.mic
                and b.bar_type=s.bar_type and coalesce(b.session_date,b.date)=x.session_date)
              group by s.symbol,s.mic,s.bar_type
           ), disagreements as (
             select a.symbol,a.mic,a.bar_type,a.date,count(distinct a.source)::int source_count
               from bars a join bars b on b.symbol=a.symbol and b.date=a.date and b.source>a.source
                and b.bar_type=a.bar_type and b.currency is not distinct from a.currency
              where abs(a.close-b.close)/greatest(a.close,b.close) > 0.02
              group by a.symbol,a.mic,a.bar_type,a.date
           )
           select b.symbol,b.source,b.mic,b.currency,b.quote_unit,b.bar_type,
                  max(coalesce(b.session_date,b.date)) last_session,
                  count(distinct coalesce(b.session_date,b.date))::int observed_sessions,
                  (select count(*)::int from disagreements d where d.symbol=b.symbol
                    and d.mic is not distinct from b.mic and d.bar_type=b.bar_type) disagreements,
                  coalesce((select m.missing_days from missing m where m.symbol=b.symbol
                    and m.mic is not distinct from b.mic and m.bar_type=b.bar_type),0) missing_days,
                  b.access_state
           from bars b group by b.symbol,b.source,b.mic,b.currency,b.quote_unit,b.bar_type,
             b.access_state
           order by last_session desc,b.source""",
        ticker,
    )
    return [PriceDiagnostic(**dict(r)) for r in rows]


@router.get("/holdings/{ticker}/returns", tags=["holdings"])
async def holding_returns(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
):
    """Return price and total return separately; total return requires verified action coverage."""
    bars = await pool.fetch(
        """select b.symbol,b.date,b.close,b.currency,b.source from public.price_bar b
           where b.symbol in (select coalesce(price_symbol,t212_ticker) from public.instrument
                              where t212_ticker=$1)
             and b.date between $2 and $3 order by b.symbol,b.source,b.date""",
        ticker,
        start,
        end,
    )
    results = []
    for symbol, source in sorted({(r["symbol"], r["source"]) for r in bars}):
        source_rows = [r for r in bars if r["symbol"] == symbol and r["source"] == source]
        currencies = {r["currency"] for r in source_rows}
        if len(currencies) != 1 or None in currencies:
            results.append(
                {
                    "symbol": symbol,
                    "source": source,
                    "currency": None,
                    "price_return": None,
                    "total_return": None,
                    "reason": "missing or mixed quote currency",
                }
            )
            continue
        points = [
            PricePoint(r["date"], Decimal(r["close"]), str(r["currency"])) for r in source_rows
        ]
        action_coverage = await pool.fetchrow(
            """select state,latest_period from public.coverage_status
               where provider='owner_review' and dataset='corporate_actions' and entity_key=$1
               order by checked_at desc limit 1""",
            symbol,
        )
        stored = await calculate_and_store_returns(
            pool,
            symbol=symbol,
            start=start,
            end=end,
            source=source,
            currency=str(next(iter(currencies))),
            actions_complete=(
                action_coverage is not None
                and action_coverage["state"] == "available"
                and action_coverage["latest_period"] is not None
                and action_coverage["latest_period"] >= end
            ),
        )
        total_return = stored["total_return"]
        price_return = stored["price_adjusted"]
        raw_price_return = stored["price_raw"]
        results.append(
            {
                "symbol": symbol,
                "source": source,
                "currency": next(iter(currencies)),
                "price_return": price_return,
                "raw_price_return": raw_price_return,
                "total_return": total_return,
                "total_return_reason": stored["total_return_reason"],
                "action_coverage": (
                    action_coverage["state"]
                    if action_coverage is not None
                    and action_coverage["latest_period"] is not None
                    and action_coverage["latest_period"] >= end
                    else "incomplete"
                ),
                "observations": len(points),
                "start": start,
                "end": end,
            }
        )
    return {"ticker": ticker, "known_as_of": end, "results": results}


@router.get("/holdings/{ticker}/corporate-actions", tags=["holdings"])
async def list_corporate_actions(ticker: TickerPath, pool: Pool, _owner: Owner):
    rows = await pool.fetch(
        """select id,symbol,action_type,ex_date,record_date,payment_date,ratio,cash_amount,
                  currency,source,source_url,publisher_at,known_at,evidence_id,status,raw
           from public.corporate_action where symbol in
             (select coalesce(price_symbol,t212_ticker) from public.instrument where t212_ticker=$1)
           order by ex_date desc,known_at desc""",
        ticker,
    )
    return [dict(r) for r in rows]


@router.post("/holdings/{ticker}/corporate-actions", tags=["holdings"])
async def record_corporate_action(
    ticker: TickerPath, body: CorporateActionInput, pool: Pool, _owner: Owner
) -> dict[str, int]:
    """Record action evidence for one exact listing symbol."""
    known = await pool.fetchval(
        """select 1 from public.instrument where t212_ticker=$1
           and coalesce(price_symbol,t212_ticker)=$2""",
        ticker,
        body.symbol,
    )
    if not known:
        raise HTTPException(422, "symbol does not match this holding's configured price symbol")
    listing_id = await pool.fetchval(
        """select l.id from public.instrument_security_link m
           join public.security_listing l on l.id=m.listing_id
           where m.ticker=$1 and m.status='confirmed' and l.ticker=$2""",
        ticker,
        body.symbol,
    )
    if listing_id is None:
        raise HTTPException(422, "a confirmed canonical listing match is required")
    action_id = await pool.fetchval(
        """insert into public.corporate_action
           (listing_id,symbol,action_type,ex_date,record_date,payment_date,ratio,cash_amount,currency,
            source,source_url,publisher_at,evidence_id,status,raw)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb)
           on conflict(listing_id,action_type,ex_date,source,source_url) do update set
             ratio=coalesce(excluded.ratio,corporate_action.ratio),
             cash_amount=coalesce(excluded.cash_amount,corporate_action.cash_amount),
             currency=coalesce(excluded.currency,corporate_action.currency),
             evidence_id=coalesce(excluded.evidence_id,corporate_action.evidence_id),
             status=case when corporate_action.status='rejected' then 'rejected'
                         when excluded.status='confirmed' then 'confirmed'
                         else corporate_action.status end
           returning id""",
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
        json.dumps(body.raw),
    )
    return {"id": action_id}


@router.post("/holdings/{ticker}/corporate-actions/coverage", tags=["holdings"])
async def confirm_action_coverage(
    ticker: TickerPath,
    body: CorporateActionCoverageInput,
    pool: Pool,
    _owner: Owner,
):
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
    return {"symbol": symbol, "covered_through": body.through, "state": "available"}


@router.get("/holdings/{ticker}/financial-history", tags=["holdings"])
async def financial_history(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
    statement: Literal["income", "cash_flow", "balance_sheet", "shares", "segment", "note"]
    | None = None,
):
    rows = await pool.fetch(
        """select id,concept,label,statement,period_start,period_end,fiscal_period,basis,value,
                  unit,currency,dimensions,source,source_url,published_at,known_at,revision,
                  amends_fact_id,text_block,evidence_id,validation
           from public.financial_statement_fact where ticker=$1
             and ($2::timestamptz is null or known_at <= $2)
             and ($3::text is null or statement=$3)
           order by period_end desc,concept,known_at desc""",
        ticker,
        as_of,
        statement,
    )
    return [dict(r) for r in rows]


@router.post("/holdings/{ticker}/financial-history/alpha-vantage/{statement}", tags=["holdings"])
async def refresh_alpha_vantage_statement(
    ticker: TickerPath,
    statement: Literal["INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW"],
    pool: Pool,
    _owner: Owner,
):
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


@router.post("/holdings/{ticker}/financial-history/finnhub-research", tags=["holdings"])
async def refresh_finnhub_research(ticker: TickerPath, pool: Pool, _owner: Owner):
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


@router.get("/holdings/{ticker}/forward-data", tags=["holdings"])
async def forward_data(
    ticker: TickerPath, pool: Pool, _owner: Owner, as_of: Annotated[datetime | None, Query()] = None
):
    rows = await pool.fetch(
        """select id,kind,provider,metric,basis,fiscal_period,period_end,currency,value,low,high,
                  analyst_count,vintage,published_at,known_at,source_url,evidence_id,raw
           from public.forward_observation where ticker=$1
             and ($2::timestamptz is null or known_at <= $2)
           order by fiscal_period,metric,known_at desc""",
        ticker,
        as_of,
    )
    return [dict(r) for r in rows]


@router.post("/holdings/{ticker}/forward-data", tags=["holdings"])
async def record_forward_data(
    ticker: TickerPath,
    body: ForwardObservationInput,
    pool: Pool,
    _owner: Owner,
):
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


@router.get("/holdings/{ticker}/issuer-ir-releases", tags=["holdings"])
async def issuer_ir_releases(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
):
    rows = await pool.fetch(
        """select id,release_type,source_url,content_hash,document_version_id,published_at,
                  known_at,parser_version,analyst_count,basis,status,metadata
           from public.issuer_ir_release where ticker=$1
             and ($2::timestamptz is null or known_at<=$2)
           order by published_at desc nulls last,known_at desc""",
        ticker,
        as_of,
    )
    return {"ticker": ticker, "known_as_of": as_of, "releases": [dict(row) for row in rows]}


@router.post("/holdings/{ticker}/issuer-ir-releases", tags=["holdings"])
async def record_issuer_ir_release(
    ticker: TickerPath,
    body: IssuerIRReleaseInput,
    pool: Pool,
    _owner: Owner,
):
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
             metadata=issuer_ir_release.metadata || excluded.metadata,known_at=now()
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


@router.post("/company-graph/edges", tags=["research"])
async def record_company_edge(body: CompanyEdgeInput, pool: Pool, _owner: Owner):
    edge_id = await store_company_edge(
        pool,
        subject_key=body.subject_key,
        object_key=body.object_key,
        object_name=body.object_name,
        edge_type=body.edge_type,
        direction=body.direction,
        source=body.source,
        source_url=body.source_url,
        evidence_tier=body.evidence_tier,
        status=body.status,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        evidence_id=body.evidence_id,
        passage=body.passage,
        detail=body.detail,
    )
    return {"id": edge_id}


@router.post("/holdings/{ticker}/company-events", tags=["holdings"])
async def record_company_event(
    ticker: TickerPath,
    body: CompanyEventInput,
    pool: Pool,
    _owner: Owner,
):
    event_id = await pool.fetchval(
        """insert into public.company_event
           (ticker,event_type,event_state,announced_at,effective_at,known_at,source,source_url,
            evidence_id,counterparty_key,terms,status,thesis_kpi,detail)
           values($1,$2,$3,$4,$5,now(),$6,$7,$8,$9,$10::jsonb,$11,$12,$13::jsonb)
           on conflict(ticker,event_type,source_url,event_state) do update set
             announced_at=coalesce(excluded.announced_at,company_event.announced_at),
             effective_at=coalesce(excluded.effective_at,company_event.effective_at),
             evidence_id=coalesce(excluded.evidence_id,company_event.evidence_id),
             counterparty_key=coalesce(excluded.counterparty_key,company_event.counterparty_key),
             terms=company_event.terms || excluded.terms,
             status=case when company_event.status in ('rejected','confirmed')
                         then company_event.status else excluded.status end,
             thesis_kpi=coalesce(excluded.thesis_kpi,company_event.thesis_kpi),
             detail=company_event.detail || excluded.detail,known_at=now()
           returning id""",
        ticker,
        body.event_type,
        body.event_state,
        body.announced_at,
        body.effective_at,
        body.source,
        body.source_url,
        body.evidence_id,
        body.counterparty_key,
        json.dumps(body.terms),
        body.status,
        body.thesis_kpi,
        json.dumps(body.detail),
    )
    return {"id": event_id}


@router.post("/holdings/{ticker}/patent-families", tags=["holdings"])
async def record_patent_family(
    ticker: TickerPath,
    body: PatentFamilyInput,
    pool: Pool,
    _owner: Owner,
):
    family_id = await store_patent_family(
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
    return {"family_id": family_id}


@router.get("/holdings/{ticker}/business-network", tags=["holdings"])
async def business_network(
    ticker: TickerPath, pool: Pool, _owner: Owner, as_of: Annotated[datetime | None, Query()] = None
):
    edges = await pool.fetch(
        """select id,subject_key,object_key,object_name,edge_type,direction,status,valid_from,
                  valid_to,evidence_tier,source,source_url,evidence_id,passage,known_at,
                  decision_reason,detail from public.company_graph_edge
           where subject_key=$1 and ($2::timestamptz is null or known_at <= $2)
             and (valid_from is null or valid_from<=coalesce($2::date,current_date))
             and (valid_to is null or valid_to>=coalesce($2::date,current_date))
           order by known_at desc,id""",
        ticker,
        as_of,
    )
    legacy_edges = await pool.fetch(
        """select id,object_key,object_name,type,source,source_ref,as_of,status,detail
           from public.entity_relationship where ticker=$1
             and ($2::date is null or as_of is null or as_of <= $2::date)
           order by as_of desc nulls last,id""",
        ticker,
        as_of,
    )
    events = await pool.fetch(
        """select id,event_type,event_state,announced_at,effective_at,known_at,source,source_url,
                  evidence_id,counterparty_key,terms,status,thesis_kpi,detail
           from public.company_event where ticker=$1 and ($2::timestamptz is null or known_at <= $2)
           order by coalesce(effective_at,announced_at) desc nulls last,id""",
        ticker,
        as_of,
    )
    patents = await pool.fetch(
        """select p.family_key,p.priority_date,p.first_publication_date,p.family_title,p.cpc_codes,
                  p.jurisdictions,p.citations_forward,p.citations_backward,p.legal_status,p.source,
                  p.source_url,p.known_at,o.applicant,o.ownership_confidence,o.ownership_basis,
                  o.valid_from ownership_valid_from,o.valid_to ownership_valid_to,o.evidence_id
           from public.patent_family p join public.patent_family_owner o on o.family_id=p.id
           where o.ticker=$1
             and ($2::timestamptz is null or p.known_at <= $2)
           order by p.priority_date desc nulls last,p.family_key""",
        ticker,
        as_of,
    )
    disclosures = await pool.fetch(
        """select id,relationship,direction,counterparty_key,counterparty_name,
                  anonymous_counterparty,reported_share,share_basis,fiscal_period,valid_from,
                  valid_to,evidence_id,passage,source_url,source,status,decision_reason,known_at
           from public.relationship_disclosure where ticker=$1
             and ($2::timestamptz is null or known_at <= $2)
           order by fiscal_period desc nulls last,known_at desc""",
        ticker,
        as_of,
    )
    normalized_edges = [dict(r) for r in edges]
    normalized_edges.extend(
        {
            "id": f"legacy:{r['id']}",
            "subject_key": ticker,
            "object_key": r["object_key"],
            "object_name": r["object_name"],
            "edge_type": r["type"],
            "direction": "outgoing",
            "status": r["status"],
            "valid_from": r["as_of"],
            "valid_to": None,
            "evidence_tier": "primary" if r["source"] == "gleif" else "candidate",
            "source": r["source"],
            "source_url": r["source_ref"],
            "evidence_id": None,
            "passage": None,
            "known_at": None,
            "decision_reason": None,
            "detail": r["detail"],
        }
        for r in legacy_edges
    )
    return {
        "ticker": ticker,
        "known_as_of": as_of,
        "edges": normalized_edges,
        "events": [dict(r) for r in events],
        "disclosures": [dict(r) for r in disclosures],
        "patents": [dict(r) for r in patents],
        "limitations": [
            "Suggested and disputed edges are candidates, not verified facts.",
            "Patent counts and citations are signals, not patent valuation or FTO advice.",
        ],
    }


@router.post("/holdings/{ticker}/relationship-disclosures", tags=["holdings"])
async def record_relationship_review(
    ticker: TickerPath, body: RelationshipReview, pool: Pool, _owner: Owner
) -> dict[str, int]:
    """Record an evidence-passage review. Only confirmed, dated records feed concentration."""
    row_id = await pool.fetchval(
        """insert into public.relationship_disclosure
           (ticker,relationship,direction,counterparty_key,counterparty_name,anonymous_counterparty,
            reported_share,share_basis,fiscal_period,valid_from,valid_to,evidence_id,passage,
            source_url,source,status,decision_reason,decided_at)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                  case when $16 in ('confirmed','rejected') then now() else null end)
           returning id""",
        ticker,
        body.relationship,
        body.direction,
        body.counterparty_key,
        body.counterparty_name,
        body.anonymous_counterparty,
        body.reported_share,
        body.share_basis,
        body.fiscal_period,
        body.valid_from,
        body.valid_to,
        body.evidence_id,
        body.passage,
        body.source_url,
        body.source,
        body.status,
        body.decision_reason,
    )
    return {"id": row_id}


@router.get("/holdings/{ticker}/customer-supplier-concentration", tags=["holdings"])
async def relationship_concentration(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
):
    rows = await pool.fetch(
        """select fiscal_period,relationship,count(*)::int disclosed_counterparties,
                  count(*) filter(where anonymous_counterparty)::int anonymous_count,
                  sum(reported_share) filter(where reported_share is not null) reported_share_sum,
                  count(*) filter(where reported_share is null)::int missing_share_count
           from public.relationship_disclosure where ticker=$1 and status='confirmed'
             and valid_from is not null and fiscal_period is not null
             and (valid_to is null or valid_to>=coalesce($2::date,current_date))
             and valid_from<=coalesce($2::date,current_date)
             and ($2::timestamptz is null or known_at<=$2)
           group by fiscal_period,relationship
           order by fiscal_period desc nulls last,relationship""",
        ticker,
        as_of,
    )
    return {
        "ticker": ticker,
        "periods": [dict(r) for r in rows],
        "limitations": [
            "Only owner-confirmed, dated disclosures are included.",
            "Reported shares are not imputed for anonymous or unnamed counterparties.",
        ],
    }


@router.get("/holdings/{ticker}/patent-trends", tags=["holdings"])
async def patent_trends(ticker: TickerPath, pool: Pool, _owner: Owner):
    rows = await pool.fetch(
        """select p.family_key,p.priority_date,p.cpc_codes,p.citations_forward
           from public.patent_family p join public.patent_family_owner o on o.family_id=p.id
           where o.ticker=$1 and (o.ownership_basis='direct' or
             (o.ownership_basis='dated_subsidiary_path' and o.valid_from is not null))""",
        ticker,
    )
    from steward.market_analysis import patent_trend

    return patent_trend([dict(r) for r in rows])
