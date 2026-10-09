"""P6: owner-only read routes for public deals, financing and corporate actions.

A deal is shown as it was KNOWN at `as_of`: the lifecycle timeline, every term version, the impacts
that the stated terms support (each with its inputs and formula version, or a reason it is null),
the
documents and the missing fields. Nothing here writes.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.routes import TickerPath
from steward.disclosure import deal_parties, deal_read
from steward.disclosure.deals import DealType
from steward.disclosure.deals import State as DealState

router = APIRouter(prefix="/disclosures", tags=["disclosures"])

AsOf = Annotated[AwareDatetime | None, Query(description="Show only what was known by this time.")]


class DealParticipantOut(BaseModel):
    role: str
    name: str
    issuer_id: int | None
    ticker: str | None
    share_class: str | None


class DealTransitionOut(BaseModel):
    id: int
    state: DealState
    effective_at: dt.datetime = Field(description="When it took effect in the world.")
    known_at: dt.datetime = Field(
        description="When it became known; never earlier than the record."
    )
    evidence_id: int
    document_version_id: int
    source_url: str | None
    quote: str | None
    span_start: int | None
    span_end: int | None
    rumor_source: str | None
    note: str | None


class DealRejectedOut(BaseModel):
    id: int
    state: str
    reason: str


class DealTermOut(BaseModel):
    name: str
    label: str
    state: Literal["stated", "not_stated"]
    value: str | None = Field(description="Whole units of the currency, or a fraction for a share.")


class DealTermChangeOut(BaseModel):
    field: str
    label: str
    old: str | None
    new: str | None


class DealTermVersionOut(BaseModel):
    id: int
    version_no: int
    supersedes_id: int | None
    origin: Literal["announced", "management_projection"]
    known_at: dt.datetime
    event_effective_at: dt.datetime | None
    currency: str | None
    document_version_id: int | None
    evidence_id: int | None
    source_url: str | None
    quote: str | None
    terms: list[DealTermOut]
    closing_conditions: str | None
    closing_conditions_state: Literal["stated", "not_stated"]
    unaffected_date: dt.date | None
    unaffected_source: str | None
    changes: list[DealTermChangeOut]
    problems: list[str]


class DealImpactOut(BaseModel):
    kind: str
    label: str
    origin: Literal["announced", "management_projection", "owner_scenario"]
    value: str | None
    unit: Literal["fraction", "currency", "shares", "ratio"]
    currency: str | None
    reason: str | None = Field(description="Why the value is null.")
    flags: list[str]
    inputs: dict[str, str]
    detail: dict[str, str]
    formula: str
    formula_version: str
    term_version_ids: list[int]


class DealBridgeSegmentOut(BaseModel):
    kind: Literal["cash", "stock", "debt", "earnout"]
    state: Literal["stated", "not_stated"]
    amount: str | None


class DealBridgeOut(BaseModel):
    currency: str | None
    segments: list[DealBridgeSegmentOut]
    known_total: str
    unstated: list[str]
    total_is_lower_bound: bool
    cash_pct: str | None
    stock_pct: str | None
    term_version_id: int | None


class DealDocumentOut(BaseModel):
    document_version_id: int
    role: str
    source_url: str | None
    known_at: dt.datetime
    filed_at: dt.datetime | None


class DealMissingOut(BaseModel):
    field: str
    label: str


class DealClosingOut(BaseModel):
    legal_closing_date: dt.date | None
    first_reporting_period_end: dt.date | None
    broker_posting_date: dt.date | None
    successor_name: str | None
    successor_ticker: str | None
    successor_issuer_id: int | None
    missing: list[str]
    problems: list[str]


class DealOut(BaseModel):
    id: int
    transaction_key: str
    deal_type: DealType
    type_label: str
    title: str
    ticker: str | None
    announced_at: dt.datetime | None
    known_at: dt.datetime
    state: DealState | None
    confidence: Literal["high", "medium", "low"]
    currency: str | None
    size: str | None = Field(description="Stated consideration (a lower bound) or financing.")
    participants: list[DealParticipantOut]
    transitions: list[DealTransitionOut]
    rejected_transitions: list[DealRejectedOut]
    term_versions: list[DealTermVersionOut]
    latest_version_id: int | None
    bridge: DealBridgeOut
    impacts: list[DealImpactOut]
    documents: list[DealDocumentOut]
    missing_fields: list[DealMissingOut]
    closing: DealClosingOut | None


class HoldingDealsOut(BaseModel):
    ticker: str
    as_of: dt.datetime
    deals: list[DealOut]
    total_known: int = Field(description="Deals known at the date before filters.")
    excluded_unknown_size: int
    notes: list[str]


@router.get("/company/{ticker}/deals", include_in_schema=False)
@router.get("/holdings/{ticker}/deals")
async def holding_public_deals(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: AsOf = None,
    deal_type: DealType | None = None,
    state: DealState | None = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
    min_size: Annotated[Decimal | None, Query(ge=0)] = None,
    max_size: Annotated[Decimal | None, Query(ge=0)] = None,
    offer_price: Annotated[
        Decimal | None,
        Query(gt=0, description="Your scenario: a per-share offer price, shown as your scenario."),
    ] = None,
) -> HoldingDealsOut:
    """Deals linked to a holding, as known at `as_of`: lifecycle, term versions, impacts, documents
    and missing fields. A size filter leaves out (and counts) deals whose size is not stated."""
    return HoldingDealsOut(
        **await deal_read.holding_deals(
            pool,
            ticker.upper(),
            as_of,
            deal_type=deal_type,
            state=state,
            currency=currency,
            min_size=min_size,
            max_size=max_size,
            owner_offer_price=offer_price,
        )
    )


class PortfolioDealRowOut(BaseModel):
    ticker: str
    deal_id: int
    title: str
    deal_type: DealType
    state: DealState | None
    weight: str


class PortfolioDealsOut(BaseModel):
    as_of: dt.datetime
    holdings: int
    denominator: str = Field(description="Total portfolio weight the figures are shares of.")
    covered_weight: str = Field(
        description="Weight in holdings with an active deal known at the date."
    )
    none_known: list[str] = Field(
        description="Holdings with no deal known: not proof that none exists."
    )
    unknown_state: list[str]
    by_state: dict[str, str]
    by_type: dict[str, str]
    rows: list[PortfolioDealRowOut]
    notes: list[str]


@router.get("/portfolio/deals")
async def portfolio_public_deals(
    pool: Pool, _owner: Owner, as_of: AsOf = None
) -> PortfolioDealsOut:
    """Portfolio weight sitting in holdings with an active deal, by state and type."""
    return PortfolioDealsOut(**await deal_read.portfolio_view(pool, as_of))


class PartySuggestionOut(BaseModel):
    participant_id: int
    deal_id: int
    role: str
    name: str
    status: str
    issuer_ids: list[int]
    basis: str | None
    note: str


class PartyDecisionIn(BaseModel):
    issuer_id: Annotated[int, Field(gt=0)]


@router.get("/deals/parties/pending")
async def pending_deal_parties(
    pool: Pool, _owner: Owner, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[PartySuggestionOut]:
    """Deal parties with no issuer yet, each with the issuers their name could belong to."""
    return [
        PartySuggestionOut(
            participant_id=s.participant_id,
            deal_id=s.deal_id,
            role=s.role,
            name=s.name,
            status=s.status,
            issuer_ids=list(s.issuer_ids),
            basis=s.basis,
            note=s.note,
        )
        for s in await deal_parties.pending(pool, limit)
    ]


@router.post("/deals/parties/{participant_id}/confirm", status_code=204)
async def confirm_deal_party(
    participant_id: int, body: PartyDecisionIn, pool: Pool, owner: Owner
) -> None:
    """The owner says this party is this issuer."""
    if not await deal_parties.confirm(
        pool, participant_id, body.issuer_id, f"owner:{owner.user_id}"
    ):
        raise HTTPException(404, "no unmatched party with that id, or no such issuer")


@router.post("/deals/parties/{participant_id}/reject", status_code=204)
async def reject_deal_party(
    participant_id: int, body: PartyDecisionIn, pool: Pool, owner: Owner
) -> None:
    """The owner says this party is not this issuer; it is not suggested again."""
    await deal_parties.reject(pool, participant_id, body.issuer_id, f"owner:{owner.user_id}")
