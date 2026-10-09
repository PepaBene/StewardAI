"""P6: match the parties named in a deal to issuers, with a reviewer step.

A deal participant is stored with a name and a role and no issuer. This proposes an issuer from the
registered name or a dated alias (`entities.resolve`) and never writes one by itself: a name match
alone is a suggestion until the owner confirms it. A confirmed match sets
`deal_participant.issuer_id`; every confirm and reject is kept in `deal_participant_decision`, and
a rejected issuer is not offered for that participant again.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import asyncpg

from steward.disclosure import entities as ent

type Pool = asyncpg.Pool | asyncpg.Connection


@dataclass(frozen=True)
class PartySuggestion:
    participant_id: int
    deal_id: int
    role: str
    name: str
    status: str
    issuer_ids: tuple[int, ...]
    basis: str | None
    note: str


async def candidates_for(
    pool: Pool, name: str, on: dt.date, rejected: set[int] | None = None
) -> ent.Resolution:
    prefix = (ent.norm(name).split() or [""])[0]
    if not prefix:
        return ent.Resolution("none", note="no name to match")
    issuers = await pool.fetch(
        """select id, legal_name, cik, lei from public.issuer
            where lower(legal_name) like $1 || '%' limit 100""",
        prefix,
    )
    aliases = await pool.fetch(
        """select issuer_id, name, kind, valid_from, valid_to from public.issuer_alias
            where lower(name) like $1 || '%' limit 100""",
        prefix,
    )
    skip = rejected or set()
    res = ent.resolve(
        name,
        [ent.IssuerRef(i["id"], i["legal_name"], i["cik"], i["lei"]) for i in issuers],
        [
            ent.Alias(a["issuer_id"], a["name"], a["kind"], a["valid_from"], a["valid_to"])
            for a in aliases
        ],
        on,
    )
    kept = tuple(i for i in res.issuer_ids if i not in skip)
    if kept == res.issuer_ids:
        return res
    if not kept:
        return ent.Resolution("none", note="the only matches were rejected before")
    return ent.Resolution(
        "candidate" if len(kept) == 1 else "ambiguous", kept, res.basis, True, res.note
    )


async def pending(pool: Pool, limit: int = 50) -> list[PartySuggestion]:
    """Participants without an issuer, each with the issuers its name could belong to."""
    rows = await pool.fetch(
        f"""select p.id, p.deal_id, p.role, p.name, p.known_at
             from public.deal_participant p
            where not exists (select 1 from ({effective_match_sql("now()")}) m
                               where m.participant_id = p.id and m.decision = 'confirmed')
            order by p.id limit $1""",  # noqa: S608 - fixed fragments, no input
        limit,
    )
    out: list[PartySuggestion] = []
    for r in rows:
        rejected = {
            int(x["issuer_id"])
            for x in await pool.fetch(
                """select issuer_id from (
                     select distinct on (issuer_id) issuer_id, decision
                       from public.deal_participant_decision where participant_id = $1
                      order by issuer_id, decided_at desc, id desc) latest
                    where decision = 'rejected'""",
                r["id"],
            )
        }
        res = await candidates_for(pool, r["name"], r["known_at"].date(), rejected)
        out.append(
            PartySuggestion(
                r["id"],
                r["deal_id"],
                r["role"],
                r["name"],
                res.status,
                res.issuer_ids,
                res.basis,
                res.note,
            )
        )
    return out


# The issuer a participant is matched to as of a moment: the latest decision made by then, and only
# if that decision is a confirmation. Reads use this, never `deal_participant.issuer_id`, which
# is a convenience copy of the present and says nothing about the past. `$n` is the as-of.
EFFECTIVE_MATCH_SQL = """
  select distinct on (pair.participant_id) pair.participant_id, pair.issuer_id, pair.decision
    from (select distinct on (d.participant_id, d.issuer_id)
                 d.participant_id, d.issuer_id, d.decision, d.decided_at, d.id
            from public.deal_participant_decision d
           where d.decided_at <= {as_of}
           order by d.participant_id, d.issuer_id, d.decided_at desc, d.id desc) pair
   order by pair.participant_id, (pair.decision = 'confirmed') desc, pair.decided_at desc,
            pair.id desc
"""


def effective_match_sql(as_of_param: str) -> str:
    """SQL rows (participant_id, issuer_id, decision), one per participant, from the decisions made
    at or before the parameter. Decisions are append-only; for each issuer the latest one stands,
    so rejecting one issuer never clears a confirmation of another. A participant whose row says
    `rejected` has no confirmed issuer."""
    return EFFECTIVE_MATCH_SQL.format(as_of=as_of_param)


async def confirm(pool: Pool, participant_id: int, issuer_id: int, by: str) -> bool:
    """The owner says this participant is this issuer. False if the participant or issuer is
    unknown, or the participant is already matched (reject that match first). The decision is
    what as-of reads use; `issuer_id` on the participant is kept only as today's convenience."""
    taken: Any = await pool.fetchval(
        f"""select count(*) from ({effective_match_sql("now()")}) m
            where m.decision = 'confirmed' and m.participant_id = $1""",  # noqa: S608
        participant_id,
    )
    if taken:
        return False
    row: Any = await pool.fetchrow(
        """select p.id from public.deal_participant p
            where p.id = $1 and exists (select 1 from public.issuer where id = $2)""",
        participant_id,
        issuer_id,
    )
    if row is None:
        return False
    await pool.execute(
        """insert into public.deal_participant_decision
             (participant_id, issuer_id, decision, decided_by)
           values ($1,$2,'confirmed',$3)""",
        participant_id,
        issuer_id,
        by,
    )
    await pool.execute(
        "update public.deal_participant set issuer_id = $2 where id = $1", participant_id, issuer_id
    )
    return True


async def reject(pool: Pool, participant_id: int, issuer_id: int, by: str) -> None:
    """Record that this participant is not this issuer. A rejection after a confirmation clears the
    effective match, and the cached `issuer_id` when it pointed at that issuer."""
    await pool.execute(
        """insert into public.deal_participant_decision
             (participant_id, issuer_id, decision, decided_by)
           values ($1,$2,'rejected',$3)""",
        participant_id,
        issuer_id,
        by,
    )
    await pool.execute(
        "update public.deal_participant set issuer_id = null where id = $1 and issuer_id = $2",
        participant_id,
        issuer_id,
    )
