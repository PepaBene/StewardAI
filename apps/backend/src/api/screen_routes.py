"""Owner-only screening over the filed-statement metrics (see `steward.screen`)."""

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.auth import Owner
from api.db import Pool
from steward.peer_metrics import financial_peer_metrics
from steward.screen import MetricName, Missing, ScreenFilter, ScreenResult, run_screen

router = APIRouter(tags=["screens"])

UNIVERSES: dict[str, tuple[str, str]] = {
    "covered": (
        "Covered issuers",
        "Every ticker the app holds filed statements for. Not a market-wide universe.",
    ),
    "watchlist": ("Watchlist", "Tickers on your watchlist that have filed statements."),
    "holdings": (
        "Holdings",
        "Tickers in the latest portfolio snapshot that have filed statements.",
    ),
}
MAX_FILTERS = 6


class ScreenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe: Literal["covered", "watchlist", "holdings"] = "covered"
    filters: Annotated[list[ScreenFilter], Field(max_length=MAX_FILTERS)] = Field(
        default_factory=list
    )
    missing: Missing = Field(
        default="exclude",
        description="exclude: drop an issuer whose filtered metric is missing. keep_unknown: "
        "keep it and list the unknown metrics.",
    )
    sort_by: MetricName | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 100
    as_of: dt.datetime | None = Field(
        default=None, description="Use only statement facts known by this time."
    )
    reconciled_only: bool = Field(
        default=False, description="Only facts a reconciliation check passed."
    )


async def universe_tickers(pool: Pool, universe: str) -> list[str]:
    if universe == "watchlist":
        rows = await pool.fetch("select distinct ticker from public.watchlist_item")
    elif universe == "holdings":
        rows = await pool.fetch(
            """select distinct ticker from public.position_snapshot
               where snapshot_date=(select max(snapshot_date) from public.position_snapshot)"""
        )
    else:
        rows = await pool.fetch(
            "select distinct ticker from public.financial_statement_fact where ticker is not null"
        )
    return sorted(str(r["ticker"]) for r in rows if r["ticker"])


@router.post("/screens/run", response_model=ScreenResult)
async def run_screen_route(body: ScreenRequest, pool: Pool, _owner: Owner) -> ScreenResult:
    """Run numeric filters over each issuer's latest computable period. Every issuer left out
    is counted with its reason; a missing metric is excluded or flagged, never read as zero."""
    if len({(f.metric, f.op) for f in body.filters}) != len(body.filters):
        raise HTTPException(422, "each metric can have one gte and one lte filter at most")
    tickers = await universe_tickers(pool, body.universe)
    facts = (
        await pool.fetch(
            """select ticker,concept,period_start,period_end,basis,value,unit,dimensions,known_at,
                      validation,published
               from public.financial_statement_fact where ticker=any($1::text[])
                 and value is not null and ($2::timestamptz is null or known_at<=$2)
               order by ticker,period_end,known_at,concept""",
            tickers,
            body.as_of,
        )
        if tickers
        else []
    )
    metrics = financial_peer_metrics([dict(r) for r in facts], reconciled_only=body.reconciled_only)
    name_rows = (
        await pool.fetch(
            "select t212_ticker,name from public.instrument where t212_ticker=any($1::text[])",
            tickers,
        )
        if tickers
        else []
    )
    label, definition = UNIVERSES[body.universe]
    return run_screen(
        metrics,
        tickers,
        body.filters,
        names={r["t212_ticker"]: r["name"] for r in name_rows if r["name"]},
        missing=body.missing,
        sort_by=body.sort_by,
        limit=body.limit,
        universe=label,
        universe_definition=definition,
    )
