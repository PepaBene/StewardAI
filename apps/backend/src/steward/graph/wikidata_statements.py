"""Statement-level Wikidata provenance (D5.11).

`parse_entity_statements` reads the documented entity JSON (`Special:EntityData/Qxxx.json`,
`entities[Q].claims[P][]` with `mainsnak.datavalue`, `rank`, `qualifiers`, `references`, and
the statement `id`). `sync_statements` stores each statement with its rank, qualifiers, the
references that back it, `last_checked_at`, and **retains** statements that later disappear or
change as superseded rows instead of deleting them: a claim that was current when we acted on
it stays reconstructable. Wikidata remains a candidate-discovery source; nothing here writes
an authoritative edge.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import asyncpg

# Properties the graph cares about (parent, subsidiary, owner, industry, product, officers...).
TRACKED_PROPERTIES = {
    "P749": "parent organization",
    "P355": "subsidiary",
    "P127": "owned by",
    "P452": "industry",
    "P1056": "product",
    "P169": "chief executive officer",
    "P488": "chairperson",
    "P856": "official website",
    "P361": "part of",
    "P1366": "replaced by",
    "P1365": "replaces",
    "P576": "dissolved, abolished or demolished date",
}
RANK_ORDER = {"preferred": 0, "normal": 1, "deprecated": 2}


@dataclass(frozen=True, slots=True)
class Statement:
    qid: str
    property_id: str
    statement_id: str
    value_qid: str | None
    value_text: str | None
    rank: str
    qualifiers: dict[str, Any] = field(default_factory=dict)
    references: list[dict[str, Any]] = field(default_factory=list)
    start_time: dt.date | None = None
    end_time: dt.date | None = None

    @property
    def content_hash(self) -> str:
        payload = json.dumps(
            [self.value_qid, self.value_text, self.rank, self.qualifiers, self.references],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _wd_date(snak: dict[str, Any]) -> dt.date | None:
    value = (snak.get("datavalue") or {}).get("value")
    text = value.get("time") if isinstance(value, dict) else None
    if not isinstance(text, str):
        return None
    try:
        return dt.date.fromisoformat(text.lstrip("+")[:10])
    except ValueError:
        return None  # e.g. "+2019-00-00T00:00:00Z" (year precision)


def _snak_value(snak: dict[str, Any]) -> tuple[str | None, str | None]:
    value = (snak.get("datavalue") or {}).get("value")
    if isinstance(value, dict):
        if isinstance(value.get("id"), str):
            return value["id"], None
        if isinstance(value.get("time"), str):
            return None, value["time"].lstrip("+")[:10]
        if isinstance(value.get("text"), str):
            return None, value["text"]
    if isinstance(value, str):
        return None, value
    return None, None


def parse_entity_statements(entity_json: dict[str, Any], qid: str) -> list[Statement]:
    entity = (entity_json.get("entities") or {}).get(qid)
    if not isinstance(entity, dict):
        raise ValueError(f"entity {qid} missing from the Wikidata response")
    out: list[Statement] = []
    for prop, claims in (entity.get("claims") or {}).items():
        if prop not in TRACKED_PROPERTIES:
            continue
        for claim in claims:
            snak = claim.get("mainsnak") or {}
            if snak.get("snaktype") != "value":
                continue  # "somevalue" / "novalue" carry no claim we can use
            value_qid, value_text = _snak_value(snak)
            if value_qid is None and value_text is None:
                continue
            qualifiers: dict[str, Any] = {}
            start = end = None
            for qprop, snaks in (claim.get("qualifiers") or {}).items():
                values = [_snak_value(s) for s in snaks if s.get("snaktype") == "value"]
                qualifiers[qprop] = [v[0] or v[1] for v in values]
                if qprop == "P580" and snaks:
                    start = _wd_date(snaks[0])
                if qprop == "P582" and snaks:
                    end = _wd_date(snaks[0])
            references = []
            for ref in claim.get("references") or []:
                refs = {
                    p: [_snak_value(s)[0] or _snak_value(s)[1] for s in snaks]
                    for p, snaks in (ref.get("snaks") or {}).items()
                }
                references.append(refs)
            out.append(
                Statement(
                    qid=qid,
                    property_id=prop,
                    statement_id=str(claim.get("id")),
                    value_qid=value_qid,
                    value_text=value_text,
                    rank=str(claim.get("rank", "normal")),
                    qualifiers=qualifiers,
                    references=references,
                    start_time=start,
                    end_time=end,
                )
            )
    return out


async def sync_statements(
    pool: asyncpg.Pool,
    qid: str,
    statements: list[Statement],
    *,
    checked_at: dt.datetime | None = None,
) -> dict[str, int]:
    """Bring the stored statements of `qid` in line with a fresh read.

    * unchanged statement: `last_checked_at` moves;
    * changed statement (same id, different content): the old row is superseded by the new;
    * statement gone from the entity: superseded with reason `removed upstream`;
    * nothing is ever deleted."""
    checked_at = checked_at or dt.datetime.now(dt.UTC)
    counts = {"unchanged": 0, "added": 0, "changed": 0, "removed": 0}
    async with pool.acquire() as conn, conn.transaction():
        current = {
            r["statement_id"]: r
            for r in await conn.fetch(
                "select id,statement_id,content_hash from public.wikidata_statement"
                " where qid=$1 and superseded_at is null for update",
                qid,
            )
        }
        seen: set[str] = set()
        for st in statements:
            seen.add(st.statement_id)
            existing = current.get(st.statement_id)
            if existing is not None and existing["content_hash"] == st.content_hash:
                await conn.execute(
                    "update public.wikidata_statement set last_checked_at=$2 where id=$1",
                    existing["id"],
                    checked_at,
                )
                counts["unchanged"] += 1
                continue
            new_id = await conn.fetchval(
                """insert into public.wikidata_statement
                   (qid,property_id,statement_id,value_qid,value_text,rank,qualifiers,
                    statement_references,start_time,end_time,content_hash,first_seen_at,
                    last_checked_at)
                   values($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9,$10,$11,$12,$12)
                   on conflict (statement_id,content_hash) do update set
                     last_checked_at=excluded.last_checked_at, superseded_at=null,
                     superseded_by_id=null
                   returning id""",
                qid,
                st.property_id,
                st.statement_id,
                st.value_qid,
                st.value_text,
                st.rank,
                json.dumps(st.qualifiers, default=str),
                json.dumps(st.references, default=str),
                st.start_time,
                st.end_time,
                st.content_hash,
                checked_at,
            )
            if existing is not None:
                await conn.execute(
                    "update public.wikidata_statement set superseded_at=$2, superseded_by_id=$3"
                    " where id=$1 and id<>$3",
                    existing["id"],
                    checked_at,
                    new_id,
                )
                counts["changed"] += 1
            else:
                counts["added"] += 1
        for statement_id, row in current.items():
            if statement_id not in seen:
                await conn.execute(
                    "update public.wikidata_statement set superseded_at=$2 where id=$1",
                    row["id"],
                    checked_at,
                )
                counts["removed"] += 1
    return counts


async def current_statements(
    pool: asyncpg.Pool, qid: str, property_id: str | None = None
) -> list[dict[str, Any]]:
    """Current statements, preferred rank first; deprecated ones are returned last and marked
    (never silently dropped, never used as the answer)."""
    rows = await pool.fetch(
        """select * from public.wikidata_statement
           where qid=$1 and superseded_at is null and ($2::text is null or property_id=$2)
           order by property_id, case rank when 'preferred' then 0 when 'normal' then 1 else 2 end,
                    id""",
        qid,
        property_id,
    )
    return [dict(r) for r in rows]
