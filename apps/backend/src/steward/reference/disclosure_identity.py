"""Identity for disclosure documents (plan P1.5): who a filing mentions, as of when.

Extends `reference/identity.py` (which resolves *holdings*) to document mentions:

* resolution is by identifier first (CIK, LEI, ISIN, FIGI), then dated aliases, and only then by
  name - and a name-only match is a *suggestion*, never a confirmed mapping;
* every decision is stored in `disclosure_entity_mapping` with its candidates, score and basis, so
  a rejected candidate is auditable;
* a disclosure joins a portfolio instrument only through a *confirmed* `instrument_security_link`;
* an anonymous counterparty ("Customer A") keeps its label and never receives a company id;
* corporate succession (rename / merger / spin-off) is read from `issuer_relationship` using only
  facts known by the cutoff.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import unicodedata
from dataclasses import dataclass

import asyncpg

SUGGEST_THRESHOLD = 0.82
_SUFFIXES = re.compile(
    r"\b(incorporated|inc|corporation|corp|company|co|limited|ltd|plc|llc|lp|ag|se|sa|nv|bv|"
    r"spa|ab|asa|oyj|holdings?|group|the)\b\.?",
    re.IGNORECASE,
)
_ANON = re.compile(
    r"^(customer|client|distributor|partner|supplier)\s+[A-Z0-9]{1,2}$", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class Candidate:
    issuer_id: int
    score: float
    basis: str  # cik | lei | isin | figi | registered_name | alias | name
    matched: str


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = _SUFFIXES.sub(" ", text.casefold())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def is_anonymous_label(mention: str) -> bool:
    """'Customer A', 'Distributor 2': an anonymised counterparty, not a company name."""
    return bool(_ANON.match(mention.strip()))


def rank_by_name(mention: str, names: dict[int, list[tuple[str, str]]]) -> list[Candidate]:
    """Score candidates by name. `names` maps issuer id -> [(name, basis)]. An exact normalized
    match is `registered_name`/`alias` (0.95/0.9); anything weaker is `name`, capped below 0.95."""
    target = normalize_name(mention)
    if not target:
        return []
    out: dict[int, Candidate] = {}
    for issuer_id, entries in names.items():
        for name, basis in entries:
            norm = normalize_name(name)
            if not norm:
                continue
            if norm == target:
                score, kind = (0.95, "registered_name") if basis == "legal" else (0.9, "alias")
            else:
                ratio = difflib.SequenceMatcher(None, target, norm).ratio()
                if ratio < SUGGEST_THRESHOLD:
                    continue
                score, kind = round(ratio * 0.9, 3), "name"
            if issuer_id not in out or score > out[issuer_id].score:
                out[issuer_id] = Candidate(issuer_id, score, kind, name)
    return sorted(out.values(), key=lambda c: (-c.score, c.issuer_id))


def decide_status(candidates: list[Candidate]) -> tuple[str, Candidate | None]:
    """`confirmed` only for a unique identifier-grade match; a registered-name/alias match that
    is unique is `suggested`; ambiguity, name-only matches and no match are not confirmed."""
    if not candidates:
        return "unresolved", None
    top = candidates[0]
    tied = len(candidates) > 1 and candidates[1].score >= top.score - 0.05
    if tied:
        return "suggested", None
    if top.basis in ("cik", "lei", "isin", "figi"):
        return "confirmed", top
    return "suggested", top


async def candidates_for(
    pool: asyncpg.Pool | asyncpg.Connection,
    mention: str,
    *,
    cik: str | None = None,
    lei: str | None = None,
    isin: str | None = None,
    figi: str | None = None,
    as_of: dt.datetime | None = None,
) -> list[Candidate]:
    cutoff = as_of or dt.datetime.now(dt.UTC)
    found: list[Candidate] = []
    if cik:
        for row in await pool.fetch(
            "select id from public.issuer where cik=$1", str(int(cik)).zfill(10)
        ) + await pool.fetch("select id from public.issuer where cik=$1", str(int(cik))):
            found.append(Candidate(row["id"], 1.0, "cik", cik))
    if lei:
        for row in await pool.fetch("select id from public.issuer where lei=$1", lei.upper()):
            found.append(Candidate(row["id"], 1.0, "lei", lei))
    for column, value, basis in (("isin", isin, "isin"), ("figi", figi, "figi")):
        if value:
            for row in await pool.fetch(
                f"select issuer_id from public.security"  # noqa: S608 - column is a fixed literal
                f" where {column}=$1 and issuer_id is not null",
                value.upper(),
            ):
                found.append(Candidate(row["issuer_id"], 1.0, basis, value))
    if found:
        return sorted({c.issuer_id: c for c in found}.values(), key=lambda c: c.issuer_id)
    names: dict[int, list[tuple[str, str]]] = {}
    for row in await pool.fetch("select id, legal_name from public.issuer"):
        names.setdefault(row["id"], []).append((row["legal_name"], "legal"))
    for row in await pool.fetch(
        """select issuer_id, name from public.issuer_alias
            where known_at <= $1 and (valid_from is null or valid_from <= $1::date)
              and (valid_to is null or valid_to >= $1::date
                   or (valid_to_known_at is not null and valid_to_known_at > $1))""",
        cutoff,
    ):
        names.setdefault(row["issuer_id"], []).append((row["name"], "alias"))
    return rank_by_name(mention, names)


async def resolve_mention(
    pool: asyncpg.Pool | asyncpg.Connection,
    mention: str,
    *,
    document_version_id: int | None = None,
    mention_kind: str = "issuer",
    cik: str | None = None,
    lei: str | None = None,
    isin: str | None = None,
    figi: str | None = None,
    as_of: dt.datetime | None = None,
) -> dict[str, object]:
    """Resolve and store the decision for one document mention. An anonymous label becomes an
    anonymous counterparty with no entity id; ambiguity or a name-only match is only suggested."""
    if is_anonymous_label(mention):
        row = await pool.fetchrow(
            """insert into public.disclosure_entity_mapping
                 (document_version_id, mention, mention_kind, anonymous_label, status,
                  match_basis, decided_by)
               values($1,$2,'counterparty',$2,'confirmed','name','rule:anonymous-label')
               on conflict (coalesce(document_version_id,0), lower(mention), mention_kind)
               do update set mention = excluded.mention returning id, status""",
            document_version_id,
            mention.strip(),
        )
        assert row is not None
        return {"id": row["id"], "status": "confirmed", "issuer_id": None, "anonymous": True}
    candidates = await candidates_for(
        pool, mention, cik=cik, lei=lei, isin=isin, figi=figi, as_of=as_of
    )
    status, chosen = decide_status(candidates)
    row = await pool.fetchrow(
        """insert into public.disclosure_entity_mapping
             (document_version_id, mention, mention_kind, issuer_id, status, score, candidate_ids,
              match_basis)
           values($1,$2,$3,$4,$5,$6,$7::jsonb,$8)
           on conflict (coalesce(document_version_id,0), lower(mention), mention_kind)
           do update set candidate_ids = excluded.candidate_ids, score = excluded.score
           returning id, status, issuer_id""",
        document_version_id,
        mention.strip(),
        mention_kind,
        chosen.issuer_id if chosen and status == "confirmed" else None,
        status,
        chosen.score if chosen else None,
        json.dumps(
            [{"issuer_id": c.issuer_id, "score": c.score, "basis": c.basis} for c in candidates]
        ),
        chosen.basis if chosen else "name",
    )
    assert row is not None
    return {
        "id": row["id"],
        "status": row["status"],
        "issuer_id": row["issuer_id"],
        "anonymous": False,
        "candidates": candidates,
    }


async def successors(
    pool: asyncpg.Pool | asyncpg.Connection, issuer_id: int, as_of: dt.datetime | None = None
) -> list[int]:
    """The issuers an issuer became (rename / merger / spin-off) using only succession facts that
    were known and in force by `as_of`; the chain is followed, cycles are ignored."""
    cutoff = as_of or dt.datetime.now(dt.UTC)
    chain: list[int] = []
    frontier = [issuer_id]
    seen = {issuer_id}
    while frontier:
        rows = await pool.fetch(
            """select successor_id from public.issuer_relationship
                where predecessor_id = any($1::bigint[]) and known_at <= $2
                  and valid_from <= $2::date and (valid_to is null or valid_to >= $2::date)
                  and relationship in ('renamed_to','merged_into')""",
            frontier,
            cutoff,
        )
        frontier = []
        for row in rows:
            if row["successor_id"] not in seen:
                seen.add(row["successor_id"])
                chain.append(row["successor_id"])
                frontier.append(row["successor_id"])
    return chain


async def portfolio_tickers(pool: asyncpg.Pool | asyncpg.Connection, issuer_id: int) -> list[str]:
    """Portfolio instruments a disclosure about `issuer_id` may be joined to: only through a
    *confirmed* instrument-security link. A suggested link joins nothing."""
    rows = await pool.fetch(
        """select l.ticker from public.instrument_security_link l
             join public.security s on s.id = l.security_id
            where s.issuer_id = $1 and l.status = 'confirmed' order by l.ticker""",
        issuer_id,
    )
    return [r["ticker"] for r in rows]
