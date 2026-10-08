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
        """select p.id, p.deal_id, p.role, p.name, p.known_at
             from public.deal_participant p
            where p.issuer_id is null order by p.id limit $1""",
        limit,
    )
    out: list[PartySuggestion] = []
    for r in rows:
        rejected = {
            int(x["issuer_id"])
            for x in await pool.fetch(
                """select issuer_id from public.deal_participant_decision
                    where participant_id = $1 and decision = 'rejected'""",
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


async def confirm(pool: Pool, participant_id: int, issuer_id: int, by: str) -> bool:
    """The owner says this participant is this issuer. False if the participant is unknown or
    already matched."""
    row: Any = await pool.fetchrow(
        """update public.deal_participant set issuer_id = $2
            where id = $1 and issuer_id is null
              and exists (select 1 from public.issuer where id = $2)
        returning id""",
        participant_id,
        issuer_id,
    )
    if row is None:
        return False
    await pool.execute(
        """insert into public.deal_participant_decision
             (participant_id, issuer_id, decision, decided_by)
           values ($1,$2,'confirmed',$3) on conflict (participant_id, issuer_id)
           do update set decision = 'confirmed', decided_by = $3, decided_at = now()""",
        participant_id,
        issuer_id,
        by,
    )
    return True


async def reject(pool: Pool, participant_id: int, issuer_id: int, by: str) -> None:
    await pool.execute(
        """insert into public.deal_participant_decision
             (participant_id, issuer_id, decision, decided_by)
           values ($1,$2,'rejected',$3) on conflict (participant_id, issuer_id)
           do update set decision = 'rejected', decided_by = $3, decided_at = now()""",
        participant_id,
        issuer_id,
        by,
    )
