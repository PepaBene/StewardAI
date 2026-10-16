"""Holdings presets and drawer (insight plan I9): one batched insight summary per holding and the
owner's free-text notes per ticker. Owner-only."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.routes import NoteParam, _as_dict, save_versioned
from steward.config.holdings_views import HoldingsViewsConfig
from steward.config.store import load_active
from steward.portfolio import holding_insight as hi
from steward.valuation.fair_value_view import Basis

router = APIRouter(tags=["holdings"])
TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


@router.get("/portfolio/holdings/insight", response_model=hi.HoldingsInsight, tags=["portfolio"])
async def get_holdings_insight(
    pool: Pool,
    owner: Owner,
    basis: Annotated[
        Basis, Query(description="Fair-value basis for the price-against-value columns.")
    ] = "dcf_default",
) -> hi.HoldingsInsight:
    """Every field the holdings presets show, in one request: last event, fair value and its step
    history, thesis line, income, score mark, fundamentals and value metrics, and note counts."""
    return await hi.holdings_insight(pool, owner.user_id, basis)


class HoldingsViewsVersion(BaseModel):
    id: int | None = Field(description="Saved version id; null while the defaults apply.")
    created_at: dt.datetime | None
    body: HoldingsViewsConfig


@router.get("/config/holdings-views", tags=["config"])
async def get_holdings_views(pool: Pool, _owner: Owner) -> HoldingsViewsVersion:
    """The selected holdings preset and the custom column order (the defaults until first saved)."""
    stored = await load_active(pool, "holdings_views", HoldingsViewsConfig)
    if stored is None:
        return HoldingsViewsVersion(id=None, created_at=None, body=HoldingsViewsConfig())
    d = _as_dict(stored)
    return HoldingsViewsVersion(id=d["id"], created_at=d["created_at"], body=d["body"])


@router.put("/config/holdings-views", tags=["config"])
async def save_holdings_views(
    body: HoldingsViewsConfig, pool: Pool, owner: Owner, note: NoteParam = None
) -> HoldingsViewsVersion:
    """Save the preset choice and custom columns as a new version (422 on an unknown column)."""
    stored = await save_versioned(
        pool, "holdings_views", body, created_by=owner.user_id, note=note, base_version_id=None
    )
    d = _as_dict(stored)
    return HoldingsViewsVersion(id=d["id"], created_at=d["created_at"], body=d["body"])


@router.get("/holdings/{ticker}/notes", response_model=list[hi.HoldingNote])
async def list_holding_notes(ticker: TickerPath, pool: Pool, owner: Owner) -> list[hi.HoldingNote]:
    return await hi.list_notes(pool, owner.user_id, ticker)


@router.post(
    "/holdings/{ticker}/notes", response_model=hi.HoldingNote, status_code=status.HTTP_201_CREATED
)
async def add_holding_note(
    ticker: TickerPath, body: hi.NoteIn, pool: Pool, owner: Owner
) -> hi.HoldingNote:
    """Store a note exactly as written; notes are never rewritten by AI."""
    return await hi.add_note(pool, owner.user_id, ticker, body.body)


@router.put("/holdings/{ticker}/notes/{note_id}", response_model=hi.HoldingNote)
async def update_holding_note(
    ticker: TickerPath, note_id: int, body: hi.NoteIn, pool: Pool, owner: Owner
) -> hi.HoldingNote:
    note = await hi.update_note(pool, owner.user_id, ticker, note_id, body.body)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    return note


@router.delete("/holdings/{ticker}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holding_note(ticker: TickerPath, note_id: int, pool: Pool, owner: Owner) -> None:
    if not await hi.delete_note(pool, owner.user_id, ticker, note_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
