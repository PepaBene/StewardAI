"""Authenticated CRUD for the owner's thesis-oriented watchlist."""

from decimal import Decimal
from typing import Annotated

import asyncpg
from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from api.auth import Owner
from api.db import Pool

router = APIRouter(prefix="/watchlist", tags=["watchlist"])
Ticker = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,32}$")]
ListName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class WatchlistItem(BaseModel):
    id: int
    ticker: str
    list_name: str
    buy_target: float | None
    sell_target: float | None
    reason: str | None
    note: str | None
    created_at: str
    updated_at: str


class WatchlistCreate(BaseModel):
    ticker: Ticker
    list_name: ListName = "Default"
    buy_target: Decimal | None = Field(default=None, gt=0)
    sell_target: Decimal | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=5000)


class WatchlistUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    list_name: ListName | None = None
    buy_target: Decimal | None = Field(default=None, gt=0)
    sell_target: Decimal | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=5000)


def _item(row: asyncpg.Record) -> WatchlistItem:
    return WatchlistItem(
        id=row["id"],
        ticker=row["ticker"],
        list_name=row["list_name"],
        buy_target=float(row["buy_target"]) if row["buy_target"] is not None else None,
        sell_target=float(row["sell_target"]) if row["sell_target"] is not None else None,
        reason=row["reason"],
        note=row["note"],
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


@router.get("", response_model=list[WatchlistItem])
async def list_watchlist(pool: Pool, owner: Owner) -> list[WatchlistItem]:
    rows = await pool.fetch(
        "select id, ticker, list_name, buy_target, sell_target, reason, note, created_at,"
        " updated_at from public.watchlist_item where owner_id = $1 order by list_name, ticker",
        owner.user_id,
    )
    return [_item(row) for row in rows]


@router.post("", response_model=WatchlistItem, status_code=status.HTTP_201_CREATED)
async def add_watchlist_item(body: WatchlistCreate, pool: Pool, owner: Owner) -> WatchlistItem:
    try:
        row = await pool.fetchrow(
            "insert into public.watchlist_item"
            " (owner_id, ticker, list_name, buy_target, sell_target, reason, note)"
            " values ($1, upper($2), $3, $4, $5, $6, $7) returning id, ticker, list_name,"
            " buy_target, sell_target, reason, note, created_at, updated_at",
            owner.user_id,
            body.ticker,
            body.list_name,
            body.buy_target,
            body.sell_target,
            body.reason,
            body.note,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ticker is already in this watchlist"
        ) from exc
    return _item(row)


@router.patch("/{item_id}", response_model=WatchlistItem)
async def update_watchlist_item(
    item_id: Annotated[int, Path(ge=1)], body: WatchlistUpdate, pool: Pool, owner: Owner
) -> WatchlistItem:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Provide at least one field")
    if "list_name" in changes and changes["list_name"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "list_name cannot be null")
    fields = {"list_name", "buy_target", "sell_target", "reason", "note"}
    assignments = []
    values: list[object] = [item_id, owner.user_id]
    for field, value in changes.items():
        if field not in fields:
            continue
        values.append(value)
        assignments.append(f"{field} = ${len(values)}")
    assignments.append("updated_at = now()")
    # `fields` is a fixed whitelist, so only column names are interpolated.
    sql = (
        f"update public.watchlist_item set {', '.join(assignments)}"  # noqa: S608
        " where id = $1 and owner_id = $2 returning id, ticker, list_name, buy_target,"
        " sell_target, reason, note, created_at, updated_at"
    )
    try:
        row = await pool.fetchrow(sql, *values)
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ticker is already in this watchlist"
        ) from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Watchlist item not found")
    return _item(row)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist_item(
    item_id: Annotated[int, Path(ge=1)], pool: Pool, owner: Owner
) -> None:
    result = await pool.execute(
        "delete from public.watchlist_item where id = $1 and owner_id = $2", item_id, owner.user_id
    )
    if result == "DELETE 0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Watchlist item not found")
