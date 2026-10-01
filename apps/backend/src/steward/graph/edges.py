"""Company graph edge and event writers with decided-row protection.

Re-syncing a vendor (Diffbot, Wikidata, a filing parser) never overwrites the status, evidence
tier, evidence link or decision of a row that was confirmed, rejected or disputed, and never
turns a disputed row back into a suggestion. Rows can only be moved by the owner decision path
in `steward.graph.decisions`. `known_at` is the first time a fact was known and is never
reset by a refresh.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Literal

import asyncpg

from steward.graph.decisions import (
    GraphDecisionError,
    confirmable,
    server_tier,
    tier_value,
)

EdgeStatus = Literal["suggested", "confirmed", "rejected", "disputed"]


async def upsert_edge(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    subject_key: str,
    object_key: str,
    object_name: str,
    edge_type: str,
    direction: str,
    source: str,
    source_url: str,
    status: EdgeStatus = "suggested",
    valid_from: dt.date | None = None,
    valid_to: dt.date | None = None,
    evidence_id: int | None = None,
    passage: str | None = None,
    detail: dict[str, object] | None = None,
) -> int:
    """Insert or refresh a candidate edge.

    `status='confirmed'` is accepted only with a cited passage or linked evidence whose
    source class (derived from the stored document, not the caller) is primary or regulatory.
    A previous owner rejection of the same (subject, normalised object, edge type) - from any
    source or url, in either relationship table - makes the new row `rejected` (trigger).
    """
    if valid_from and valid_to and valid_to < valid_from:
        raise ValueError("relationship valid_to precedes valid_from")
    if status in ("rejected", "disputed"):
        raise GraphDecisionError(
            "rejecting or disputing an edge is an owner decision (decide_edge), not an upsert"
        )
    tier = await server_tier(pool, evidence_id=evidence_id, source=source)
    if status == "confirmed" and (not confirmable(tier) or (evidence_id is None and not passage)):
        raise ValueError("confirmed edges require primary evidence and a cited passage")
    new_id: int = await pool.fetchval(
        """insert into public.company_graph_edge as t
           (subject_key,object_key,object_name,edge_type,direction,status,valid_from,valid_to,
            evidence_tier,source,source_url,evidence_id,passage,detail,decided_at)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,
                  case when $6='confirmed' then now() else null end)
           on conflict(subject_key,object_key,edge_type,source,source_url) do update set
             object_name=excluded.object_name,
             direction=case when t.status in ('confirmed','rejected','disputed')
                            then t.direction else excluded.direction end,
             valid_from=case when t.status in ('confirmed','rejected','disputed')
                             then t.valid_from
                             else coalesce(excluded.valid_from,t.valid_from) end,
             valid_to=case when t.status in ('confirmed','rejected','disputed')
                           then t.valid_to else coalesce(excluded.valid_to,t.valid_to) end,
             evidence_tier=case when t.status in ('confirmed','rejected','disputed')
                                then t.evidence_tier else excluded.evidence_tier end,
             evidence_id=case when t.status in ('confirmed','rejected','disputed')
                              then t.evidence_id
                              else coalesce(excluded.evidence_id,t.evidence_id) end,
             passage=case when t.status in ('confirmed','rejected','disputed') then t.passage
                          else coalesce(excluded.passage,t.passage) end,
             detail=t.detail || excluded.detail,
             status=case when t.status in ('confirmed','rejected','disputed') then t.status
                         else excluded.status end,
             decided_at=case when t.status in ('confirmed','rejected','disputed')
                               then t.decided_at
                             when excluded.status='confirmed' then now()
                             else t.decided_at end
           returning id""",
        subject_key,
        object_key,
        object_name,
        edge_type,
        direction,
        status,
        valid_from,
        valid_to,
        tier_value(tier),
        source,
        source_url,
        evidence_id,
        passage,
        json.dumps(detail or {}, default=str),
    )
    return new_id


async def upsert_event(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    ticker: str,
    event_type: str,
    event_state: str,
    source: str,
    source_url: str,
    status: EdgeStatus = "suggested",
    announced_at: dt.date | None = None,
    effective_at: dt.date | None = None,
    evidence_id: int | None = None,
    counterparty_key: str | None = None,
    terms: dict[str, object] | None = None,
    thesis_kpi: str | None = None,
    detail: dict[str, object] | None = None,
    deal_key: str | None = None,
    known_at: dt.datetime | None = None,
) -> int:
    """Insert or refresh a company event (one row per state per source document).

    A confirmed event needs linked evidence and earns its tier from that document's source
    class. A refresh never resets `known_at` (the moment this state first became known), and
    never changes the status or terms of a decided row.
    """
    if status in ("rejected", "disputed"):
        raise GraphDecisionError(
            "rejecting or disputing an event is an owner decision (decide_event), not an upsert"
        )
    tier = await server_tier(pool, evidence_id=evidence_id, source=source)
    if status == "confirmed":
        if evidence_id is None:
            raise GraphDecisionError("confirmed company events require linked primary evidence")
        if not confirmable(tier):
            raise GraphDecisionError(
                f"evidence {evidence_id} is {tier} material; only primary or regulatory"
                " documents confirm an event (use the owner review path to confirm it)"
            )
    new_id: int = await pool.fetchval(
        """insert into public.company_event as t
           (ticker,event_type,event_state,announced_at,effective_at,known_at,source,source_url,
            evidence_id,counterparty_key,terms,status,thesis_kpi,detail,evidence_tier,deal_key,
            decided_at)
           values($1,$2,$3,$4,$5,coalesce($15::timestamptz,now()),$6,$7,$8,$9,$10::jsonb,$11,$12,
                  $13::jsonb,$14,$16,case when $11='confirmed' then now() else null end)
           on conflict(ticker,event_type,source_url,event_state) do update set
             announced_at=coalesce(t.announced_at,excluded.announced_at),
             effective_at=coalesce(t.effective_at,excluded.effective_at),
             evidence_id=coalesce(t.evidence_id,excluded.evidence_id),
             counterparty_key=coalesce(t.counterparty_key,excluded.counterparty_key),
             terms=case when t.status in ('confirmed','rejected','disputed')
                        then t.terms else t.terms || excluded.terms end,
             status=case when t.status in ('confirmed','rejected','disputed') then t.status
                         else excluded.status end,
             evidence_tier=case when t.status in ('confirmed','rejected','disputed')
                                then t.evidence_tier else excluded.evidence_tier end,
             thesis_kpi=coalesce(t.thesis_kpi,excluded.thesis_kpi),
             deal_key=coalesce(t.deal_key,excluded.deal_key),
             detail=t.detail || excluded.detail,
             decided_at=case when t.status in ('confirmed','rejected','disputed')
                               then t.decided_at
                             when excluded.status='confirmed' then now()
                             else t.decided_at end
           returning id""",
        ticker,
        event_type,
        event_state,
        announced_at,
        effective_at,
        source,
        source_url,
        evidence_id,
        counterparty_key,
        json.dumps(terms or {}, default=str),
        status,
        thesis_kpi,
        json.dumps(detail or {}, default=str),
        tier_value(tier),
        known_at,
        deal_key,
    )
    return new_id
