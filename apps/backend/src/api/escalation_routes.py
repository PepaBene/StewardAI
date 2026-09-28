"""Authenticated API routes for the owner's handling of escalations: acknowledge, resolve,
reopen, and the oversight figures. Listing and reading escalations is in api.news_routes.

Included before the news router, so /escalations/stats is not taken for an escalation id.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from api.auth import Owner
from api.db import IdPath, Pool
from steward.news.escalation_lifecycle import (
    EscalationStats,
    escalation_stats,
    set_escalation_status,
)
from steward.news.read import EscalationOut, EscalationStatus, list_escalations

router = APIRouter()


@router.get("/escalations/stats", tags=["reports"])
async def get_escalation_stats(
    pool: Pool,
    _owner: Owner,
    days: Annotated[int, Query(ge=1, le=365, description="Window length, today included.")] = 30,
) -> EscalationStats:
    """How escalations are being handled: how many are open, acknowledged and resolved now,
    the median time to the first acknowledgement, and per day of the window how many were
    written and acknowledged (days in Europe/Prague)."""
    return await escalation_stats(pool, days)


class EscalationStatusUpdate(BaseModel):
    status: EscalationStatus


@router.patch("/escalations/{report_id}", tags=["reports"])
async def update_escalation(
    report_id: IdPath, body: EscalationStatusUpdate, pool: Pool, _owner: Owner
) -> EscalationOut:
    """Acknowledge, resolve or reopen an escalation. Acknowledging (or resolving one never
    acknowledged) records when; reopening clears the resolution but keeps the first
    acknowledgement. Setting the current status again changes nothing."""
    if not await set_escalation_status(pool, report_id, body.status):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No escalation {report_id}")
    found = await list_escalations(pool, None, 1, report_id=report_id)
    return found[0]
