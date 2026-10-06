"""Facts found by web research (`gap_research`), the owner's power to overturn them, and the
summary of everything the AI approvers decided on the owner's behalf."""

import datetime as dt
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query
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


class DomainTierInput(BaseModel):
    tier: Literal["T2", "T3", "rejected"]
    note: str | None = Field(default=None, max_length=300)


DomainPath = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9.\-]{0,251}\.[a-z]{2,24}$")]


@router.get("/web-domains", tags=["holdings"], response_model=None)
async def web_domains(pool: Pool, _owner: Owner) -> dict[str, Any]:
    """Domains the AI judge placed in a source tier, with who decided, so any can be changed."""
    rows = await pool.fetch(
        "select domain, tier, decided_by, confidence, reasons, decided_at"
        " from public.web_domain_tier order by decided_at desc"
    )
    return {"domains": [dict(r) for r in rows]}


@router.put("/web-domains/{domain}", tags=["holdings"], response_model=None)
async def set_web_domain(
    domain: DomainPath, body: DomainTierInput, pool: Pool, owner: Owner
) -> dict[str, Any]:
    """Set a domain's source tier yourself. Never above tier 2: tier 1 is the issuer's own
    domain, regulators and exchanges, set in the gap-research config. Final: the AI judge never
    overwrites it."""
    row = await pool.fetchrow(
        """insert into public.web_domain_tier (domain, tier, decided_by, reasons)
           values ($1, $2, $3, $4)
           on conflict (domain) do update set tier = excluded.tier,
             decided_by = excluded.decided_by, reasons = excluded.reasons, confidence = null,
             decided_at = now()
           returning domain, tier, decided_by""",
        domain,
        body.tier,
        f"owner:{owner.user_id}",
        body.note or "set by the owner",
    )
    assert row is not None
    return dict(row)


@router.get("/approvals/ai-summary", tags=["approvals"], response_model=None)
async def ai_approval_summary(
    pool: Pool, _owner: Owner, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> dict[str, Any]:
    """What the AI approvers decided in the last `days` days, per queue, so any decision is
    easy to find and overturn: suggested listings, peers, investor pages and web facts."""
    interval = dt.timedelta(days=days)
    listings = await pool.fetch(
        """select ticker, status, decided_at, evidence ->> 'owner_reason' as reason
           from public.instrument_security_link
           where decided_by is null and evidence ->> 'owner_reason' like 'AI approval%'
             and decided_at >= now() - $1::interval order by decided_at desc""",
        interval,
    )
    peers = await pool.fetch(
        """select p.id, p.ticker, p.peer_ticker, p.status, e ->> 'reason' as reason,
                  (e ->> 'at')::timestamptz as at
           from public.peer_set p, jsonb_array_elements(p.status_history) e
           where e ->> 'reason' like 'AI approval%'
             and (e ->> 'at')::timestamptz >= now() - $1::interval order by at desc""",
        interval,
    )
    pages = await pool.fetch(
        """select ticker, attempted_at, detail -> 'applied' as applied
           from public.ir_discovery_decision
           where outcome = 'applied' and attempted_at >= now() - $1::interval
           order by attempted_at desc""",
        interval,
    )
    facts = await pool.fetch(
        """select id, ticker, slot, value_text, tier, status, confidence, source_url, retrieved_at
           from public.web_fact
           where decided_by like 'ai:%' and retrieved_at >= now() - $1::interval
           order by retrieved_at desc""",
        interval,
    )
    return {
        "days": days,
        "listings": [dict(r) for r in listings],
        "peers": [dict(r) for r in peers],
        "investor_pages": [dict(r) for r in pages],
        "web_facts": [dict(r) for r in facts],
    }
