"""Read models over every graph row (edges, events, disclosures) and their decision history.

Decisions overwrite the row (status, decided_at, decision_reason); the append-only history is
`graph_status_log`, written by a trigger on every status change. The log records the status
change and the reason but not who decided: only the current decider of an edge or a suggested
relationship is known, so it is attached to the newest entry only.
"""

from __future__ import annotations

from typing import Any, Literal

import asyncpg

HistoryKind = Literal["edge", "event", "disclosure", "suggestion"]

# kind -> (table, column holding the owner's reason, column holding the decider or null)
HISTORY_TABLES: dict[str, tuple[str, str, str]] = {
    "edge": ("company_graph_edge", "decision_reason", "decided_by"),
    "event": ("company_event", "decision_reason", "null::uuid"),
    "disclosure": ("relationship_disclosure", "decision_reason", "null::uuid"),
    "suggestion": ("entity_relationship", "exception_reason", "decided_by"),
}

# list kind -> (select, status column filter table alias handled in SQL, kind column)
_LISTS: dict[str, tuple[str, str, str]] = {
    "edges": (
        """select id, subject_key ticker, edge_type kind, direction, object_name counterparty,
                  object_key, status, evidence_tier, source, source_url, evidence_id, passage,
                  known_at, decided_at, decision_reason, valid_from, valid_to
           from public.company_graph_edge""",
        "subject_key",
        "edge_type",
    ),
    "events": (
        """select id, ticker, event_type kind, event_state, counterparty_key counterparty,
                  status, evidence_tier, source, source_url, evidence_id, known_at, decided_at,
                  decision_reason, announced_at, effective_at
           from public.company_event""",
        "ticker",
        "event_type",
    ),
    "disclosures": (
        """select id, ticker, relationship kind, direction, counterparty_name counterparty,
                  anonymous_counterparty, reported_share, fiscal_period, status, source,
                  source_url, evidence_id, passage, known_at, decided_at, decision_reason,
                  valid_from, valid_to
           from public.relationship_disclosure""",
        "ticker",
        "relationship",
    ),
}


def history_entries(
    log: list[dict[str, Any]], decided_by: str | None, current_status: str | None
) -> list[dict[str, Any]]:
    """Oldest-first entries; the current decider goes on the newest entry that reached the
    current status (earlier deciders are not recorded)."""
    entries = [
        {
            "changed_at": row["changed_at"],
            "from_status": row["from_status"],
            "to_status": row["to_status"],
            "reason": row["reason"],
            "decided_by": None,
        }
        for row in log
    ]
    if decided_by and entries and entries[-1]["to_status"] == current_status:
        entries[-1]["decided_by"] = decided_by
    return entries


async def list_graph_rows(
    pool: asyncpg.Pool,
    what: Literal["edges", "events", "disclosures"],
    *,
    status: str | None,
    ticker: str | None,
    kind: str | None,
    limit: int,
    offset: int,
) -> tuple[int, list[dict[str, Any]]]:
    select, ticker_col, kind_col = _LISTS[what]
    where = (
        f"where ($1::text is null or status=$1) and ($2::text is null or {ticker_col}=$2)"
        f" and ($3::text is null or {kind_col}=$3)"
    )
    total = await pool.fetchval(
        f"select count(*) from ({select} {where}) q",  # noqa: S608 - constants only
        status,
        ticker,
        kind,
    )
    rows = await pool.fetch(
        f"{select} {where} order by known_at desc, id desc limit $4 offset $5",
        status,
        ticker,
        kind,
        limit,
        offset,
    )
    return int(total or 0), [dict(r) for r in rows]


async def decision_history(
    pool: asyncpg.Pool, kind: HistoryKind, row_id: int
) -> tuple[str, str | None, list[dict[str, Any]]] | None:
    """(current status, current decider, entries) or None when the row does not exist."""
    table, reason_col, by_col = HISTORY_TABLES[kind]
    row = await pool.fetchrow(
        f"select status, {by_col}::text decided_by, {reason_col} reason, decided_at"  # noqa: S608
        f" from public.{table} where id=$1",
        row_id,
    )
    if row is None:
        return None
    log = await pool.fetch(
        """select changed_at, from_status, to_status, reason from public.graph_status_log
           where table_name=$1 and row_id=$2 order by changed_at, id""",
        table,
        row_id,
    )
    entries = history_entries([dict(r) for r in log], row["decided_by"], row["status"])
    if not entries and row["decided_at"] is not None:
        # No logged change (a table without the log trigger): the row's own last decision.
        entries = [
            {
                "changed_at": row["decided_at"],
                "from_status": None,
                "to_status": row["status"],
                "reason": row["reason"],
                "decided_by": row["decided_by"],
            }
        ]
    return row["status"], row["decided_by"], entries


async def resolve_reconciliation(
    pool: asyncpg.Pool,
    reconciliation_id: int,
    resolution: Literal["resolved", "dismissed"],
    reason: str,
    decided_by: str,
) -> dict[str, Any] | None:
    """Close an open reconciliation: 'resolved' stores status `accepted`. The reason and the
    owner are kept in `detail.resolution` (the table has no reason column)."""
    status = "accepted" if resolution == "resolved" else "dismissed"
    row = await pool.fetchrow(
        """update public.graph_reconciliation
           set status=$2,
               detail=detail || jsonb_build_object('resolution', jsonb_build_object(
                 'status',$2::text,'reason',$3::text,'decided_by',$4::text,'decided_at',now()))
           where id=$1 and status='open' returning id,ticker,status""",
        reconciliation_id,
        status,
        reason,
        decided_by,
    )
    return dict(row) if row else None
