"""Authenticated API routes for Phase 2b: identity and structure, relationship decisions,
price history and risk, reference-data gaps, the exchange calendar, and on-demand runs."""

import datetime as dt
import json
from typing import Annotated

import asyncpg
from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import IdPath, Pool
from api.routes import _enqueue
from api.typed_responses import IdentityDecisions
from steward.config.common import Ticker
from steward.config.jobs import ExchangeClosure
from steward.db_json import decode_row
from steward.ownership.read import HoldingOwnership, holding_ownership
from steward.portfolio.read import JobSummary
from steward.reference.lifecycle import (
    aliases_as_of,
    legal_name_as_of,
    relationships_as_of,
)
from steward.reference.matching import Match, record_match_decision
from steward.reference.read import (
    ExchangeCalendar,
    HoldingIdentity,
    PriceHistory,
    ReferenceGaps,
    RelationshipDecisionError,
    RelationshipOut,
    confirm_closures,
    decide_relationship,
    exchange_calendar,
    holding_identity,
    price_history,
    reference_gaps,
)
from steward.sources.documents import document_versions_as_of, record_evidence_as_of

router = APIRouter()

TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


class IdentityOverride(BaseModel):
    issuer_id: int | None = Field(default=None, ge=1)
    security_id: int | None = Field(default=None, ge=1)
    listing_id: int | None = Field(default=None, ge=1)
    decision: str = Field(pattern=r"^(confirmed|rejected|unresolved)$")
    reason: str = Field(min_length=1, max_length=500)


class ListingLifecyclePoint(BaseModel):
    mic: str = Field(description="Trading venue (market identifier code) the record is for.")
    publication_date: dt.date | None
    published_at: dt.datetime | None = Field(
        default=None, description="The register's publication timestamp of this record."
    )
    valid_from: dt.date | None = Field(
        default=None, description="Start of the window the record was in force (FIRDS)."
    )
    valid_to: dt.date | None = Field(
        default=None, description="End of that window; null while open or unreported."
    )
    first_trade_at: dt.datetime | None = Field(
        default=None, description="The venue's reported trading start for the instrument."
    )
    status: str | None
    terminated_at: dt.datetime | None
    issuer_lei: str | None
    currency: str | None
    source_url: str
    known_at: dt.datetime


class LegalEntitySnapshot(BaseModel):
    lei: str
    known_at: dt.datetime
    valid_from: dt.date | None
    valid_to: dt.date | None
    legal_name: str | None
    other_names: list[str]
    entity_status: str | None
    registration_status: str | None
    successor_lei: str | None
    source_url: str


@router.get("/reference/listings/{isin}/history", tags=["reference"])
async def listing_history(
    isin: Annotated[str, Path(pattern=r"^[A-Z0-9]{12}$")],
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[dt.datetime | None, Query()] = None,
) -> list[ListingLifecyclePoint]:
    # FIRDS re-publishes a venue's record with new validity windows: rows that differ only in
    # register-internal ids are one row here (the latest read of it), and the fields that do
    # distinguish two states of the same venue are returned instead of being hidden.
    rows = await pool.fetch(
        """with s as (
             select mic,publication_date,status,terminated_at,issuer_lei,currency,source_url,
                    known_at,
                    (raw->>'publication_date')::timestamptz published_at,
                    (raw->>'valid_from_date')::timestamptz::date valid_from,
                    (raw->>'valid_to_date')::timestamptz::date valid_to,
                    (raw->>'mrkt_trdng_start_date')::timestamptz first_trade_at
             from public.listing_lifecycle_snapshot
             where isin=$1 and ($2::timestamptz is null or known_at <= $2)
           )
           select distinct on (mic,publication_date,published_at,status,terminated_at,issuer_lei,
                    currency,valid_from,valid_to,first_trade_at)
                  mic,publication_date,published_at,valid_from,valid_to,first_trade_at,status,
                  terminated_at,issuer_lei,currency,source_url,known_at
           from s
           order by mic,publication_date,published_at,status,terminated_at,issuer_lei,currency,
                    valid_from,valid_to,first_trade_at,known_at desc""",
        isin,
        as_of,
    )
    return [ListingLifecyclePoint(**dict(row)) for row in rows]


@router.get("/reference/legal-entities/{lei}/history", tags=["reference"])
async def legal_entity_history(
    lei: Annotated[str, Path(pattern=r"^[A-Z0-9]{20}$")],
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[dt.datetime | None, Query()] = None,
) -> list[LegalEntitySnapshot]:
    rows = await pool.fetch(
        """select lei,known_at,valid_from,valid_to,legal_name,other_names,entity_status,
                  registration_status,successor_lei,source_url
           from public.legal_entity_snapshot
           where lei=$1 and ($2::timestamptz is null or known_at <= $2)
           order by known_at desc""",
        lei,
        as_of,
    )
    return [
        LegalEntitySnapshot(
            **{
                **dict(row),
                "other_names": json.loads(row["other_names"])
                if isinstance(row["other_names"], str)
                else row["other_names"],
            }
        )
        for row in rows
    ]


@router.get("/reference/identity-decisions", tags=["reference"], response_model=IdentityDecisions)
async def identity_decisions(pool: Pool, _owner: Owner) -> list[dict[str, object]]:
    rows = await pool.fetch(
        """select id,instrument_ticker,candidate_issuer_id,candidate_security_id,
                  candidate_listing_id,decision,score,reasons,decided_at,decided_by
           from public.identity_match_decision order by decided_at desc limit 500"""
    )
    return [decode_row(row, ("reasons",)) for row in rows]


async def _validated_override_ids(
    conn: asyncpg.Connection, ticker: str, body: IdentityOverride
) -> tuple[int | None, int | None, int | None]:
    """Resolve and check the ids an owner names, so a bad id is a 4xx and a confirmation can
    never point at nothing (B5)."""
    if not await conn.fetchval("select 1 from public.instrument where t212_ticker=$1", ticker):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown instrument {ticker}")
    issuer_id, security_id, listing_id = body.issuer_id, body.security_id, body.listing_id
    if listing_id is not None:
        listing = await conn.fetchrow(
            "select security_id from public.security_listing where id=$1", listing_id
        )
        if listing is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown listing_id")
        if security_id is not None and security_id != listing["security_id"]:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "listing_id belongs to another security"
            )
        security_id = listing["security_id"]
    if security_id is not None:
        security = await conn.fetchrow(
            "select issuer_id from public.security where id=$1", security_id
        )
        if security is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown security_id")
        if issuer_id is not None and security["issuer_id"] not in (None, issuer_id):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "security_id belongs to another issuer"
            )
        issuer_id = issuer_id if issuer_id is not None else security["issuer_id"]
    if issuer_id is not None and not await conn.fetchval(
        "select 1 from public.issuer where id=$1", issuer_id
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown issuer_id")
    if body.decision == "confirmed" and security_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Confirming an identity needs a security_id (or a listing_id); an issuer alone "
            "does not identify an instrument",
        )
    return issuer_id, security_id, listing_id


@router.post("/reference/identity/{ticker}/override", tags=["reference"])
async def override_identity(
    ticker: TickerPath, body: IdentityOverride, pool: Pool, owner: Owner
) -> dict[str, int]:
    confirmed = body.decision == "confirmed"
    try:
        async with pool.acquire() as conn, conn.transaction():
            issuer_id, security_id, listing_id = await _validated_override_ids(conn, ticker, body)
            match = Match(
                None,
                1 if confirmed else 0,
                body.decision,
                (body.reason,),
                level="security" if security_id is not None else "none",
            )
            decision_id = await record_match_decision(
                conn,
                match,
                instrument_ticker=ticker,
                issuer_id=issuer_id,
                security_id=security_id,
                listing_id=listing_id,
                decided_by=owner.user_id,
            )
            await conn.execute(
                """insert into public.instrument_security_link
                   (ticker,security_id,listing_id,status,score,evidence,decided_at,decided_by)
                   values($1,$2,$3,$4,$5,jsonb_build_object('reason',$6::text),now(),$7)
                   on conflict(ticker) do update set security_id=excluded.security_id,
                     listing_id=excluded.listing_id,status=excluded.status,score=excluded.score,
                     evidence=excluded.evidence,decided_at=excluded.decided_at,
                     decided_by=excluded.decided_by,updated_at=now()""",
                ticker,
                security_id,
                listing_id,
                body.decision,
                1 if confirmed else 0,
                body.reason,
                owner.user_id,
            )
    except asyncpg.ForeignKeyViolationError as exc:
        # Lost a race with a delete; still the caller's reference that is wrong, not a 500.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Referenced record not found: {exc.detail}"
        ) from exc
    return {"decision_id": decision_id}


@router.get("/holdings/{ticker}/identity", tags=["holdings"])
async def get_holding_identity(ticker: TickerPath, pool: Pool, _owner: Owner) -> HoldingIdentity:
    """Identifiers, listings, home market, legal entity, group structure and the
    relationships waiting for confirmation."""
    identity = await holding_identity(pool, ticker)
    if identity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nothing is known about {ticker}")
    return identity


class RelationshipDecision(BaseModel):
    relationship: RelationshipOut
    holding_profiles_version_id: int | None = Field(
        description="The new holding-profiles version, when the confirmation changed it."
    )


async def _decide(
    relationship_id: int, confirm: bool, pool: Pool, owner_id: str
) -> RelationshipDecision:
    try:
        rel, version = await decide_relationship(pool, relationship_id, confirm, owner_id)
    except RelationshipDecisionError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.not_found else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(exc)) from exc
    return RelationshipDecision(relationship=rel, holding_profiles_version_id=version)


@router.post("/relationships/{relationship_id}/confirm", tags=["holdings"])
async def confirm_relationship(
    relationship_id: IdPath, pool: Pool, owner: Owner
) -> RelationshipDecision:
    """Confirm a suggested relationship. A competitor, supplier or customer is added to the
    holding profile (a new config version), so the next news run matches on it."""
    return await _decide(relationship_id, True, pool, owner.user_id)


@router.post("/relationships/{relationship_id}/reject", tags=["holdings"])
async def reject_relationship(
    relationship_id: IdPath, pool: Pool, owner: Owner
) -> RelationshipDecision:
    """Reject a suggested relationship; it is never suggested again."""
    return await _decide(relationship_id, False, pool, owner.user_id)


@router.get("/holdings/{ticker}/prices", tags=["holdings"])
async def get_price_history(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    days: Annotated[int, Query(ge=7, le=3700)] = 365,
) -> PriceHistory:
    """Daily closes with the benchmark, and the risk card computed from the stored bars."""
    return await price_history(pool, ticker, days)


@router.get("/reference/gaps", tags=["reference"])
async def get_reference_gaps(pool: Pool, _owner: Owner) -> ReferenceGaps:
    """Instruments whose identity could not be resolved, and symbols with no price history."""
    return await reference_gaps(pool)


@router.get("/calendar/exchanges", tags=["reference"])
async def get_exchange_calendar(pool: Pool, _owner: Owner) -> ExchangeCalendar:
    """Confirmed exchange closures and the public holidays suggested as closures."""
    return await exchange_calendar(pool, dt.datetime.now(dt.UTC).date())


class ClosureConfirmation(BaseModel):
    closures: list[ExchangeClosure] = Field(min_length=1)


class ClosureResult(BaseModel):
    jobs_version_id: int


@router.post("/calendar/exchanges/closures", tags=["reference"])
async def add_exchange_closures(
    body: ClosureConfirmation, pool: Pool, owner: Owner
) -> ClosureResult:
    """Confirm closures into the Jobs config (a new version); used from the next run."""
    return ClosureResult(jobs_version_id=await confirm_closures(pool, body.closures, owner.user_id))


class ReferenceRunRequest(BaseModel):
    ticker: Ticker | None = Field(default=None, description="One holding, or all when null.")
    force: bool = Field(default=False, description="Refresh OpenFIGI and Wikidata now.")


@router.post("/jobs/reference-data", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_reference_data(body: ReferenceRunRequest, pool: Pool, owner: Owner) -> JobSummary:
    """Resolve identity, reference data and structure now."""
    return await _enqueue(
        pool, "reference_data", owner.user_id, body.model_dump(mode="json", exclude_none=True)
    )


@router.post("/jobs/price-history", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_price_history(pool: Pool, owner: Owner) -> JobSummary:
    """Collect price and FX history now."""
    return await _enqueue(pool, "price_history", owner.user_id)


@router.post("/jobs/holiday-refresh", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_holiday_refresh(pool: Pool, owner: Owner) -> JobSummary:
    """Load public holidays for this and next year now."""
    return await _enqueue(pool, "holiday_refresh", owner.user_id)


@router.post("/jobs/returns-refresh", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_returns_refresh(pool: Pool, owner: Owner) -> JobSummary:
    """Compute and store price and total returns for the held securities now."""
    return await _enqueue(pool, "returns_refresh", owner.user_id)


@router.post("/jobs/exchange-probes", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_exchange_probes(pool: Pool, owner: Owner) -> JobSummary:
    """Probe the free price endpoints, one held symbol per exchange, now."""
    return await _enqueue(pool, "exchange_probes", owner.user_id)


@router.get("/holdings/{ticker}/ownership", tags=["reference"])
async def get_holding_ownership(
    ticker: Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")], pool: Pool, _owner: Owner
) -> HoldingOwnership:
    """Institutional ownership from SEC Form 13F data sets: holders, shares and value per
    report period (newest first) with changes, and the largest holders. Only US-listed
    holdings are covered; every figure carries its period and filing date."""
    return await holding_ownership(pool, ticker)


class DocumentVersionOut(BaseModel):
    id: int
    document_id: int
    content_hash: str
    filed_at: dt.datetime | None
    accepted_at: dt.datetime | None
    published_at: dt.datetime | None
    retrieved_at: dt.datetime
    known_at: dt.datetime
    amends_version_id: int | None
    superseded_by_id: int | None = Field(
        description="Shown only when the superseding version was itself known at the cutoff."
    )
    is_current: bool = Field(description="The version in force at the cutoff.")
    raw_location: str | None


@router.get("/reference/documents/versions", tags=["reference"])
async def document_versions(
    pool: Pool,
    _owner: Owner,
    regulator: Annotated[str, Query(min_length=1, max_length=64)],
    external_id: Annotated[str, Query(min_length=1, max_length=512)],
    as_of: Annotated[
        dt.datetime | None, Query(description="Knowledge cutoff; default now.")
    ] = None,
) -> list[DocumentVersionOut]:
    """The versions of a public document known at the cutoff, oldest first (D1.8)."""
    rows = await document_versions_as_of(pool, regulator, external_id, as_of)
    return [DocumentVersionOut(**row) for row in rows]


class RecordEvidenceOut(BaseModel):
    id: int
    domain_table: str
    record_key: str
    linked_at: dt.datetime
    document_version_id: int
    content_hash: str
    version_known_at: dt.datetime
    accepted_at: dt.datetime | None
    filed_at: dt.datetime | None
    published_at: dt.datetime | None
    regulator: str
    external_id: str
    stable_url: str
    evidence_id: int | None
    quote: str | None
    page: str | None
    table_name: str | None


@router.get("/reference/records/{domain_table}/{record_key}/evidence", tags=["reference"])
async def record_evidence(
    domain_table: Annotated[str, Path(pattern=r"^[a-z_0-9]{1,64}$")],
    record_key: Annotated[str, Path(min_length=1, max_length=256)],
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[
        dt.datetime | None, Query(description="Knowledge cutoff; default now.")
    ] = None,
) -> list[RecordEvidenceOut]:
    """The document versions and quotes behind a domain record as recorded by the cutoff."""
    rows = await record_evidence_as_of(pool, domain_table, record_key, as_of)
    return [RecordEvidenceOut(**row) for row in rows]


class IssuerAliasOut(BaseModel):
    name: str
    kind: str
    valid_from: dt.date | None
    valid_to: dt.date | None
    known_at: dt.datetime


class IssuerRelationshipOut(BaseModel):
    id: int
    predecessor_id: int
    successor_id: int
    relationship: str
    valid_from: dt.date
    valid_to: dt.date | None
    known_at: dt.datetime


class IssuerAsOf(BaseModel):
    issuer_id: int
    date: dt.date
    known_at: dt.datetime
    legal_name: str | None = Field(description="The legal name on that date, if one was known.")
    aliases: list[IssuerAliasOut]
    relationships: list[IssuerRelationshipOut]


@router.get("/reference/issuers/{issuer_id}/as-of", tags=["reference"])
async def issuer_as_of(
    issuer_id: IdPath,
    pool: Pool,
    _owner: Owner,
    date: Annotated[dt.date, Query(description="The day the screen is dated.")],
    known_at: Annotated[
        dt.datetime | None, Query(description="Knowledge cutoff; default now.")
    ] = None,
) -> IssuerAsOf:
    """An issuer's names and successor/predecessor links as they applied on `date` and as
    recorded by `known_at`: a historical screen never inherits a later rename or merger."""
    if not await pool.fetchval("select 1 from public.issuer where id=$1", issuer_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown issuer {issuer_id}")
    cutoff = known_at or dt.datetime.now(dt.UTC)
    return IssuerAsOf(
        issuer_id=issuer_id,
        date=date,
        known_at=cutoff,
        legal_name=await legal_name_as_of(pool, issuer_id, date, known_at=cutoff),
        aliases=[
            IssuerAliasOut(**a) for a in await aliases_as_of(pool, issuer_id, date, known_at=cutoff)
        ],
        relationships=[
            IssuerRelationshipOut(**r)
            for r in await relationships_as_of(pool, issuer_id, date, known_at=cutoff)
        ],
    )
