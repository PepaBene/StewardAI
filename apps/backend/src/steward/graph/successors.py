"""Reconcile parent / successor changes with deal events (D5.10).

A change of the accounting parent (GLEIF Level 2), a named successor LEI or a Wikidata parent
that differs from what we hold is *explained* when a confirmed (or at least recorded) deal event
for the same counterparty closed on or before the change was observed, *pending* when the deal
has only been announced or approved, and *unexplained* otherwise. An unexplained change, or one
whose deal was terminated or reversed, is queued for the owner. An accounting parent is never
promoted to a beneficial shareholder: this module only records and links, it writes no edge.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Literal

import asyncpg

from steward.graph.deals import derive_state, load_deal_events
from steward.graph.keys import normalize_object_key

ChangeKind = Literal["parent_changed", "successor_named", "parent_removed"]


async def reconcile_parent_change(
    pool: asyncpg.Pool,
    *,
    ticker: str,
    change_kind: ChangeKind,
    from_key: str | None,
    to_key: str | None,
    observed_source: str,
    observed_at: dt.datetime | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify one observed change against the deal events and store the outcome."""
    observed_at = observed_at or dt.datetime.now(dt.UTC)
    counterparty = normalize_object_key(to_key or from_key or "")
    candidates = await pool.fetch(
        """select distinct deal_key from public.company_event
           where ticker=$1 and deal_key is not null and event_type='deal'
             and status<>'rejected' and counterparty_key=$2""",
        ticker,
        counterparty,
    )
    outcome = "unexplained"
    deal_event_id: int | None = None
    deal_detail: dict[str, Any] = {}
    for candidate in candidates:
        events = await load_deal_events(pool, ticker, candidate["deal_key"])
        # What was known when the change was observed, not what we know now.
        state = derive_state(events, as_of=observed_at)
        if state.current is None:
            continue
        last = state.history[-1]
        deal_detail = {
            "deal_key": candidate["deal_key"],
            "state": state.current,
            "confirmed": last.status == "confirmed",
        }
        deal_event_id = last.id
        if state.current == "closed":
            outcome = "explained_by_closed_deal"
            break
        if state.current in {"terminated", "reversed"}:
            outcome = "deal_terminated_or_reversed"
        elif state.current in {"proposed", "announced", "amended", "approved"}:
            outcome = "deal_pending"
    row = await pool.fetchrow(
        """insert into public.graph_reconciliation
           (ticker,change_kind,from_key,to_key,observed_source,observed_at,outcome,deal_event_id,
            detail)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
           on conflict (ticker,change_kind,coalesce(from_key,''),coalesce(to_key,''),
                        observed_source)
           do update set outcome=excluded.outcome, deal_event_id=excluded.deal_event_id,
             detail=graph_reconciliation.detail || excluded.detail
           returning id,outcome,status,deal_event_id""",
        ticker,
        change_kind,
        from_key,
        to_key,
        observed_source,
        observed_at,
        outcome,
        deal_event_id,
        json.dumps({**(detail or {}), **deal_detail}, default=str),
    )
    assert row is not None
    return dict(row)


async def open_reconciliations(
    pool: asyncpg.Pool, ticker: str | None = None
) -> list[dict[str, Any]]:
    """Changes that need the owner: anything not explained by a closed deal."""
    rows = await pool.fetch(
        """select id,ticker,change_kind,from_key,to_key,observed_source,observed_at,outcome,
                  deal_event_id,detail
           from public.graph_reconciliation
           where status='open' and outcome<>'explained_by_closed_deal'
             and ($1::text is null or ticker=$1)
           order by observed_at desc,id desc""",
        ticker,
    )
    return [dict(r) for r in rows]
