"""Facts found by web research (`gap_research`) and the owner's power to overturn them."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool

router = APIRouter()

TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,32}$")]


class WebFactDecision(BaseModel):
    status: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=300)


@router.get("/holdings/{ticker}/web-facts", tags=["holdings"], response_model=None)
async def web_facts(ticker: TickerPath, pool: Pool, _owner: Owner) -> dict[str, Any]:
    """Facts found on reliable web pages for slots the structured sources leave empty, with
    their tier, verbatim quote, source and who decided. Shown as a labelled fallback only."""
    rows = await pool.fetch(
        """select id, slot, value_text, value_num, value_date, unit, period, source_url, host,
                  tier, quote, retrieved_at, status, decided_by, confidence, reasons, origin
           from public.web_fact where ticker = $1 and status <> 'superseded'
           order by slot, retrieved_at desc""",
        ticker,
    )
    return {"ticker": ticker, "facts": [dict(r) for r in rows]}


@router.post("/web-facts/{fact_id}/decision", tags=["holdings"], response_model=None)
async def decide_web_fact(
    fact_id: Annotated[int, Path(ge=1)], body: WebFactDecision, pool: Pool, owner: Owner
) -> dict[str, Any]:
    """Approve or reject a web fact. The owner's decision is final: gap research never
    overwrites it."""
    reason = body.note or "decided by the owner"
    row = await pool.fetchrow(
        """update public.web_fact set status = $2, decided_by = $3, reasons = $4,
             status_history = status_history || jsonb_build_array(jsonb_build_object(
               'status', $2::text, 'by', $3::text, 'reason', $4::text))
           where id = $1 returning id, ticker, slot, status""",
        fact_id,
        body.status,
        f"owner:{owner.user_id}",
        reason,
    )
    if row is None:
        raise HTTPException(404, f"No web fact {fact_id}")
    return dict(row)
