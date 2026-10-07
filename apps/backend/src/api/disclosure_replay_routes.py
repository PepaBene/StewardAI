"""P8.6 / P8.7 routes: decision replay and the evidence-aware answer path. Owner-only.

Replay never shows a filing, estimate, deal or holder known after the requested date in its `known`
half; later changes are returned in `since`. The answer is a deterministic template over verified
stored claims, with each source and date, and says what it could not find.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel, Field

from api.auth import Owner
from api.db import Pool
from steward.disclosure import answer as answer_mod
from steward.disclosure import estimate_read, replay

router = APIRouter(prefix="/disclosures", tags=["disclosures"])


class ReplayItemOut(BaseModel):
    label: str
    known_at: dt.datetime | None
    detail: str
    ref: str | None


class ReplayDatasetOut(BaseModel):
    known: list[ReplayItemOut]
    since: list[ReplayItemOut]
    leaked_dropped: int
    error: str | None


class ReplayHoldingOut(BaseModel):
    ticker: str
    quantity: str
    value: str
    currency: str
    issuer_id: int | None
    datasets: dict[str, ReplayDatasetOut]


class ReplayOut(BaseModel):
    as_of: dt.datetime
    read_at: dt.datetime
    snapshot_date: dt.date | None
    holdings: list[ReplayHoldingOut]
    notes: list[str]


@router.get("/replay")
async def disclosure_replay(
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[AwareDatetime, Query(description="Reconstruct what was known by this time.")],
) -> ReplayOut:
    """Holdings and the filings, estimates, deals, owners and insider trades known at `as_of`;
    what became known afterwards is listed apart under `since`."""
    return ReplayOut(**await replay.replay(pool, as_of))


class AnswerRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=30)
    question: str = Field(min_length=3, max_length=500)
    as_of: AwareDatetime | None = None


class AnswerEvidenceOut(BaseModel):
    kind: str
    id: int
    label: str
    counterparty: str | None
    value: str | None
    period_end: dt.date | None
    quote: str | None
    section: str | None
    known_at: dt.datetime
    source: str
    document_url: str | None
    anonymous: bool


class AnswerOut(BaseModel):
    ticker: str
    as_of: dt.datetime
    text: str | None
    grounded: bool
    phrased_by: str
    limits: list[str]
    evidence: list[AnswerEvidenceOut]
    retrieved: int
    note: str | None


@router.post("/answer")
async def disclosure_answer(body: AnswerRequest, pool: Pool, _owner: Owner) -> AnswerOut:
    """Answer from verified stored evidence only, as known at `as_of` (default now)."""
    ticker = body.ticker.upper()
    when = body.as_of or dt.datetime.now(dt.UTC)
    issuer: Any = await estimate_read.issuer_for_ticker(pool, ticker)
    out = await answer_mod.answer(
        pool, ticker, int(issuer["id"]) if issuer else None, body.question, when
    )
    return AnswerOut(ticker=ticker, as_of=when, phrased_by="template", **out)
