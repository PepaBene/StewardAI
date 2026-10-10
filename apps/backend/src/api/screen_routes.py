"""Owner-only screening over the filed-statement metrics (see `steward.screen`)."""

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.auth import Owner
from api.db import Pool
from steward.disclosure.statement_read import with_valuation_gate
from steward.peer_metrics import financial_peer_metrics
from steward.screen import MetricName, Missing, RowGate, ScreenFilter, ScreenResult, run_screen

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
    valuation_gate_only: bool = Field(
        default=False,
        description="Keep only rows whose period passed the valuation gate. A row with no gate "
        "is dropped, never read as a pass; the count is in the caveats.",
    )


async def universe_tickers(pool: Pool, universe: str, owner_id: object) -> list[str]:
    if universe == "watchlist":
        rows = await pool.fetch(
            "select distinct ticker from public.watchlist_item where owner_id=$1", owner_id
        )
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
async def run_screen_route(body: ScreenRequest, pool: Pool, owner: Owner) -> ScreenResult:
    """Run numeric filters over each issuer's latest computable period. Every issuer left out
    is counted with its reason; a missing metric is excluded or flagged, never read as zero."""
    if len({(f.metric, f.op) for f in body.filters}) != len(body.filters):
        raise HTTPException(422, "each metric can have one gte and one lte filter at most")
    tickers = await universe_tickers(pool, body.universe, owner.user_id)
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
    result = run_screen(
        metrics,
        tickers,
        body.filters,
        names={r["t212_ticker"]: r["name"] for r in name_rows if r["name"]},
        missing=body.missing,
        sort_by=body.sort_by,
        # The gate filter must see every match, or matches past the limit never get a turn.
        limit=max(len(tickers), 1) if body.valuation_gate_only else body.limit,
        universe=label,
        universe_definition=definition,
    )
    return await _with_gates(pool, result, body.as_of, body.valuation_gate_only, body.limit)


async def _with_gates(
    pool: Pool,
    result: ScreenResult,
    as_of: dt.datetime | None,
    only_passing: bool = False,
    limit: int = 100,
) -> ScreenResult:
    """Attach each row's valuation gate. A row whose issuer or period has no gate says so (null),
    so a screen never reads a missing gate as a pass. The filters themselves are not changed."""
    if not result.rows:
        return result
    issuers = await pool.fetch(
        """select distinct on (ticker) ticker, issuer_id from public.financial_statement_fact
            where ticker = any($1::text[]) and issuer_id is not null
            order by ticker, known_at desc""",
        [r.ticker for r in result.rows],
    )
    by_ticker = {r["ticker"]: r["issuer_id"] for r in issuers}
    gated = await with_valuation_gate(
        pool,
        [
            {"ticker": r.ticker, "issuer_id": by_ticker.get(r.ticker), "period_end": r.period_end}
            for r in result.rows
        ],
        as_of,
    )
    for row, g in zip(result.rows, gated, strict=True):
        row.valuation_gate = RowGate(**g["valuation_gate"])
    if only_passing:
        kept = [
            r for r in result.rows if r.valuation_gate and r.valuation_gate.usable_for_valuation
        ]
        dropped = len(result.rows) - len(kept)
        result.matched = len(kept)
        result.rows = kept[:limit]
        result.shown = len(result.rows)
        result.caveats = [
            *result.caveats,
            f"{dropped} matching rows were left out because their period did not pass the "
            "valuation gate or has no gate.",
        ]
    return result
