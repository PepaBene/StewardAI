"""Status transitions for graph rows and the explicit owner decision path.

Vendors and extractors may only *suggest*: an automated upsert never changes the status or
evidence tier of a row that has been decided (confirmed, rejected or disputed). The owner
moves a row through `decide_*`, which is the only way to confirm without primary evidence
(tier `owner_review`, with a reason), to reject or dispute a confirmed row, or to reopen a
rejection (which also clears the shared rejection store).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

import asyncpg

from steward.graph.keys import (
    PRIMARY_CLASSES,
    EvidenceClass,
    canonical_edge_type,
    evidence_class,
)

Status = Literal["suggested", "confirmed", "rejected", "disputed"]
Decision = Literal["confirm", "reject", "dispute", "reopen"]
DECIDED = frozenset({"confirmed", "rejected", "disputed"})

# decision -> {current status: resulting status}
TRANSITIONS: dict[str, dict[str, str]] = {
    "confirm": {"suggested": "confirmed", "disputed": "confirmed"},
    "reject": {"suggested": "rejected", "confirmed": "rejected", "disputed": "rejected"},
    "dispute": {"suggested": "disputed", "confirmed": "disputed"},
    "reopen": {"rejected": "suggested", "disputed": "suggested"},
}


class GraphDecisionError(ValueError):
    """An illegal transition or a confirmation without evidence."""


class NotFound(GraphDecisionError):
    pass


def next_status(current: str, decision: str) -> str:
    try:
        return TRANSITIONS[decision][current]
    except KeyError:
        raise GraphDecisionError(f"cannot {decision} a {current} row") from None


async def evidence_class_of(
    conn: asyncpg.Connection | asyncpg.Pool, evidence_id: int
) -> EvidenceClass:
    """Class of the document (or observation) a piece of evidence points into."""
    row = await conn.fetchrow(
        """select d.regulator, o.source observation_source
           from public.source_evidence e
           left join public.source_document_version v on v.id=e.document_version_id
           left join public.source_document d on d.id=v.document_id
           left join public.source_observation o on o.id=e.observation_id
           where e.id=$1""",
        evidence_id,
    )
    if row is None:
        raise GraphDecisionError(f"evidence {evidence_id} does not exist")
    return evidence_class(row["regulator"] or row["observation_source"])


async def server_tier(
    conn: asyncpg.Connection | asyncpg.Pool, *, evidence_id: int | None, source: str
) -> EvidenceClass:
    """The evidence tier a write earns, derived on the server and never from the caller: the
    linked document's source class when there is evidence, else the writing source's class,
    never above `unknown` for a source we cannot classify."""
    if evidence_id is not None:
        return await evidence_class_of(conn, evidence_id)
    return evidence_class(source)


def tier_value(tier: EvidenceClass) -> str:
    return "unverified" if tier == "unknown" else tier


def confirmable(tier: EvidenceClass) -> bool:
    return tier in PRIMARY_CLASSES


async def decide_edge(
    pool: asyncpg.Pool,
    edge_id: int,
    decision: Decision,
    *,
    reason: str | None,
    decided_by: uuid.UUID | str | None = None,
) -> dict[str, object]:
    """Owner decision on a company_graph_edge."""
    if decision in ("reject", "dispute", "reopen", "confirm") and not (reason or "").strip():
        raise GraphDecisionError("an owner decision needs a reason")
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "select * from public.company_graph_edge where id=$1 for update", edge_id
        )
        if row is None:
            raise NotFound(f"no edge {edge_id}")
        target = next_status(row["status"], decision)
        tier = row["evidence_tier"]
        if decision == "confirm":
            if row["evidence_id"] is None and not row["passage"]:
                raise GraphDecisionError("confirming needs a linked evidence or a cited passage")
            derived = (
                await evidence_class_of(conn, row["evidence_id"])
                if row["evidence_id"] is not None
                else "owner_review"
            )
            # The owner's own review is an explicit tier, never a silent upgrade of vendor data.
            tier = tier_value(derived) if confirmable(derived) else "owner_review"
        if decision == "reopen":
            await conn.execute(
                "delete from public.graph_rejection where subject_key=$1"
                " and object_key_norm=$2 and edge_type=$3",
                row["subject_key"],
                row["object_key_norm"],
                canonical_edge_type(row["edge_type"]),
            )
            await conn.execute(
                "update public.entity_relationship set status='suggested', decided_at=null"
                " where ticker=$1 and status='rejected'"
                " and public.graph_norm_object_key(object_key)=$2"
                " and public.graph_canon_edge_type(type)=$3",
                row["subject_key"],
                row["object_key_norm"],
                canonical_edge_type(row["edge_type"]),
            )
        updated = await conn.fetchrow(
            """update public.company_graph_edge set status=$2, evidence_tier=$3,
                 decided_at=case when $2='suggested' then null else now() end,
                 decision_reason=$4, decided_by=$5::uuid
               where id=$1 returning *""",
            edge_id,
            target,
            tier,
            reason,
            str(decided_by) if decided_by else None,
        )
    assert updated is not None
    return dict(updated)


async def decide_event(
    pool: asyncpg.Pool,
    event_id: int,
    decision: Decision,
    *,
    reason: str | None,
) -> dict[str, object]:
    """Owner decision on a company_event. Confirming needs linked evidence (G8)."""
    if not (reason or "").strip():
        raise GraphDecisionError("an owner decision needs a reason")
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "select * from public.company_event where id=$1 for update", event_id
        )
        if row is None:
            raise NotFound(f"no event {event_id}")
        target = next_status(row["status"], decision)
        tier = row["evidence_tier"]
        if decision == "confirm":
            if row["evidence_id"] is None:
                raise GraphDecisionError("a confirmed event needs linked evidence")
            derived = await evidence_class_of(conn, row["evidence_id"])
            tier = tier_value(derived) if confirmable(derived) else "owner_review"
        updated = await conn.fetchrow(
            """update public.company_event set status=$2, evidence_tier=$3,
                 decided_at=case when $2='suggested' then null else now() end,
                 decision_reason=$4
               where id=$1 returning *""",
            event_id,
            target,
            tier,
            reason,
        )
    assert updated is not None
    return dict(updated)


async def decide_disclosure(
    pool: asyncpg.Pool,
    disclosure_id: int,
    decision: Decision,
    *,
    reason: str | None,
) -> dict[str, object]:
    """Owner decision on a relationship_disclosure. A confirmation needs a named or explicitly
    anonymous counterparty, a known direction, a fiscal period and a start date."""
    if not (reason or "").strip():
        raise GraphDecisionError("an owner decision needs a reason")
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "select * from public.relationship_disclosure where id=$1 for update", disclosure_id
        )
        if row is None:
            raise NotFound(f"no disclosure {disclosure_id}")
        target = next_status(row["status"], decision)
        if decision == "confirm":
            missing = [
                name
                for name, ok in (
                    ("a known direction", row["direction"] != "unknown"),
                    ("a fiscal period", bool(row["fiscal_period"])),
                    ("a start date", row["valid_from"] is not None),
                    (
                        "a named counterparty or an explicit anonymous flag",
                        bool(row["counterparty_name"] or row["counterparty_key"])
                        or row["anonymous_counterparty"],
                    ),
                )
                if not ok
            ]
            if missing:
                raise GraphDecisionError("cannot confirm without " + ", ".join(missing))
        updated = await conn.fetchrow(
            """update public.relationship_disclosure set status=$2,
                 decided_at=case when $2='suggested' then null else now() end,
                 decision_reason=$3
               where id=$1 returning *""",
            disclosure_id,
            target,
            reason,
        )
        if decision in ("confirm", "reject"):
            await conn.execute(
                """update public.relationship_contradiction set status='resolved',
                     resolution=$2, resolved_at=now()
                   where status='open' and (disclosure_id=$1 or other_disclosure_id=$1)
                     and $3""",
                disclosure_id,
                f"disclosure {target} by owner",
                decision == "reject",
            )
    assert updated is not None
    return dict(updated)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
