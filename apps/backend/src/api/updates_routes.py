"""The typed updates feed (insight I13.1): one card model for the Inbox, News and the dashboard."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from steward.feed import updates

router = APIRouter(tags=["updates"])


@router.get("/updates")
async def get_updates(
    pool: Pool,
    owner: Owner,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
    type: Annotated[updates.UpdateType | None, Query()] = None,
    unread: bool = False,
    ticker: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=300)] = 100,
) -> updates.UpdatesFeed:
    """Inbox items, news stories, what-changed items and dividend events of the last `days`, as
    typed cards with the holding's latest close and one-day move. Counts per type (with unread)
    are taken before the type filter."""
    return await updates.updates_feed(
        pool, owner.user_id, days=days, type_filter=type, unread_only=unread, ticker=ticker,
        limit=limit,
    )  # fmt: skip


class MarkRead(BaseModel):
    keys: list[Annotated[str, Field(min_length=3, max_length=200)]] = Field(
        min_length=1, max_length=500
    )


class MarkedRead(BaseModel):
    marked: int


@router.post("/updates/read")
async def mark_updates_read(pool: Pool, owner: Owner, body: MarkRead) -> MarkedRead:
    """Mark updates read; an inbox item's status becomes `read`."""
    return MarkedRead(marked=await updates.mark_read(pool, owner.user_id, body.keys))
