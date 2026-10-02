"""The owner's decision trail for free-source alerts (credit stress, trade exposure, patent
status, company graph): why each alert was raised, throttled, ignored or left for review."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from api.auth import Owner
from api.db import Pool
from api.routes import _enqueue
from steward.free_source_alerts import TrailOut, read_trail
from steward.portfolio.read import JobSummary

router = APIRouter()


@router.get("/alerts/free-source-trail", tags=["alerts"], response_model=TrailOut)
async def get_free_source_alert_trail(
    pool: Pool,
    _owner: Owner,
    rule: Literal["credit_stress", "trade_exposure", "patent_status", "company_graph"]
    | None = None,
    outcome: Literal[
        "raised",
        "throttled",
        "revision_ignored",
        "baseline",
        "not_material",
        "no_baseline",
        "persisting",
        "review_needed",
        "below_severity",
        "blocked_by_gate",
    ]
    | None = None,
    ticker: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> TrailOut:
    """Newest first. Silent outcomes (value below threshold, identical re-fetch) are not
    stored; they appear only in the job step detail. `blocked_by_gate` rows are provisional:
    a Phase 8 verification gate was closed, and the next run replaces the row once it opens."""
    return await read_trail(
        pool, rule=rule, outcome=outcome, ticker=ticker, limit=limit, before_id=before_id
    )


@router.post("/jobs/free-source-alerts", tags=["jobs"], status_code=202)
async def run_free_source_alerts(pool: Pool, owner: Owner) -> JobSummary:
    """Evaluate the free-source alert rules now."""
    return await _enqueue(pool, "free_source_alerts", owner.user_id)
