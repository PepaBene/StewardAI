"""Shared point-in-time reads over the phase 3-5 stores.

Route handlers and the holding/portfolio/research read models all call these, so there is one
query per truth table. Every function takes an optional `as_of` cutoff and returns only records
whose `known_at` is on or before it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import asyncpg

from steward.db_json import decode_jsonb_object, decode_structured_rows
from steward.forward_consensus import consensus_series
from steward.geo_codes import has_column


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
    # jsonb arrives as text (no codec is registered on the pool): decode at the read site.
    return decode_structured_rows(rows, ("raw",))


def reconciliation_state(validation: dict[str, Any]) -> str:
    """ "reconciled", "failed" or "unchecked" from a fact's `validation.reconciled`."""
    value = validation.get("reconciled")
    return "reconciled" if value is True else "failed" if value is False else "unchecked"


async def financial_history_rows(
    pool: asyncpg.Pool,
    ticker: str,
    as_of: dt.datetime | None,
    statement: str | None = None,
    *,
    reconciled_only: bool = False,
    line_item: str | None = None,
) -> list[dict[str, Any]]:
    """Stored statement facts known at `as_of`, jsonb columns decoded, each with its
    `reconciliation` state ("reconciled", "failed", "unchecked"). `reconciled_only` keeps only
    the facts a check passed; a fact a check failed is returned flagged, never silently used."""
    rows = await pool.fetch(
        """select id,concept,label,statement,line_item,period_start,period_end,fiscal_period,
                  basis,value,value_text,unit,currency,dimensions,source,source_url,published_at,
                  accepted_at,known_at,revision,amends_fact_id,amends_accession,is_amendment,
                  accession,form,cik,issuer_id,text_block,evidence_id,validation,published
           from public.financial_statement_fact where ticker=$1
             and ($2::timestamptz is null or known_at <= $2)
             and ($3::text is null or statement=$3)
             and ($4::text is null or line_item=$4)
           order by period_end desc,concept,known_at desc""",
        ticker,
        as_of,
        statement,
        line_item,
    )
    out: list[dict[str, Any]] = []
    for row in decode_structured_rows(rows, ("dimensions", "validation")):
        row["validation"] = decode_jsonb_object(row["validation"])
        row["reconciliation"] = reconciliation_state(row["validation"])
        if reconciled_only and row["reconciliation"] != "reconciled":
            continue
        out.append(row)
    return out


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
    return decode_structured_rows(rows, ("raw",))


async def forward_payload(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None
) -> dict[str, Any]:
    """Forward observations plus the consensus series, grouped by provider, currency and basis
    so a revision is never a difference between two sources."""
    rows = await forward_rows(pool, ticker, as_of)
    return {
        "ticker": ticker,
        "known_as_of": as_of,
        "observations": rows,
        "consensus_series": consensus_series(rows),
    }


async def has_table(pool: asyncpg.Pool, table: str) -> bool:
    return bool(
        await pool.fetchval(
            "select exists (select 1 from information_schema.tables"
            " where table_schema='public' and table_name=$1)",
            table,
        )
    )


def status_as_of_sql(table: str, alias: str, param: str, *, log: bool, decided: bool) -> str:
    """SQL expression for a row's review status at the cutoff `param` (a timestamptz that is
    null for the current view). The status log (`graph_status_log`) gives the status in force at
    the cutoff; without it a decision made after the cutoff (`decided_at`) is shown as it was
    before: `suggested`. A current view (null cutoff) shows the stored status."""
    fallback = (
        f"case when {alias}.decided_at is not null and {alias}.decided_at > {param}"
        f" then 'suggested' else {alias}.status end"
        if decided
        else f"{alias}.status"
    )
    if not log:
        return fallback
    return (
        "coalesce((select l.to_status from public.graph_status_log l"  # noqa: S608 - built from constants
        f" where l.table_name='{table}' and l.row_id={alias}.id and l.changed_at <= {param}"
        f" order by l.changed_at desc, l.id desc limit 1), {fallback})"
    )


async def business_network_payload(
    pool: asyncpg.Pool,
    ticker: str,
    as_of: dt.datetime | None,
    *,
    historical: bool | None = None,
) -> dict[str, Any]:
    """Company graph, events, disclosures and patents of a holding as known at `as_of`.

    Statuses are the statuses in force at the cutoff (a confirmation or rejection made after it
    is not applied). `historical` (default: whether a cutoff was given) excludes legacy
    relationships without an `as_of` date, which cannot be placed before any cutoff."""
    historical = as_of is not None if historical is None else historical
    log = await has_table(pool, "graph_status_log")
    event_decided = await has_column(pool, "company_event", "decided_at")
    edge_status = status_as_of_sql("company_graph_edge", "e", "$2", log=log, decided=True)
    event_status = status_as_of_sql("company_event", "ev", "$2", log=log, decided=event_decided)
    disclosure_status = status_as_of_sql(
        "relationship_disclosure", "d", "$2", log=log, decided=True
    )
    edges = await pool.fetch(
        f"""select e.id,e.subject_key,e.object_key,e.object_name,e.edge_type,e.direction,
                  {edge_status} as status,e.valid_from,
                  e.valid_to,e.evidence_tier,e.source,e.source_url,e.evidence_id,e.passage,
                  e.known_at,e.decision_reason,e.detail from public.company_graph_edge e
           where e.subject_key=$1 and ($2::timestamptz is null or e.known_at <= $2)
             and (e.valid_from is null or e.valid_from<=coalesce($2::date,current_date))
             and (e.valid_to is null or e.valid_to>=coalesce($2::date,current_date))
           order by e.known_at desc,e.id""",  # noqa: S608 - the status fragment is built from constants
        ticker,
        as_of,
    )
    legacy_edges = await pool.fetch(
        """select id,object_key,object_name,type,source,source_ref,as_of,status,detail
           from public.entity_relationship where ticker=$1
             and ($2::date is null or as_of <= $2::date
                  or (as_of is null and not $3::boolean))
           order by as_of desc nulls last,id""",
        ticker,
        as_of,
        historical,
    )
    events = await pool.fetch(
        f"""select ev.id,ev.event_type,ev.event_state,ev.announced_at,ev.effective_at,ev.known_at,
                  ev.source,ev.source_url,ev.evidence_id,ev.counterparty_key,ev.terms,
                  {event_status} as status,ev.thesis_kpi,ev.detail
           from public.company_event ev
           where ev.ticker=$1 and ($2::timestamptz is null or ev.known_at <= $2)
           order by coalesce(ev.effective_at,ev.announced_at) desc nulls last,ev.id""",  # noqa: S608 - the status fragment is built from constants
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
             and (o.valid_from is null or o.valid_from <= coalesce($2::date,current_date))
             and (o.valid_to is null or o.valid_to >= coalesce($2::date,current_date))
           order by p.priority_date desc nulls last,p.family_key""",
        ticker,
        as_of,
    )
    disclosures = await pool.fetch(
        f"""select d.id,d.relationship,d.direction,d.counterparty_key,d.counterparty_name,
                  d.anonymous_counterparty,d.reported_share,d.share_basis,d.fiscal_period,
                  d.valid_from,d.valid_to,d.evidence_id,d.passage,d.source_url,d.source,
                  {disclosure_status} as status,d.decision_reason,d.known_at
           from public.relationship_disclosure d where d.ticker=$1
             and ($2::timestamptz is null or d.known_at <= $2)
           order by d.fiscal_period desc nulls last,d.known_at desc""",  # noqa: S608 - the status fragment is built from constants
        ticker,
        as_of,
    )
    from steward.graph.patents import attributed_families

    attributed = {f["family_key"] for f in await attributed_families(pool, ticker, as_of)}
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
        "patents": [{**dict(r), "attributed": r["family_key"] in attributed} for r in patents],
        "limitations": [
            "Suggested and disputed edges are candidates, not verified facts.",
            "Patent counts and citations are signals, not patent valuation or FTO advice.",
        ],
    }


async def patent_trend_rows(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None
) -> list[dict[str, Any]]:
    """Patent trend of the families attributed to the holding: direct applicants, plus
    subsidiary families whose applicant is covered, on the priority date, by a confirmed
    subsidiary edge (re-validated at read time); one row per family."""
    from steward.graph.patents import attributed_families, patent_trend

    return patent_trend(await attributed_families(pool, ticker, as_of))
