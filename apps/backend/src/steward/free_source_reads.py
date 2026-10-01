"""Shared point-in-time reads over the phase 3-5 stores.

Route handlers and the holding/portfolio/research read models all call these, so there is one
query per truth table. Every function takes an optional `as_of` cutoff and returns only records
whose `known_at` is on or before it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import asyncpg


async def company_research_rows(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """select id,provider,dataset,metric,period_end,value,text_value,unit,currency,source_url,
                  published_at,known_at,match_confidence,raw
           from public.company_research_observation where ticker=$1
             and ($2::timestamptz is null or known_at<=$2)
           order by dataset,metric,period_end desc nulls last,known_at desc""",
        ticker,
        as_of,
    )
    return [dict(r) for r in rows]


async def financial_history_rows(
    pool: asyncpg.Pool,
    ticker: str,
    as_of: dt.datetime | None,
    statement: str | None = None,
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """select id,concept,label,statement,period_start,period_end,fiscal_period,basis,value,
                  unit,currency,dimensions,source,source_url,published_at,known_at,revision,
                  amends_fact_id,text_block,evidence_id,validation
           from public.financial_statement_fact where ticker=$1
             and ($2::timestamptz is null or known_at <= $2)
             and ($3::text is null or statement=$3)
           order by period_end desc,concept,known_at desc""",
        ticker,
        as_of,
        statement,
    )
    return [dict(r) for r in rows]


async def forward_rows(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """select id,kind,provider,metric,basis,fiscal_period,period_end,currency,value,low,high,
                  analyst_count,vintage,published_at,known_at,source_url,evidence_id,raw
           from public.forward_observation where ticker=$1
             and ($2::timestamptz is null or known_at <= $2)
           order by fiscal_period,metric,known_at desc""",
        ticker,
        as_of,
    )
    return [dict(r) for r in rows]


async def business_network_payload(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None
) -> dict[str, Any]:
    edges = await pool.fetch(
        """select id,subject_key,object_key,object_name,edge_type,direction,status,valid_from,
                  valid_to,evidence_tier,source,source_url,evidence_id,passage,known_at,
                  decision_reason,detail from public.company_graph_edge
           where subject_key=$1 and ($2::timestamptz is null or known_at <= $2)
             and (valid_from is null or valid_from<=coalesce($2::date,current_date))
             and (valid_to is null or valid_to>=coalesce($2::date,current_date))
           order by known_at desc,id""",
        ticker,
        as_of,
    )
    legacy_edges = await pool.fetch(
        """select id,object_key,object_name,type,source,source_ref,as_of,status,detail
           from public.entity_relationship where ticker=$1
             and ($2::date is null or as_of is null or as_of <= $2::date)
           order by as_of desc nulls last,id""",
        ticker,
        as_of,
    )
    events = await pool.fetch(
        """select id,event_type,event_state,announced_at,effective_at,known_at,source,source_url,
                  evidence_id,counterparty_key,terms,status,thesis_kpi,detail
           from public.company_event where ticker=$1 and ($2::timestamptz is null or known_at <= $2)
           order by coalesce(effective_at,announced_at) desc nulls last,id""",
        ticker,
        as_of,
    )
    patents = await pool.fetch(
        """select p.family_key,p.priority_date,p.first_publication_date,p.family_title,p.cpc_codes,
                  p.jurisdictions,p.citations_forward,p.citations_backward,p.legal_status,p.source,
                  p.source_url,p.known_at,o.applicant,o.ownership_confidence,o.ownership_basis,
                  o.valid_from ownership_valid_from,o.valid_to ownership_valid_to,o.evidence_id
           from public.patent_family p join public.patent_family_owner o on o.family_id=p.id
           where o.ticker=$1
             and ($2::timestamptz is null or p.known_at <= $2)
           order by p.priority_date desc nulls last,p.family_key""",
        ticker,
        as_of,
    )
    disclosures = await pool.fetch(
        """select id,relationship,direction,counterparty_key,counterparty_name,
                  anonymous_counterparty,reported_share,share_basis,fiscal_period,valid_from,
                  valid_to,evidence_id,passage,source_url,source,status,decision_reason,known_at
           from public.relationship_disclosure where ticker=$1
             and ($2::timestamptz is null or known_at <= $2)
           order by fiscal_period desc nulls last,known_at desc""",
        ticker,
        as_of,
    )
    normalized_edges: list[dict[str, Any]] = [dict(r) for r in edges]
    normalized_edges.extend(
        {
            "id": f"legacy:{r['id']}",
            "subject_key": ticker,
            "object_key": r["object_key"],
            "object_name": r["object_name"],
            "edge_type": r["type"],
            "direction": "outgoing",
            "status": r["status"],
            "valid_from": r["as_of"],
            "valid_to": None,
            "evidence_tier": "primary" if r["source"] == "gleif" else "candidate",
            "source": r["source"],
            "source_url": r["source_ref"],
            "evidence_id": None,
            "passage": None,
            "known_at": None,
            "decision_reason": None,
            "detail": r["detail"],
        }
        for r in legacy_edges
    )
    return {
        "ticker": ticker,
        "known_as_of": as_of,
        "edges": normalized_edges,
        "events": [dict(r) for r in events],
        "disclosures": [dict(r) for r in disclosures],
        "patents": [dict(r) for r in patents],
        "limitations": [
            "Suggested and disputed edges are candidates, not verified facts.",
            "Patent counts and citations are signals, not patent valuation or FTO advice.",
        ],
    }


async def patent_trend_rows(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None
) -> list[dict[str, Any]]:
    from steward.market_analysis import patent_trend

    rows = await pool.fetch(
        """select p.family_key,p.priority_date,p.cpc_codes,p.citations_forward
           from public.patent_family p join public.patent_family_owner o on o.family_id=p.id
           where o.ticker=$1 and (o.ownership_basis='direct' or
             (o.ownership_basis='dated_subsidiary_path' and o.valid_from is not null))
             and ($2::timestamptz is null or p.known_at <= $2)""",
        ticker,
        as_of,
    )
    return patent_trend([dict(r) for r in rows])
