"""Owner-only company graph decisions, review queues and read models.

Mount this router next to `free_sources_router`:

    from api.graph_routes import router as graph_router
    app.include_router(graph_router)
"""

from datetime import datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.routes import _enqueue
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
from steward.graph.relationships import portfolio_knock_on, review_queue
from steward.graph.successors import open_reconciliations
from steward.portfolio.read import JobSummary
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


@router.get("/relationship-review/queue", tags=["research"], response_model=None)
async def relationship_review_queue(
    pool: Pool,
    _owner: Owner,
    ticker: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """Filing hits waiting for a passage read, suggested disclosures awaiting a decision and
    open contradictions."""
    return dict(await review_queue(pool, ticker, limit))


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


@router.get("/company-graph/reconciliations", tags=["research"], response_model=None)
async def graph_reconciliations(
    pool: Pool,
    _owner: Owner,
    ticker: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = None,
) -> dict[str, Any]:
    """Parent / successor changes that no closed deal explains."""
    return {"open": await open_reconciliations(pool, ticker)}


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


@router.post("/jobs/graph-review", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_graph_review(pool: Pool, owner: Owner) -> JobSummary:
    """Review queued relationship candidates, extract deal events and reconcile parent changes
    now."""
    return await _enqueue(pool, "graph_review", owner.user_id)
