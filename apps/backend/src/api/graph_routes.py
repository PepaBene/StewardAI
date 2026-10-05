"""Owner-only company graph decisions, review queues and read models.

Mount this router next to `free_sources_router`:

    from api.graph_routes import router as graph_router
    app.include_router(graph_router)
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from steward.graph import provider_gate
from steward.graph.deals import deal_timeline
from steward.graph.decisions import (
    GraphDecisionError,
    NotFound,
    decide_disclosure,
    decide_edge,
    decide_event,
)
from steward.graph.kpi import kpi_event_links
from steward.graph.passages import review_hit
from steward.graph.patents import competitor_overlap_rows
from steward.graph.reads import (
    HistoryKind,
    decision_history,
    list_graph_rows,
    resolve_reconciliation,
)
from steward.graph.relationships import portfolio_knock_on, review_queue
from steward.graph.successors import open_reconciliations
from steward.settings import get_settings
from steward.sources.edgar import EdgarClient

router = APIRouter()
TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]
ProviderPath = Annotated[str, Path(pattern=r"^[a-z0-9_]{2,40}$")]


class DecisionBody(BaseModel):
    """The owner's explicit decision. `confirm` needs evidence (or, for an edge, a cited
    passage); `reject` and `dispute` may move an already confirmed row; `reopen` returns a
    rejected or disputed row to review (and lifts the shared rejection)."""

    decision: Literal["confirm", "reject", "dispute", "reopen"]
    reason: str = Field(min_length=10, max_length=2000)


def _decision_error(exc: GraphDecisionError) -> HTTPException:
    return HTTPException(404 if isinstance(exc, NotFound) else 409, str(exc))


@router.post("/company-graph/edges/{edge_id}/decision", tags=["research"], response_model=None)
async def decide_company_edge(
    edge_id: int, body: DecisionBody, pool: Pool, owner: Owner
) -> dict[str, Any]:
    try:
        row = await decide_edge(
            pool, edge_id, body.decision, reason=body.reason, decided_by=owner.user_id
        )
    except GraphDecisionError as exc:
        raise _decision_error(exc) from exc
    return {"id": edge_id, "status": row["status"], "evidence_tier": row["evidence_tier"]}


@router.post(
    "/holdings/{ticker}/company-events/{event_id}/decision", tags=["holdings"], response_model=None
)
async def decide_company_event(
    ticker: TickerPath, event_id: int, body: DecisionBody, pool: Pool, _owner: Owner
) -> dict[str, Any]:
    owner_of = await pool.fetchval("select ticker from public.company_event where id=$1", event_id)
    if owner_of != ticker:
        raise HTTPException(404, f"no event {event_id} for {ticker}")
    try:
        row = await decide_event(pool, event_id, body.decision, reason=body.reason)
    except GraphDecisionError as exc:
        raise _decision_error(exc) from exc
    return {"id": event_id, "status": row["status"], "evidence_tier": row["evidence_tier"]}


@router.post(
    "/holdings/{ticker}/relationship-disclosures/{disclosure_id}/decision",
    tags=["holdings"],
    response_model=None,
)
async def decide_relationship_disclosure(
    ticker: TickerPath, disclosure_id: int, body: DecisionBody, pool: Pool, _owner: Owner
) -> dict[str, Any]:
    owner_of = await pool.fetchval(
        "select ticker from public.relationship_disclosure where id=$1", disclosure_id
    )
    if owner_of != ticker:
        raise HTTPException(404, f"no disclosure {disclosure_id} for {ticker}")
    try:
        row = await decide_disclosure(pool, disclosure_id, body.decision, reason=body.reason)
    except GraphDecisionError as exc:
        raise _decision_error(exc) from exc
    return {"id": disclosure_id, "status": row["status"]}


class QueueFilingHit(BaseModel):
    id: int
    ticker: str
    filer_name: str | None = None
    form: str | None = None
    filed_at: datetime | None = None
    url: str | None = None
    review_state: str
    status: str  # same value as review_state: pending, fetched or failed
    review_note: str | None = None
    attempts: int | None = None
    disclosure_id: int | None = None


class QueueDisclosure(BaseModel):
    id: int
    ticker: str
    relationship: str
    direction: str
    counterparty_name: str | None = None
    anonymous_counterparty: bool
    reported_share: float | None = None
    share_basis: str | None = None
    fiscal_period: str | None = None
    passage: str | None = None
    source_url: str | None = None
    known_at: datetime
    origin: str | None = None
    status: Literal["suggested", "disputed"]


class QueueContradiction(BaseModel):
    id: int
    ticker: str
    relationship: str | None = None
    fiscal_period: str | None = None
    kind: str
    disclosure_id: int | None = None
    other_disclosure_id: int | None = None
    detail: Any = None
    created_at: datetime
    status: str


class ReviewQueueResponse(BaseModel):
    filing_hits: list[QueueFilingHit]
    suggested_disclosures: list[QueueDisclosure]
    contradictions: list[QueueContradiction]


@router.get("/relationship-review/queue", tags=["research"], response_model=ReviewQueueResponse)
async def relationship_review_queue(
    pool: Pool,
    _owner: Owner,
    ticker: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ReviewQueueResponse:
    """Filing hits waiting for a passage read, suggested disclosures awaiting a decision and
    open contradictions. Every item carries its `status`."""
    return ReviewQueueResponse.model_validate(dict(await review_queue(pool, ticker, limit)))


@router.post("/relationship-review/hits/{hit_id}/review", tags=["research"], response_model=None)
async def review_filing_hit(hit_id: int, pool: Pool, _owner: Owner) -> dict[str, Any]:
    """Fetch the cited filing now, read the passages naming the holding and store them as
    suggested disclosures."""
    settings = get_settings()
    if not settings.sec_user_agent:
        raise HTTPException(409, "SEC_USER_AGENT is not configured")
    hit = await pool.fetchrow(
        "select * from public.filing_hit where id=$1 and kind='relationship_candidate'", hit_id
    )
    if hit is None:
        raise HTTPException(404, f"no filing-review candidate {hit_id}")
    names = [
        r["n"]
        for r in await pool.fetch(
            """select name n from public.instrument where t212_ticker=$1 and name is not null
               union select alias from public.entity_alias where ticker=$1
                 and kind in ('legal_name','other_name','alias')""",
            hit["ticker"],
        )
    ]
    if not names:
        raise HTTPException(422, "the holding has no resolved name to search the passage for")
    async with httpx.AsyncClient(timeout=60) as http:
        return await review_hit(
            pool, EdgarClient(http, settings.sec_user_agent), hit, holding_names=names
        )


class ContradictionResolution(BaseModel):
    resolution: Literal["resolved", "dismissed"]
    note: str = Field(min_length=5, max_length=1000)


@router.post(
    "/relationship-review/contradictions/{contradiction_id}/resolve",
    tags=["research"],
    response_model=None,
)
async def resolve_contradiction(
    contradiction_id: int, body: ContradictionResolution, pool: Pool, _owner: Owner
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """update public.relationship_contradiction set status=$2, resolution=$3, resolved_at=now()
           where id=$1 and status='open' returning id,status""",
        contradiction_id,
        body.resolution,
        body.note,
    )
    if row is None:
        raise HTTPException(404, "no open contradiction with that id")
    return dict(row)


@router.get("/holdings/{ticker}/deals", tags=["holdings"], response_model=None)
async def holding_deals(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    """Evidence-linked transaction timeline per deal, with the state as known at `as_of`, the
    confirmed terms and the dilution / leverage they imply (confirmed terms only)."""
    return {
        "ticker": ticker,
        "known_as_of": as_of,
        "deals": await deal_timeline(pool, ticker, as_of),
        "limitations": [
            "Suggested events show where a deal may stand; only confirmed events set the"
            " confirmed state and the terms behind dilution and leverage.",
        ],
    }


@router.get("/holdings/{ticker}/thesis-kpi-links", tags=["holdings"], response_model=None)
async def thesis_kpi_links(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    return {"ticker": ticker, "links": await kpi_event_links(pool, ticker, as_of)}


@router.get("/holdings/{ticker}/patent-competitor-overlap", tags=["holdings"], response_model=None)
async def patent_overlap(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    return {"ticker": ticker, "peers": await competitor_overlap_rows(pool, ticker, as_of)}


class ReconciliationItem(BaseModel):
    id: int
    ticker: str
    change_kind: str
    from_key: str | None = None
    to_key: str | None = None
    observed_source: str
    observed_at: datetime
    outcome: str
    deal_event_id: int | None = None
    detail: Any = None


class ReconciliationsResponse(BaseModel):
    open: list[ReconciliationItem]


@router.get(
    "/company-graph/reconciliations", tags=["research"], response_model=ReconciliationsResponse
)
async def graph_reconciliations(
    pool: Pool,
    _owner: Owner,
    ticker: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = None,
) -> ReconciliationsResponse:
    """Parent / successor changes that no closed deal explains and the owner has not closed."""
    rows = await open_reconciliations(pool, ticker)
    return ReconciliationsResponse(open=[ReconciliationItem.model_validate(r) for r in rows])


class ReconciliationResolution(BaseModel):
    resolution: Literal["resolved", "dismissed"]
    reason: str = Field(min_length=10, max_length=2000)


class ReconciliationResolved(BaseModel):
    id: int
    ticker: str
    status: Literal["accepted", "dismissed"]


@router.post(
    "/company-graph/reconciliations/{reconciliation_id}/resolve",
    tags=["research"],
    response_model=ReconciliationResolved,
)
async def resolve_graph_reconciliation(
    reconciliation_id: int, body: ReconciliationResolution, pool: Pool, owner: Owner
) -> ReconciliationResolved:
    """Close an open reconciliation with the owner's reason; it stops showing as unexplained.
    `resolved` is stored as status `accepted`; the reason is kept in `detail.resolution`."""
    reason = body.reason.strip()
    if len(reason) < 10:
        raise HTTPException(422, "the reason needs at least 10 characters")
    row = await resolve_reconciliation(
        pool, reconciliation_id, body.resolution, reason, str(owner.user_id)
    )
    if row is None:
        raise HTTPException(404, "no open reconciliation with that id")
    return ReconciliationResolved.model_validate(row)


class GraphRow(BaseModel):
    """One edge, event or disclosure; `kind` is the edge type, event type or relationship."""

    id: int
    ticker: str
    kind: str
    direction: str | None = None
    counterparty: str | None = None
    status: str
    evidence_tier: str | None = None
    source: str
    source_url: str
    evidence_id: int | None = None
    passage: str | None = None
    known_at: datetime
    decided_at: datetime | None = None
    decision_reason: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    object_key: str | None = None
    event_state: str | None = None
    announced_at: date | None = None
    effective_at: date | None = None
    anonymous_counterparty: bool | None = None
    reported_share: float | None = None
    fiscal_period: str | None = None


class GraphRowsPage(BaseModel):
    total: int
    limit: int
    offset: int
    rows: list[GraphRow]


GraphStatus = Literal["suggested", "confirmed", "rejected", "disputed"]
TickerQuery = Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")]
KindQuery = Annotated[str | None, Query(pattern=r"^[a-z_]{2,40}$")]
LimitQuery = Annotated[int, Query(ge=1, le=500)]
OffsetQuery = Annotated[int, Query(ge=0)]


async def _page(
    pool: Pool,
    what: Literal["edges", "events", "disclosures"],
    status: str | None,
    ticker: str | None,
    kind: str | None,
    limit: int,
    offset: int,
) -> GraphRowsPage:
    total, rows = await list_graph_rows(
        pool, what, status=status, ticker=ticker, kind=kind, limit=limit, offset=offset
    )
    return GraphRowsPage(
        total=total, limit=limit, offset=offset, rows=[GraphRow.model_validate(r) for r in rows]
    )


@router.get("/company-graph/edges", tags=["research"], response_model=GraphRowsPage)
async def list_company_edges(
    pool: Pool,
    _owner: Owner,
    status: GraphStatus | None = None,
    ticker: TickerQuery = None,
    kind: KindQuery = None,
    limit: LimitQuery = 100,
    offset: OffsetQuery = 0,
) -> GraphRowsPage:
    """Company-graph edges across all holdings, newest first, with each edge's current status,
    evidence / source URL, `known_at` and last decision reason. `kind` is the edge type."""
    return await _page(pool, "edges", status, ticker, kind, limit, offset)


@router.get("/company-graph/events", tags=["research"], response_model=GraphRowsPage)
async def list_company_events(
    pool: Pool,
    _owner: Owner,
    status: GraphStatus | None = None,
    ticker: TickerQuery = None,
    kind: KindQuery = None,
    limit: LimitQuery = 100,
    offset: OffsetQuery = 0,
) -> GraphRowsPage:
    """Company events across all holdings. `kind` is the event type."""
    return await _page(pool, "events", status, ticker, kind, limit, offset)


@router.get("/company-graph/disclosures", tags=["research"], response_model=GraphRowsPage)
async def list_relationship_disclosures(
    pool: Pool,
    _owner: Owner,
    status: GraphStatus | None = None,
    ticker: TickerQuery = None,
    kind: KindQuery = None,
    limit: LimitQuery = 100,
    offset: OffsetQuery = 0,
) -> GraphRowsPage:
    """Relationship disclosures across all holdings. `kind` is customer, supplier or
    procurement_award."""
    return await _page(pool, "disclosures", status, ticker, kind, limit, offset)


class HistoryEntry(BaseModel):
    changed_at: datetime
    from_status: str | None = None
    to_status: str
    reason: str | None = None
    decided_by: str | None = None  # known only for the newest entry (the current decider)


class DecisionHistory(BaseModel):
    kind: HistoryKind
    id: int
    current_status: str
    entries: list[HistoryEntry]  # oldest first
    limitations: list[str]


async def _history(pool: Pool, kind: HistoryKind, row_id: int) -> DecisionHistory:
    found = await decision_history(pool, kind, row_id)
    if found is None:
        raise HTTPException(404, f"no {kind} {row_id}")
    status, _by, entries = found
    return DecisionHistory(
        kind=kind,
        id=row_id,
        current_status=status,
        entries=[HistoryEntry.model_validate(e) for e in entries],
        limitations=[
            "The status log records each status change and its reason; only the current"
            " decider is known, so earlier deciders are not listed.",
        ],
    )


@router.get(
    "/company-graph/edges/{edge_id}/decision-history",
    tags=["research"],
    response_model=DecisionHistory,
)
async def edge_decision_history(edge_id: int, pool: Pool, _owner: Owner) -> DecisionHistory:
    return await _history(pool, "edge", edge_id)


@router.get(
    "/company-graph/events/{event_id}/decision-history",
    tags=["research"],
    response_model=DecisionHistory,
)
async def event_decision_history(event_id: int, pool: Pool, _owner: Owner) -> DecisionHistory:
    return await _history(pool, "event", event_id)


@router.get(
    "/company-graph/disclosures/{disclosure_id}/decision-history",
    tags=["research"],
    response_model=DecisionHistory,
)
async def disclosure_decision_history(
    disclosure_id: int, pool: Pool, _owner: Owner
) -> DecisionHistory:
    return await _history(pool, "disclosure", disclosure_id)


@router.get(
    "/relationship-review/suggestions/{suggestion_id}/decision-history",
    tags=["research"],
    response_model=DecisionHistory,
)
async def suggestion_decision_history(
    suggestion_id: int, pool: Pool, _owner: Owner
) -> DecisionHistory:
    """History of an owner relationship suggestion (entity_relationship). That table has no
    status-log trigger, so the entry is its last recorded decision only."""
    return await _history(pool, "suggestion", suggestion_id)


@router.post(
    "/provider-access/{provider}/{endpoint}/reset-block", tags=["sources"], response_model=None
)
async def reset_provider_block(
    provider: ProviderPath, endpoint: ProviderPath, pool: Pool, _owner: Owner
) -> dict[str, Any]:
    """Owner action: lift a hard block / cooldown after fixing the credential or plan."""
    try:
        return await provider_gate.reset_block(pool, provider, endpoint)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/portfolio/relationship-knock-on", tags=["portfolio"], response_model=None)
async def relationship_knock_on(
    pool: Pool, _owner: Owner, as_of: Annotated[datetime | None, Query()] = None
) -> dict[str, Any]:
    """Confirmed, dated customer / supplier links between two holdings."""
    return {
        "known_as_of": as_of,
        "links": await portfolio_knock_on(pool, as_of),
        "limitations": [
            "Only confirmed, dated, directed links between two open positions are listed.",
            "Shares are as reported by the filer and are not summed or imputed.",
        ],
    }
