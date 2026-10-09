"""The pilot defect log (M8.3): problems the owner finds while using the app that no automatic
check sees, ranked by the share of the portfolio they touch. Append-only: closing a defect is a
newer row that points at the first, so the report and its fix both stay on record."""

from __future__ import annotations

import datetime as dt
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.quality.cockpit import _weights as holding_weights

State = Literal["open", "fixed", "wont_fix"]


class DefectIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    detail: str | None = Field(default=None, max_length=2000)
    ticker: str | None = Field(default=None, max_length=32, description="The holding it affects.")
    feature: str | None = Field(default=None, max_length=60, description="Release-gate feature id.")


class DefectUpdate(BaseModel):
    state: State
    detail: str | None = Field(default=None, max_length=2000)


class DefectRow(BaseModel):
    id: int
    parent_id: int | None
    title: str
    detail: str | None
    ticker: str | None
    feature: str | None
    state: State
    created_at: dt.datetime


class Defect(BaseModel):
    id: int = Field(description="The id of the first report.")
    title: str
    detail: str | None
    ticker: str | None
    feature: str | None
    state: State
    opened_at: dt.datetime
    weight: float | None = Field(
        description="Share of the portfolio held in the affected ticker today; None when no "
        "ticker is named or it is not held."
    )


def weight_for(ticker: str | None, weights: dict[str, float]) -> float | None:
    """Portfolio weight of a typed ticker: the broker ticker itself (AAPL_US_EQ), or a bare
    symbol (aapl, AAPL) folded to the held broker ticker whose symbol part matches, ignoring
    case. Several listings of one symbol are added up; None when nothing held matches."""
    if not ticker:
        return None
    key = ticker.strip().upper()
    exact = {t: w for t, w in weights.items() if t.upper() == key}
    if exact:
        return sum(exact.values())
    bare = [w for t, w in weights.items() if t.split("_")[0].upper() == key]
    return sum(bare) if bare else None


def current(rows: list[DefectRow], weights: dict[str, float]) -> list[Defect]:
    """One line per defect with its latest state, largest affected weight first, open before
    closed."""
    roots: dict[int, DefectRow] = {}
    latest: dict[int, DefectRow] = {}
    for r in sorted(rows, key=lambda r: r.id):
        root = r.parent_id if r.parent_id in roots else r.id
        if r.parent_id is None:
            roots[r.id] = r
        latest[root] = r
    out = [
        Defect(
            id=root, title=roots[root].title, detail=row.detail or roots[root].detail,
            ticker=roots[root].ticker, feature=roots[root].feature, state=row.state,
            opened_at=roots[root].created_at,
            weight=weight_for(roots[root].ticker, weights),
        )
        for root, row in latest.items()
        if root in roots
    ]  # fmt: skip
    return sorted(
        out,
        key=lambda d: (d.state != "open", -(d.weight if d.weight is not None else -1.0), -d.id),
    )


_COLUMNS = "id, parent_id, title, detail, ticker, feature, state, created_at"


async def report(pool: asyncpg.Pool, owner_id: str, body: DefectIn) -> None:
    await pool.execute(
        "insert into public.pilot_defect (owner_id, title, detail, ticker, feature)"
        " values ($1,$2,$3,$4,$5)",
        owner_id,
        body.title.strip(),
        body.detail,
        body.ticker.strip().upper() if body.ticker else None,
        body.feature,
    )


async def update(pool: asyncpg.Pool, owner_id: str, defect_id: int, body: DefectUpdate) -> bool:
    root = await pool.fetchrow(
        "select id, title, ticker, feature from public.pilot_defect"
        " where id = $1 and owner_id = $2 and parent_id is null",
        defect_id, owner_id,
    )  # fmt: skip
    if root is None:
        return False
    await pool.execute(
        "insert into public.pilot_defect"
        " (owner_id, parent_id, title, detail, ticker, feature, state)"
        " values ($1,$2,$3,$4,$5,$6,$7)",
        owner_id,
        defect_id,
        root["title"],
        body.detail,
        root["ticker"],
        root["feature"],
        body.state,
    )
    return True


async def list_current(pool: asyncpg.Pool, owner_id: str) -> list[Defect]:
    rows = await pool.fetch(
        f"select {_COLUMNS} from public.pilot_defect"  # noqa: S608 - constant column list
        " where owner_id = $1 order by id limit 2000",
        owner_id,
    )
    weights, _, _ = await holding_weights(pool)
    return current([DefectRow(**dict(r)) for r in rows], weights)
