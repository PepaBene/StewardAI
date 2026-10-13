"""Authenticated API routes for the thesis feature: ask for a thesis, read it, accept a buy
thesis, and run the portfolio review. Every run is started by the owner; nothing is traded."""

import datetime as dt
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import IdPath, Pool
from api.routes import _enqueue
from steward.portfolio.read import JobSummary
from steward.thesis.job import accept_memo

router = APIRouter()

Ticker = Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


async def _canonical(pool: Pool, ticker: str) -> str:
    found = await pool.fetchval(
        "select t212_ticker from public.instrument where upper(t212_ticker) = upper($1)"
        " order by t212_ticker = $1 desc limit 1",
        ticker,
    )
    return str(found) if found else ticker


class ThesisRequest(BaseModel):
    ticker: Ticker
    mode: Literal["held", "buy"] = Field(
        default="held",
        description="held: a position you own (the thesis is saved when it is written). buy: a "
        "stock you might buy (the thesis waits until you accept it).",
    )


@router.post("/jobs/thesis", tags=["thesis"], status_code=status.HTTP_202_ACCEPTED)
async def run_thesis(body: ThesisRequest, pool: Pool, owner: Owner) -> JobSummary:
    """Research the stock with every record the app holds plus web search, then write its
    thesis: stance, executive summary, detailed memo, KPIs, exit conditions, scenarios and
    targets. Runs on its own once started."""
    ticker = await _canonical(pool, body.ticker)
    return await _enqueue(pool, "thesis", owner.user_id, {"ticker": ticker, "mode": body.mode})


class ReviewRequest(BaseModel):
    tickers: list[Ticker] = Field(
        default_factory=list,
        max_length=8,
        description="Stocks you want researched as buy candidates, besides those the review finds.",
    )


@router.post("/jobs/portfolio-review", tags=["thesis"], status_code=status.HTTP_202_ACCEPTED)
async def run_portfolio_review(body: ReviewRequest, pool: Pool, owner: Owner) -> JobSummary:
    """Review what to sell, trim and buy. Flags holdings, finds gaps against your strategy,
    researches buy candidates in depth and judges each against your return hurdle and
    asymmetry gate."""
    tickers = [await _canonical(pool, t) for t in body.tickers]
    return await _enqueue(pool, "portfolio_review", owner.user_id, {"tickers": tickers})


class MemoSummary(BaseModel):
    id: int
    ticker: str
    mode: str
    stance: str
    status: str
    executive_summary: str
    research_report_id: int | None
    review_id: int | None
    created_at: dt.datetime


class MemoOut(MemoSummary):
    body: dict[str, Any]


def _json(raw: Any) -> Any:
    return json.loads(raw) if isinstance(raw, str) else raw


@router.get("/thesis/memos", tags=["thesis"])
async def list_memos(
    pool: Pool,
    _owner: Owner,
    ticker: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[MemoSummary]:
    rows = await pool.fetch(
        "select id, ticker, mode, stance, status, executive_summary, research_report_id,"
        " review_id, created_at from public.thesis_memo"
        " where ($1::text is null or ticker = $1) order by id desc limit $2",
        ticker,
        limit,
    )
    return [MemoSummary(**dict(r)) for r in rows]


@router.get("/thesis/memos/{memo_id}", tags=["thesis"])
async def get_memo(memo_id: IdPath, pool: Pool, _owner: Owner) -> MemoOut:
    row = await pool.fetchrow(
        "select id, ticker, mode, stance, status, executive_summary, research_report_id,"
        " review_id, created_at, body from public.thesis_memo where id = $1",
        memo_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No thesis memo {memo_id}")
    return MemoOut(**{**dict(row), "body": _json(row["body"])})


class AcceptOut(BaseModel):
    memo_id: int
    ticker: str
    saved_version_id: int


@router.post("/thesis/memos/{memo_id}/accept", tags=["thesis"])
async def accept_buy_memo(memo_id: IdPath, pool: Pool, _owner: Owner) -> AcceptOut:
    """You decided to buy: save the memo's thesis as the stock's thesis."""
    try:
        done = await accept_memo(pool, memo_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return AcceptOut(**done)


@router.post("/thesis/memos/{memo_id}/dismiss", tags=["thesis"])
async def dismiss_memo(memo_id: IdPath, pool: Pool, _owner: Owner) -> dict[str, str]:
    result = await pool.execute(
        "update public.thesis_memo set status = 'dismissed' where id = $1 and status = 'open'",
        memo_id,
    )
    if result.endswith(" 0"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an open memo can be dismissed")
    return {"status": "dismissed"}


class ReviewOut(BaseModel):
    id: int
    status: str
    summary: str
    body: dict[str, Any]
    created_at: dt.datetime
    finished_at: dt.datetime | None


@router.get("/thesis/reviews", tags=["thesis"])
async def list_reviews(pool: Pool, _owner: Owner) -> list[ReviewOut]:
    rows = await pool.fetch(
        "select id, status, summary, body, created_at, finished_at from public.portfolio_review"
        " order by id desc limit 20"
    )
    return [ReviewOut(**{**dict(r), "body": _json(r["body"])}) for r in rows]


@router.get("/thesis/reviews/{review_id}", tags=["thesis"])
async def get_review(review_id: IdPath, pool: Pool, _owner: Owner) -> ReviewOut:
    row = await pool.fetchrow(
        "select id, status, summary, body, created_at, finished_at from public.portfolio_review"
        " where id = $1",
        review_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No review {review_id}")
    return ReviewOut(**{**dict(row), "body": _json(row["body"])})
